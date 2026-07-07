"""Load companion + soft_check overlays for interactive-HTML plotting.

D66/D67 split the baseline-role model into three:

* **primary** — the stored simulation result; regression anchor. Rendered
  by the existing "Reference" trace.
* **soft_checks** — warn-wrapped scorable baselines (e.g. cross-backend
  output, another system's primary). The reporter already consumes them
  as :class:`Baseline` via :meth:`ReferenceStore.get_soft_checks`.
* **companions** — plot-only pointers to external files (experimental
  data, analytical solutions, rig logs). Never scored. Reporter loads
  them here.

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
by passing a ``config`` (with ``reference_root`` + ``simulator_backend``
+ ``os_name``) into :func:`load_overlays`; omit the config and only
user-registered overlays are returned (existing behavior).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Overlay:
    """One visual overlay attached to a test report.

    Populated for every registered soft_check + companion on the model.
    ``variables`` is keyed by variable name — the reporter only renders
    overlays for variables the current test already plots.
    """

    name: str
    role: str  # "soft_check" | "companion"
    kind: str | None = None  # companion only: "external" | "frozen"
    status: str = "loaded"  # "loaded" | "missing" | "invalid"
    note: str = ""
    variables: dict[str, OverlayVariable] = field(default_factory=dict)


@dataclass
class OverlayVariable:
    time: list[float]
    values: list[float]


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


def load_sibling_backend_overlays(config, model_id: str) -> list[Overlay]:
    """Find peer-backend references for ``model_id`` and render them as overlays.

    Scans ``<reference_root>/<backend>/<os>/`` directories, excluding the
    current partition (``config.simulator_backend`` + ``config.os_name``),
    for ``ref_*.json`` files whose ``model_id`` matches. Each match becomes
    an :class:`Overlay` with ``role="companion"`` + ``kind="sibling-backend"``
    and ``name`` like ``"Dymola/windows"``. Every failure mode
    (missing root, unreadable ref, obsolete status) is silently skipped —
    this path must never break a report.
    """
    if config is None:
        return []
    reference_root = getattr(config, "reference_root", None)
    if reference_root is None:
        return []
    current_backend = getattr(config, "simulator_backend", None) or ""
    current_os = getattr(config, "os_name", None) or ""
    index = _sibling_backend_index(
        str(reference_root),
        current_backend,
        current_os,
    )
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
                    data = json.loads(ref_file.read_text(encoding="utf-8"))
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

    Ref files store ``{"simulation": {...}, "variables": [{"name":..., "values":[...]}, ...]}``
    with the time vector either under ``time`` at top level or — for older
    schemas — via :func:`storage.reference_store._extract_baselines`. We
    go straight for the common shape and fall back to a safe no-op.
    """
    time = ref_data.get("time")
    if not isinstance(time, list):
        # Some schemas store time under a baseline entry. Try that.
        baselines = ref_data.get("baselines") or {}
        primary = baselines.get("primary") if isinstance(baselines, dict) else None
        if isinstance(primary, dict):
            time = primary.get("time")
    if not isinstance(time, list):
        return {}
    out: dict[str, OverlayVariable] = {}
    for entry in ref_data.get("variables") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("expression")
        values = entry.get("values")
        if not name or not isinstance(values, list):
            continue
        out[name] = OverlayVariable(time=list(time), values=list(values))
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _baseline_to_overlay_vars(baseline) -> dict[str, OverlayVariable]:
    """Flatten a :class:`Baseline` into per-variable overlay data."""
    time = list(baseline.time or [])
    out: dict[str, OverlayVariable] = {}
    for var in baseline.variables or []:
        name = var.get("name") or var.get("expression")
        if not name:
            continue
        values = var.get("values")
        if not isinstance(values, list):
            continue
        out[name] = OverlayVariable(time=list(time), values=list(values))
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

    return Overlay(
        name=companion.name,
        role="companion",
        kind=companion.kind,
        status="loaded",
        variables=vars_,
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
        name = entry.get("name") or entry.get("expression")
        values = entry.get("values")
        if not name or not isinstance(values, list):
            continue
        out[name] = OverlayVariable(time=list(time), values=list(values))
    return out


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
    """
    for traj in trajectories:
        name = traj.get("name", "")
        attached = []
        for ov in overlays:
            if ov.status != "loaded":
                continue
            var = ov.variables.get(name)
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


def overlay_summary(overlays: list[Overlay]) -> list[dict]:
    """Test-level summary of every overlay — includes missing/invalid ones
    so the report can show a "not rendered" hint.
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
            }
        )
    return out
