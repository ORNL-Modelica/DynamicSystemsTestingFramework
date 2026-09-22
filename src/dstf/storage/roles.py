"""Soft_check and companion storage — the two non-primary baseline roles (D66).

Both roles hang off a model's primary ref file: soft_checks in
``<ref_dir>/soft_checks/ref_NNNN/<name>.json`` (scored via ``against:``, but
the validator forces ``warn`` wrapping so they never hard-fail) and companions
in ``<ref_dir>/companions/ref_NNNN/<name>.{json,csv}`` (plot-only overlays,
never scored against; the metadata records which of the companion's own
variables draw on which of the test's).

The CRUD is packaged as :class:`_RolesMixin` so the public call sites stay
``store.add_soft_check(...)`` / ``store.get_companions(...)``. The mixin's
whole dependency on the store is the seam declared at the top of the class:
``ref_dir`` and ``_ref_file_for_model``.
"""

import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .baseline import PRIMARY_BASELINE, Baseline
from .ref_index import _atomic_write_text

logger = logging.getLogger(__name__)


# review 2026-07-06 (finding 35): soft_check/companion names are interpolated
# into file paths (``soft_checks/ref_NNNN/<name>.json``) — an unvalidated name
# containing a separator or ".." writes outside the role dir and becomes
# invisible to list/remove/``against``. Windows-illegal characters are also
# rejected because reference partitions are shared across OSes.
_ROLE_NAME_FORBIDDEN = ("/", "\\", "..", ":", "*", "?", '"', "<", ">", "|", "\0")


def _validate_role_name(name: str, role: str) -> None:
    """Reject soft_check/companion names that differ from their filename-safe
    form, with a clear error naming the offending character."""
    if not name or not isinstance(name, str):
        raise ValueError(f"{role} name must be a non-empty string")
    for token in _ROLE_NAME_FORBIDDEN:
        if token in name:
            raise ValueError(
                f"{role} name {name!r} contains {token!r} — names must be "
                f"plain filenames (no path separators, '..', or characters "
                f"illegal on Windows)"
            )
    if name == "." or name != name.strip():
        raise ValueError(
            f"{role} name {name!r} is not filename-safe (leading/trailing "
            f"whitespace or '.' are not allowed)"
        )


def _ref_stem(ref_file: Path) -> str:
    """Extract ``ref_NNNN`` stem from ``<ref_dir>/ref_NNNN.json``."""
    return ref_file.stem


def _soft_check_dir(ref_dir: Path, ref_file: Path) -> Path:
    return ref_dir / "soft_checks" / _ref_stem(ref_file)


def _companion_dir(ref_dir: Path, ref_file: Path) -> Path:
    return ref_dir / "companions" / _ref_stem(ref_file)


_COMPANION_FORMAT_EXT = {"csv": ".csv", "json": ".json"}


def infer_companion_format(path: str) -> str:
    """Infer companion format from file extension. Defaults to JSON."""
    lower = path.lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".json"):
        return "json"
    return "json"


#: Suffix separating a frozen JSON companion's data file from its metadata.
#: Both live in the same directory and the metadata is always ``<name>.json``,
#: so the data file MUST NOT be ``<name>.json`` — the metadata write would
#: overwrite it.
_FROZEN_JSON_DATA_SUFFIX = ".data.json"


def _frozen_data_basename(name: str, fmt: str) -> str:
    """Filename for a frozen companion's data file, beside its metadata."""
    if _COMPANION_FORMAT_EXT.get(fmt, ".json") == ".json":
        return f"{name}{_FROZEN_JSON_DATA_SUFFIX}"
    return f"{name}{_COMPANION_FORMAT_EXT[fmt]}"


#: ``accept``'s key order for a variable entry
#: (:meth:`ReferenceStore.accept`). Any other key an entry carries follows,
#: in its own order.
_ENTRY_KEY_ORDER = ("index", "name", "expression", "values")


def _with_expression(variable: dict) -> dict:
    """A COPY of a variable entry as this store writes one: ``expression``
    present, and ``accept``'s key order — index, name, expression, values.

    ``add_soft_check`` is handed entries someone else produced —
    ``import-baseline`` reads a file that may predate the key, and a
    cross-backend chain builds its own. A writer never leaves the field out
    (an entry without it is one no writer of this version made), so the
    missing key becomes the empty text.

    The order is imposed rather than patched in beside whatever ``name``
    it found: an entry that already carried ``expression``, or one whose
    ``name`` came after its ``values``, kept its own order and two
    soft_check files written by the same command read differently. The
    input is never mutated — the caller's entry may be a live result's.

    A null ``expression`` becomes the empty text, like a missing one: JSON
    someone else wrote may hold it, and every reader of this field takes
    ``""`` for "no expression" (``display_label``, the soft check, the
    migrate hint) — ``None`` would be a third value none of them tests for.
    """
    out: dict = {
        key: (variable.get(key) or "") if key == "expression" else variable[key]
        for key in _ENTRY_KEY_ORDER
        if key in variable or key == "expression"
    }
    for key, value in variable.items():
        if key not in out:
            out[key] = value
    return out


@dataclass(frozen=True)
class Companion:
    """External or frozen reference data used as a plot-only overlay.

    Companions are never scored against (D66) — they exist purely as
    visual context (experimental data, analytical solutions, rig logs).
    ``kind="external"`` stores only a path pointer; the file may move
    or disappear and the reporter degrades gracefully.
    ``kind="frozen"`` has been copied into ref storage (data_file sits
    next to this metadata) and is stable across repo moves.

    ``variables`` is the companion's own record of which of ITS variables
    draw on which of the test's — ``{test variable name: companion
    variable name}``, the ``variables`` key of the metadata JSON. It is
    what the reporter reads; without it the overlay loader has to guess
    from spellings. Empty for a registration that declared none, and for
    one written before the key existed.

    ``mapping_warnings`` is what reading that record found wrong, one
    line per finding. A record with any of these is still a record: the
    overlay says so and draws by what survived, rather than falling back
    on spellings behind the author's back (D132).
    """

    name: str
    kind: str  # "external" | "frozen"
    format: str  # "csv" | "json"
    path: str | None = None  # absolute path for external
    data_file: str | None = None  # basename for frozen (sibling of metadata)
    provenance: dict = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    mapping_warnings: tuple[str, ...] = ()


def _read_variable_mapping(entry: dict) -> tuple[dict[str, str], tuple[str, ...]]:
    """The ``variables`` mapping of a companion's metadata, and what is
    wrong with it: ``(pairs, warnings)``.

    Old data is tolerated — a registration written before the key existed
    has no mapping, and reads as none, as does the empty object a
    registration with no ``--map`` writes. A hand-edited file is a
    different thing: a ``variables`` that is not an object, or an entry
    whose value is not a variable name, is a MALFORMED record, not an
    absent one. The entry is dropped rather than raised (a companion is
    plot-only and must never break a report) and the finding is said —
    the warnings reach the report's overlay table through
    :class:`~dstf.reporting.overlay_loader.Overlay`, and the companion
    keeps its record's authority rather than falling back on spellings.
    """
    if "variables" not in entry:
        return {}, ()
    raw = entry["variables"]
    if not isinstance(raw, dict):
        return {}, (
            "the 'variables' record is not an object of <test variable>: "
            "<companion variable> pairs, so nothing is drawn from it",
        )
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value:
            mapping[key] = value
        else:
            warnings.append(f"mapping for {key} is not a variable name")
    return mapping, tuple(warnings)


class _RolesMixin:
    """Soft_check + companion CRUD, mixed into :class:`ReferenceStore`.

    Not usable on its own: the two members below are the seam the store
    supplies, and they are the mixin's only coupling to it.
    """

    #: The partition directory holding ``ref_NNNN.json`` — set by the store.
    ref_dir: Path

    def _ref_file_for_model(self, model_id: str) -> Path | None:
        """Resolve a model's primary ref file. Provided by the store."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Soft_checks (D66) — another regression system's primary imported
    # here, or the output of a cross-backend chain (e.g. dymola-via-fmpy).
    # Scored via ``against: "<name>"`` but validator enforces ``warn``
    # wrapping — soft_checks never hard-fail.
    # ------------------------------------------------------------------

    def _soft_check_dir_for(self, model_id: str) -> Path | None:
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None:
            return None
        return _soft_check_dir(self.ref_dir, ref_file)

    def get_soft_checks(self, model_id: str) -> dict[str, Baseline]:
        """Return all soft_checks registered for a model.

        Empty dict if the model has no primary baseline or no
        soft_checks are registered.
        """
        sc_dir = self._soft_check_dir_for(model_id)
        if sc_dir is None or not sc_dir.is_dir():
            return {}
        out: dict[str, Baseline] = {}
        for sc_file in sorted(sc_dir.glob("*.json")):
            name = sc_file.stem
            try:
                entry = json.loads(sc_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping unreadable soft_check %s: %s", sc_file, e)
                continue
            out[name] = Baseline(
                name=name,
                time=entry.get("time", []),
                variables=entry.get("variables", []),
                diagnostics=entry.get("diagnostics", []),
                simulation=entry.get("simulation", {}),
                comparison=entry.get("comparison", {}),
                statistics=entry.get("statistics", {}),
                provenance=entry.get("provenance", {}),
            )
        return out

    def add_soft_check(
        self,
        model_id: str,
        name: str,
        time: list[float],
        variables: list[dict],
        *,
        diagnostics: list[dict] | None = None,
        provenance: dict | None = None,
        simulation: dict | None = None,
        statistics: dict | None = None,
        overwrite: bool = True,
    ) -> bool:
        """Register a soft_check baseline for a model.

        The model must already have a primary baseline on disk.
        Name collision with an existing companion is rejected.

        Returns True on write, False if overwrite=False and the name exists.
        """
        if name == PRIMARY_BASELINE:
            raise ValueError(
                "'primary' is reserved for the stored simulation result; "
                "soft_checks live alongside, not in place of, primary."
            )
        # review 2026-07-06 (finding 35): names become filenames — validate.
        _validate_role_name(name, "soft_check")

        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            raise FileNotFoundError(
                f"No primary baseline for {model_id!r}. Run the test with "
                f"--accept first, then add soft_checks."
            )

        companions = self.get_companions(model_id)
        if name in companions:
            raise ValueError(
                f"name {name!r} is already registered as a companion for "
                f"{model_id!r}; companions and soft_checks share a namespace."
            )

        sc_dir = _soft_check_dir(self.ref_dir, ref_file)
        sc_file = sc_dir / f"{name}.json"
        if not overwrite and sc_file.exists():
            return False

        entry: dict[str, Any] = {
            "time": list(time),
            "variables": [_with_expression(v) for v in variables],
        }
        if diagnostics:
            entry["diagnostics"] = list(diagnostics)
        if simulation:
            entry["simulation"] = dict(simulation)
        if statistics:
            entry["statistics"] = dict(statistics)
        if provenance:
            entry["provenance"] = dict(provenance)

        sc_dir.mkdir(parents=True, exist_ok=True)
        # review 2026-07-06 (finding 31): atomic — no partial soft_check files.
        _atomic_write_text(sc_file, json.dumps(entry, indent=2) + "\n")
        return True

    def remove_soft_check(self, model_id: str, name: str) -> bool:
        """Delete a soft_check. Returns True if removed, False if not found."""
        # review 2026-07-06 (finding 35): reject path-escaping names here too.
        _validate_role_name(name, "soft_check")
        sc_dir = self._soft_check_dir_for(model_id)
        if sc_dir is None:
            return False
        sc_file = sc_dir / f"{name}.json"
        if not sc_file.exists():
            return False
        sc_file.unlink()
        # Remove empty parent directory.
        try:
            sc_dir.rmdir()
        except OSError:
            pass
        return True

    # ------------------------------------------------------------------
    # Companions (D66) — external CSV / JSON / analytical data pointed
    # to by file path; plot-only overlays; never scored against. Stored
    # as ``kind="external"`` (path pointer) or ``kind="frozen"``
    # (copied into ref storage).
    # ------------------------------------------------------------------

    def _companion_dir_for(self, model_id: str) -> Path | None:
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None:
            return None
        return _companion_dir(self.ref_dir, ref_file)

    def get_companions(self, model_id: str) -> dict[str, Companion]:
        """Return all registered companions for a model (metadata only).

        Does NOT read or validate the underlying data file — reporter
        consumers are responsible for loading with graceful degradation.
        """
        co_dir = self._companion_dir_for(model_id)
        if co_dir is None or not co_dir.is_dir():
            return {}
        out: dict[str, Companion] = {}
        for meta_file in sorted(co_dir.glob("*.json")):
            # A frozen JSON companion keeps its data in `<name>.data.json`
            # beside the `<name>.json` metadata (see freeze_companion) —
            # never mistake that data file for a registration.
            if meta_file.name.endswith(_FROZEN_JSON_DATA_SUFFIX):
                continue
            name = meta_file.stem
            try:
                entry = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "Skipping unreadable companion meta %s: %s", meta_file, e
                )
                continue
            # Companion metadata always declares its `kind`; anything else
            # in this directory is not a registration.
            if entry.get("kind") not in ("external", "frozen"):
                continue
            mapping, mapping_warnings = _read_variable_mapping(entry)
            out[name] = Companion(
                name=name,
                kind=entry["kind"],
                format=entry.get("format", "json"),
                path=entry.get("path"),
                data_file=entry.get("data_file"),
                provenance=entry.get("provenance", {}),
                variables=mapping,
                mapping_warnings=mapping_warnings,
            )
        return out

    def add_companion(
        self,
        model_id: str,
        name: str,
        path: Path,
        *,
        format: str | None = None,
        provenance: dict | None = None,
        variables: dict[str, str] | None = None,
    ) -> bool:
        """Register an external file as a companion (pointer only, no copy).

        The file must exist at registration (fail fast on a typo'd path);
        if it disappears *later* the reporter degrades gracefully.

        Args:
            path: absolute or relative path to the external file. Relative
                paths are resolved against the current working directory at
                registration time; the resolved absolute path is stored.
            format: ``"csv"`` or ``"json"``. Auto-detected from the file
                extension if not given.
            variables: which of the companion's variables draw on which of
                the test's — ``{test variable name: companion variable
                name}``. Two test variables MAY name one companion
                variable: that draws the one curve on both plots, which is
                a legitimate thing to ask of a rig channel that stands in
                for several modelled quantities. Whether the names on
                either side exist is not checked here — the caller that
                has both files open does that (``dstf companion add``).
        """
        if name == PRIMARY_BASELINE:
            raise ValueError("'primary' is reserved for the stored simulation result")
        # review 2026-07-06 (finding 35): names become filenames — validate.
        _validate_role_name(name, "companion")
        # A `<x>.data` companion's metadata file would be `<x>.data.json`,
        # which is where a frozen `<x>` keeps its data (see freeze_companion).
        if name.endswith(".data"):
            raise ValueError(
                f"companion name {name!r} may not end in '.data' — that "
                f"suffix is reserved for frozen JSON data files"
            )

        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            raise FileNotFoundError(
                f"No primary baseline for {model_id!r}. Run the test with "
                f"--accept first, then add companions."
            )

        soft_checks = self.get_soft_checks(model_id)
        if name in soft_checks:
            raise ValueError(
                f"name {name!r} is already registered as a soft_check for "
                f"{model_id!r}; companions and soft_checks share a namespace."
            )

        # review 2026-07-06 (finding 34): resolve against the CWD (what the
        # user typed) and store the absolute path — the overlay loader used
        # to resolve relative paths against ref_dir, so relative
        # registrations always loaded as "missing". Companions are
        # user-registered files, so a missing file is a registration error:
        # fail fast here instead of surfacing a silent missing overlay later.
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(
                f"companion file does not exist: {path} "
                f"(resolved to {resolved}) — companions must point at an "
                f"existing file at registration time"
            )
        path_str = str(resolved)
        fmt = format or infer_companion_format(path_str)

        meta: dict[str, Any] = {
            "kind": "external",
            "format": fmt,
            "path": path_str,
            # A writer never leaves the mapping out, so metadata WITHOUT
            # the key is a registration no writer of this version made.
            "variables": dict(variables or {}),
        }
        if provenance:
            meta["provenance"] = dict(provenance)

        co_dir = _companion_dir(self.ref_dir, ref_file)
        co_dir.mkdir(parents=True, exist_ok=True)
        meta_file = co_dir / f"{name}.json"
        # review 2026-07-06 (finding 31): atomic — no partial metadata files.
        _atomic_write_text(meta_file, json.dumps(meta, indent=2) + "\n")
        return True

    def freeze_companion(self, model_id: str, name: str) -> bool:
        """Copy an external companion's data into ref storage.

        Converts ``kind="external"`` to ``kind="frozen"``. The data file
        is copied beside the metadata as ``<name>.csv`` for CSV and
        ``<name>.data.json`` for JSON — the metadata itself occupies
        ``<name>.json``, so a JSON data file under that name would be
        destroyed by the metadata write below. Already-frozen companions
        are a no-op.

        Returns True on freeze, False if the companion is missing or
        already frozen.
        """
        # review 2026-07-06 (finding 35): reject path-escaping names here too.
        _validate_role_name(name, "companion")
        companions = self.get_companions(model_id)
        co = companions.get(name)
        if co is None:
            return False
        if co.kind == "frozen":
            logger.info("companion %r is already frozen; no-op", name)
            return False

        src = Path(co.path) if co.path else None
        if src is None or not src.exists():
            raise FileNotFoundError(
                f"Cannot freeze companion {name!r}: source path {co.path!r} "
                f"does not exist"
            )

        co_dir = self._companion_dir_for(model_id)
        assert co_dir is not None
        data_basename = _frozen_data_basename(name, co.format)
        dst = co_dir / data_basename
        # review 2026-07-06 (finding 31): copy via tmp + replace so an
        # interrupted freeze can't leave a truncated data file behind.
        tmp_dst = dst.with_name(f"{dst.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(src, tmp_dst)
            tmp_dst.replace(dst)
        finally:
            try:
                if tmp_dst.exists():
                    tmp_dst.unlink()
            except OSError:
                pass

        meta_file = co_dir / f"{name}.json"
        meta: dict[str, Any] = {
            "kind": "frozen",
            "format": co.format,
            "data_file": data_basename,
            # Freezing copies the data; it does not rename anything, so
            # the mapping rides across unchanged.
            "variables": dict(co.variables),
        }
        if co.provenance:
            meta["provenance"] = dict(co.provenance)
        # review 2026-07-06 (finding 31): atomic — no partial metadata files.
        _atomic_write_text(meta_file, json.dumps(meta, indent=2) + "\n")
        return True

    def remove_companion(self, model_id: str, name: str) -> bool:
        """Delete a companion registration. For frozen companions the
        associated data file is also removed. Returns True if removed."""
        # review 2026-07-06 (finding 35): reject path-escaping names here too.
        _validate_role_name(name, "companion")
        co_dir = self._companion_dir_for(model_id)
        if co_dir is None:
            return False
        meta_file = co_dir / f"{name}.json"
        if not meta_file.exists():
            return False
        companions = self.get_companions(model_id)
        co = companions.get(name)
        meta_file.unlink()
        if co and co.kind == "frozen" and co.data_file:
            data_file = co_dir / co.data_file
            if data_file.exists():
                data_file.unlink()
        try:
            co_dir.rmdir()
        except OSError:
            pass
        return True
