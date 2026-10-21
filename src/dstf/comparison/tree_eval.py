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

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

import numpy as np

from ..backends import VariableResult
from ..backends.common.statistics import read_summary
from ..backends.types import DatasetType
from ..storage.baseline import DiagnosticSummary, StoredVariable
from .comparator import migration_hint
from .geometry import Window
from .metric_tree import (
    Combinator,
    MetricResult,
    leaf_from_variable,
    stamp_leaf_path,
)
from .modes import ComparisonMode, resolve_mode
from .registry import DEFAULT_METRIC, MetricDescriptor
from .registry import combinator as registry_combinator
from .registry import metric as registry_metric
from .sides import Sides, parse_sides
from .tree_spec import (
    SUMMARY_FIELDS,
    CombinatorSpec,
    LeafSpec,
    SpecNode,
    reads_a_summary,
    resolves_a_variable,
)
from .types import VariableComparison

logger = logging.getLogger(__name__)

PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class BaselineView:
    """Pre-processed view of one baseline for leaf evaluation.

    Packages the stored variables by name and the baseline's shared time
    vector. Multiple named
    baselines coexist in the evaluator's input (``{"primary": ...,
    "experiment": ...}``); a leaf's ``against`` field picks which one it
    scores against.
    """

    name: str
    ref_vars_by_name: dict[str, StoredVariable]
    shared_ref_time: np.ndarray
    #: Statistics captured with the baseline (dotted keys like
    #: ``translation.scalar_unknowns``) — the baseline side of a ``scalar``
    #: leaf's stats-path quantity — and the stored summaries of the names
    #: it listed, which a summary-reading leaf on a name the baseline holds
    #: no curve of reads (`_summary_arrays`).
    statistics: dict = field(default_factory=dict)
    diagnostics: list[DiagnosticSummary] = field(default_factory=list)


@dataclass(frozen=True)
class ScalarSources:
    """The current run's stored scalars: its statistics, for stats-path
    ``scalar`` leaves, and the names it listed, for a summary-reading leaf
    on a name the run extracted no curve of (`_summary_arrays`).

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
    path: str = "/metrics",
    model_id: str = "",
    held_in: str = "",
    companions: frozenset[str] = frozenset(),
    _warned: set[tuple[str, str]] | None = None,
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

    ``scalars`` carries the run's statistics, for ``scalar`` leaves whose
    quantity is a stats path, and the listed names' recorded diagnostics,
    for a summary-reading leaf whose curve the run did not extract
    (`_summary_arrays`); a leaf whose series the run holds never reads it.

    ``path`` is this node's RFC 6902 path; leaves are stamped with it
    (``stamp_leaf_path``) so results address the spec, not a position in a
    flat list.

    ``model_id`` only names the test in the migrate-hint log line: a suite
    of tests printing the same nameless sentence says how many baselines
    are un-migrated and not one of the models whose baseline to open.

    ``held_in`` names the folder ``baselines`` were read from
    (``FMPy/macos``): a leaf naming a baseline that is not among them says
    which folder does not store it. The same spec is scored in every
    folder and a soft check is imported into one, so under a ``warn`` that
    is an advisory criterion not scored, never a reason to refuse the tree.
    ``companions`` are the names this folder stores as companions: a leaf
    naming one says so, since a companion is only drawn.
    """
    sources = scalars if scalars is not None else ScalarSources()
    # The migrate hint is a fact about a VARIABLE's baseline entry, not
    # about a leaf: a TRANSFORM test with five criteria on one un-migrated
    # variable printed the same sentence five times. The set is created by
    # the outermost call and threaded down, so it spans one test. Keyed by
    # the baseline too — the same variable missing from the primary AND
    # from a soft_check is two things to fix, not one repeat.
    seen = _warned if _warned is not None else set()
    if isinstance(spec, LeafSpec):
        result = _evaluate_leaf(
            spec,
            var_results_by_name,
            baselines,
            base_tolerance,
            dataset_kind,
            sources,
            seen,
            model_id,
            held_in,
            companions,
        )
        stamp_leaf_path(result, path)
        return result

    children = [
        evaluate_spec(
            c,
            var_results_by_name,
            baselines,
            base_tolerance,
            dataset_kind,
            sources,
            path=f"{path}/children/{i}",
            model_id=model_id,
            held_in=held_in,
            companions=companions,
            _warned=seen,
        )
        for i, c in enumerate(spec.children)
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


def leaf_comparisons_by_path(tree: MetricResult) -> dict[str, VariableComparison]:
    """``{leaf_path: VariableComparison}`` for every stamped leaf, in tree order.

    The report and the figure layer look leaves up here, by the path a patch
    op would use — never by position in a flat list.
    """
    out: dict[str, VariableComparison] = {}

    def _walk(node: MetricResult) -> None:
        if not node.children:
            vc = node.diagnostics.get("variable")
            path = node.diagnostics.get("leaf_path")
            if vc is not None and path is not None:
                out[path] = vc
            return
        for child in node.children:
            _walk(child)

    _walk(tree)
    return out


def advisory_comparisons(tree: MetricResult | None) -> list[VariableComparison]:
    """The leaf comparisons under a ``warn``, in tree order — the same
    objects :func:`collect_leaf_variables` returns. ``warn`` never fails its
    parent, so a failure among them informs and cannot fail the test: a
    surface that counts failures leaves these out, or calls them advisory.
    ``[]`` for no tree.

    A ``warn`` node is the one whose diagnostics carry ``warned``
    (``WarnCombinator.combine`` always sets it, to either value)."""
    out: list[VariableComparison] = []

    def _walk(node: MetricResult, under_warn: bool) -> None:
        if not node.children:
            if under_warn:
                _walk_leaves(node, out)
            return
        for child in node.children:
            _walk(child, under_warn or "warned" in (node.diagnostics or {}))

    if tree is not None:
        _walk(tree, False)
    return out


def failed_groups(tree: MetricResult | None) -> list[tuple[str, MetricResult]]:
    """The groups that fail ON THEIR OWN: a combinator that did not pass
    though every child of it did — a ``weighted`` whose sum is on the wrong
    side of its threshold, or undefined — as ``(place, node)`` in tree
    order, the place as a parse error writes it (``metrics.children[1]``).
    None from under a ``warn``: nothing there can fail the test. ``[]`` for
    no tree.

    A test can FAIL with no failing criterion; it then fails at one of
    these, and a surface that lists what failed has only them to name."""
    out: list[tuple[str, MetricResult]] = []

    def _walk(node: MetricResult, place: str) -> None:
        if not node.children or "warned" in (node.diagnostics or {}):
            return
        if not node.passed and all(child.passed for child in node.children):
            out.append((place, node))
        for i, child in enumerate(node.children):
            _walk(child, f"{place}.children[{i}]")

    if tree is not None:
        _walk(tree, "metrics")
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
    warned: set[tuple[str, str]] | None = None,
    model_id: str = "",
    held_in: str = "",
    companions: frozenset[str] = frozenset(),
) -> MetricResult:
    # Own set when called outside a walk: one leaf, one line, never silence.
    warned = set() if warned is None else warned
    desc = registry_metric(leaf.metric)
    quantity = leaf.params.get("quantity")
    # A path leaf reads its numbers from the run's statistics only, so it
    # resolves no variable of the result and has no expression to carry.
    # Every other leaf names one, and carries its expression onto whatever
    # comparison it returns — a leaf that fails before it ever scores (a
    # lost baseline, a misconfigured metric) is exactly the one a person
    # reads, so it must not fall back to the bare name (the Task 3 review).
    # ONE definition of "resolves no variable", shared with the spec walk
    # the expression soft-check reads.
    scores_stored_scalar = not resolves_a_variable(leaf)
    var_result = (
        None if scores_stored_scalar else var_results_by_name.get(leaf.variable)
    )
    expression = var_result.expression if var_result is not None else ""

    #: The baseline this leaf reads: its `against`, once its mode is built
    #: and is one that reads a baseline. None until then, and for a mode
    #: that scores from its own config.
    reads: str | None = None

    def _leaf(vc: VariableComparison) -> MetricResult:
        vc.expression = expression
        # The tolerance this leaf applies, on every return: one that fails
        # before it scores (a variable the result lacks, a baseline the test
        # does not have) reports the one it would have applied, never the
        # comparison's 0.0 default.
        vc.tolerance_used = tolerance
        # A criterion whose config is an error read nothing, whenever the
        # error was found: the flat path's sentinel says the same.
        config_error = (vc.diagnostics or {}).get("config_error")
        vc.against = None if config_error else reads
        return leaf_from_variable(vc)

    try:
        tolerance = _leaf_tolerance(leaf, base_tolerance)
    except ValueError as exc:
        # A tolerance that is neither a number nor a lower/upper pair fails
        # its leaf, not the run (D107); the base is the one it reports as
        # applied.
        tolerance = Sides(float(base_tolerance), float(base_tolerance))
        return _leaf(_leaf_config_error_comparison(leaf, exc))

    # A metric declares which dataset kinds it can score. Unscorable input
    # is a hard fail with a diagnostic naming both sides, not a crash inside
    # the mode. Unreachable for the builtins (all accept TIME_SERIES, which
    # is all any backend produces) — it guards registered metrics. After the
    # expression is resolved, like every other early return: this sentinel
    # is one a person reads, and it named the variable twice as differently
    # as the bare name and the label (the Task 4 review).
    if desc is not None and dataset_kind not in desc.accepted_datasets:
        return _leaf(_incompatible_dataset_comparison(leaf, desc, dataset_kind))

    override = _leaf_override_dict(leaf)
    try:
        mode = resolve_mode(override, tolerance, default_points=False)
    except ValueError as exc:
        # A misconfigured leaf fails that leaf, like the flat
        # variable_overrides path — one bad spec entry must not abort the
        # whole run with an anonymous traceback (D107).
        return _leaf(_leaf_config_error_comparison(leaf, exc))

    # leaf.against picks the named baseline. Missing name →
    # hard fail with a clear label (loud on user-facing reports). Unless
    # the leaf is baseline-free (range / declared-events / declared-peaks)
    # — then we don't need any baseline at all (idea #59 / D83).
    baseline = baselines.get(leaf.against)
    reads = None if mode.is_baseline_free() else leaf.against
    if baseline is None and not mode.is_baseline_free():
        return _leaf(_missing_baseline_comparison(leaf, held_in, companions))

    windowed = False
    # A criterion one stored number answers, on a side that holds that
    # number and not the curve: a listed name only such criteria name is
    # not extracted (`tree_variables`). Where both sides hold the curve,
    # today's path below.
    summary = (
        _summary_arrays(leaf, mode, var_result, baseline, scalars or ScalarSources())
        if reads_a_summary(leaf)
        else None
    )
    if isinstance(summary, VariableComparison):
        return _leaf(summary)
    if summary is not None:
        ref_time, ref_values, act_time, act_values, index = summary
    elif scores_stored_scalar and isinstance(quantity, str):
        # A string ``quantity`` reads a statistic of the log instead of a
        # tracked series — where the data comes from is the evaluator's
        # call, the same seam the window below occupies (a future
        # ``{"expr": ...}`` quantity extends this spot). The window is
        # skipped deliberately: a statistic is not a time series.
        #
        # The switch is gated on the metric's ``accepts_quantity`` flag, not
        # on the param alone: a stray ``quantity`` on an nrmse/tube/points
        # leaf must leave that leaf scoring its trajectory. Ungated, both
        # sides collapsed to one identical sample and a 2x-off signal passed.
        resolved = _scalar_path_arrays(
            leaf,
            quantity,
            mode,
            baseline,
            scalars or ScalarSources(),
            run_variables=var_results_by_name,
        )
        if isinstance(resolved, VariableComparison):
            return _leaf(resolved)
        ref_time, ref_values, act_time, act_values = resolved
        index = 0
    else:
        ref_var = baseline.ref_vars_by_name.get(leaf.variable) if baseline else None

        if var_result is None:
            # Missing actual result is always a hard fail — no mode can score
            # without the actual trajectory.
            return _leaf(
                _missing_variable_comparison(leaf.variable, var_result, mode=mode.name)
            )

        if ref_var is None:
            if not mode.is_baseline_free():
                # A baseline that stores this variable under its EXPRESSION
                # is un-migrated, not missing: the same clause the flat path
                # logs, from the same helper, on the leaf's own reason as
                # well — the tree path shows no log line to the person
                # reading the report. Logged only when it applies; a
                # variable simply new to the baseline is re-accepted. The
                # REASON rides on every leaf; the LOG LINE is said once per
                # (variable, baseline) — `warned` is the test's set.
                hint = migration_hint(
                    baseline.ref_vars_by_name if baseline else {},
                    leaf.variable,
                    expression,
                )
                key = (leaf.against, leaf.variable)
                if hint and key not in warned:
                    warned.add(key)
                    logger.warning(
                        "%s: baseline %r holds no variable named %r%s",
                        model_id or "?",
                        leaf.against,
                        leaf.variable,
                        hint,
                    )
                return _leaf(
                    _missing_variable_comparison(
                        leaf.variable, var_result, mode=mode.name, hint=hint
                    )
                )
            ref_time = np.array([])
            ref_values = np.array([])
        else:
            assert baseline is not None  # a stored variable is in a baseline
            ref_time = baseline.shared_ref_time
            ref_values = np.array(ref_var.values)

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
        return _leaf(_leaf_config_error_comparison(leaf, exc))
    vc.index = index
    vc.name = leaf.variable
    if windowed:
        # The window is a leaf property, not a mode's: the evaluator owns it.
        vc.geometry = vc.geometry + (
            Window(id="window", start=leaf.window_start, end=leaf.window_end),
        )
    leaf_result = _leaf(vc)
    if quantity is not None:
        leaf_result.diagnostics["quantity"] = quantity
    if windowed:
        leaf_result.diagnostics["window"] = {
            "start": leaf.window_start,
            "end": leaf.window_end,
        }
    return leaf_result


def _summary_arrays(
    leaf: LeafSpec,
    mode: ComparisonMode,
    var_result: VariableResult | None,
    baseline: BaselineView | None,
    scalars: ScalarSources,
) -> (
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]
    | VariableComparison
    | None
):
    """A summary-reading leaf (:func:`reads_a_summary`) on a side that LACKS
    the curve and holds the name's summary: the run, a recorded diagnostic
    (`ScalarSources.diagnostics`); the baseline, a stored summary
    (`BaselineView.diagnostics`) with that field. None where neither side
    is such a side: the caller's curve path, unchanged.

    Otherwise ``(ref_time, ref_values, act_time, act_values, index)``: a
    side with its curve hands the curve, which the mode reduces as it
    reduces any; a side without hands its summary's number as one sample,
    as `_scalar_path_arrays` does. The reference is empty for a
    baseline-free mode (declared bounds). ``index`` is the run's curve's,
    else 0 — a diagnostic is numbered in its own series, and
    `build_trajectories` pairs a comparison with a series by its index:
    a diagnostic's index 1 drew the first tracked variable's curve under the
    diagnostic's name. A side holding neither reads as the curve path's missing
    variable."""
    field_name = leaf.params["quantity"]["reduce"]
    ref_var = baseline.ref_vars_by_name.get(leaf.variable) if baseline else None
    run_entry = next((d for d in scalars.diagnostics if d.name == leaf.variable), None)
    stored = (
        next((d for d in baseline.diagnostics if d.name == leaf.variable), None)
        if baseline is not None
        else None
    )
    stored_number = _as_float(getattr(stored, field_name)) if stored else None
    run_from_summary = var_result is None and run_entry is not None
    base_from_summary = ref_var is None and stored_number is not None
    if not (run_from_summary or base_from_summary):
        return None

    run = var_result
    if run is None and run_entry is not None:
        number = _summarize_values(run_entry.values, field_name)
        if number is not None:
            run = VariableResult(
                index=0,
                time=np.array([0.0]),
                values=np.array([number]),
                name=leaf.variable,
            )
    if run is None:
        return _missing_variable_comparison(leaf.variable, None, mode=mode.name)

    if ref_var is not None:
        assert baseline is not None  # a stored variable is in a baseline
        ref_time, ref_values = baseline.shared_ref_time, np.array(ref_var.values)
    elif stored_number is not None:
        ref_time, ref_values = np.array([0.0]), np.array([stored_number])
    elif mode.is_baseline_free():
        ref_time, ref_values = np.array([]), np.array([])
    else:
        return _missing_variable_comparison(leaf.variable, run, mode=mode.name)
    return ref_time, ref_values, run.time, run.values, run.index


def _scalar_path_arrays(
    leaf: LeafSpec,
    path: str,
    mode,
    baseline: BaselineView | None,
    scalars: ScalarSources,
    *,
    run_variables: Mapping[str, VariableResult],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | VariableComparison:
    """Resolve a stats-path quantity on both sides into 1-sample arrays.

    Returns ``(ref_time, ref_values, act_time, act_values)``, or a hard-fail
    :class:`VariableComparison` naming the path and the side it is missing
    from. The reference side is left empty when the leaf scores against
    declared bounds (baseline-free) — there may be no baseline at all.
    ``run_variables`` are the run's, only for the missing path's hint.
    """
    act = resolve_scalar_path(path, scalars.statistics)
    if act is None:
        run_names = {*run_variables, *(d.name for d in scalars.diagnostics)}
        return _missing_scalar_path_comparison(leaf, path, "current run", run_names)

    empty = np.array([])
    ref_time, ref_values = empty, empty
    if baseline is not None:
        ref = resolve_scalar_path(path, baseline.statistics)
        if ref is None:
            if not mode.is_baseline_free():
                held = {
                    *baseline.ref_vars_by_name,
                    *(d.name for d in baseline.diagnostics),
                }
                return _missing_scalar_path_comparison(
                    leaf, path, f"baseline {baseline.name!r}", held
                )
        else:
            ref_time, ref_values = np.array([0.0]), np.array([ref])
    return ref_time, ref_values, np.array([0.0]), np.array([act])


def resolve_scalar_path(path: str, statistics: dict) -> float | None:
    """Resolve a dotted quantity path to a number, or ``None`` if absent:
    a dotted walk into ``statistics`` (``translation.scalar_unknowns``),
    applied identically to both sides. A path names a statistic of the
    simulator's log, never a variable: a variable's number is its own
    criterion, ``{"reduce": ...}`` (:func:`_summary_arrays` reads a listed
    name's stored summary)."""
    return _dotted_lookup(statistics, path)


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
    parts = path.split(".")
    for i, part in enumerate(parts):
        if not isinstance(node, dict):
            return None
        if part not in node:
            # A summary a baseline written before the parser always wrote
            # it: 0 where its list is present and empty (the zero rule).
            return _as_float(read_summary(node, part)) if i == len(parts) - 1 else None
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


def build_leaf_mode(leaf: LeafSpec, base_tolerance: float) -> ComparisonMode:
    """The mode ``leaf`` is scored with, built as :func:`_evaluate_leaf`
    builds it. ``ValueError`` — the leaf's config error at compare time —
    when the leaf can never be scored: a tolerance that is no number or
    pair, a ``range`` with neither bound, a ``scalar`` with no quantity.
    An update asks before it writes a file
    (``discovery.spec_update.refuse_newly_unscorable``): a write that makes
    such a leaf is refused."""
    return resolve_mode(
        _leaf_override_dict(leaf),
        _leaf_tolerance(leaf, base_tolerance),
        default_points=False,
    )


def _leaf_tolerance(leaf: LeafSpec, base: float) -> Sides:
    """The tolerance ``leaf`` applies: its own, else the test's ``base``.
    One that is neither a number nor a lower/upper pair (``"abc"``,
    ``null``, a pair missing a side) is a ``ValueError`` naming it, which
    the caller turns into the leaf's config error (D107)."""
    return parse_sides(leaf.params.get("tolerance", base), "tolerance")


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

    The name stays the variable — the figure layer pairs leaves with plots
    by it — and the reason rides in ``diagnostics["error"]``, where
    ``registry.summarize``, the console and JUnit already read it.
    """
    accepted = ", ".join(sorted(d.value for d in desc.accepted_datasets))
    return VariableComparison(
        index=0,
        name=leaf.variable,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
        diagnostics={
            "error": (
                f"metric {leaf.metric!r} accepts {accepted}; got {dataset_kind.value}"
            )
        },
    )


def _leaf_config_error_comparison(
    leaf: LeafSpec,
    exc: ValueError,
) -> VariableComparison:
    """Hard-fail sentinel for a leaf whose metric config is invalid.

    The name is the variable — the figure layer pairs leaves with plots by
    it — and the reason (the metric that could not build, plus the raised
    message, which names the correct key) rides in ``diagnostics["error"]``,
    matching the flat-override path's shape.

    ``config_error`` marks the sentinel as config-shaped, distinguishing
    it from a data-shaped one like ``_no_data_failure``: an empty window
    is data — the same spec scores once the samples are there — while a
    metric config that never built is a defect in the spec itself.
    """
    return VariableComparison(
        index=0,
        name=leaf.variable,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
        diagnostics={
            "error": f"metric {leaf.metric!r} misconfigured: {exc}",
            "config_error": True,
        },
    )


def _missing_baseline_comparison(
    leaf: LeafSpec, held_in: str, companions: frozenset[str] = frozenset()
) -> VariableComparison:
    """Build a hard-fail sentinel for a leaf naming a baseline this folder
    does not store.

    The name stays the variable so the leaf still finds its plot; the
    sentence in ``diagnostics["error"]`` names the baseline and the folder
    (``held_in``) that does not hold it — or, for a name this folder stores
    as a companion (``companions``), says that it is one: it is stored, and
    only drawn.
    """
    if leaf.against in companions:
        said = f"{leaf.against!r} is a companion here: it is only drawn, never scored"
    else:
        what = (
            "no primary baseline"
            if leaf.against == "primary"
            else f"no soft check {leaf.against!r}"
        )
        where = f" for {held_in}" if held_in else ""
        said = f"{what} is stored{where}"
    return VariableComparison(
        index=0,
        name=leaf.variable,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
        diagnostics={"error": said},
    )


def _missing_scalar_path_comparison(
    leaf: LeafSpec,
    path: str,
    side: str,
    names: Collection[str] = (),
) -> VariableComparison:
    """Hard-fail sentinel for a quantity path absent from one side.

    The name stays the leaf's variable (the path itself when none was
    authored) so the leaf keeps its place in the report; the diagnostic
    names the path and the side (``"current run"`` / ``"baseline 'x'"``) —
    a stats path that silently scored as 0 would be a green regression
    test measuring nothing.

    A path that names a variable that side holds (``names``: its
    variables and its listed names) — ``X.final`` / ``.min`` / ``.max``,
    which the 1.1 add form wrote, or a bare ``X``, which the card's ``+``
    wrote — says how a variable's number is written. A message, no alias.
    """
    return VariableComparison(
        index=0,
        name=leaf.variable,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode=leaf.metric,
        diagnostics={
            "error": f"quantity {path!r} not found in {side}"
            + _one_spelling(path, names)
        },
    )


def _one_spelling(path: str, names: Collection[str]) -> str:
    """The hint a path naming a variable gets (`_missing_scalar_path_comparison`)."""
    name, _, field_name = path.rpartition(".")
    if not (name in names and field_name in SUMMARY_FIELDS):
        name, field_name = path, "final"
        if name not in names:
            return ""
    return (
        " — a variable's number is written "
        f'{{"variable": "{name}", "quantity": {{"reduce": "{field_name}"}}}}'
    )


def _missing_variable_comparison(
    name: str,
    var_result: VariableResult | None,
    *,
    mode: str,
    hint: str = "",
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
        # The criterion's own metric: with none, a scalar or tube criterion
        # on a missing variable read as an nrmse one.
        mode=mode,
        # The two call sites are told apart by what they had: no result for
        # the variable at all, or a result the baseline does not carry. The
        # second one carries the migration hint when the baseline holds the
        # variable under its expression (``hint`` is "" otherwise).
        diagnostics={
            "error": (
                f"variable {name!r} not found in the result"
                if var_result is None
                else f"variable {name!r} not found in the baseline{hint}"
            )
        },
    )
