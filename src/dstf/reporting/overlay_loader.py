"""Load companion + soft_check overlays for interactive-HTML plotting.

D66/D67 split the baseline-role model into three:

* **primary** — the stored simulation result; regression anchor. Rendered
  by the existing "Reference" trace.
* **soft_checks** — warn-wrapped scorable baselines (e.g. cross-backend
  output, another system's primary). The reporter already consumes them
  as :class:`Baseline` via :meth:`ReferenceStore.get_soft_checks`.
* **companions** — plot-only pointers to external files (experimental
  data, analytical solutions, rig logs). Never scored. Reporter loads
  them here. A companion's metadata records which of ITS variables draw
  on which of the test's; :func:`attach_overlays_to_trajectories` is
  where that record is read, and what it does without one.

Graceful degradation is the whole contract: a companion whose backing
file has been moved or renamed must not break the report. Missing /
unparseable files surface as an :class:`Overlay` with ``status="missing"``
or ``status="invalid"`` — the template can skip the trace (no false
visual overlay) but still show the overlay's presence in the picker as
a "not found" hint.

Two overlay formats supported today:

* **JSON** — same shape as a ref file baseline: ``{"time": [...],
  "variables": [{"name": str, "values": [...]}, ...]}``.
* **CSV** — header row names the columns. First column is the time axis
  (name is ignored). Remaining columns are variable values keyed by
  their column headers.

Callers get a flat ``list[Overlay]`` — role-keyed dicts are the
template's concern, not the loader's.

**Cross-backend auto-companions.** In addition to user-registered
overlays, the loader can auto-discover peer-backend references (same
model_id, stored under a sibling ``<reference_root>/<backend>/<os>/``
partition) and surface them as read-only companions with
``kind="sibling-backend"``. Useful when a new simulator has simulated
but not yet been baselined — the user can eyeball "does this new sim's
trajectory match the old simulator's stored reference?" on the same
plot. No persistence — purely a visual overlay at report time. Gated
by passing a ``config`` (with ``reference_root`` + ``backend``
+ ``os_name``) into :func:`load_overlays`; omit the config and only
user-registered overlays are returned (existing behavior).
"""

from __future__ import annotations

import csv
import json
import logging
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..storage.reference_store import read_reference_file

logger = logging.getLogger(__name__)


@dataclass
class Overlay:
    """One visual overlay attached to a test report.

    Populated for every registered soft_check + companion on the model.
    ``variables`` is keyed by the overlay's OWN variable names — the
    reporter only renders overlays for variables the current test plots,
    and ``mapping`` is what says which is which.

    ``mapping`` is ``{test variable name: this overlay's variable name}``,
    read from a companion's metadata. Soft_checks and the auto-discovered
    sibling-backend references carry none: they are DSTF's own data, keyed
    by the same names the test uses. See
    :func:`attach_overlays_to_trajectories` for what an absent mapping
    means.

    ``mapping_malformed`` says the companion recorded a mapping that could
    not be read as pairs (see
    :func:`~dstf.storage.roles._read_variable_mapping`). The record is
    still a record: such an overlay draws only what survived reading it,
    and never falls back on spellings.

    ``warnings`` says what this overlay could not draw and why — a
    malformed or stale mapping, filled in by :func:`_load_companion`, and
    a spelling that could stand for two of its variables, filled in by
    :func:`attach_overlays_to_trajectories`. :func:`overlay_summary`
    surfaces them on the report's overlay table.
    """

    name: str
    role: str  # "soft_check" | "companion"
    kind: str | None = None  # companion only: "external" | "frozen"
    status: str = "loaded"  # "loaded" | "missing" | "invalid"
    note: str = ""
    variables: dict[str, OverlayVariable] = field(default_factory=dict)
    mapping: dict[str, str] = field(default_factory=dict)
    mapping_malformed: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class OverlayVariable:
    time: list[float]
    values: list[float]
    #: What the overlay's own source calls this quantity, when it records
    #: one: a companion that is itself a DSTF baseline of another library
    #: stores an ``expression`` beside each variable's name (D132). It is
    #: the last spelling the read rule tries for an unmapped companion —
    #: never a key.
    expression: str = ""


def load_overlays(store, model_id: str, config=None) -> list[Overlay]:
    """Return every soft_check + companion available for this model.

    Pure read path — never mutates the store. Missing / unparseable
    companion files produce an :class:`Overlay` with ``status="missing"``
    or ``"invalid"`` rather than raising. Soft_checks come from the
    store's existing loader (which already tolerates bad files by
    skipping them — same semantics apply here).

    When ``config`` is provided, also auto-discovers sibling-backend
    references (same ``model_id``, stored under peer
    ``<reference_root>/<backend>/<os>/`` partitions) and includes them
    as read-only companions with ``kind="sibling-backend"`` — useful
    for visually validating a new simulator against references from an
    already-trusted one before accepting baselines.
    """
    overlays: list[Overlay] = []
    if store is None or not model_id:
        return overlays

    # Soft_checks arrive as Baseline objects — convert to the flat
    # overlay shape keyed by variable name.
    try:
        soft_checks = store.get_soft_checks(model_id)
    except Exception as e:
        logger.warning("Failed to enumerate soft_checks for %s: %s", model_id, e)
        soft_checks = {}

    for name, baseline in soft_checks.items():
        ov_vars = _baseline_to_overlay_vars(baseline)
        overlays.append(
            Overlay(
                name=name,
                role="soft_check",
                status="loaded" if ov_vars else "invalid",
                note="" if ov_vars else "soft_check has no variable data",
                variables=ov_vars,
            )
        )

    # Companions: read metadata first (never opens the data file) then
    # try to load the data, catching every failure mode.
    try:
        companions = store.get_companions(model_id)
    except Exception as e:
        logger.warning("Failed to enumerate companions for %s: %s", model_id, e)
        companions = {}

    for _name, companion in companions.items():
        overlays.append(_load_companion(store, model_id, companion))

    # Auto-discovered sibling-backend companions (opt-in via config).
    if config is not None:
        overlays.extend(load_sibling_backend_overlays(config, model_id))

    return overlays


_INDEX_LOCK = threading.Lock()


def index_args(config) -> tuple[str, str, str] | None:
    """The three values the sibling index is keyed by, or None when the
    config names no reference root."""
    reference_root = getattr(config, "reference_root", None)
    if reference_root is None:
        return None
    return (
        str(reference_root),
        getattr(config, "backend", None) or "",
        getattr(config, "os_name", None) or "",
    )


def sibling_backend_index(reference_root: str, current_backend: str, current_os: str):
    """The cached index, built under a lock: ``lru_cache`` does not
    serialise concurrent first calls, and the authoring Session warms the
    index on a thread while a click may ask for it."""
    with _INDEX_LOCK:
        return _sibling_backend_index(reference_root, current_backend, current_os)


def warm_sibling_backend_index(config) -> None:
    """Build the index for ``config`` now (the tool calls this right after
    open, off the UI thread), so no click pays for it."""
    args = index_args(config)
    if args is not None:
        sibling_backend_index(*args)


def load_sibling_backend_overlays(config, model_id: str) -> list[Overlay]:
    """Find peer-backend references for ``model_id`` and render them as overlays.

    Scans ``<reference_root>/<backend>/<os>/`` directories, excluding the
    current partition (``config.backend`` + ``config.os_name``),
    for ``ref_*.json`` files whose ``model_id`` matches. Each match becomes
    an :class:`Overlay` with ``role="companion"`` + ``kind="sibling-backend"``
    and ``name`` like ``"Dymola/windows"``. Every failure mode
    (missing root, unreadable ref, obsolete status) is silently skipped —
    this path must never break a report.
    """
    args = index_args(config) if config is not None else None
    if args is None:
        return []
    index = sibling_backend_index(*args)
    matches = index.get(model_id, ())
    overlays: list[Overlay] = []
    for ref in matches:
        ov_vars = _ref_data_to_overlay_vars(ref.data)
        overlays.append(
            Overlay(
                name=f"{ref.backend}/{ref.os}",
                role="companion",
                kind="sibling-backend",
                status="loaded" if ov_vars else "invalid",
                note="" if ov_vars else "sibling ref has no variable trajectories",
                variables=ov_vars,
            )
        )
    return overlays


@dataclass(frozen=True)
class _SiblingRef:
    backend: str
    os: str
    path: Path
    data: dict


@lru_cache(maxsize=8)
def _sibling_backend_index(
    reference_root: str,
    current_backend: str,
    current_os: str,
) -> dict[str, tuple[_SiblingRef, ...]]:
    """Build ``model_id -> (SiblingRef, ...)`` once per (root, current).

    ``lru_cache`` is safe here because the file tree is read-only during
    a report run. Any later accept-and-rerender invalidates the cache by
    using fresh args only on the partition the user *isn't* excluding —
    the sibling partitions it scans don't change mid-run.
    """
    index: dict[str, list[_SiblingRef]] = {}
    root = Path(reference_root)
    if not root.exists():
        return {}
    for backend_dir in sorted(root.iterdir()):
        if not backend_dir.is_dir():
            continue
        for os_dir in sorted(backend_dir.iterdir()):
            if not os_dir.is_dir():
                continue
            # Skip the current partition — its refs are the primary
            # baselines, not overlays.
            if backend_dir.name == current_backend and os_dir.name == current_os:
                continue
            for ref_file in sorted(os_dir.glob("ref_*.json")):
                try:
                    data = read_reference_file(ref_file)
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("status") == "obsolete":
                    continue
                mid = data.get("model_id")
                if not mid:
                    continue
                index.setdefault(mid, []).append(
                    _SiblingRef(
                        backend=backend_dir.name,
                        os=os_dir.name,
                        path=ref_file,
                        data=data,
                    )
                )
    return {k: tuple(v) for k, v in index.items()}


def _ref_data_to_overlay_vars(ref_data: dict) -> dict[str, OverlayVariable]:
    """Flatten a raw ref-file dict (primary baseline shape) into overlay vars.

    A ref file stores its primary flat: ``{"simulation": {...}, "time": [...],
    "variables": [{"name":..., "values":[...]}, ...]}``. Anything else is a
    safe no-op.
    """
    time = ref_data.get("time")
    if not isinstance(time, list):
        return {}
    out: dict[str, OverlayVariable] = {}
    for entry in ref_data.get("variables") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        values = entry.get("values")
        # A name stored twice draws its FIRST entry, the one the
        # comparator scores and the context builder draws.
        if not name or not isinstance(values, list) or name in out:
            continue
        out[name] = OverlayVariable(
            time=list(time),
            values=list(values),
            expression=_entry_expression(entry),
        )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _entry_expression(entry: dict) -> str:
    """The ``expression`` a reference-shape variable entry records, as text.

    Missing, null and non-text all read as "no expression" — the same
    thing every other reader of the field takes ``""`` for (D132).
    """
    value = entry.get("expression")
    return value if isinstance(value, str) else ""


def _baseline_to_overlay_vars(baseline) -> dict[str, OverlayVariable]:
    """Flatten a :class:`Baseline` into per-variable overlay data."""
    time = list(baseline.time or [])
    out: dict[str, OverlayVariable] = {}
    for var in baseline.variables or []:
        name = var.get("name")
        # A name stored twice draws its FIRST entry, the one the
        # comparator scores and the context builder draws.
        if not name or name in out:
            continue
        values = var.get("values")
        if not isinstance(values, list):
            continue
        out[name] = OverlayVariable(
            time=list(time),
            values=list(values),
            expression=_entry_expression(var),
        )
    return out


def _load_companion(store, model_id: str, companion) -> Overlay:
    """Read a companion's data file, returning an Overlay with status.

    Every failure mode maps to ``status != "loaded"`` with a ``note``
    suitable for surfacing in the UI picker. The loader never raises.
    """
    path = _resolve_companion_path(store, model_id, companion)
    if path is None:
        return Overlay(
            name=companion.name,
            role="companion",
            kind=companion.kind,
            status="missing",
            note=f"no path resolved (kind={companion.kind!r})",
        )
    if not path.exists():
        return Overlay(
            name=companion.name,
            role="companion",
            kind=companion.kind,
            status="missing",
            note=f"file not found: {path}",
        )

    fmt = (companion.format or "json").lower()
    try:
        vars_ = _load_csv(path) if fmt == "csv" else _load_json(path)
    except Exception as e:
        logger.warning(
            "Failed to load companion %r (%s): %s",
            companion.name,
            path,
            e,
        )
        return Overlay(
            name=companion.name,
            role="companion",
            kind=companion.kind,
            status="invalid",
            note=f"parse error: {e}",
        )

    if not vars_:
        return Overlay(
            name=companion.name,
            role="companion",
            kind=companion.kind,
            status="invalid",
            note="no variables parsed from file",
        )

    mapping = dict(companion.variables)
    # What the author wrote and what the file holds are read together
    # exactly once, here: a mapping naming a variable the file lacks draws
    # nothing (the file has been replaced since, and the mapping is now
    # stale), and saying so is the only way the author finds out.
    warnings = list(companion.mapping_warnings) + [
        f"mapping for {test_var} names {companion_var}, which {path} does not hold"
        for test_var, companion_var in mapping.items()
        if companion_var not in vars_
    ]
    return Overlay(
        name=companion.name,
        role="companion",
        kind=companion.kind,
        status="loaded",
        variables=vars_,
        mapping=mapping,
        mapping_malformed=bool(companion.mapping_warnings),
        warnings=warnings,
    )


def _resolve_companion_path(store, model_id: str, companion) -> Path | None:
    """Locate the data file on disk. Handles both external + frozen kinds."""
    if companion.kind == "frozen":
        # Frozen companions store their data beside the metadata in the
        # ref dir — we reach it via the store's companion-dir lookup.
        co_dir = store._companion_dir_for(model_id)
        if co_dir is None or not companion.data_file:
            return None
        return co_dir / companion.data_file
    # External: review 2026-07-06 (finding 34) — add_companion now resolves
    # paths against the CWD at registration time and stores them absolute,
    # so absolute paths are the normal case. A relative path here is a
    # legacy entry (registered before the fix): keep the historical
    # ref_dir-relative resolution for those, and say so.
    if not companion.path:
        return None
    p = Path(companion.path)
    if p.is_absolute():
        return p
    ref_dir = getattr(store, "ref_dir", None)
    if ref_dir is None:
        return p
    logger.info(
        "companion %r has a legacy relative path %r — resolving against "
        "the reference dir %s (new registrations store absolute paths)",
        companion.name,
        companion.path,
        ref_dir,
    )
    return (Path(ref_dir) / p).resolve()


def _load_json(path: Path) -> dict[str, OverlayVariable]:
    """Parse a JSON companion file. Expects ``time: [...]`` + ``variables: [...]``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    time = list(data.get("time") or [])
    out: dict[str, OverlayVariable] = {}
    for entry in data.get("variables", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        values = entry.get("values")
        # A name stored twice draws its FIRST entry, the one the
        # comparator scores and the context builder draws.
        if not name or not isinstance(values, list) or name in out:
            continue
        out[name] = OverlayVariable(
            time=list(time),
            values=list(values),
            expression=_entry_expression(entry),
        )
    return out


def companion_variable_names(path: Path, fmt: str) -> list[str]:
    """The names a companion file gives its variables, in file order.

    What ``dstf companion add`` checks a ``--map``'s right-hand sides
    against: a JSON companion's ``variables[].name``, a CSV companion's
    column headers after the time column. Raises whatever the parse
    raises — the caller is registering, not rendering, so a file it
    cannot read is a registration error rather than a silent overlay.
    """
    vars_ = _load_csv(path) if (fmt or "json").lower() == "csv" else _load_json(path)
    return list(vars_)


def _load_csv(path: Path) -> dict[str, OverlayVariable]:
    """Parse a wide CSV: first column is time, rest are value columns.

    Header row names the variables. Rows with non-numeric values in any
    cell are dropped silently — the reporter only plots whatever it can
    parse rather than failing the whole companion on one bad row.
    """
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return {}
        if len(header) < 2:
            return {}
        var_names = [h.strip() for h in header[1:]]
        times: list[float] = []
        cols: list[list[float]] = [[] for _ in var_names]
        for row in reader:
            if not row or len(row) < len(header):
                continue
            try:
                t = float(row[0])
                vals = [float(row[i + 1]) for i in range(len(var_names))]
            except (TypeError, ValueError):
                continue
            times.append(t)
            for i, v in enumerate(vals):
                cols[i].append(v)
    return {
        var_names[i]: OverlayVariable(time=list(times), values=list(cols[i]))
        for i in range(len(var_names))
        if var_names[i]
    }


def attach_overlays_to_trajectories(
    trajectories: list[dict],
    overlays: list[Overlay],
) -> None:
    """Stamp each trajectory with its ``overlays`` list, in place.

    Only overlays that carry the variable's data appear on that plot.
    Missing/invalid overlays surface at the test level (future: summary
    picker); they're dropped from per-trajectory lists since there's
    nothing to render.

    **The mapping is the record.** An overlay is another source's data,
    and a companion names the quantity the way ITS result file does. Which
    of its variables draws on which of the test's is a fact only whoever
    registered it knows, so the companion records it in its own metadata
    (``dstf companion add … --map TEST_VAR=COMPANION_VAR``). Where an
    overlay HAS a mapping, that is the whole rule: a trajectory the
    mapping does not name gets no trace from it, even when the overlay
    holds a variable spelled exactly like it. Guessing behind a recorded
    answer is how somebody else's curve ends up under the wrong plot.

    **The fallbacks are for a companion nobody mapped** — the ones
    registered before there was a mapping to write — and for NOTHING
    else. A soft_check, the primary of a sibling partition and a
    cross-backend import are DSTF's own data, keyed by the names this
    test uses: they match BY NAME ONLY. A soft_check is also SCORED by
    name (``against:``), so the curve drawn for it has to be the curve
    scored for it; a spelling rule that found some other variable of it
    would draw one thing and score another. In order, the companion
    variable whose

    1. NAME is the trajectory's name. The two sources agree on a spelling
       the test already uses; nothing else needs to be true.
    2. NAME is the trajectory's EXPRESSION. A variable of the Modelica
       ``UnitTests`` component is named positionally (``unitTests.x[2]``),
       which no other tool can be expected to use; the expression the
       model wrote is the only spelling the two have in common (D132).
    3. EXPRESSION is the trajectory's name, or its expression. The mirror
       of 2, for a companion that is itself a DSTF baseline of another
       library: there the POSITIONAL name is on the companion's side and
       the quantity's text is what it records beside it.

    A fallback step that finds more than one candidate attaches nothing
    and records a warning on the overlay (``x={a.Q, a.Q}`` is legal, so
    one expression may belong to two of a companion's variables). Nothing
    is drawn rather than the first of two, and the warning names the
    trajectory, the candidates, and the mapping that would settle it.
    """
    for traj in trajectories:
        name = traj.get("name", "")
        expression = traj.get("expression", "")
        attached = []
        for ov in overlays:
            if ov.status != "loaded":
                continue
            var, ambiguous = _overlay_variable_for(ov, name, expression)
            if ambiguous:
                _record_ambiguity(ov, name, ambiguous)
            if var is None:
                continue
            attached.append(
                {
                    "name": ov.name,
                    "role": ov.role,
                    "kind": ov.kind,
                    "time": list(var.time),
                    "values": list(var.values),
                }
            )
        traj["overlays"] = attached


def _overlay_variable_for(
    ov: Overlay,
    name: str,
    expression: str,
) -> tuple[OverlayVariable | None, list[str]]:
    """This overlay's variable for one trajectory, else why not.

    Returns ``(variable, [])`` for a hit, ``(None, [])`` for a clean miss,
    and ``(None, candidates)`` when a fallback step could not choose.
    """
    # A companion that recorded a mapping is read by that alone — even
    # when nothing readable survived it (``mapping_malformed``): the
    # author wrote an answer, and guessing behind a broken one is still
    # guessing.
    if ov.mapping or ov.mapping_malformed:
        mapped = ov.mapping.get(name)
        # A mapping naming a variable the file does not hold draws nothing:
        # the file has been replaced since, and the mapping is now wrong.
        # ``_load_companion`` has already warned about it.
        return (ov.variables.get(mapped) if mapped else None), []

    var = ov.variables.get(name)
    if var is not None:
        return var, []
    if not _matches_by_more_than_name(ov):
        return None, []
    if expression:
        var = ov.variables.get(expression)
        if var is not None:
            return var, []

    wanted = {name, expression} - {""}
    candidates = sorted(
        key for key, value in ov.variables.items() if value.expression in wanted
    )
    if len(candidates) > 1:
        return None, candidates
    if candidates:
        return ov.variables[candidates[0]], []
    return None, []


def _matches_by_more_than_name(ov: Overlay) -> bool:
    """Whether this overlay's variables may be found by anything but name.

    Only a REGISTERED companion may: it is an outside file, naming the
    quantity the way its own source does. Everything else on the picker
    is DSTF's own data under the test's own names — a soft_check
    (imported whole, and SCORED by name through ``against:``, so the
    curve drawn has to be the curve scored), a cross-backend import,
    which is stored as one, and the auto-discovered primary of a sibling
    partition. For those the name is the whole rule (D132).
    """
    return ov.role == "companion" and ov.kind != "sibling-backend"


def _record_ambiguity(ov: Overlay, name: str, candidates: list[str]) -> None:
    """Say, once, that this overlay could not tell which curve is which.

    Only a companion reaches here — nothing else falls back on spellings
    (:func:`_matches_by_more_than_name`) — which is why the remedy is
    worded as one: ``dstf companion add`` is the command that would
    settle it, and there is no such command for a soft_check.

    Once, because the finding outlives the pass that made it: the
    authoring Session caches these overlay objects per model and
    re-attaches them over the same trajectories on every ``view()``, so
    an ambiguity would otherwise be appended again on every click.
    """
    text = (
        f"{name}: {len(candidates)} of this overlay's variables carry it as "
        f"their expression ({', '.join(candidates)}), so nothing is drawn for "
        f"it — add a mapping: dstf companion add <model> {ov.name} <path> "
        f"--map {name}=<one of them>"
    )
    if text not in ov.warnings:
        ov.warnings.append(text)


def overlay_summary(overlays: list[Overlay]) -> list[dict]:
    """Test-level summary of every overlay — includes missing/invalid ones
    so the report can show a "not rendered" hint.

    ``warnings`` carries what :func:`attach_overlays_to_trajectories` found
    it could not decide, so call this AFTER attaching — which is what the
    report's ``context_builder`` does, its only caller. (The authoring tool
    reads the overlays themselves, not this summary.)
    """
    out = []
    for ov in overlays:
        out.append(
            {
                "name": ov.name,
                "role": ov.role,
                "kind": ov.kind,
                "status": ov.status,
                "note": ov.note,
                "variables": sorted(ov.variables.keys()),
                "warnings": list(ov.warnings),
            }
        )
    return out
