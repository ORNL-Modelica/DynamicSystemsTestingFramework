"""Shared result-extraction helpers for DSresult-MAT backends.

Dymola and OpenModelica write the same DSresult MAT layout and follow the
same ``unitTests.x[i]`` convention, so variable selection, extraction and the
diagnostics/timing fold into ``statistics`` are one implementation here rather
than a copy per backend. Backends keep their own MAT reading and their own
timing computation — only what is genuinely identical lives here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ...discovery.test_registry import TestModel
from ..types import VariableResult, resolve_variable_patterns
from .mat_reader import list_result_mat_variables

logger = logging.getLogger(__name__)


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

    # UnitTests component variables
    if test.source in ("unit_tests", "both") and test.n_vars > 0:
        for i in range(1, test.n_vars + 1):
            needed.add(f"unitTests.x[{i}]")

    # Pattern-based variables — need the full name list to resolve globs
    if test.variable_patterns:
        all_names = list_result_mat_variables(mat_path)
        if all_names is None:
            return None  # fallback: load everything
        resolved = resolve_variable_patterns(test.variable_patterns, all_names)
        needed.update(resolved)

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

    # 1. UnitTests variables (source = "unit_tests" or "both")
    if test.source in ("unit_tests", "both") and test.n_vars > 0:
        for i in range(1, test.n_vars + 1):
            var_name = f"unitTests.x[{i}]"
            if var_name in mat_data:
                time_arr, values = mat_data[var_name]
                # Use parsed expressions only when they map 1:1 to variables.
                # Complex expressions like cat() produce fewer names than
                # variables, so fall back to x[i] for all to avoid misleading
                # labels. When a single bare variable maps to multiple vars
                # (e.g. x=y where y is an array), use varname[i] labels.
                if len(test.x_expressions) == test.n_vars:
                    label = test.x_expressions[i - 1]
                elif len(test.x_expressions) == 1 and test.n_vars > 1:
                    label = f"{test.x_expressions[0]}[{i}]"
                else:
                    label = f"x[{i}]"
                results.append(
                    VariableResult(index=idx, time=time_arr, values=values, name=label)
                )
                seen.add(var_name)
                seen.add(label)
                idx += 1
            else:
                # Loud on every backend: a requested variable absent from the
                # output is a spec/model mismatch, not something to swallow.
                logger.warning(
                    "Variable %s not found in simulation output for %s",
                    var_name,
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

    # 3. Diagnostic variables (auto-captured, not compared — D54–D55)
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


def fold_diagnostics_into_statistics(
    stats: dict | None,
    diagnostics: list[VariableResult],
) -> dict | None:
    """Add each diagnostic's final value to ``stats``, returning the dict.

    Creates the dict if there is anything to fold and ``stats`` is None.
    float32 trajectories are rounded to 7 significant digits (their real
    precision) so the on-disk reference JSON does not carry binary noise.
    """
    if not diagnostics:
        return stats
    if stats is None:
        stats = {}
    for diag in diagnostics:
        if len(diag.values) > 0:
            sig = 7 if diag.values.dtype == np.float32 else 15
            stats[diag.name] = float(f"%.{sig}g" % diag.values[-1])
    return stats


def merge_timing_stats(stats: dict | None, timing: dict[str, float]) -> dict:
    """Overlay wall-clock summary keys onto ``stats["timing"]``.

    Non-destructive: whatever raw per-phase seconds a backend's log parser
    already put under ``timing`` survive.
    """
    if stats is None:
        stats = {}
    stats.setdefault("timing", {}).update(timing)
    return stats


__all__ = [
    "compute_needed_variables",
    "extract_variables",
    "fold_diagnostics_into_statistics",
    "merge_timing_stats",
]
