"""The per-run batch manifest: test_key ↔ model_id, and the run's results.

Lives in :mod:`dstf.storage` with the other on-disk record keepers — it is a
JSON file in the work dir, written through the same atomic primitive as the
reference files. The backends package re-exports it (``from dstf.backends
import BatchManifest``) because every backend's ``run_tests`` returns one.

``TestRunResult`` is imported only for typing: keeping it out of the runtime
import graph is what lets ``dstf.storage.batch_manifest`` be imported first,
before ``dstf.backends`` has run its package ``__init__``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .ref_index import _atomic_write_text
from .run_stats import write_run_stats

if TYPE_CHECKING:
    from ..backends.types import TestRunResult

logger = logging.getLogger(__name__)

#: A run that was stopped on request (the simulation queue's cancel). It is
#: recorded, not left pending: a pending entry falls through to the
#: status.json cross-check, where an earlier run's "failed" row for the same
#: model would be applied to a test nobody failed.
CANCELLED_RUN_STATUS = "cancelled"

#: ``last_run_status`` values that mean "this run recorded what became of
#: this test". Only these make a manifest entry authoritative for a reader.
RECORDED_RUN_STATUSES = ("passed", "failed", "timed_out", CANCELLED_RUN_STATUS)

#: Stamped on every in-scope entry when a run starts, and overwritten the
#: instant that test's result lands. An entry still saying ``pending`` when a
#: reader arrives means a run began and never reached a verdict for it — an
#: abort, a crash, a Ctrl-C — so the reader must NOT treat it as authoritative
#: and must fall back to the status.json cross-check. Without this stamp a
#: PREVIOUS run's ``passed`` survived ``assign_test_keys``' carry-forward into
#: an aborted run's manifest and reported green over a partial ``.mat``.
PENDING_RUN_STATUS = "pending"

#: The two statuses of a last run that left NO result — it never finished the
#: test, or was told to stop. Whatever file such a run left is partial, and
#: is neither read nor accepted.
NO_RESULT_RUN_STATUSES = (PENDING_RUN_STATUS, CANCELLED_RUN_STATUS)


@dataclass
class BatchManifest:
    """Maps test keys to model IDs (and optionally ref IDs) and tracks run results."""

    work_dir: Path
    manifest: dict[str, dict]  # test_NNNN -> {"model_id": ..., "ref_id": ...}
    results: list[TestRunResult] = field(default_factory=list)
    #: Serializes the read-modify-save in :meth:`record_result`, which every
    #: dispatch thread calls as its test finishes.
    _write_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def test_dir(self, test_key: str) -> Path:
        """Per-test subdirectory for all simulation artifacts."""
        return self.work_dir / test_key

    def model_id(self, test_key: str) -> str:
        """Get the model ID for a test key."""
        return self.manifest[test_key]["model_id"]

    def save(self) -> Path:
        path = self.work_dir / "batch_manifest.json"
        # review 2026-07-06 finding 66: atomic replace — a torn write made
        # every subsequent run/compare crash at startup until the file was
        # hand-deleted.
        _atomic_write_text(path, json.dumps(self.manifest, indent=2))
        return path

    def record_result(self, result: TestRunResult, metadata: dict | None) -> None:
        """Stamp one test's verdict into its manifest entry and persist NOW.

        The run's per-test outcome has to outlive the process that produced
        it: a later ``compare`` holds no run results, so without this it can
        only read the ``.mat`` on disk — and a partial/stale one from a failed
        run reads back as a success (D93 follow-on). ``last_run_status`` is
        that record, in status.json's vocabulary
        (``passed`` / ``failed`` / ``timed_out``), plus
        :data:`CANCELLED_RUN_STATUS` for a run that was stopped on request.

        Written per result rather than in a run epilogue **because the
        epilogue is exactly what an abort skips**: Ctrl-C or an exception in
        the dispatch loop would leave every entry at
        :data:`PENDING_RUN_STATUS`, discarding verdicts the run had already
        reached. Each save is a small atomic replace, on the same order of
        cost as the status.json write the progress reporter already does per
        state change.

        Entries this run produced no result for are untouched, which is what
        makes ``--merge`` and ``--rerun`` reports honest about the tests they
        did not re-run.
        """
        if result.cancelled:
            status = CANCELLED_RUN_STATUS
        elif result.timed_out:
            status = "timed_out"
        else:
            status = "passed" if result.success else "failed"
        with self._write_lock:
            for entry in self.manifest.values():
                if entry["model_id"] == result.model_id:
                    entry["last_run_status"] = status
            self.save()
        # The run's walls and statistics, and *metadata* — the run that
        # produced this result (RunMetadata.as_dict, None when it recorded
        # none) — beside the result they describe (storage.run_stats): an
        # accept reads its provenance from there, never from status.json,
        # which is one snapshot per work dir. After the verdict is saved and
        # outside its lock: the file is the test's own, and its write never
        # raises.
        write_run_stats(self.test_dir(result.test_key), result, metadata)

    def mark_pending(self, model_ids: set[str]) -> None:
        """Stamp :data:`PENDING_RUN_STATUS` on the entries a run is about to
        (re-)run, so a stale verdict cannot survive into it.

        ``assign_test_keys`` carries every prior entry forward, so without
        this a previous run's ``passed`` would still be sitting on a test that
        this run fails and then aborts before recording — and the reader would
        trust it. Entries outside *model_ids* keep their own last verdict.

        Does not save: the caller (``_prepare_run``) saves the manifest once
        the keys are assigned.
        """
        for entry in self.manifest.values():
            if entry["model_id"] in model_ids:
                entry["last_run_status"] = PENDING_RUN_STATUS

    def enrich_ref_ids(self, ref_index) -> None:
        """Add ref_id to each entry using the reference index."""
        for _test_key, entry in self.manifest.items():
            ref_id = ref_index.get_id(entry["model_id"])
            entry["ref_id"] = f"ref_{ref_id}" if ref_id else None
        self.save()

    @classmethod
    def load(cls, path: Path) -> BatchManifest:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        except (ValueError, OSError) as exc:
            # review 2026-07-06 finding 66: a corrupt manifest must not crash
            # startup. Quarantine it (keeping the bytes for forensics) and
            # start from an empty manifest — every caller already treats a
            # missing manifest as empty, so test keys are simply re-assigned
            # on the next run. (JSONDecodeError is a ValueError subclass.)
            corrupt = path.with_name(f"{path.name}.corrupt")
            where = ""
            try:
                path.replace(corrupt)
                where = f"; moved to {corrupt.name}"
            except OSError:
                pass
            logger.warning(
                "Corrupt batch manifest %s (%s)%s — starting with an empty manifest",
                path,
                exc,
                where,
            )
            return cls(work_dir=path.parent, manifest={})
        # Handle legacy format: test_key -> model_id string
        normalized = {}
        for key, val in data.items():
            if isinstance(val, str):
                normalized[key] = {"model_id": val, "ref_id": None}
            else:
                normalized[key] = val
        return cls(work_dir=path.parent, manifest=normalized)


def unfinished_run_models(work_dir: Path) -> set[str]:
    """Model IDs whose last run started them and never reached a verdict.

    A manifest entry still reading :data:`PENDING_RUN_STATUS` means a run
    stamped it at startup and was aborted (Ctrl-C, crash, killed worker pool)
    before that test finished; one reading :data:`CANCELLED_RUN_STATUS` was
    stopped on request. The accept path uses this: blessing such a
    test's on-disk ``.mat`` would bake in whatever the simulator had flushed
    at the moment it died, and a truncated result file can still parse.

    Entries with no field at all are NOT included — a manifest written before
    the field existed says nothing about completeness, so those keep the
    pre-existing status.json-guard behaviour (old data, not old code).
    Returns an empty set when the work dir has no manifest.
    """
    manifest_path = work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return set()
    return {
        entry["model_id"]
        for entry in BatchManifest.load(manifest_path).manifest.values()
        if entry.get("last_run_status") in NO_RESULT_RUN_STATUSES
    }
