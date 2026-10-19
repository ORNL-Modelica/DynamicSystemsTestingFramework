"""Shared result-extraction helpers for DSresult-MAT backends.

Dymola and OpenModelica write the same DSresult MAT layout and follow the
same ``<instance>.x[i]`` convention, so variable selection, extraction (the
listed names among them) and the timing block of ``statistics`` are one
implementation here rather than a copy per backend. Backends keep their own
MAT reading and their own timing computation — only what is genuinely
identical lives here.

Also here: :func:`tracked_results`, which of a read result's variables a test
TRACKS — the rule the comparator and the authoring Session both ask.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from ...discovery.test_registry import TestModel
from ...discovery.unit_tests import unit_test_variables
from ..types import (
    TestResult,
    TestRunResult,
    VariableResult,
    _pattern_to_regex,
    is_glob_pattern,
    resolve_variable_patterns,
)
from .mat_reader import list_result_mat_variables, mat_shortfall

logger = logging.getLogger(__name__)


def criterion_variables(test: TestModel, diagnostic_vars: Iterable[str]) -> list[str]:
    """The variables ``test``'s criteria name (`tree_variables`): what every
    backend records and extracts AFTER the UnitTests variables and the
    ``variables`` patterns, so a criterion added later renumbers neither
    (a stored baseline records each variable's index). Imported here, not at module level: importing
    ``dstf.comparison`` runs its package init, which imports the comparator,
    which imports ``dstf.backends`` — a cycle from any backend module."""
    from ...comparison.tree_spec import tree_variables

    return tree_variables(test.metric_tree_spec, diagnostic_vars)


def tracked_results(test: TestModel, result: TestResult) -> list[VariableResult]:
    """The variables of ``result`` that ``test`` TRACKS: its UnitTests
    variables and its ``variables`` patterns matched against the result's
    names, ``"*"`` every one. What the flat path (no tree) scores, what a
    tree warns of when no leaf covers it, and the Variables drawer's
    Tracked group.

    Never simply every variable the result holds. A tree names variables
    too, and a backend records and extracts them beside the tracked ones:
    a result read while a criterion was staged (a rerun runs with the
    pending edits) keeps that variable after the criterion is discarded.
    Scored, with no baseline, it failed the test for a variable the test
    never asked about; under a saved tree it was named a tracked variable
    without a criterion. On a result extracted for the test as it stands
    with no tree this is every variable: extraction is exactly these two
    groups.

    Matched quietly: the backend that extracted the result already warned
    about a pattern that matched nothing, and this runs on every rescore. A
    variable with NO name is a backend bug: no name or pattern tracks it,
    and leaving it out is said, not done quietly."""
    if "*" in test.variable_patterns:
        return list(result.variables)
    for v in result.variables:
        if not v.name:
            logger.warning(
                "%s: result variable %d has no name and is not tracked",
                test.model_id,
                v.index,
            )
    tracked = tracks(test)
    return [v for v in result.variables if tracked(v.name)]


def tracks(test: TestModel) -> Callable[[str], bool]:
    """Whether ``test`` TRACKS a variable of that name: one of its
    UnitTests variables, a ``variables`` pattern that names it or a glob
    that matches it (``"*"`` every name). THE one rule, asked of a result's
    variables (:func:`tracked_results`) and of a baseline's (the
    comparator: a tracked variable the baseline holds and the run does
    not)."""
    wanted = {v.name for v in unit_test_variables(test)}
    globs = []
    for pattern in test.variable_patterns:
        if is_glob_pattern(pattern):
            globs.append(_pattern_to_regex(pattern))
        else:
            wanted.add(pattern)
    return lambda name: name in wanted or any(g.fullmatch(name) for g in globs)


def compute_needed_variables(
    mat_path: Path,
    test: TestModel,
    diagnostic_vars: list[str],
) -> set[str] | None:
    """Determine which variables need to be extracted from a .mat file.

    Returns a set of variable names, or None to load everything (fallback when
    the name matrix cannot be read). This avoids loading all data for large
    files with thousands of variables.
    """
    needed: set[str] = set()

    # UnitTests component variables, under their result-file names
    needed.update(v.name for v in unit_test_variables(test))

    # Pattern-based variables — need the full name list to resolve globs
    if test.variable_patterns:
        all_names = list_result_mat_variables(mat_path)
        if all_names is None:
            return None  # fallback: load everything
        resolved = resolve_variable_patterns(test.variable_patterns, all_names)
        needed.update(resolved)

    # Variables a criterion names (literal: no name matrix needed)
    needed.update(criterion_variables(test, diagnostic_vars))

    needed.update(diagnostic_vars)

    return needed if needed else None


def extract_variables(
    mat_data: dict,
    test: TestModel,
    diagnostic_vars: list[str],
) -> tuple[list[VariableResult], list[VariableResult]]:
    """Extract tracked variables and diagnostic variables from parsed mat data.

    Returns (variables, diagnostics).
    """
    results: list[VariableResult] = []
    seen: set[str] = set()
    idx = 1

    # 1. UnitTests variables (source = "unit_tests" or "both"): the name is
    # the result file's, the model's expression rides beside it as a label.
    # ``seen`` therefore holds result-file names only — a pattern resolving
    # to a DIFFERENT variable that an expression happens to spell is that
    # other variable, and is extracted too.
    for ut in unit_test_variables(test):
        if ut.name in mat_data:
            time_arr, values = mat_data[ut.name]
            results.append(
                VariableResult(
                    index=idx,
                    time=time_arr,
                    values=values,
                    name=ut.name,
                    expression=ut.expression,
                )
            )
            seen.add(ut.name)
            idx += 1
        else:
            # Loud on every backend: a requested variable absent from the
            # output is a spec/model mismatch, not something to swallow.
            logger.warning(
                "Variable %s not found in simulation output for %s",
                ut.name,
                test.model_id,
            )

    # 2. Pattern-based variables (source = "spec" or "both")
    if test.variable_patterns:
        available = list(mat_data.keys())
        resolved = resolve_variable_patterns(test.variable_patterns, available)
        for var_name in resolved:
            if var_name in seen:
                continue
            if var_name in mat_data:
                time_arr, values = mat_data[var_name]
                results.append(
                    VariableResult(
                        index=idx, time=time_arr, values=values, name=var_name
                    )
                )
                seen.add(var_name)
                idx += 1

    # 3. Variables a criterion names, APPENDED: a criterion added later
    # renumbers neither of the first two groups (a stored baseline records
    # each variable's index).
    for var_name in criterion_variables(test, diagnostic_vars):
        if var_name in seen:
            continue
        if var_name in mat_data:
            time_arr, values = mat_data[var_name]
            results.append(
                VariableResult(index=idx, time=time_arr, values=values, name=var_name)
            )
            seen.add(var_name)
            idx += 1
        else:
            logger.warning(
                "Variable %s not found in simulation output for %s",
                var_name,
                test.model_id,
            )

    # 4. The listed names (diagnostic_variables), recorded as diagnostics:
    # the baseline stores each one's summary, and a scalar's final, min or
    # max over the whole run is scored from it where the curve is not
    # extracted (D162).
    diagnostics: list[VariableResult] = []
    diag_idx = 1
    for var_name in diagnostic_vars:
        if var_name in mat_data:
            time_arr, values = mat_data[var_name]
            diagnostics.append(
                VariableResult(
                    index=diag_idx,
                    time=time_arr,
                    values=values,
                    name=var_name,
                )
            )
            diag_idx += 1

    return results, diagnostics


def unfinished_result(
    test: TestModel,
    mat_path: Path,
    variables: list[VariableResult],
    diagnostics: list[VariableResult],
    stats: dict | None,
) -> TestResult | None:
    """The FAILED result an unfinished result file reads as — ``None`` for
    a finished one, which the caller goes on to return as a success.

    A file its writer did not finish (a killed or crashed run, a cut copy:
    :func:`mat_shortfall`) is read like any other, so the rows it holds can
    be SHOWN. Being a failure is what keeps them from being scored or
    offered as a baseline: the reader takes its row count from the file's
    size, so without this a partial trajectory would read as a result. A
    file with no time point shows nothing.
    """
    shortfall = mat_shortfall(mat_path)
    if shortfall is None:
        return None
    held = any(len(v.time) for v in variables)
    return TestResult(
        model_id=test.model_id,
        success=False,
        variables=variables if held else [],
        diagnostics=diagnostics if held else [],
        error_message=shortfall,
        statistics=stats,
    )


def merge_timing_stats(stats: dict | None, timing: dict[str, float]) -> dict:
    """Overlay wall-clock summary keys onto ``stats["timing"]``.

    Non-destructive: whatever raw per-phase seconds a backend's log parser
    already put under ``timing`` survive.
    """
    if stats is None:
        stats = {}
    stats.setdefault("timing", {}).update(timing)
    return stats


def run_timing(rr: TestRunResult) -> dict[str, float]:
    """The run's wall-clock phase summary — the ``statistics["timing"]`` keys
    every backend's result carries (``translation_wall``, ``sim_wall``,
    ``other_wall``, ``total_wall``): seconds, rounded to 2, only the keys the
    run measured.

    All are wall-clock seconds measured on the Python side, distinct from a
    tool's own CPU-time statistics. ``{}`` when the run measured neither a
    translation nor a simulation wall (Dymola batch: its ``elapsed`` is a
    batch average, and a block built from it would show a time no test took).
    ``other_wall`` — what the run spent outside translation and simulation —
    only when both walls are known: a missing wall is not a 0.
    """
    if rr.translation_wall is None and rr.sim_wall is None:
        return {}
    timing: dict[str, float] = {}
    if rr.translation_wall is not None:
        timing["translation_wall"] = round(rr.translation_wall, 2)
    if rr.sim_wall is not None:
        timing["sim_wall"] = round(rr.sim_wall, 2)
    if rr.elapsed:
        if rr.translation_wall is not None and rr.sim_wall is not None:
            other = rr.elapsed - rr.translation_wall - rr.sim_wall
            timing["other_wall"] = round(max(0.0, other), 2)
        timing["total_wall"] = round(rr.elapsed, 2)
    return timing


__all__ = [
    "compute_needed_variables",
    "criterion_variables",
    "extract_variables",
    "merge_timing_stats",
    "run_timing",
    "tracked_results",
    "tracks",
]
