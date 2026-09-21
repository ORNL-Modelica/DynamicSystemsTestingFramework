"""Compare simulation results against stored references — orchestration.

The comparison surface is split across three modules:

  * :mod:`.types` — :class:`VariableComparison` / :class:`StructuralWarning`
    / :class:`TestComparison` dataclasses. Imported widely; lives
    separately so the algorithm and orchestration modules can both
    consume it without an import cycle.
  * :mod:`.algorithms` — per-mode math (``_compare_trajectories`` /
    ``_compare_tube`` / ``_compare_points`` / ``_compare_range`` /
    ``_compare_scalar`` / ``_compare_event_timing`` /
    ``_compare_dominant_frequency`` plus
    their FFT/event-detection helpers). Pure compute.
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
from collections.abc import Iterable
from typing import TYPE_CHECKING

import numpy as np

from ..backends import TestResult
from ..backends.types import DatasetType
from ..discovery.test_registry import TestModel
from ..discovery.unit_tests import unit_test_variables
from ..storage.reference_store import ReferenceStore

# Re-export facade (see module docstring): modes/tree_eval/metric_tree and the
# test suite import the per-mode algorithm functions from here, not from
# .algorithms directly. comparator.py does not call them itself, so they read
# as unused — keep the noqa to preserve the public import surface.
from .algorithms import (  # noqa: F401
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_scalar,
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
from .registry import metric as registry_metric
from .types import (
    DEFAULT_TOLERANCE,
    StructuralWarning,
    TestComparison,
    VariableComparison,
)

if TYPE_CHECKING:
    # Annotation-only — runtime import is inside compare_test() to break the
    # comparator <-> metric_tree cycle (metric_tree imports VariableComparison
    # from .types).
    pass

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
        ref_val: object = ref_stats
        cur_val: object = cur_stats
        for p in parts:
            # .get(p) — a missing key must yield None (skip), not {}. With
            # {} the None-guard below never fired and every baseline-free
            # run produced phantom "{} → 5" warnings (review 2026-07-06).
            ref_val = ref_val.get(p) if isinstance(ref_val, dict) else None
            cur_val = cur_val.get(p) if isinstance(cur_val, dict) else None

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


def migration_hint(baseline_names: Iterable[str], expression: str) -> str:
    """The clause that names the migration command, or ``""``.

    A variable whose baseline entry is missing is un-migrated exactly when
    the baseline holds an entry under the text the variable's EXPRESSION
    spells — the name every UnitTests baseline was stored under before the
    identity change. Both scoring paths say it, from here: a hint on every
    missing baseline would point a whole suite of brand-new variables at a
    command that would rewrite nothing (the Task 3 review).
    """
    if not expression or expression not in set(baseline_names):
        return ""
    return (
        f" — the baseline stores it under its expression {expression!r}; "
        "`dstf migrate-variable-names` renames it"
    )


def _check_expressions(
    variables: list[dict],
    result: TestResult,
    *,
    against: str = "",
) -> list[StructuralWarning]:
    """A UnitTests variable is keyed by its position (``unitTests.x[2]``), so
    an edit to the model's ``x={...}`` can put a different quantity under the
    same name. The baseline records the expression it was accepted with; a
    run measuring another one says so. A warning, never a failure — and
    silent when either side recorded nothing (a pattern-tracked variable, a
    backend that writes no expression).

    ``variables`` is the entry list of ONE baseline and ``against`` its name
    (empty for the primary, which needs no saying). Pairing is by name, and
    a name stored twice is read from the FIRST entry — the one the scoring
    pairs with; reading the last reported a change no leaf ever scored.
    """
    recorded: dict[str, str] = {}
    for entry in variables:
        name = entry.get("name") or ""
        if name and name not in recorded:
            recorded[name] = entry.get("expression") or ""
    where = f" (against {against})" if against else ""
    return [
        StructuralWarning(
            field=f"Expression of {var.name}{where}",
            reference_value=recorded[var.name],
            current_value=var.expression,
        )
        for var in result.variables
        if var.expression
        and recorded.get(var.name)
        and recorded[var.name] != var.expression
    ]


def _named_baseline_expression_warnings(
    spec,
    baselines: dict,
    result: TestResult,
) -> list[StructuralWarning]:
    """The expression check on every NON-primary baseline the tree scores
    against (an ``against: fmpy`` leaf, a soft_check import).

    A named baseline records its own expressions, and the primary's check
    never reads them — a soft_check accepted against ``a.Q`` while the model
    now measures ``b.Q`` compares two different quantities in silence. The
    check runs once per baseline the tree names, not once per leaf naming
    it, so two leaves on one variable say it once.
    """
    from ..storage.baseline import PRIMARY_BASELINE
    from .tree_spec import collect_baselines

    out: list[StructuralWarning] = []
    for name in collect_baselines(spec):
        if name == PRIMARY_BASELINE:
            continue
        baseline = baselines.get(name)
        if baseline is not None:
            out += _check_expressions(baseline.variables, result, against=name)
    return out


#: Where an override key was read from, for the message below. The two
#: tiers are merged before scoring, so the key alone does not say which
#: file to open.
_SPEC_TIER = "test_spec.json"
_REF_TIER = "the reference JSON"


def _warn_about_overrides_that_name_nothing(
    model_id: str,
    spec_overrides: dict[str, dict],
    ref_overrides: dict[str, dict],
    result: TestResult,
) -> None:
    """An override key that names no variable of this result does nothing.

    It scored nothing before either — the flat path has always looked its
    keys up by the result variable's name — but it was silent about it. A key
    that names a variable's EXPRESSION is the shape a spec written before the
    identity change has (``pipe.T[1]`` where the result file says
    ``unitTests.x[1]``): the message says which name it meant and names the
    command that rewrites it. One line per test; no status changes.

    It also says WHICH FILE holds each key. Overrides come from two tiers —
    the test's entry in ``test_spec.json`` and the ``comparison`` section of
    the baseline the run scored against — which are merged before anything
    is scored; told only the key, a user opens the wrong one. A key present
    in both is named for both.

    When one expression belongs to several variables (``x=cat(1, y, y)``
    spells every element the same), the message names all of them: which one
    the key meant is the user's call, not a guess this makes for them.
    """
    names = {v.name for v in result.variables if v.name}
    unknown = [key for key in {**ref_overrides, **spec_overrides} if key not in names]
    if not unknown:
        return
    by_expression: dict[str, list[str]] = {}
    for v in result.variables:
        if v.expression and v.name:
            by_expression.setdefault(v.expression, []).append(v.name)
    parts = []
    for key in unknown:
        tiers = [
            tier
            for tier, keys in ((_SPEC_TIER, spec_overrides), (_REF_TIER, ref_overrides))
            if key in keys
        ]
        where = f" in {' and '.join(tiers)}"
        owners = by_expression.get(key)
        of = (
            f" (the expression of {', '.join(repr(n) for n in owners)})"
            if owners
            else ""
        )
        parts.append(f"{key!r}{where}{of}")
    hint = (
        " — a key that names an expression is rewritten by "
        "`dstf migrate-variable-names`"
        if any(key in by_expression for key in unknown)
        else ""
    )
    logger.warning(
        "%s: variable_overrides key(s) name no variable of this result and "
        "are ignored: %s%s",
        model_id,
        ", ".join(parts),
        hint,
    )


def _tracked_but_not_scored(
    test: TestModel, result: TestResult
) -> StructuralWarning | None:
    """A tree scores only what its leaves name; a tracked variable no leaf
    covers is stored and plotted but never judged, and CI stays green while
    it drifts. Say so on every surface that shows warnings (D126). The flat
    path has no such gap — it scores every tracked variable."""
    from .tree_spec import collect_variables

    assert test.metric_tree_spec is not None
    covered = set(collect_variables(test.metric_tree_spec))
    unscored = [v.name for v in result.variables if v.name and v.name not in covered]
    if not unscored:
        return None
    return StructuralWarning(
        field="Tracked variables without a criterion",
        reference_value=f"{len(covered)} scored by the tree",
        current_value=", ".join(unscored),
    )


def compare_test(
    test: TestModel,
    result: TestResult,
    reference: dict,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    store=None,  # Optional[ReferenceStore] — if provided, soft_checks from the new subdir layout are merged in
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
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
        dataset_kind: The :class:`~dstf.backends.types.DatasetType` the
            backend that produced *result* declares it emits. Threaded into
            the MetricTree evaluator so a leaf whose metric doesn't accept
            the kind hard-fails with a diagnostic naming both. Defaults to
            ``TIME_SERIES`` — the only kind any backend produces today.
    """
    from .metric_tree import implicit_and_tree
    from .modes import resolve_mode
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
            simulate_only=True,
        )

    structural_warnings = _check_structural_changes(
        reference, result
    ) + _check_expressions(reference.get("variables", []), result)

    # Pairing is by name. Pairing by numeric index silently scored variables
    # against the wrong baseline trajectory after any spec reorder (review
    # 2026-07-06, finding 8); a first-entry-wins map is also what the
    # expression check reads.
    ref_vars_by_name = {}
    for v in reference.get("variables", []):
        rn = v.get("name", "")
        if rn and rn not in ref_vars_by_name:
            ref_vars_by_name[rn] = v
    shared_ref_time = reference.get("time")
    if shared_ref_time is not None:
        shared_ref_time = np.array(shared_ref_time)
    comparisons = []

    # Resolve base comparison tolerance: per-test > reference > config >
    # default. `is not None` chain, NOT `or` — an explicit tolerance of 0
    # (strictest possible) must be honored (review 2026-07-06, finding 7).
    ref_comparison = reference.get("comparison", {})
    if test.comparison_tolerance is not None:
        base_tolerance = test.comparison_tolerance
    elif ref_comparison.get("tolerance") is not None:
        base_tolerance = ref_comparison["tolerance"]
    else:
        base_tolerance = default_tolerance

    # Phase 3.3: when the spec provides an explicit MetricTree, it fully
    # replaces the implicit flat-AND + per-variable overrides. The user's
    # tree declares *which* variables participate and *how* each is scored,
    # so the legacy ``comparison.variable_overrides`` is ignored on this
    # path (documented — same fields move into each leaf's params).
    if test.metric_tree_spec is not None:
        from ..storage.reference_store import _extract_baselines
        from .tree_eval import BaselineView, ScalarSources

        var_results_by_name = {v.name: v for v in result.variables if v.name}
        # Load primary from the flat ref file; merge in soft_checks from the
        # `soft_checks/ref_NNNN/` subdir when a store is supplied (D66). Leaves
        # pick which baseline to score against via `leaf.against` (defaults
        # to primary). With no store there are no named baselines, so an
        # `against: <name>` leaf gets the missing-baseline sentinel.
        all_baselines = _extract_baselines(reference)
        if store is not None:
            all_baselines.update(store.get_soft_checks(test.model_id))
        baselines: dict[str, BaselineView] = {}
        for name, bl in all_baselines.items():
            refs_by_name: dict[str, dict] = {}
            for rv in bl.variables:
                rn = rv.get("name", "")
                if rn and rn not in refs_by_name:
                    refs_by_name[rn] = rv
            bl_time = np.array(bl.time) if bl.time else None
            baselines[name] = BaselineView(
                name=name,
                ref_vars_by_name=refs_by_name,
                shared_ref_time=bl_time,
                statistics=bl.statistics,
                diagnostics=bl.diagnostics,
            )
        structural_warnings += _named_baseline_expression_warnings(
            test.metric_tree_spec, all_baselines, result
        )
        tree = evaluate_spec(
            test.metric_tree_spec,
            var_results_by_name,
            baselines,
            base_tolerance,
            dataset_kind,
            # Stored scalars for `scalar` leaves reading a statistics /
            # diagnostics path instead of a tracked series.
            ScalarSources(
                statistics=result.statistics or {},
                diagnostics=result.diagnostics,
            ),
        )
        unscored = _tracked_but_not_scored(test, result)
        return TestComparison(
            model_id=test.model_id,
            passed=tree.passed,
            test_id=ref_test_id,
            variables=collect_leaf_variables(tree),
            warnings=structural_warnings + ([unscored] if unscored else []),
            metric_tree=tree,
        )

    # Merge variable overrides: spec overrides take precedence over reference
    ref_var_overrides = ref_comparison.get("variable_overrides", {})
    merged_overrides = {**ref_var_overrides, **test.variable_overrides}
    _warn_about_overrides_that_name_nothing(
        test.model_id, test.variable_overrides, ref_var_overrides, result
    )

    for var_result in result.variables:
        # A variable is paired with the baseline entry carrying its name.
        # There is no index fallback: a result variable always has the name
        # its result file gives it (a nameless one is a backend bug), and an
        # entry that merely sits at the same index is a different quantity
        # after any reorder. A baseline that has none says so once per
        # variable, with the migration clause when it is the un-migrated
        # shape rather than a variable the baseline never held.
        ref_var = ref_vars_by_name.get(var_result.name)
        if ref_var is None and reference.get("variables"):
            logger.warning(
                "%s: the baseline holds no variable named %r — treating as "
                "missing baseline (re-accept to refresh)%s",
                test.model_id,
                var_result.name,
                migration_hint(ref_vars_by_name, var_result.expression),
            )

        # A variable is scored and overridden under the name its result file
        # gives it; the expression beside it is a label and no key of any
        # kind. Resolved BEFORE the ref-missing check so baseline-free modes
        # still find their overrides.
        name = var_result.name

        var_override = merged_overrides.get(name, {})
        tolerance = var_override.get("tolerance", base_tolerance)
        try:
            mode = resolve_mode(var_override, tolerance, default_points=default_points)
        except ValueError as exc:
            # An unrecognized mode string must fail the variable loudly —
            # silently falling back to NRMSE changed the scoring algorithm
            # on a typo (review 2026-07-06). ``mode=""`` rather than the
            # "nrmse" field default: no mode resolved, so the report must
            # not caption this "nrmse leaf not scored". The offending
            # string isn't stamped either — it would feed a bogus name to
            # the reporter's mode lookups; it rides in the error text.
            comparisons.append(
                VariableComparison(
                    index=var_result.index,
                    name=name,
                    passed=False,
                    nrmse=float("inf"),
                    rmse=float("inf"),
                    signal_range=0.0,
                    max_abs_error=float("inf"),
                    max_abs_error_time=0.0,
                    reference_final=float("nan"),
                    actual_final=float("nan"),
                    mode="",
                    diagnostics={"error": str(exc), "config_error": True},
                )
            )
            continue

        # A stats-path quantity is resolved by the tree evaluator from the
        # run's statistics/diagnostics; this flat path only has the
        # variable's own trajectory, and ScalarMode would silently reduce
        # THAT instead (review finding, D106).
        desc = registry_metric(mode.name)
        if (
            desc is not None
            and desc.accepts_quantity
            and isinstance(var_override.get("quantity"), str)
        ):
            comparisons.append(
                VariableComparison(
                    index=var_result.index,
                    name=name,
                    passed=False,
                    nrmse=float("inf"),
                    rmse=float("inf"),
                    signal_range=0.0,
                    max_abs_error=float("inf"),
                    max_abs_error_time=0.0,
                    reference_final=float("nan"),
                    actual_final=float("nan"),
                    mode=mode.name,
                    diagnostics={
                        "error": (
                            f"quantity {var_override['quantity']!r}: a stats-path "
                            "quantity needs a `metrics` tree; variable_overrides "
                            "can only reduce the variable's own trajectory"
                        )
                    },
                )
            )
            continue

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
                    name=name,
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
    #
    # The loop appends exactly one comparison per result variable, in order,
    # so the expression rides onto each of them here rather than at all five
    # of the loop's append sites.
    for vc, var_result in zip(comparisons, result.variables, strict=True):
        vc.expression = var_result.expression
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
    from .tree_eval import _leaf_override_dict
    from .tree_spec import LeafSpec

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
    # Mixed tests: any tracked variable WITHOUT an override becomes an
    # implicit NRMSE leaf, which needs a baseline — so the test as a
    # whole is not baseline-free (review 2026-07-06, finding 10; matches
    # the docstring's "mixed trees fall back to NO_REF"). Glob patterns
    # can't be proven covered, so they also disqualify.
    #
    # Both sources are tracked, not one or the other: a ``both`` test whose
    # spec patterns are all covered still has the component's variables
    # scoring nrmse against a baseline.
    tracked = list(test.variable_patterns) + [v.name for v in unit_test_variables(test)]
    if tracked and set(tracked) - set(test.variable_overrides):
        return False
    base_tolerance = (
        test.comparison_tolerance
        if test.comparison_tolerance is not None
        else default_tolerance
    )
    for override in test.variable_overrides.values():
        tol = float(override.get("tolerance", base_tolerance))
        try:
            mode = resolve_mode(override, tol, default_points=False)
        except Exception:
            return False
        if not mode.is_baseline_free():
            return False
    return True


def compare_one(
    test: TestModel,
    result: TestResult | None,
    store: ReferenceStore,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
) -> TestComparison:
    """One test's comparison, short-circuits included: no result and a
    failed simulation are SIM_FAIL; no baseline is NO_REF unless the test is
    simulate-only or every leaf scores without a reference. ``compare_all``
    is this per test; the authoring Session's rescore is this for one."""
    if result is None:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            sim_success=False,
            error_message="No simulation results found",
        )

    # A simulation that FAILED is SIM_FAIL regardless of whether a baseline
    # exists — this check MUST precede the no-baseline short-circuit below.
    # Backends differ in how they signal failure: Dymola omits the test
    # (caught by `result is None` above), but OpenModelica's read_result
    # returns a success=False TestResult (missing .mat) that is NOT None,
    # so without this it fell through to the no-baseline branch and was
    # mislabeled NO_REF with passed=True. Surfaced 2026-07-07 across 91
    # OM/TRANSFORM compile/solver failures on models that never got OM
    # baselines (mirrors compare_test's own `not result.success` guard).
    if not result.success:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            sim_success=False,
            error_message=result.error_message or "Simulation failed",
        )

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
        return TestComparison(
            model_id=test.model_id,
            passed=True,
            has_reference=False,
            error_message="No reference baseline stored",
        )

    comp = compare_test(
        test,
        result,
        reference if reference is not None else {},
        default_tolerance=default_tolerance,
        default_points=default_points,
        store=store,
        dataset_kind=dataset_kind,
    )
    if reference is None:
        # Record that no baseline was stored even though the
        # simulate_only / baseline-free path produced a result.
        # Downstream renderers use this together with the metric
        # tree's pass/fail to decide how to render the badge.
        comp.has_reference = False
    return comp


def compare_all(
    tests: list[TestModel],
    results: dict[str, TestResult],
    store: ReferenceStore,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
) -> list[TestComparison]:
    """Compare all test results against stored references.

    ``dataset_kind`` is what the backend that produced *results* declares it
    emits (see :func:`dstf.backends.dataset_kind_for`); it is forwarded to every
    :func:`compare_test`.
    """
    import sys as _sys

    print(f"Comparing {len(tests)} tests against references...", file=_sys.stderr)
    return [
        compare_one(
            test,
            results.get(test.model_id),
            store,
            default_tolerance=default_tolerance,
            default_points=default_points,
            dataset_kind=dataset_kind,
        )
        for test in tests
    ]
