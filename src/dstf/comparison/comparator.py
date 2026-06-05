"""Compare simulation results against stored references — orchestration.

The comparison surface is split across three modules:

  * :mod:`.types` — :class:`VariableComparison` / :class:`StructuralWarning`
    / :class:`TestComparison` dataclasses. Imported widely; lives
    separately so the algorithm and orchestration modules can both
    consume it without an import cycle.
  * :mod:`.algorithms` — per-mode math (``_compare_trajectories`` /
    ``_compare_tube`` / ``_compare_points`` / ``_compare_range`` /
    ``_compare_event_timing`` / ``_compare_dominant_frequency`` plus
    their FFT/event-detection helpers). Pure compute; mirrors the JS
    ``MODE_SCORERS`` for the live-preview live-recompute path.
  * THIS MODULE — :func:`compare_test` and :func:`compare_all`
    orchestration: pulls the right baseline, dispatches to the right
    algorithm via the :class:`MetricTree` evaluator, threads structural
    warnings, derives the final :class:`TestComparison.passed` flag.

For historical-import compatibility, :class:`VariableComparison` /
:class:`StructuralWarning` / :class:`TestComparison` and the
``_compare_*`` algorithm functions are re-exported here. New code
should import them from :mod:`.types` and :mod:`.algorithms` directly.
"""

import logging
from typing import Optional, TYPE_CHECKING

import numpy as np

from ..discovery.test_registry import TestModel
from ..simulators import TestResult
from ..storage.reference_store import ReferenceStore
from .algorithms import (
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
    _compute_fft_spectrum,
    _dedup_time_series,
    _find_event_boundaries,
    _find_strongest_peak_in_window,
    _find_top_n_peaks,
    _interpolate_tube_widths,
    _min_delta_in_box,
    _split_segments,
)
from .types import (
    DEFAULT_TOLERANCE,
    StructuralWarning,
    TestComparison,
    VariableComparison,
    _EPS,
)

if TYPE_CHECKING:
    # Annotation-only — runtime import is inside compare_test() to break the
    # comparator <-> metric_tree cycle (metric_tree imports VariableComparison
    # from .types).
    from .metric_tree import MetricResult

logger = logging.getLogger(__name__)


def _check_structural_changes(
    reference: dict,
    result: TestResult,
) -> list[StructuralWarning]:
    """Compare structural statistics between reference and current run."""
    warnings = []

    ref_stats = reference.get("statistics", {})
    cur_stats = result.statistics or {}

    checks = [
        ("translation.continuous_time_states", "Continuous states"),
        ("translation.nonlinear_count", "Nonlinear system count"),
        (
            "translation.nonlinear_after_manipulation_max",
            "Nonlinear max size (after manipulation)",
        ),
        ("translation.linear_count", "Linear system count"),
        (
            "translation.linear_after_manipulation_max",
            "Linear max size (after manipulation)",
        ),
        ("translation.scalar_unknowns", "Scalar unknowns"),
        ("translation.scalar_equations", "Scalar equations"),
        ("translation.numerical_jacobians", "Numerical Jacobians"),
        ("translation.init_nonlinear_count", "Init nonlinear system count"),
        ("translation.init_numerical_jacobians", "Init numerical Jacobians"),
        ("EventCounter", "Event count"),
    ]

    for dotted_key, label in checks:
        parts = dotted_key.split(".")
        ref_val = ref_stats
        cur_val = cur_stats
        for p in parts:
            ref_val = ref_val.get(p, {}) if isinstance(ref_val, dict) else None
            cur_val = cur_val.get(p, {}) if isinstance(cur_val, dict) else None

        if ref_val is None or cur_val is None:
            continue
        if str(ref_val) != str(cur_val):
            warnings.append(
                StructuralWarning(
                    field=label,
                    reference_value=str(ref_val),
                    current_value=str(cur_val),
                )
            )

    return warnings


def compare_test(
    test: TestModel,
    result: TestResult,
    reference: dict,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    store=None,  # Optional[ReferenceStore] — if provided, soft_checks from the new subdir layout are merged in
) -> TestComparison:
    """Compare a test's simulation results against its reference.

    Args:
        test: Test specification with model ID, variable overrides, etc.
        result: Simulation output (variables with time series).
        reference: Stored reference data dict.
        default_tolerance: Fallback tolerance when no per-test or per-ref
            tolerance is set.
        default_points: When True, variables without an explicit ``mode``
            override use points-mode (with empty points list = final-value
            comparison) instead of full NRMSE. Variables with
            ``mode: "tube"`` are *not* affected.
    """
    from .modes import resolve_mode
    from .metric_tree import implicit_and_tree
    from .tree_eval import collect_leaf_variables, evaluate_spec

    ref_test_id = reference.get("test_id")

    if not result.success:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=ref_test_id,
            sim_success=False,
            error_message=result.error_message or "Simulation failed",
        )

    # PTA.5 — simulate_only short-circuit: when the recognizer marked this
    # test as "just check that it simulates", skip per-variable comparison
    # and the whole baseline machinery. The sim already succeeded above.
    if test.simulate_only:
        from .metric_tree import MetricResult

        leaf = MetricResult(
            passed=True,
            score=None,
            label="simulate-only",
            diagnostics={"note": "no comparison performed; simulation succeeded"},
        )
        return TestComparison(
            model_id=test.model_id,
            passed=True,
            test_id=ref_test_id,
            variables=[],
            metric_tree=leaf,
        )

    structural_warnings = _check_structural_changes(reference, result)

    ref_vars = {v["index"]: v for v in reference.get("variables", [])}
    shared_ref_time = reference.get("time")
    if shared_ref_time is not None:
        shared_ref_time = np.array(shared_ref_time)
    comparisons = []

    # Resolve base comparison tolerance: per-test > reference > config > default
    ref_comparison = reference.get("comparison", {})
    base_tolerance = (
        test.comparison_tolerance
        or ref_comparison.get("tolerance")
        or default_tolerance
    )

    # Phase 3.3: when the spec provides an explicit MetricTree, it fully
    # replaces the implicit flat-AND + per-variable overrides. The user's
    # tree declares *which* variables participate and *how* each is scored,
    # so the legacy ``comparison.variable_overrides`` is ignored on this
    # path (documented — same fields move into each leaf's params).
    if test.metric_tree_spec is not None:
        from ..storage.reference_store import _extract_baselines
        from .tree_eval import BaselineView

        var_results_by_name = {v.name: v for v in result.variables if v.name}
        # Load primary from the flat ref file; merge in soft_checks from the
        # `soft_checks/ref_NNNN/` subdir when a store is supplied (D66). Leaves
        # pick which baseline to score against via `leaf.against` (defaults
        # to primary). The transition also reads any pre-migration flat
        # `baselines` dict so unmigrated ref files still evaluate correctly.
        all_baselines = _extract_baselines(reference)
        if store is not None:
            all_baselines.update(store.get_soft_checks(test.model_id))
        baselines: dict[str, BaselineView] = {}
        for name, bl in all_baselines.items():
            refs_by_name: dict[str, dict] = {}
            for rv in bl.variables:
                rn = rv.get("name") or rv.get("expression", "")
                if rn and rn not in refs_by_name:
                    refs_by_name[rn] = rv
            bl_time = np.array(bl.time) if bl.time else None
            baselines[name] = BaselineView(
                name=name,
                ref_vars_by_name=refs_by_name,
                shared_ref_time=bl_time,
            )
        tree = evaluate_spec(
            test.metric_tree_spec,
            var_results_by_name,
            baselines,
            base_tolerance,
        )
        return TestComparison(
            model_id=test.model_id,
            passed=tree.passed,
            test_id=ref_test_id,
            variables=collect_leaf_variables(tree),
            warnings=structural_warnings,
            metric_tree=tree,
        )

    # Merge variable overrides: spec overrides take precedence over reference
    ref_var_overrides = ref_comparison.get("variable_overrides", {})
    merged_overrides = {**ref_var_overrides, **test.variable_overrides}

    for var_result in result.variables:
        ref_var = ref_vars.get(var_result.index)

        # Resolve the variable's display name BEFORE the ref-missing check
        # so baseline-free modes can still use overrides keyed by the
        # actual variable name.
        ref_name = ref_var.get("name", ref_var.get("expression", "")) if ref_var else ""
        name = var_result.name or ref_name
        if not name or "\n" in name or name.startswith("cat("):
            name = f"x[{var_result.index}]"

        var_override = merged_overrides.get(name, {})
        tolerance = var_override.get("tolerance", base_tolerance)
        mode = resolve_mode(var_override, tolerance, default_points=default_points)

        if ref_var is None:
            # Baseline-free modes (range; event-timing with declared events;
            # dominant-frequency with declared peaks) don't consult the
            # reference, so a missing ref_var is fine — run the comparison
            # with empty ref arrays. Other modes still hard-fail here
            # because they can't score without a baseline.
            if mode.is_baseline_free():
                empty = np.array([])
                vc = mode.compare(empty, empty, var_result.time, var_result.values)
                vc.index = var_result.index
                vc.name = name
                vc.tolerance_used = tolerance
                comparisons.append(vc)
                continue
            comparisons.append(
                VariableComparison(
                    index=var_result.index,
                    name=var_result.name,
                    passed=False,
                    nrmse=float("inf"),
                    rmse=float("inf"),
                    signal_range=0.0,
                    max_abs_error=float("inf"),
                    max_abs_error_time=0.0,
                    reference_final=float("nan"),
                    actual_final=float(var_result.values[-1])
                    if len(var_result.values) > 0
                    else float("nan"),
                )
            )
            continue

        if shared_ref_time is not None:
            ref_time = shared_ref_time
        else:
            ref_time = np.array(ref_var["time"])
        ref_values = np.array(ref_var["values"])

        vc = mode.compare(ref_time, ref_values, var_result.time, var_result.values)
        vc.index = var_result.index
        vc.name = name
        vc.tolerance_used = tolerance
        comparisons.append(vc)

    # Phase 3.1: pass/fail flows from the MetricTree root, not a separate
    # accumulator. Today the tree is the implicit flat-AND of per-variable
    # leaves — same semantics as before. Phase 3.3+ swaps in user-authored
    # trees from test_spec.json.
    tree = implicit_and_tree(comparisons)

    return TestComparison(
        model_id=test.model_id,
        passed=tree.passed,
        test_id=ref_test_id,
        variables=comparisons,
        warnings=structural_warnings,
        metric_tree=tree,
    )


def _test_is_baseline_free(
    test: TestModel,
    default_tolerance: float,
) -> bool:
    """Does every leaf in ``test``'s metric tree score without a reference?

    Baseline-free modes (``range``; ``event-timing`` with declared events;
    ``dominant-frequency`` with declared peaks) compute pass/fail using
    only the actual simulation data plus config declared in the leaf
    itself. When every leaf is baseline-free, ``compare_all`` can skip
    the NO_REF short-circuit and still produce meaningful pass/fail on a
    fresh test with no saved baseline (idea #59 / D83).

    Mixed trees — any leaf that consults a reference — fall back to the
    legacy NO_REF behavior. There's no partial-run support.
    """
    from .modes import resolve_mode
    from .tree_spec import CombinatorSpec, LeafSpec
    from .tree_eval import _leaf_override_dict

    # User-authored spec tree: walk every leaf and resolve its mode.
    if test.metric_tree_spec is not None:
        leaves: list[LeafSpec] = []

        def _collect(node):
            if isinstance(node, LeafSpec):
                leaves.append(node)
                return
            for c in node.children:
                _collect(c)

        _collect(test.metric_tree_spec)
        if not leaves:
            return False
        for leaf in leaves:
            override = _leaf_override_dict(leaf)
            tol = float(leaf.params.get("tolerance", default_tolerance))
            try:
                mode = resolve_mode(override, tol, default_points=False)
            except Exception:
                # A leaf whose config doesn't resolve to a valid mode can't
                # be baseline-free by our check — fall back to NO_REF.
                return False
            if not mode.is_baseline_free():
                return False
        return True

    # Implicit-AND path: every tracked variable becomes a leaf. A test
    # without any per-variable override defaults to NRMSE, which needs a
    # reference — only overrides that select a baseline-free mode
    # qualify. An empty override dict means "no leaves that can qualify",
    # so treat the test as not baseline-free (stay with NO_REF).
    if not test.variable_overrides:
        return False
    base_tolerance = test.comparison_tolerance or default_tolerance
    for override in test.variable_overrides.values():
        tol = float(override.get("tolerance", base_tolerance))
        try:
            mode = resolve_mode(override, tol, default_points=False)
        except Exception:
            return False
        if not mode.is_baseline_free():
            return False
    return True


def compare_all(
    tests: list[TestModel],
    results: dict[str, TestResult],
    store: ReferenceStore,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
) -> list[TestComparison]:
    """Compare all test results against stored references."""
    import sys as _sys

    print(f"Comparing {len(tests)} tests against references...", file=_sys.stderr)
    comparisons = []

    for test in tests:
        result = results.get(test.model_id)
        if result is None:
            comparisons.append(
                TestComparison(
                    model_id=test.model_id,
                    passed=False,
                    sim_success=False,
                    error_message="No simulation results found",
                )
            )
            continue

        reference = store.get_reference(test.model_id)
        # simulate_only tests don't need a baseline — their pass criterion
        # is "did it simulate successfully?". Dispatch to compare_test with
        # an empty dict reference so its simulate_only short-circuit runs
        # (returning passed=True + metric_tree with label="simulate-only")
        # instead of collapsing to the generic NO_REF state.
        #
        # Baseline-free tests (every leaf is range / declared-events /
        # declared-peaks) follow the same pattern: skip the NO_REF guard
        # so the scorers can produce real pass/fail from config alone.
        # Mixed trees still short-circuit — partial runs aren't supported
        # by this fix (idea #59 / D83).
        if (
            reference is None
            and not test.simulate_only
            and not _test_is_baseline_free(test, default_tolerance)
        ):
            comparisons.append(
                TestComparison(
                    model_id=test.model_id,
                    passed=True,
                    has_reference=False,
                    error_message="No reference baseline stored",
                )
            )
            continue

        comp = compare_test(
            test,
            result,
            reference if reference is not None else {},
            default_tolerance=default_tolerance,
            default_points=default_points,
            store=store,
        )
        if reference is None:
            # Record that no baseline was stored even though the
            # simulate_only / baseline-free path produced a result.
            # Downstream renderers use this together with the metric
            # tree's pass/fail to decide how to render the badge.
            comp.has_reference = False
        comparisons.append(comp)

    return comparisons
