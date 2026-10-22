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
"""

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from ..backends import TestResult
from ..backends.common.result_variables import tracked_results, tracks
from ..backends.common.statistics import diagnostic_final
from ..backends.types import DatasetType, VariableResult
from ..discovery.test_registry import TestModel
from ..discovery.unit_tests import unit_test_variables
from ..storage.baseline import (
    PRIMARY_BASELINE,
    Baseline,
    ReferenceFile,
    StoredVariable,
)
from ..storage.reference_store import ReferenceStore
from .registry import metric as registry_metric
from .sides import base_tolerance_refused, is_base_tolerance, parse_sides
from .types import (
    DEFAULT_TOLERANCE,
    StructuralWarning,
    TestComparison,
    VariableComparison,
)

logger = logging.getLogger(__name__)

#: How a refusal says WHERE the base tolerance it read is written, by the
#: source the resolution names.
_BASE_TOLERANCE_WHERE = {
    "test": "test_spec.json comparison.tolerance",
    "reference": "the baseline's comparison.tolerance",
    "project": "the project's tolerance",
}


def _base_tolerance(
    test: TestModel, reference: ReferenceFile | None, default_tolerance: object
) -> tuple[Any, str]:
    """The test's base tolerance and its source: per-test > reference >
    project. An ``is not None`` chain, NOT ``or`` — an explicit tolerance of
    0 (strictest possible) must be honored (review 2026-07-06, finding 7)."""
    ref_comparison = reference.comparison if reference is not None else {}
    if test.comparison_tolerance is not None:
        return test.comparison_tolerance, "test"
    if ref_comparison.get("tolerance") is not None:
        return ref_comparison["tolerance"], "reference"
    return default_tolerance, "project"


def refused_base_tolerance(
    test: TestModel, reference: ReferenceFile | None, default_tolerance: object
) -> TestComparison | None:
    """The refusal of a test whose base tolerance — the one in effect — is
    not ONE finite number, or ``None``. nrmse, the default metric, has one
    ceiling, and every leaf without a tolerance of its own would inherit the
    pair; whatever else a spec or a reference JSON holds there (a string, a
    list, a bool, NaN) goes with it: never scored at the next tolerance down.

    THE one decision, and the first thing a comparison of a test that ran
    makes (``compare_one``; ``compare_test`` for a direct caller). Decided
    later, a test with no baseline read NO_REF — "accept it" — and one whose
    every criterion scores without a baseline passed over it."""
    value, source = _base_tolerance(test, reference, default_tolerance)
    if is_base_tolerance(value):
        return None
    return TestComparison(
        model_id=test.model_id,
        passed=False,
        test_id=reference.test_id if reference is not None else None,
        has_reference=reference is not None,
        error_message=base_tolerance_refused(_BASE_TOLERANCE_WHERE[source], value),
    )


#: How many of a test's tracked names — or of its override keys —
#: :func:`nothing_scored` spells out.
_NAMED_TRACKED = 4


def _named_few(names: list[str]) -> str:
    """Up to `_NAMED_TRACKED` of ``names``, then ``and N more``."""
    named = ", ".join(names[:_NAMED_TRACKED])
    if len(names) > _NAMED_TRACKED:
        named += f" and {len(names) - _NAMED_TRACKED} more"
    return named


def nothing_scored(test: TestModel, result: TestResult) -> str | None:
    """Why a test with no tree scores NOTHING of *result*, or ``None``: the
    result holds none of the variables the test tracks — its ``variables``
    patterns match no name in it, its UnitTests variables are not there (a
    model replaced by an empty class of the same name still simulates). The
    flat path scores one criterion per tracked variable the result holds,
    and the AND of none of them is true: such a test read PASS, with no
    criterion and no word.

    THE one decision: the comparison fails the test with this sentence
    (``reporting.status.refusal`` puts it where a failing criterion would
    be named), and an accept skips it with the same one — the baseline it
    would write holds nothing the test asks about. A test with a tree is
    not asked: each of its leaves fails by itself when its variable is not
    in the result. A simulate-only test is not asked either — one a
    recognizer marked, or one that declares nothing to score
    (``TestModel.is_simulate_only``): that it ran is its criterion.

    A test that tracks NOTHING and holds a ``variable_overrides`` entry is
    asked, and has a sentence of its own: there is no tracked variable for
    the result to hold, and what is wrong is the override, which names a
    variable the test does not track. It is the scored path's log line for
    such a key (``_warn_about_overrides_that_name_nothing``) where no other
    variable is scored beside it."""
    if test.is_simulate_only or test.metric_tree_spec is not None:
        return None
    if not result.success or tracked_results(test, result):
        return None
    tracked = [v.name for v in unit_test_variables(test)] + list(test.variable_patterns)
    # A reason, as every refusal is: a page leads it with "Not scored: ".
    if not tracked:
        return (
            "variable_overrides key(s) name no variable this test tracks "
            f"({_named_few(list(test.variable_overrides))}) — an override changes "
            'how a tracked variable is scored, and "variables" (or a criterion '
            'of "metrics") is what tracks one; nothing was scored'
        )
    return (
        "the result holds none of the variables this test tracks "
        f"({_named_few(tracked)}) — nothing was scored"
    )


def broken_roles(test: TestModel) -> str | None:
    """Why the test's ``metrics`` tree is wrong in its FILE, as the
    validator's sentences (:func:`.validator.validate_tree` with no lookup)
    each led by its place in the tree, or ``None``: no criterion against the primary outside every ``warn``; a
    criterion outside a ``warn`` that names anything but the primary; a
    ``weighted`` that sums scores of opposite directions. A test with no
    tree breaks none — its criteria are the primary's, outside any ``warn``.

    THE one decision where a test is scored. It reads the file and never
    the folder: a soft check is imported into one ``<Backend>/<os>`` folder
    and the same spec is scored in every one, so a criterion under ``warn``
    naming a baseline this folder does not hold is an advisory criterion
    not scored (``tree_eval``), not a broken tree — "a soft check can warn,
    never fail". ``spec update`` and the tool, which write the file and
    hold the store, refuse a name the folder does not hold as well. A tree
    wrong in its file fails the test with these sentences and scores
    nothing (``reporting.status.refusal``), as a base tolerance that is no
    number does."""
    if test.metric_tree_spec is None:
        return None
    from .validator import validate_tree

    errors = validate_tree(test.metric_tree_spec)
    # Every rule the tree breaks, each led by where it is in the tree, as
    # `spec update` says it (`metrics.children[1]: This criterion ...`): a
    # tree of many criteria names the one to fix. Each message is a
    # sentence that ends in its own full stop.
    return " ".join(str(e) for e in errors) or None


def stored_override_problem(
    reference: ReferenceFile, store: ReferenceStore, model_id: str
) -> str | None:
    """Why a baseline's stored ``comparison.variable_overrides`` cannot be
    read, as a sentence naming the reference file, or ``None``: an override
    that is no object, or a key its mode does not read — the check a spec's
    override goes through (``strict_keys.unread_override_key``).

    A baseline keeps the comparison settings it was accepted with, and they
    are merged under the spec's at compare. A stored ``{"mode": "range",
    "min_value": -1, "max_val": 0.5}`` scored as a range with no upper
    bound, and passed with the value at 10 — the spec file's own hole, one
    file over. Every stored override is checked, read by this comparison or
    not (a ``metrics`` tree reads none, the spec's own replaces one): the
    file holds it either way, and an accept keeps it.

    THE one decision, with two readers. A command that compares many tests
    stops on it before it does anything else (:func:`refuse_stored_overrides`:
    ``run`` before it simulates, ``compare`` before it scores). A comparison
    of ONE test refuses that test with it (:func:`compare_one`): the tool
    opens a project test by test, and a file to repair under one of them is
    no reason to score none."""
    from .sides import _as_json
    from .strict_keys import unread_override_key

    overrides = reference.comparison.get("variable_overrides")
    if overrides is None:
        return None

    def said(place: str, problem: str) -> str:
        # The file is asked for only here: a refusal names it.
        ref_file = store.reference_file(model_id)
        return f"{ref_file}: comparison.variable_overrides{place}: {problem}"

    if not isinstance(overrides, dict):
        return said("", f"must be an object, got {_as_json(overrides)}")
    for variable, override in overrides.items():
        if not isinstance(override, dict):
            return said(f"[{variable}]", f"must be an object, got {_as_json(override)}")
        found = unread_override_key(override)
        if found is not None:
            mode, problem = found
            return said(f"[{variable}]", f"{mode} · {variable}: {problem}")
    return None


def refuse_stored_overrides(tests: Iterable[TestModel], store: ReferenceStore) -> None:
    """``ConfigError`` for the first of *tests* whose stored baseline holds
    an override nothing reads (:func:`stored_override_problem`). What a
    command calls with the tests it is about to compare, before it simulates
    or scores any of them: the reference files are there at discovery, and a
    run that found one at its compare had simulated everything for no
    verdict."""
    from ..config import ConfigError

    for test in tests:
        reference = store.get_reference(test.model_id)
        if reference is None:
            continue
        problem = stored_override_problem(reference, store, test.model_id)
        if problem is not None:
            raise ConfigError(problem)


def _check_structural_changes(
    reference: ReferenceFile,
    result: TestResult,
) -> list[StructuralWarning]:
    """Compare structural statistics between reference and current run."""
    warnings = []

    ref_stats = reference.baseline.statistics
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

    # The event count is the EventCounter diagnostic's final on both sides
    # (its summary in the baseline), compared as the statistics are: as
    # text. Over the 686 stored Dymola baselines it equals the copy the
    # statistics held before 1.2 in every one.
    ref_events = diagnostic_final(reference.baseline.diagnostics, "EventCounter")
    cur_events = diagnostic_final(result.diagnostics or [], "EventCounter")
    if (
        ref_events is not None
        and cur_events is not None
        and str(ref_events) != str(cur_events)
    ):
        warnings.append(
            StructuralWarning(
                field="Event count",
                reference_value=str(ref_events),
                current_value=str(cur_events),
            )
        )
    return warnings


def _first_by_name(variables: list[StoredVariable]) -> dict[str, StoredVariable]:
    """A baseline's variables by name, the FIRST of a name stored twice:
    the one the scoring pairs with and the expression check reads."""
    out: dict[str, StoredVariable] = {}
    for v in variables:
        if v.name and v.name not in out:
            out[v.name] = v
    return out


def migration_hint(
    baseline_entries: Mapping[str, StoredVariable], name: str, expression: str
) -> str:
    """The clause that says which name a baseline entry must be stored
    under, or ``""``.

    A variable whose baseline entry is missing is un-migrated exactly when
    the baseline holds an UN-MIGRATED entry under the text the variable's
    EXPRESSION spells — the name every UnitTests baseline was stored under
    before the identity change. Both scoring paths say it, from here, with
    ``name``: the variable's result-file name, which is what that entry
    has to be stored under. A hint on every missing baseline would tell a
    whole suite of brand-new variables to rename an entry no baseline
    holds (the Task 3 review).

    Two things the raw texts do not decide, so this reads the ENTRIES:

    * The stored name is compared NORMALIZED
      (:func:`~dstf.discovery.mo_parser.normalize_expression`). A baseline
      written from a ``.mo`` source that wrapped the line holds
      ``heatTransfer.alphas[\\n        1, 1]``, and the expression beside
      the result variable is normalized; compared raw, the most common
      un-migrated shape in a real library got no hint at all.
    * An entry that already carries ``"expression"`` (an ``expression``
      that is not ``None``) was written after the identity change and is
      not un-migrated. A migrated baseline may hold
      a spec-tracked ``pipe.T[1]`` beside a NEW ``unitTests.x[1]`` whose
      expression happens to be ``pipe.T[1]``; a hint there would name a
      rename that must not happen.
    """
    from ..discovery.mo_parser import normalize_expression

    if not expression:
        return ""
    if not any(
        normalize_expression(stored) == expression and entry.expression is None
        for stored, entry in baseline_entries.items()
    ):
        return ""
    return (
        f" — the baseline stores it under its expression {expression!r}; "
        f"the stored name must be the result-file name {name!r}"
    )


def _check_expressions(
    variables: list[StoredVariable],
    result: TestResult,
    *,
    against: str = "",
    only: Iterable[str] | None = None,
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

    ``only`` narrows the result variables checked. The primary passes None
    — every variable is compared against it — while a NAMED baseline is
    given the variables some leaf actually scores against it: an
    ``against: fmpy`` soft-check holding the whole result's variables
    otherwise warned about quantities that baseline is never compared with.
    """
    scope = None if only is None else set(only)
    recorded: dict[str, str] = {}
    for entry in variables:
        if entry.name and entry.name not in recorded:
            recorded[entry.name] = entry.expression or ""
    where = f" (against {against})" if against else ""
    return [
        StructuralWarning(
            field=f"Expression of {var.name}{where}",
            reference_value=recorded[var.name],
            current_value=var.expression,
        )
        for var in result.variables
        if var.expression
        and (scope is None or var.name in scope)
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
    check runs once per (variable, baseline) PAIR the tree scores, not once
    per leaf naming it: two leaves on one variable say it once, and a
    variable no leaf scores against this baseline is not checked against it
    at all — nothing compares those two numbers, so a difference between
    them is not a finding about this run.
    """
    from .tree_spec import collect_variables_by_baseline

    out: list[StructuralWarning] = []
    for name, variables in collect_variables_by_baseline(spec).items():
        if name == PRIMARY_BASELINE:
            continue
        baseline = baselines.get(name)
        if baseline is not None:
            out += _check_expressions(
                baseline.variables, result, against=name, only=variables
            )
    return out


def _named_companions(
    test: TestModel, store: ReferenceStore | None, held: set[str]
) -> frozenset[str]:
    """The names ``test``'s tree scores against that this folder stores no
    baseline of (``held``) and does store as a companion: a leaf naming one
    says it is a companion here, not that no soft check of its name is
    stored. The store is asked only when a leaf names a missing baseline."""
    from .tree_spec import collect_variables_by_baseline

    assert test.metric_tree_spec is not None
    missing = set(collect_variables_by_baseline(test.metric_tree_spec)) - held
    if store is None or not missing:
        return frozenset()
    return frozenset(store.get_companions(test.model_id)) & missing


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
    ``unitTests.x[1]``): the message says which name it meant, and that the
    key must be that name. One line per test; no status changes.

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
        " — a key that names an expression must be the result-file name of "
        "the variable it means, named beside it"
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
    # What the test TRACKS, not whatever the result holds: a variable a
    # discarded criterion had re-read is no tracked variable of it.
    unscored = [
        v.name
        for v in tracked_results(test, result)
        if v.name and v.name not in covered
    ]
    if not unscored:
        return None
    return StructuralWarning(
        field="Tracked but not scored by any criterion",
        statement=", ".join(unscored),
    )


def compare_test(
    test: TestModel,
    result: TestResult,
    reference: ReferenceFile,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    store=None,  # Optional[ReferenceStore] — if provided, soft_checks from the new subdir layout are merged in
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
) -> TestComparison:
    """Compare a test's simulation results against its reference.

    Args:
        test: Test specification with model ID, variable overrides, etc.
        result: Simulation output (variables with time series).
        reference: The stored reference file.
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
    from .modes import override_metric, resolve_mode
    from .tree_eval import collect_leaf_variables, evaluate_spec

    ref_test_id = reference.test_id

    if not result.success:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=ref_test_id,
            sim_success=False,
            error_message=result.error_message or "Simulation failed",
        )

    refused = refused_base_tolerance(test, reference, default_tolerance)
    if refused is not None:
        return refused

    # PTA.5 — simulate_only short-circuit: when the recognizer marked this
    # test as "just check that it simulates", or it declares nothing to
    # score, skip per-variable comparison and the whole baseline machinery.
    # The sim already succeeded above.
    if test.is_simulate_only:
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

    nothing = nothing_scored(test, result)
    if nothing is not None:
        # Not a PASS: no criterion was scored, so there is no verdict about
        # the model at all. FAIL, the status a tree's leaf gives a variable
        # the result does not hold, with the reason where the failing
        # criterion would be named.
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=ref_test_id,
            error_message=nothing,
        )

    structural_warnings = _check_structural_changes(
        reference, result
    ) + _check_expressions(reference.baseline.variables, result)

    # Pairing is by name. Pairing by numeric index silently scored variables
    # against the wrong baseline trajectory after any spec reorder (review
    # 2026-07-06, finding 8); a first-entry-wins map is also what the
    # expression check reads.
    ref_vars_by_name = _first_by_name(reference.baseline.variables)
    shared_ref_time = np.array(reference.baseline.time)
    comparisons = []

    ref_comparison = reference.comparison
    base_tolerance, tolerance_source = _base_tolerance(
        test, reference, default_tolerance
    )

    # Phase 3.3: when the spec provides an explicit MetricTree, it fully
    # replaces the implicit flat-AND + per-variable overrides. The user's
    # tree declares *which* variables participate and *how* each is scored,
    # so the legacy ``comparison.variable_overrides`` is ignored on this
    # path (documented — same fields move into each leaf's params).
    if test.metric_tree_spec is not None:
        from .tree_eval import BaselineView, ScalarSources

        var_results_by_name = {v.name: v for v in result.variables if v.name}
        # Load primary from the flat ref file; merge in soft_checks from the
        # `soft_checks/ref_NNNN/` subdir when a store is supplied (D66). Leaves
        # pick which baseline to score against via `leaf.against` (defaults
        # to primary). With no store there are no named baselines, so an
        # `against: <name>` leaf gets the missing-baseline sentinel.
        all_baselines: dict[str, Baseline] = {PRIMARY_BASELINE: reference.baseline}
        if store is not None:
            all_baselines.update(store.get_soft_checks(test.model_id))
        baselines: dict[str, BaselineView] = {}
        for name, bl in all_baselines.items():
            baselines[name] = BaselineView(
                name=name,
                ref_vars_by_name=_first_by_name(bl.variables),
                shared_ref_time=np.array(bl.time),
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
            # The run's stored numbers: its statistics, for a `scalar` leaf
            # reading a statistics path; its listed names, for a number
            # read from a summary where no curve was extracted.
            ScalarSources(
                statistics=result.statistics or {},
                diagnostics=result.diagnostics,
            ),
            model_id=test.model_id,
            held_in=store.folder if store is not None else "",
            companions=_named_companions(test, store, set(baselines)),
        )
        unscored = _tracked_but_not_scored(test, result)
        return TestComparison(
            model_id=test.model_id,
            passed=tree.passed,
            test_id=ref_test_id,
            variables=collect_leaf_variables(tree),
            warnings=structural_warnings + ([unscored] if unscored else []),
            metric_tree=tree,
            tolerance_source=tolerance_source,
            base_tolerance=base_tolerance,
        )

    # Merge variable overrides: spec overrides take precedence over reference
    ref_var_overrides = ref_comparison.get("variable_overrides", {})
    merged_overrides = {**ref_var_overrides, **test.variable_overrides}
    _warn_about_overrides_that_name_nothing(
        test.model_id, test.variable_overrides, ref_var_overrides, result
    )

    tracked = tracked_results(test, result)
    #: Per tracked variable, the baseline its criterion reads (None: none).
    baselines_read: list[str | None] = []
    for var_result in tracked:
        # A variable is paired with the baseline entry carrying its name.
        # There is no index fallback: a result variable always has the name
        # its result file gives it (a nameless one is a backend bug), and an
        # entry that merely sits at the same index is a different quantity
        # after any reorder. A baseline that has none says so once per
        # variable, with the name to store it under when it is the
        # un-migrated shape rather than a variable the baseline never held.
        ref_var = ref_vars_by_name.get(var_result.name)
        if ref_var is None and reference.baseline.variables:
            logger.warning(
                "%s: the baseline holds no variable named %r — treating as "
                "missing baseline (re-accept to refresh)%s",
                test.model_id,
                var_result.name,
                migration_hint(
                    ref_vars_by_name, var_result.name, var_result.expression
                ),
            )

        # A variable is scored and overridden under the name its result file
        # gives it; the expression beside it is a label and no key of any
        # kind. Resolved BEFORE the ref-missing check so baseline-free modes
        # still find their overrides.
        name = var_result.name

        var_override = merged_overrides.get(name, {})
        # The metric this criterion is scored by — or, where its config is
        # an error, could not be: what a sentinel below is named by.
        metric_name = override_metric(var_override, default_points)
        # The baseline it reads: none until its mode is built, and none for
        # a mode that scores from its own config.
        baselines_read.append(None)
        try:
            tolerance = parse_sides(
                var_override.get("tolerance", base_tolerance), "tolerance"
            )
            mode = resolve_mode(var_override, tolerance, default_points=default_points)
        except ValueError as exc:
            comparisons.append(_config_error_comparison(var_result, exc, metric_name))
            continue
        if not mode.is_baseline_free():
            baselines_read[-1] = PRIMARY_BASELINE

        # A stats-path quantity is resolved by the tree evaluator from the
        # run's statistics; this flat path only has the variable's own
        # trajectory, and ScalarMode would silently reduce THAT instead
        # (review finding, D106).
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
                    mode=mode.name,
                    # As on the no-baseline comparison below: the one it
                    # would have applied, not the default 0.0.
                    tolerance_used=tolerance,
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
                try:
                    vc = mode.compare(empty, empty, var_result.time, var_result.values)
                except ValueError as exc:
                    comparisons.append(
                        _config_error_comparison(var_result, exc, metric_name)
                    )
                    continue
                vc.index = var_result.index
                vc.name = name
                vc.tolerance_used = tolerance
                comparisons.append(vc)
                continue
            # Not scored, by its own metric, and saying why — as the tree
            # path's leaf on a variable its baseline lacks does.
            from .tree_eval import _missing_variable_comparison

            vc = _missing_variable_comparison(
                name,
                var_result,
                mode=mode.name,
                hint=migration_hint(
                    ref_vars_by_name, var_result.name, var_result.expression
                ),
            )
            vc.tolerance_used = tolerance
            comparisons.append(vc)
            continue

        ref_time = shared_ref_time
        ref_values = np.array(ref_var.values)

        try:
            vc = mode.compare(ref_time, ref_values, var_result.time, var_result.values)
        except ValueError as exc:
            comparisons.append(_config_error_comparison(var_result, exc, metric_name))
            # A criterion whose config is an error read nothing, whenever
            # the error was found.
            baselines_read[-1] = None
            continue
        vc.index = var_result.index
        vc.name = name
        vc.tolerance_used = tolerance
        comparisons.append(vc)

    # Phase 3.1: pass/fail flows from the MetricTree root, not a separate
    # accumulator. Today the tree is the implicit flat-AND of per-variable
    # leaves — same semantics as before. Phase 3.3+ swaps in user-authored
    # trees from test_spec.json.
    #
    # The loop appends exactly one comparison per tracked variable, in
    # order, so the expression — and the baseline the criterion read —
    # rides onto each of them here rather than at all five of the loop's
    # append sites.
    for vc, var_result, read in zip(comparisons, tracked, baselines_read, strict=True):
        vc.expression = var_result.expression
        vc.against = read
    comparisons += _lost_variable_comparisons(
        test,
        result,
        ref_vars_by_name,
        merged_overrides,
        base_tolerance,
        default_points,
    )
    tree = implicit_and_tree(comparisons)

    return TestComparison(
        model_id=test.model_id,
        passed=tree.passed,
        test_id=ref_test_id,
        variables=comparisons,
        warnings=structural_warnings,
        metric_tree=tree,
        tolerance_source=tolerance_source,
        base_tolerance=base_tolerance,
    )


def _lost_variable_comparisons(
    test: TestModel,
    result: TestResult,
    ref_vars_by_name: Mapping[str, StoredVariable],
    overrides: Mapping[str, dict],
    base_tolerance: float,
    default_points: bool,
) -> list[VariableComparison]:
    """The flat path's criteria that could not be scored because the RUN
    lost their variable: one per variable the test tracks
    (``result_variables.tracks``) that the baseline holds and the result
    does not, in the baseline's order — the sentinel a tree's leaf gives it
    (``variable 'y' not found in the result``), and the test fails.

    The flat path scores one criterion per tracked variable the result
    holds, so a run that lost ``y`` of {x, y} was scored on ``x`` alone:
    PASS, Criteria 1/1 and no word, where the same loss under a ``metrics``
    tree fails — and an accept then wrote a baseline without ``y``.

    Only what the BASELINE holds: it is what says the variable was there.
    A pattern that matches nothing on either side stays the backend's log
    line, and a baseline variable the test no longer tracks is not asked
    about. The sentinel carries what its criterion would have: the
    override's metric and tolerance, the baseline's expression (the run
    has none to give) and the baseline it would have read."""
    from .modes import override_metric, resolve_mode
    from .tree_eval import _missing_variable_comparison

    held = {v.name for v in result.variables}
    tracked = tracks(test)
    lost: list[VariableComparison] = []
    for name, entry in ref_vars_by_name.items():
        if name in held or not tracked(name):
            continue
        override = overrides.get(name, {})
        metric_name = override_metric(override, default_points)
        vc = _missing_variable_comparison(name, None, mode=metric_name)
        vc.expression = entry.expression or ""
        try:
            tolerance = parse_sides(
                override.get("tolerance", base_tolerance), "tolerance"
            )
            mode = resolve_mode(override, tolerance, default_points=default_points)
        except ValueError:
            # Its config is an error too; the loss is what is said.
            vc.mode = metric_name if registry_metric(metric_name) is not None else ""
        else:
            vc.mode = mode.name
            vc.tolerance_used = tolerance
            vc.against = None if mode.is_baseline_free() else PRIMARY_BASELINE
        lost.append(vc)
    return lost


def _config_error_comparison(
    var_result: VariableResult, exc: ValueError, metric_name: str
) -> VariableComparison:
    """The flat path's sentinel for a variable whose override is a config
    error: the variable fails, the others are scored — as a leaf's does on
    the tree path (D107), where one bad spec entry must not abort the run.

    A ``ValueError`` is that error wherever it is raised: building the mode
    (a tolerance that is neither a number nor a lower/upper pair; an
    unrecognized mode string, which must fail loudly — silently falling
    back to NRMSE changed the scoring algorithm on a typo, review
    2026-07-06) or scoring with it, for what a mode can only detect then (a
    tube's unknown width mode, `abs` without control points).

    ``mode`` is ``metric_name`` — the metric the override is scored by
    (``modes.override_metric``) — as the tree path's sentinel carries its
    leaf's: a ``{"mode": "range"}`` that could not be built is a `range`
    criterion on every surface, and was `x: nrmse` on the console beside
    ``"metric": "range"`` in verdicts.json. A name nothing is registered
    under is no metric: that one sentinel has ``mode=""`` (never the
    "nrmse" field default, which it was not scored by — and never the
    offending string, which would go to every lookup by mode); what was
    written rides in the error text."""
    return VariableComparison(
        index=var_result.index,
        name=var_result.name,
        passed=False,
        nrmse=float("inf"),
        mode=metric_name if registry_metric(metric_name) is not None else "",
        diagnostics={"error": str(exc), "config_error": True},
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
            try:
                tol = parse_sides(
                    leaf.params.get("tolerance", default_tolerance), "tolerance"
                )
                mode = resolve_mode(override, tol, default_points=False)
            except Exception:
                # A leaf whose config doesn't resolve to a valid mode — a
                # malformed tolerance included — can't be baseline-free by
                # our check — fall back to NO_REF.
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
        try:
            tol = parse_sides(override.get("tolerance", base_tolerance), "tolerance")
            mode = resolve_mode(override, tol, default_points=False)
        except Exception:
            return False
        if not mode.is_baseline_free():
            return False
    return True


def _no_reference(model_id: str) -> ReferenceFile:
    """The reference a test with none is compared against: no fields, an
    empty primary baseline — a simulate-only or baseline-free test reads
    nothing from it."""
    return ReferenceFile(
        model_id=model_id,
        test_id=None,
        status=None,
        date_added=None,
        last_updated=None,
        sampling=None,
        comparison={},
        baseline=Baseline(name=PRIMARY_BASELINE, time=[], variables=[]),
    )


def compare_one(
    test: TestModel,
    result: TestResult | None,
    store: ReferenceStore,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
) -> TestComparison:
    """One test's comparison, short-circuits included: no result is NOT RUN
    (``ran=False``, D145) and a failed simulation SIM_FAIL; no baseline is
    NO_REF unless the test is simulate-only or every leaf scores without a
    reference. ``compare_all`` is this per test; the authoring Session's
    rescore is this for one."""
    if result is None:
        # No result exists: the reader hands back none for a test with no
        # manifest entry or one its last run never finished
        # (`Backend.read_results`). Nothing ran, so nothing failed.
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            sim_success=False,
            ran=False,
            error_message="No simulation results found",
        )

    # A simulation that FAILED is SIM_FAIL regardless of whether a baseline
    # exists — this check MUST precede the no-baseline short-circuit below.
    # A backend signals a failed run with a success=False TestResult: the
    # reader builds one from a failed run result, and OpenModelica's
    # read_result returns one for a missing .mat. Before this check it
    # fell through to the no-baseline branch and was
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
    if reference is not None:
        # A stored override nothing reads: this test is refused, with the
        # sentence naming its reference file. A command has stopped on it
        # before it got here (`refuse_stored_overrides`).
        unread = stored_override_problem(reference, store, test.model_id)
        if unread is not None:
            return TestComparison(
                model_id=test.model_id,
                passed=False,
                test_id=reference.test_id,
                has_reference=True,
                error_message=unread,
            )
    # The base tolerance is judged HERE, before the test is anything else: a
    # test with no baseline is not NO_REF over a base no accept may store.
    refused = refused_base_tolerance(test, reference, default_tolerance)
    if refused is not None:
        return refused
    # Nor is it NO_REF — "accept it" — when its result holds nothing it
    # tracks: no accept can give such a test a criterion.
    nothing = nothing_scored(test, result)
    if nothing is not None:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=reference.test_id if reference is not None else None,
            has_reference=reference is not None,
            error_message=nothing,
        )
    # Nor when its tree is wrong in its file: no accept repairs that.
    broken = broken_roles(test)
    if broken is not None:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=reference.test_id if reference is not None else None,
            has_reference=reference is not None,
            error_message=broken,
        )
    # simulate_only tests don't need a baseline — their pass criterion
    # is "did it simulate successfully?". Dispatch to compare_test with
    # an empty reference so its simulate_only short-circuit runs
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
        and not test.is_simulate_only
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
        reference if reference is not None else _no_reference(test.model_id),
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
