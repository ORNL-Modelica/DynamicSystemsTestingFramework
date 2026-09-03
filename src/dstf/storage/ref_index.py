"""In-memory index over ref_NNNN.json files, plus the atomic-write primitive.

The ref files are the source of truth — there is no persistent manifest.
:class:`RefIndex` builds the model_id ↔ test_id mapping by scanning them and
owns ID allocation (O_EXCL claim + per-partition high-water mark) so IDs are
never reused. :func:`_atomic_write_text` lives here because ID allocation is
its most safety-critical caller; every other JSON write in the package uses it
too.
"""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


# Pattern matching ref_NNNN.json filenames
_REF_FILE_PATTERN = re.compile(r"^ref_(\d{4,})\.json$")

#: Per-partition high-water mark for allocated test IDs. A dot-file, so the
#: ``ref_*.json`` scan glob never picks it up.
_ID_HIGH_WATER_FILE = ".id_high_water.json"


def _atomic_write_text(path: Path, text: str, *, newline: str | None = None) -> None:
    """Atomically replace ``path`` with ``text``.

    review 2026-07-06 (finding 31): all reference writes were plain
    ``write_text`` — a Ctrl-C mid-accept corrupted the baseline, the next
    scan silently dropped it, and ``next_id()`` re-allocated its ID to a
    different model. Same tmp-file + replace pattern as
    ``dashboard_render._atomic_write`` (unique tmp name in the same
    directory, retry loop for Windows file locking), except this variant
    always raises on failure — a silently stale baseline is a
    data-integrity bug, not a cosmetic one.

    ``newline`` is passed through to :func:`open`; callers that already
    embedded their own line terminators (``csv`` writes ``\\r\\n``) pass
    ``""`` to suppress the platform translation that would double them.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        last_err: OSError | None = None
        for delay in (0, 0.05, 0.1, 0.2, 0.5):
            if delay:
                time.sleep(delay)
            try:
                tmp.replace(path)
                return
            except OSError as e:
                last_err = e
        assert last_err is not None
        raise last_err
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


class RefIndex:
    """In-memory index mapping model IDs to ref files.

    Built by scanning ref_NNNN.json files in the reference directory.
    No persistent manifest file — the ref files are the source of truth.
    """

    def __init__(self, ref_dir: Path):
        self.ref_dir = ref_dir
        self._by_model: dict[str, str] = {}  # model_id -> test_id
        self._by_id: dict[str, dict] = {}  # test_id -> {model_id, status}
        self._loaded = False

    def _scan(self):
        """Scan ref files and build the in-memory index."""
        self._by_model.clear()
        self._by_id.clear()

        if not self.ref_dir.exists():
            self._loaded = True
            return

        for ref_file in sorted(self.ref_dir.glob("ref_*.json")):
            m = _REF_FILE_PATTERN.match(ref_file.name)
            if not m:
                continue
            test_id = m.group(1)
            try:
                # Whole-file read + parse. Only model_id/status are used, and
                # they sit at the top, but json has no partial-parse entry
                # point — a header-only read is idea #64, not done here.
                text = ref_file.read_text(encoding="utf-8")
                data = json.loads(text)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping unreadable ref file %s: %s", ref_file.name, e)
                continue

            model_id = data.get("model_id")
            if not model_id:
                logger.warning("Ref file %s has no model_id — skipping", ref_file.name)
                continue

            status = data.get("status", "active")
            self._by_id[test_id] = {"model_id": model_id, "status": status}
            if status != "obsolete":
                existing = self._by_model.get(model_id)
                if existing is not None and existing != test_id:
                    # review 2026-07-06 (finding 36): two active ref files
                    # claim the same model_id. Keep the LOWEST-numbered one —
                    # deterministic, and the oldest is the established
                    # baseline — and warn loudly instead of silent last-wins.
                    keep = min(existing, test_id, key=int)
                    drop = max(existing, test_id, key=int)
                    logger.warning(
                        "Duplicate model_id %r in %s and %s — keeping the "
                        "lowest-numbered file (%s); mark the other obsolete "
                        "or delete it.",
                        model_id,
                        RefIndex.ref_filename(keep),
                        RefIndex.ref_filename(drop),
                        RefIndex.ref_filename(keep),
                    )
                    self._by_model[model_id] = keep
                else:
                    self._by_model[model_id] = test_id

        self._loaded = True

    def _ensure_loaded(self):
        if not self._loaded:
            self._scan()

    def get_id(self, model_id: str) -> str | None:
        """Look up the numeric ID for a model. Returns None if not found."""
        self._ensure_loaded()
        return self._by_model.get(model_id)

    def get_model_id(self, test_id: str) -> str | None:
        """Look up the model ID for a numeric test ID."""
        self._ensure_loaded()
        entry = self._by_id.get(test_id)
        if entry and entry["status"] != "obsolete":
            return entry["model_id"]
        return None

    def _read_high_water(self) -> int:
        """Return the persisted high-water mark, or 0 when there is none.

        Absent / unreadable file → 0, i.e. fall back to scan-only allocation.
        Partitions written before the high-water file existed keep working.
        """
        hw_file = self.ref_dir / _ID_HIGH_WATER_FILE
        try:
            data = json.loads(hw_file.read_text(encoding="utf-8"))
            return int(data["high_water"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return 0

    def _bump_high_water(self, test_id: str) -> None:
        """Raise the persisted high-water mark to cover ``test_id``.

        Concurrent bumps are last-writer-wins on the atomic replace, so a
        plain read-check-write could REGRESS the mark: P1 (claimed 0005)
        reads 4, P2 (claimed 0006) writes 6, P1 overwrites with 5 (A5
        review finding 1; a write-then-verify retry does not close it —
        the higher writer can verify before the lower writer's overwrite).
        Instead every bump folds in a FRESH disk scan: a competitor
        O_EXCL-claims its ref file BEFORE bumping (register's claim
        ordering), so any mark we could clobber corresponds to a ref file
        already visible on disk, and writing ``max(claimed, scan, stored)``
        makes last-writer-wins harmless.
        """
        value = int(test_id)
        try:
            self.ref_dir.mkdir(parents=True, exist_ok=True)
            scan_max = max(
                (
                    int(m.group(1))
                    for m in (
                        _REF_FILE_PATTERN.match(p.name)
                        for p in self.ref_dir.glob("ref_*.json")
                    )
                    if m
                ),
                default=0,
            )
            target = max(value, scan_max)
            if self._read_high_water() >= target:
                return
            _atomic_write_text(
                self.ref_dir / _ID_HIGH_WATER_FILE,
                json.dumps({"high_water": target}, indent=2) + "\n",
            )
        except OSError as e:
            # Losing the mark only costs us the "never reused" guarantee for
            # this partition — never a failed accept.
            logger.warning("Could not persist test-ID high-water mark: %s", e)

    def next_id(self) -> str:
        """Return the next available numeric ID.

        IDs are never reused: the next ID clears both the highest ref file
        currently on disk *and* the persisted high-water mark, so deleting the
        highest-numbered ref (``manifest cleanup`` after it was marked
        obsolete) does not hand its ID to a different model in a later run.
        """
        self._ensure_loaded()
        max_id = max((int(k) for k in self._by_id), default=0)
        return f"{max(max_id, self._read_high_water()) + 1:04d}"

    def register(self, model_id: str) -> str:
        """Register a model and return its ID. Reuses existing ID if already registered.

        review 2026-07-06 (findings 32+33): new IDs are claimed by
        *exclusively creating* the ref file on disk (``open(..., "x")``).
        A stale in-memory index (e.g. after ``cleanup_obsolete`` freed the
        highest ID) or a concurrent ``--accept`` in another process could
        both hand out an already-taken ID — the O_EXCL create makes the
        claim atomic; on collision we rescan and move to the next free ID
        instead of silently overwriting another model's baseline.
        """
        existing = self.get_id(model_id)
        if existing is not None:
            return existing

        self.ref_dir.mkdir(parents=True, exist_ok=True)
        candidate = int(self.next_id())
        while True:
            test_id = f"{candidate:04d}"
            ref_file = self.ref_dir / self.ref_filename(test_id)
            try:
                with open(ref_file, "x", encoding="utf-8") as f:
                    # Minimal valid claim stub — store_reference atomically
                    # replaces it with the full baseline immediately after.
                    json.dump(
                        {"model_id": model_id, "test_id": test_id, "status": "active"},
                        f,
                    )
            except FileExistsError:
                # ID already taken on disk (concurrent writer, or a file our
                # stale index missed). Rescan, then move past the collision —
                # the max() guard guarantees progress even when the colliding
                # file is unreadable and therefore invisible to next_id().
                self._scan()
                claimed = self.get_id(model_id)
                if claimed is not None:
                    return claimed  # another process registered this model
                candidate = max(candidate + 1, int(self.next_id()))
                continue
            self._by_id[test_id] = {"model_id": model_id, "status": "active"}
            self._by_model[model_id] = test_id
            self._bump_high_water(test_id)
            return test_id

    def active_tests(self) -> dict[str, str]:
        """Return dict of test_id -> model_id for all active (non-obsolete, non-skip) tests."""
        self._ensure_loaded()
        return {
            tid: entry["model_id"]
            for tid, entry in self._by_id.items()
            if entry["status"] not in ("obsolete",)
        }

    def all_tests(self) -> dict[str, dict]:
        """Return dict of test_id -> {model_id, status} for all tests."""
        self._ensure_loaded()
        return dict(self._by_id)

    @staticmethod
    def ref_filename(test_id: str) -> str:
        """Reference filename for a given numeric test ID."""
        return f"ref_{test_id}.json"
