"""Evaluate a parsed MetricTree spec against simulation + reference data.

Takes a :class:`SpecNode` from :mod:`tree_spec` plus the per-variable
simulation result and reference dicts, and walks the tree to produce an
evaluated :class:`MetricResult` root.

Leaves evaluate via the existing :func:`resolve_mode` factory — the spec
is just a declarative envelope around the same per-variable comparison
that :func:`compare_test` already does in the implicit-AND path. Internal
nodes look ``spec.combinator`` up in :mod:`registry` to get the concrete
``Combinator`` from :mod:`metric_tree`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..backends import VariableResult
from ..backends.types import DatasetType
from .comparator import VariableComparison
from .metric_tree import Combinator, MetricResult, leaf_from_variable
from .modes import resolve_mode
from .registry import DEFAULT_METRIC, MetricDescriptor
from .registry import combinator as registry_combinator
from .registry import metric as registry_metric
from .tree_spec import CombinatorSpec, LeafSpec, SpecNode

PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class BaselineView:
    """Pre-processed view of one baseline for leaf evaluation.

    Packages the per-variable reference dicts and the shared time vector
    (or ``None`` when each variable carries its own time). Multiple named
    baselines coexist in the evaluator's input (``{"primary": ...,
    "experiment": ...}``); a leaf's ``against`` field picks which one it
    scores against.
    """

    name: str
    ref_vars_by_name: dict[str, dict]
    shared_ref_time: np.ndarray | None = None
    #: Statistics captured with the baseline (dotted keys like
    #: ``translation.scalar_unknowns``) and the stored diagnostics summaries
    #: (``{"name", "final", "min", "max"}``) — the baseline side of a
    #: ``scalar`` leaf's stats-path quantity.
    statistics: dict = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ScalarSources:
    """The current run's stored scalars, for stats-path ``scalar`` leaves.

    Mirrors :class:`BaselineView`'s two scalar fields for the actual side.
    The difference is the diagnostics shape: a run's diagnostics are
    :class:`~dstf.backends.VariableResult` trajectories, so ``final`` /
    ``min`` / ``max`` are computed live rather than read from a summary.
    """

    statistics: dict = field(default_factory=dict)
    diagnostics: list[VariableResult] = field(default_factory=list)


def evaluate_spec(
    spec: SpecNode,
    var_results_by_name: dict[str, VariableResult],
    baselines: dict[str, BaselineView],
    base_tolerance: float,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
    scalars: ScalarSources | None = None,
) -> MetricResult:
    """Walk a spec tree and produce an evaluated :class:`MetricResult` root.

    ``base_tolerance`` is used when a leaf's params omit ``tolerance`` (the
    per-test / config / default chain resolved by the caller).

    ``baselines`` maps baseline name (``"primary"``, ``"experiment"``, ...)
    to a :class:`BaselineView`; each leaf's ``against`` field picks which
    one it scores against, defaulting to ``"primary"``.

    ``dataset_kind`` is the :class:`~dstf.backends.types.DatasetType` the
    supplied results carry. Every backend produces ``TIME_SERIES`` today,
    so it is a parameter rather than a field on ``VariableResult`` — a leaf
    whose metric doesn't accept the kind hard-fails with a diagnostic.

    ``scalars`` carries the run's statistics + diagnostics for ``scalar``
    leaves whose quantity is a stats path; leaves that reduce a tracked
    series never touch it.
    """
    sources = scalars if scalars is not None else ScalarSources()
    if isinstance(spec, LeafSpec):
        return _evaluate_leaf(
            spec, var_results_by_name, baselines, base_tolerance, dataset_kind, sources
        )

    children = [
        evaluate_spec(
            c, var_results_by_name, baselines, base_tolerance, dataset_kind, sources
        )
        for c in spec.children
    ]
    return _build_combinator(spec).combine(children)


def collect_leaf_variables(tree: MetricResult) -> list[VariableComparison]:
    """Return the ``VariableComparison`` stashed on each leaf, in tree order.

    ``leaf_from_variable`` puts the originating ``VariableComparison`` in
    ``diagnostics['variable']``. This helper walks the tree and collects
    them so the reporter (which still takes a flat list) sees the
    variables referenced by the user's tree.
    """
    out: list[VariableComparison] = []
    _walk_leaves(tree, out)
    return out


def flatten_evaluation(
    tree: MetricResult,
    *,
    root: str = "/metrics",
) -> dict[str, dict]:
    """Flatten an evaluated MetricResult tree into a ``{path: {...}}`` dict.

    The returned mapping is keyed by the same JSON-Pointer paths that
    :func:`tree_spec.collect_leaf_paths` / :func:`tree_spec.spec_to_view`
    produce, so the reporter can merge evaluation results into a spec
    view by path lookup.

    Each entry carries ``passed``, ``score``, ``label``, plus any diagnostics
    the evaluator stashed on that node (``mode``, ``tolerance``,
    ``max_abs_error``, ``against``, ``window``, ``n_passed``, ``warned``, ...).
    """
    out: dict[str, dict] = {}
    _flatten_evaluation(tree, root, out)
    return out


def _flatten_evaluation(
    node: MetricResult,
    path: str,
    out: dict[str, dict],
) -> None:
    entry = {
        "passed": bool(node.passed),
        "score": node.score,
        "label": node.label,
    }
    diag = node.diagnostics or {}
    # Copy JSON-safe diagnostic keys (skip the stashed VariableComparison).
    for key, val in diag.items():
        if key == "variable":
            continue
        entry[key] = val
    out[path] = entry
    for i, child in enumerate(node.children):
        _flatten_evaluation(child, f"{path}/children/{i}", out)


def to_view(tree: MetricResult) -> dict:
    """Serialize a ``MetricResult`` tree into a JSON-safe nested dict
    for Jinja consumption. Shape is render-oriented — no simulation data,
    no numpy arrays.

    Fields per node:
      * ``kind``      — "leaf" | "combinator"
      * ``passed``    — bool
      * ``label``     — human-readable (variable name for leaves,
                        "and[N]" / "or[N]" / "warn" / "k-of-n[K/N]" for combinators)
      * ``score``     — float or None
      * ``mode``      — metric mode on leaves (nrmse / tube / points)
      * ``tolerance`` — on leaves, the tolerance applied
      * ``warned``    — True on ``warn`` nodes whose child failed
      * ``children``  — recursive list (empty for leaves)
    """
    node: dict = {
        "kind": "leaf" if not tree.children else "combinator",
        "passed": bool(tree.passed),
        "label": tree.label,
        "score": tree.score,
        "children": [to_view(c) for c in tree.children],
    }
    diag = tree.diagnostics or {}
    if not tree.children:
        # Leaf diagnostics come from leaf_from_variable + _evaluate_leaf
        if "mode" in diag:
            node["mode"] = diag["mode"]
        if "tolerance" in diag:
            node["tolerance"] = diag["tolerance"]
        if "max_abs_error" in diag:
            node["max_abs_error"] = diag["max_abs_error"]
        if "against" in diag:
            node["against"] = diag["against"]
    else:
        # Combinator diagnostics: n_failed / n_passed / k / n / warned
        for key in ("n_failed", "n_passed", "k", "n", "warned"):
            if key in diag:
                node[key] = diag[key]
    return node


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _walk_leaves(node: MetricResult, out: list[VariableComparison]) -> None:
    if not node.children:
        vc = node.diagnostics.get("variable")
        if vc is not None:
            out.append(vc)
        return
    for child in node.children:
        _walk_leaves(child, out)


def _build_combinator(spec: CombinatorSpec) -> Combinator:
    desc = registry_combinator(spec.combinator)
    if desc is None:
        # tree_spec validates on parse; this is just a defensive fallback.
        raise ValueError(f"Unknown combinator: {spec.combinator!r}")
    return desc.build(spec)


def _evaluate_leaf(
    leaf: LeafSpec,
    var_results_by_name: dict[str, VariableResult],
    baselines: dict[str, BaselineView],
    base_tolerance: float,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
    scalars: ScalarSources | None = None,
) -> MetricResult:
    # A metric declares which dataset kinds it can score. Unscorable input
    # is a hard fail with a diagnostic naming both sides, not a crash inside
    # the mode. Unreachable for the builtins (all accept TIME_SERIES, which
    # is all any backend produces) — it guards registered metrics.
    desc = registry_metric(leaf.metric)
    if desc is not None and dataset_kind not in desc.accepted_datasets:
        return leaf_from_variable(
            _incompatible_dataset_comparison(leaf, desc, dataset_kind)
        )

    override = _leaf_override_dict(leaf)
    tolerance = float(leaf.params.get("tolerance", base_tolerance))
    try:
        mode = resolve_mode(override, tolerance, default_points=False)
    except ValueError as exc:
        # A misconfigured leaf fails that leaf, like the flat
        # variable_overrides path — one bad spec entry must not abort the
        # whole run with an anonymous traceback (D107).
        return leaf_from_variable(_leaf_config_error_comparison(leaf, exc))

    # leaf.against picks the named baseline. Missing name →
    # hard fail with a clear label (loud on user-facing reports). Unless
    # the leaf is baseline-free (range / declared-events / declared-peaks)
    # — then we don't need any baseline at all (idea #59 / D83).
    baseline = baselines.get(leaf.against)
    if baseline is None and not mode.is_baseline_free():
        return leaf_from_variable(_missing_baseline_comparison(leaf))

    quantity = leaf.params.get("quantity")
    windowed = False
    if desc is not None and desc.accepts_quantity and isinstance(quantity, str):
        # A string ``quantity`` reads a stored scalar instead of a tracked
        # series — where the data comes from is the evaluator's call, the
        # same seam the window below occupies (a future ``{"expr": ...}``
        # quantity extends this spot). The window is skipped deliberately:
        # a stored scalar is not a time series.
        #
        # The switch is gated on the metric's ``accepts_quantity`` flag, not
        # on the param alone: a stray ``quantity`` on an nrmse/tube/points
        # leaf must leave that leaf scoring its trajectory. Ungated, both
        # sides collapsed to one identical sample and a 2x-off signal passed.
        resolved = _scalar_path_arrays(
            leaf, quantity, mode, baseline, scalars or ScalarSources()
        )
        if isinstance(resolved, VariableComparison):
            return leaf_from_variable(resolved)
        ref_time, ref_values, act_time, act_values = resolved
        index = 0
    else:
        var_result = var_results_by_name.get(leaf.variable)
        ref_var = baseline.ref_vars_by_name.get(leaf.variable) if baseline else None

        if var_result is None:
            # Missing actual result is always a hard fail — no mode can score
            # without the actual trajectory.
            return leaf_from_variable(
                _missing_variable_comparison(leaf.variable, var_result)
            )

        if ref_var is None:
            if not mode.is_baseline_free():
                return leaf_from_variable(
                    _missing_variable_comparison(leaf.variable, var_result)
                )
            ref_time = np.array([])
            ref_values = np.array([])
        else:
            if baseline is not None and baseline.shared_ref_time is not None:
                ref_time = baseline.shared_ref_time
            else:
                ref_time = np.array(ref_var["time"])
            ref_values = np.array(ref_var["values"])

        act_time = var_result.time
        act_values = var_result.values
        index = var_result.index

        # Apply the leaf window (idea #46) — slice both sides uniformly before
        # handing to mode.compare. Mode configs stay untouched; slicing is a
        # cross-cutting leaf property. Skip the ref slice when there's no
        # reference at all (baseline-free path).
        if leaf.window_start is not None or leaf.window_end is not None:
            windowed = True
            if len(ref_time) > 0:
                ref_time, ref_values = _slice_window(
                    ref_time,
                    ref_values,
                    leaf.window_start,
                    leaf.window_end,
                )
            act_time, act_values = _slice_window(
                act_time,
                act_values,
                leaf.window_start,
                leaf.window_end,
            )

    try:
        vc = mode.compare(ref_time, ref_values, act_time, act_values)
    except ValueError as exc:
        # Config errors a mode can only detect at compare time (a tube
        # control point with no widths, an unknown width mode) — same
        # leaf-scoped failure as the resolve_mode guard above (D107).
        return leaf_from_variable(_leaf_config_error_comparison(leaf, exc))
    vc.index = index
    vc.name = leaf.variable
    vc.tolerance_used = tolerance
    leaf_result = leaf_from_variable(vc)
    # Record which baseline this leaf scored against, so the reporter can
    # show "against=experiment" on non-primary leaves.
    leaf_result.diagnostics["against"] = leaf.against
    if quantity is not None:
        leaf_result.diagnostics["quantity"] = quantity
    if windowed:
        leaf_result.diagnostics["window"] = {
            "start": leaf.window_start,
            "end": leaf.window_end,
        }
    return leaf_result


def _scalar_path_arrays(
    leaf: LeafSpec,
    path: str,
    mode,
    baseline: BaselineView | None,
    scalars: ScalarSources,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | VariableComparison:
    """Resolve a stats-path quantity on both sides into 1-sample arrays.

    Returns ``(ref_time, ref_values, act_time, act_values)``, or a hard-fail
    :class:`VariableComparison` naming the path and the side it is missing
    from. The reference side is left empty when the leaf scores against
    declared bounds (baseline-free) — there may be no baseline at all.
    """
    act = _resolve_scalar_path(path, scalars.statistics, scalars.diagnostics)
    if act is None:
        return _missing_scalar_path_comparison(leaf, path, "current run")

    empty = np.array([])
    ref_time, ref_values = empty, empty
    if baseline is not None:
        ref = _resolve_scalar_path(path, baseline.statistics, baseline.diagnostics)
        if ref is None:
            if not mode.is_baseline_free():
                return _missing_scalar_path_comparison(
                    leaf, path, f"baseline {baseline.name!r}"
                )
        else:
            ref_time, ref_values = np.array([0.0]), np.array([ref])
    return ref_time, ref_values, np.array([0.0]), np.array([act])


def _resolve_scalar_path(
    path: str,
    statistics: dict,
    diagnostics: list,
) -> float | None:
    """Resolve a dotted quantity path to a number, or ``None`` if absent.

    Two lookups, in order, applied identically to both sides:

    1. a dotted walk into ``statistics`` (``translation.scalar_unknowns``);
    2. the last segment read as a diagnostics-summary field —
       ``"CPUtime.final"`` means diagnostic ``CPUtime``, field ``final``
       (one of final / min / max).

    Statistics win on a collision: ``EventCounter`` lives in both maps, and
    the statistics entry is the one the structural-warning checks already
    treat as authoritative.

    Diagnostics come in three shapes: the baseline's stored summaries
    (``{"name", "final", "min", "max"}``), pre-summary baselines carrying
    the full ``{"name", "values"}`` array (old data — the reporter's
    ``_build_trajectories`` still back-reads it), and the current run's
    ``VariableResult`` trajectories. The last two are summarized here; the
    run's trajectories are deliberately not stored (reference_store:
    CPUtime is nondeterministic).
    """
    value = _dotted_lookup(statistics, path)
    if value is not None:
        return value

    name, _, field_name = path.rpartition(".")
    if not name or field_name not in ("final", "min", "max"):
        return None
    for entry in diagnostics or []:
        if isinstance(entry, VariableResult):
            if entry.name != name:
                continue
            return _summarize_values(entry.values, field_name)
        if isinstance(entry, dict) and entry.get("name") == name:
            if field_name in entry:
                return _as_float(entry.get(field_name))
            # Pre-summary baseline: derive from the stored full array.
            return _summarize_values(entry.get("values"), field_name)
    return None


def _summarize_values(values, field_name: str) -> float | None:
    """Return the ``final`` / ``min`` / ``max`` of a diagnostic's array."""
    if values is None:
        return None
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return None
    if field_name == "final":
        return float(array[-1])
    return float(array.min() if field_name == "min" else array.max())


def _dotted_lookup(statistics: dict, path: str) -> float | None:
    """Walk ``path`` through nested dicts; ``None`` unless it lands on a number."""
    node: object = statistics or {}
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _as_float(node)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slice_window(
    t: np.ndarray,
    v: np.ndarray,
    start: float | None,
    end: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(t, v)`` restricted to ``[start, end]`` (inclusive).

    Open-ended on either side supported. A degenerate window returns empty
    arrays — modes that can't handle empty data (NRMSE / tube) then fail
    loudly downstream with a clear error, rather than being silently
    widened back to the full trace here.
    """
    mask = np.ones(len(t), dtype=bool)
    if start is not None:
        mask &= t >= start
    if end is not None:
        mask &= t <= end
    return t[mask], v[mask]


def _leaf_override_dict(leaf: LeafSpec) -> dict:
    """Translate a LeafSpec to the flat override dict resolve_mode expects.

    Spec metric names and override ``mode`` strings are the same identifier,
    so this is the identity mapping plus one rule: the default metric has no
    ``mode`` key (its absence is what selects it).
    """
    override = dict(leaf.params)
    if leaf.metric != DEFAULT_METRIC:
        override["mode"] = leaf.metric
    return override


def _incompatible_dataset_comparison(
    leaf: LeafSpec,
    desc: MetricDescriptor,
    dataset_kind: DatasetType,
) -> VariableComparison:
    """Build a hard-fail sentinel for a metric that can't score this data.

    Takes the already-resolved descriptor — the caller just checked its
    ``accepted_datasets``, and a re-lookup here would need a dead None
    branch (mypy union-attr).
    """
    accepted = ", ".join(sorted(d.value for d in desc.accepted_datasets))
    return VariableComparison(
        index=0,
        name=(
            f"{leaf.variable} (metric {leaf.metric!r} accepts {accepted}; "
            f"got {dataset_kind.value})"
        ),
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
    )


def _leaf_config_error_comparison(
    leaf: LeafSpec,
    exc: ValueError,
) -> VariableComparison:
    """Hard-fail sentinel for a leaf whose metric config is invalid.

    Names the variable and the metric so the report says which leaf to fix;
    the raised message (which names the correct key) rides in
    ``diagnostics["error"]``, matching the flat-override path's shape.

    ``config_error`` distinguishes this from a data-shaped sentinel like
    ``_no_data_failure``: a config that never built has no live-scoring
    state, so the reporter pins the leaf CLI-authoritative. An empty window
    is data, not config — that leaf stays live so dragging the window back
    over the samples re-scores it.
    """
    return VariableComparison(
        index=0,
        name=f"{leaf.variable} (metric {leaf.metric!r} misconfigured)",
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
        diagnostics={"error": str(exc), "config_error": True},
    )


def _missing_baseline_comparison(leaf: LeafSpec) -> VariableComparison:
    """Build a hard-fail sentinel for a leaf referencing an unknown baseline."""
    return VariableComparison(
        index=0,
        name=f"{leaf.variable} (against={leaf.against!r}: baseline not found)",
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
    )


def _missing_scalar_path_comparison(
    leaf: LeafSpec,
    path: str,
    side: str,
) -> VariableComparison:
    """Hard-fail sentinel for a quantity path absent from one side.

    Names the path and the side (``"current run"`` / ``"baseline 'x'"``) —
    a stats path that silently scored as 0 would be a green regression
    test measuring nothing. When the leaf took its label from the path
    (no authored ``variable``), the path is stated once, not twice.
    """
    label = "" if leaf.variable == path else f"{leaf.variable} "
    return VariableComparison(
        index=0,
        name=f"{label}(quantity {path!r} not found in {side})",
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
    )


def _missing_variable_comparison(
    name: str,
    var_result: VariableResult | None,
) -> VariableComparison:
    actual_final = float("nan")
    index = 0
    if var_result is not None and len(var_result.values) > 0:
        actual_final = float(var_result.values[-1])
        index = var_result.index
    return VariableComparison(
        index=index,
        name=name,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=actual_final,
    )
