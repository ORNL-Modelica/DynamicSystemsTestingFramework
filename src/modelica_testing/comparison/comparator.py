"""Compare simulation results against stored references."""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from ..simulators import TestResult
from ..storage.reference_store import ReferenceStore

logger = logging.getLogger(__name__)

# Machine epsilon guard for relative error (same as AbsRelRMS.mo)
_EPS = 100 * np.finfo(np.float64).eps


@dataclass
class VariableComparison:
    """Comparison result for a single tracked variable."""
    index: int
    expression: str
    passed: bool
    abs_error_rms: float
    rel_error_rms: float
    max_abs_error: float
    reference_final: float
    actual_final: float


@dataclass
class TestComparison:
    """Comparison result for a full test model."""
    model_id: str
    passed: bool
    variables: list[VariableComparison] = field(default_factory=list)
    error_message: Optional[str] = None
    sim_success: bool = True
    has_reference: bool = True


def _compare_trajectories(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    tolerance: float,
) -> tuple[bool, float, float, float]:
    """Compare two time series.

    Interpolates actual values to reference time grid and computes errors.
    Mirrors the AbsRelRMS.mo logic.

    Returns (passed, abs_rms, rel_rms, max_abs).
    """
    # Interpolate actual to reference time grid
    actual_interp = np.interp(ref_time, act_time, act_values)

    n = len(ref_values)
    abs_error = actual_interp - ref_values

    # Apply machine epsilon filter (same as AbsRelRMS.mo)
    abs_error_filtered = np.where(np.abs(abs_error) <= _EPS, 0.0, abs_error)

    # Relative error: if |ref| <= eps, use actual value directly
    rel_error = np.where(
        np.abs(ref_values) <= _EPS,
        actual_interp,
        abs_error / ref_values,
    )
    rel_error_filtered = np.where(np.abs(rel_error) <= _EPS, 0.0, rel_error)

    abs_rms = float(np.sqrt(np.sum(abs_error_filtered ** 2) / n))
    rel_rms = float(np.sqrt(np.sum(rel_error_filtered ** 2) / n))
    max_abs = float(np.max(np.abs(abs_error_filtered)))

    passed = abs_rms < tolerance

    return passed, abs_rms, rel_rms, max_abs


def _compare_final_values(
    ref_final: float,
    act_final: float,
    tolerance: float,
) -> tuple[bool, float, float, float]:
    """Compare only final values.

    Returns (passed, abs_error, rel_error, abs_error).
    """
    abs_err = abs(act_final - ref_final)
    if abs(ref_final) <= _EPS:
        rel_err = abs(act_final)
    else:
        rel_err = abs_err / abs(ref_final)

    passed = abs_err < tolerance
    return passed, abs_err, rel_err, abs_err


def compare_test(
    test: TestModel,
    result: TestResult,
    reference: dict,
    config: Config,
) -> TestComparison:
    """Compare a test's simulation results against its reference."""
    if not result.success:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            sim_success=False,
            error_message=result.error_message or "Simulation failed",
        )

    ref_vars = {v["index"]: v for v in reference.get("variables", [])}
    # Shared time array (new format) or fall back to per-variable (old format)
    shared_ref_time = reference.get("time")
    if shared_ref_time is not None:
        shared_ref_time = np.array(shared_ref_time)
    comparisons = []
    all_passed = True

    for var_result in result.variables:
        ref_var = ref_vars.get(var_result.index)
        if ref_var is None:
            comparisons.append(VariableComparison(
                index=var_result.index,
                expression="",
                passed=False,
                abs_error_rms=float("inf"),
                rel_error_rms=float("inf"),
                max_abs_error=float("inf"),
                reference_final=float("nan"),
                actual_final=float(var_result.values[-1]) if len(var_result.values) > 0 else float("nan"),
            ))
            all_passed = False
            continue

        # Use shared time, fall back to per-variable for backward compat
        if shared_ref_time is not None:
            ref_time = shared_ref_time
        else:
            ref_time = np.array(ref_var["time"])
        ref_values = np.array(ref_var["values"])
        expression = ref_var.get("expression", "")

        if config.final_only:
            ref_final = ref_values[-1] if len(ref_values) > 0 else 0.0
            act_final = float(var_result.values[-1]) if len(var_result.values) > 0 else 0.0
            passed, abs_rms, rel_rms, max_abs = _compare_final_values(
                ref_final, act_final, config.tolerance
            )
        else:
            passed, abs_rms, rel_rms, max_abs = _compare_trajectories(
                ref_time, ref_values,
                var_result.time, var_result.values,
                config.tolerance,
            )
            ref_final = float(ref_values[-1]) if len(ref_values) > 0 else 0.0
            act_final = float(var_result.values[-1]) if len(var_result.values) > 0 else 0.0

        comparisons.append(VariableComparison(
            index=var_result.index,
            expression=expression,
            passed=passed,
            abs_error_rms=abs_rms,
            rel_error_rms=rel_rms,
            max_abs_error=max_abs,
            reference_final=ref_final,
            actual_final=act_final,
        ))

        if not passed:
            all_passed = False

    return TestComparison(
        model_id=test.model_id,
        passed=all_passed,
        variables=comparisons,
    )


def compare_all(
    tests: list[TestModel],
    results: dict[str, TestResult],
    store: ReferenceStore,
    config: Config,
) -> list[TestComparison]:
    """Compare all test results against stored references."""
    comparisons = []

    for test in tests:
        result = results.get(test.model_id)
        if result is None:
            comparisons.append(TestComparison(
                model_id=test.model_id,
                passed=False,
                sim_success=False,
                error_message="No simulation results found",
            ))
            continue

        reference = store.get_reference(test.model_id)
        if reference is None:
            comparisons.append(TestComparison(
                model_id=test.model_id,
                passed=False,
                has_reference=False,
                error_message="No reference results stored",
            ))
            continue

        comp = compare_test(test, result, reference, config)
        comparisons.append(comp)

    return comparisons
