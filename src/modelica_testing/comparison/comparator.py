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

# Machine epsilon guard — signals with range below this are treated as constant
_EPS = 100 * np.finfo(np.float64).eps


@dataclass
class VariableComparison:
    """Comparison result for a single tracked variable."""
    index: int
    name: str
    passed: bool
    nrmse: float  # Normalized RMSE (pass/fail metric)
    rmse: float  # Raw RMSE
    signal_range: float  # max(ref) - min(ref), for context
    max_abs_error: float  # Largest absolute deviation
    max_abs_error_time: float  # Time at which it occurs
    reference_final: float
    actual_final: float
    is_constant: bool = False  # True if reference signal has zero range
    tolerance_used: float = 0.0  # The tolerance threshold applied for this variable
    mode: str = "nrmse"  # Comparison mode: "nrmse" or "tube"
    # Tube-specific metrics (populated when mode="tube")
    tube_points_inside: Optional[float] = None  # Fraction of points inside tube (0-1)
    tube_worst_violation: Optional[float] = None  # Largest violation (absolute)
    tube_worst_violation_time: Optional[float] = None  # Time of worst violation


@dataclass
class StructuralWarning:
    """Warning about structural changes between reference and current run."""
    field: str
    reference_value: str
    current_value: str


@dataclass
class TestComparison:
    """Comparison result for a full test model."""
    model_id: str
    passed: bool
    test_id: Optional[str] = None  # ref file ID (e.g., "0001")
    variables: list[VariableComparison] = field(default_factory=list)
    warnings: list[StructuralWarning] = field(default_factory=list)
    error_message: Optional[str] = None
    sim_success: bool = True
    has_reference: bool = True


def _find_event_boundaries(time: np.ndarray) -> list[tuple[int, int]]:
    """Find event boundaries where duplicate time values occur.

    Dymola may produce 2 or 3 duplicate time points per event. This function
    groups consecutive duplicates and returns (first_dup, last_dup) pairs.
    The segment before the event ends at first_dup (inclusive, pre-event value).
    The segment after starts at last_dup (inclusive, post-event value).
    """
    boundaries = []
    i = 1
    while i < len(time):
        if time[i] == time[i - 1]:
            first_dup = i  # first duplicate index
            while i < len(time) and time[i] == time[first_dup - 1]:
                i += 1
            last_dup = i - 1  # last duplicate index
            boundaries.append((first_dup, last_dup))
        else:
            i += 1
    return boundaries


def _split_segments(
    time: np.ndarray,
    values: np.ndarray,
    boundaries: list[tuple[int, int]],
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split time/values at event boundaries into piecewise segments.

    Each segment has strictly monotonic time. At a boundary, the pre-event
    value (first duplicate) ends the previous segment, and the post-event
    value (last duplicate) starts the next segment. Any intermediate
    duplicates are skipped.
    """
    if not boundaries:
        return [(time, values)]

    segments = []
    prev = 0
    for first_dup, last_dup in boundaries:
        # Segment up to and including the pre-event value
        segments.append((time[prev:first_dup], values[prev:first_dup]))
        prev = last_dup  # Next segment starts at the post-event value

    # Final segment
    if prev < len(time):
        segments.append((time[prev:], values[prev:]))

    return segments


def _dedup_time_series(
    time: np.ndarray, values: np.ndarray, keep: str = "last"
) -> tuple[np.ndarray, np.ndarray]:
    """Remove duplicate time entries, keeping either 'first' or 'last' value."""
    if keep == "first":
        # Keep first occurrence: mask where time differs from previous
        mask = np.concatenate(([True], np.diff(time) != 0))
    else:
        # Keep last occurrence: mask where time differs from next
        mask = np.concatenate((np.diff(time) != 0, [True]))
    return time[mask], values[mask]


def _compare_trajectories(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    tolerance: float,
) -> VariableComparison:
    """Compare two time series using piecewise NRMSE.

    If the reference has duplicate time values (events), the comparison
    is done piecewise between event boundaries. This preserves event
    discontinuities and avoids interpolation across jumps.

    For each segment:
    - Pre-event segment: interpolate actual using pre-event values (first at duplicate times)
    - Post-event segment: interpolate actual using post-event values (last at duplicate times)

    Pass/fail metric:
    - NRMSE = RMSE / (max(ref) - min(ref)) for varying signals
    - RMSE directly for constant signals (range ~ 0)
    - Pass if NRMSE < tolerance
    """
    # Detect event boundaries in reference time
    boundaries = _find_event_boundaries(ref_time)
    segments = _split_segments(ref_time, ref_values, boundaries)

    # Deduplicate actual time series for clean interpolation.
    # For pre-event segments (ending at an event), use first value at duplicate times.
    # For post-event segments (starting at an event), use last value.
    act_time_pre, act_values_pre = _dedup_time_series(act_time, act_values, keep="first")
    act_time_post, act_values_post = _dedup_time_series(act_time, act_values, keep="last")

    # Compare piecewise: interpolate actual onto each reference segment
    all_abs_errors = []
    all_ref_times = []
    n_segments = len(segments)

    for seg_idx, (seg_time, seg_values) in enumerate(segments):
        if len(seg_time) == 0:
            continue

        is_last_segment = (seg_idx == n_segments - 1)

        if is_last_segment or n_segments == 1:
            # Last (or only) segment: no event at end, use post-event for start
            seg_actual = np.interp(seg_time, act_time_post, act_values_post)
        elif seg_idx == 0:
            # First segment ending at an event: use pre-event values
            seg_actual = np.interp(seg_time, act_time_pre, act_values_pre)
        else:
            # Interior segment: starts post-event, ends pre-event.
            # Use pre-event for bulk (correct at end boundary), then fix
            # the first point with the post-event value (correct at start).
            seg_actual = np.interp(seg_time, act_time_pre, act_values_pre)
            seg_actual[0] = np.interp(seg_time[0], act_time_post, act_values_post)

        seg_error = np.abs(seg_actual - seg_values)
        all_abs_errors.append(seg_error)
        all_ref_times.append(seg_time)

    if not all_abs_errors:
        return VariableComparison(
            index=0, name="", passed=True,
            nrmse=0.0, rmse=0.0, signal_range=0.0,
            max_abs_error=0.0, max_abs_error_time=0.0,
            reference_final=0.0, actual_final=0.0,
        )

    # Concatenate all segment errors for aggregate metrics
    abs_error = np.concatenate(all_abs_errors)
    error_times = np.concatenate(all_ref_times)

    rmse = float(np.sqrt(np.mean(abs_error ** 2)))

    # Max absolute error and its location
    max_abs_idx = int(np.argmax(abs_error))
    max_abs_error = float(abs_error[max_abs_idx])
    max_abs_error_time = float(error_times[max_abs_idx])

    # Signal range for normalization (across entire reference, including jumps)
    ref_min = float(np.min(ref_values))
    ref_max = float(np.max(ref_values))
    signal_range = ref_max - ref_min

    # NRMSE: normalize by range, or use raw RMSE for constant signals
    is_constant = signal_range < _EPS
    if is_constant:
        nrmse = rmse
    else:
        nrmse = rmse / signal_range

    passed = nrmse < tolerance

    ref_final = float(ref_values[-1]) if len(ref_values) > 0 else 0.0
    # Use actual value at final reference time
    act_final = float(np.interp(ref_time[-1], act_time, act_values))

    return VariableComparison(
        index=0,  # Set by caller
        name="",  # Set by caller
        passed=passed,
        nrmse=nrmse,
        rmse=rmse,
        signal_range=signal_range,
        max_abs_error=max_abs_error,
        max_abs_error_time=max_abs_error_time,
        reference_final=ref_final,
        actual_final=act_final,
        is_constant=is_constant,
    )


def _compare_final_values(
    ref_final: float,
    act_final: float,
    tolerance: float,
) -> VariableComparison:
    """Compare only final values.

    Uses absolute error for near-zero references,
    relative error otherwise.
    """
    abs_err = abs(act_final - ref_final)

    if abs(ref_final) > _EPS:
        nrmse = abs_err / abs(ref_final)
    elif abs(act_final) > _EPS:
        nrmse = abs(act_final)
    else:
        nrmse = 0.0

    passed = nrmse < tolerance

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        nrmse=nrmse,
        rmse=abs_err,
        signal_range=0.0,
        max_abs_error=abs_err,
        max_abs_error_time=0.0,
        reference_final=ref_final,
        actual_final=act_final,
        is_constant=True,
    )


def _interpolate_tube_widths(
    eval_time: np.ndarray,
    tube_points: list[dict],
    interpolation: str = "linear",
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate tube upper/lower widths at evaluation times.

    Supports two point formats:
    - Symmetric (legacy): {"time": t, "abs": a, "rel": r}
      → upper = lower = value
    - Asymmetric: {"time": t, "upper": u, "lower": l}
      → independent upper/lower widths

    Returns (upper_widths, lower_widths) arrays matching eval_time.
    Before first point: hold. After last: hold.
    """
    if not tube_points:
        return np.zeros_like(eval_time), np.zeros_like(eval_time)

    pts = sorted(tube_points, key=lambda p: p["time"])
    ctrl_times = np.array([p["time"] for p in pts])

    # Determine field names — check first point for format
    if "upper" in pts[0] or "lower" in pts[0]:
        # Asymmetric format
        ctrl_upper = np.array([p.get("upper", p.get("abs", 0.0)) for p in pts])
        ctrl_lower = np.array([p.get("lower", p.get("abs", 0.0)) for p in pts])
    else:
        # Legacy symmetric format (abs/rel fields)
        ctrl_upper = np.array([p.get("abs", p.get("rel", 0.0)) for p in pts])
        ctrl_lower = ctrl_upper.copy()

    if len(pts) == 1 or interpolation == "constant":
        indices = np.searchsorted(ctrl_times, eval_time, side="right") - 1
        indices = np.clip(indices, 0, len(pts) - 1)
        return ctrl_upper[indices], ctrl_lower[indices]

    upper_widths = np.interp(eval_time, ctrl_times, ctrl_upper)
    lower_widths = np.interp(eval_time, ctrl_times, ctrl_lower)
    return upper_widths, lower_widths


def _compare_tube(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    tube_config: dict,
) -> VariableComparison:
    """Compare using a tolerance tube around the reference trajectory.

    Tube width modes:
    - "band" (or legacy "abs"): widths are offsets in signal units (ref ± width)
    - "rel": widths are fractions of |reference| (ref ± frac * |ref|)
    - "absolute": upper/lower are literal y-axis bounds (not offsets)
    - Legacy (both abs and rel): width = max(abs, rel * |ref|)

    Supports symmetric (upper == lower) and asymmetric tubes.
    Supports constant tube (tube_abs/tube_rel) or time-varying
    (tube_points with interpolation).

    A point passes if: tube_lower <= actual <= tube_upper.
    The test passes if ALL points are inside the tube (strict).
    """
    act_interp = np.interp(ref_time, act_time, act_values)

    # tube_width_mode: "band" (offset in signal units), "rel" (fraction of |ref|),
    # "absolute" (literal y-values), "abs" (legacy alias for "band"), or None (legacy)
    tube_width_mode = tube_config.get("tube_width_mode")
    # Normalize legacy "abs" to "band"
    if tube_width_mode == "abs":
        tube_width_mode = "band"
    min_width = tube_config.get("tube_min_width", 0.0)

    tube_points = tube_config.get("tube_points")
    if tube_points:
        interpolation = tube_config.get("tube_interpolation", "linear")
        raw_upper, raw_lower = _interpolate_tube_widths(
            ref_time, tube_points, interpolation,
        )
    else:
        # Constant tube (shorthand)
        const_abs = tube_config.get("tube_abs", 0.0)
        const_rel = tube_config.get("tube_rel", 0.0)
        if tube_width_mode == "band":
            raw_upper = np.full_like(ref_time, const_abs)
            raw_lower = raw_upper.copy()
        elif tube_width_mode == "rel":
            raw_upper = np.full_like(ref_time, const_rel)
            raw_lower = raw_upper.copy()
        else:
            # Legacy: both abs and rel
            raw_upper = np.maximum(
                np.full_like(ref_time, const_abs),
                np.full_like(ref_time, const_rel) * np.abs(ref_values),
            )
            raw_lower = raw_upper.copy()

    if tube_width_mode == "absolute":
        # Absolute mode: raw values are literal y-axis bounds
        tube_upper = raw_upper
        tube_lower = raw_lower
    else:
        # Band/rel modes: raw values are offsets from reference
        if tube_width_mode == "rel":
            upper_width = raw_upper * np.abs(ref_values)
            lower_width = raw_lower * np.abs(ref_values)
        elif tube_width_mode == "band":
            upper_width = raw_upper
            lower_width = raw_lower
        elif tube_points and ("upper" in tube_points[0] or "lower" in tube_points[0]):
            upper_width = raw_upper
            lower_width = raw_lower
        else:
            # Legacy format: already computed as max(abs, rel * |ref|)
            upper_width = raw_upper
            lower_width = raw_lower

        # Apply minimum width floor
        if min_width > 0:
            upper_width = np.maximum(upper_width, min_width)
            lower_width = np.maximum(lower_width, min_width)

        tube_upper = ref_values + upper_width
        tube_lower = ref_values - lower_width

    # Check: tube_lower <= actual <= tube_upper
    above_upper = act_interp - tube_upper  # Positive = above tube
    below_lower = tube_lower - act_interp  # Positive = below tube
    violations = np.maximum(above_upper, below_lower)

    n_total = len(ref_time)
    n_inside = int(np.sum(violations <= 0))
    fraction_inside = n_inside / n_total if n_total > 0 else 1.0

    worst_idx = int(np.argmax(violations))
    worst_violation = float(max(violations[worst_idx], 0.0))
    worst_violation_time = float(ref_time[worst_idx])

    passed = n_inside == n_total

    # NRMSE for reporting (always based on actual vs reference difference)
    diff = act_interp - ref_values
    abs_error = np.abs(diff)
    rmse = float(np.sqrt(np.mean(abs_error ** 2)))
    signal_range = float(np.max(ref_values) - np.min(ref_values))
    is_constant = signal_range < _EPS
    nrmse = rmse if is_constant else rmse / signal_range

    max_abs_idx = int(np.argmax(abs_error))
    max_abs_error = float(abs_error[max_abs_idx])
    max_abs_error_time = float(ref_time[max_abs_idx])

    ref_final = float(ref_values[-1]) if len(ref_values) > 0 else 0.0
    act_final = float(act_interp[-1]) if len(act_interp) > 0 else 0.0

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        nrmse=nrmse,
        rmse=rmse,
        signal_range=signal_range,
        max_abs_error=max_abs_error,
        max_abs_error_time=max_abs_error_time,
        reference_final=ref_final,
        actual_final=act_final,
        is_constant=is_constant,
        mode="tube",
        tube_points_inside=fraction_inside,
        tube_worst_violation=worst_violation,
        tube_worst_violation_time=worst_violation_time,
    )


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
        ("translation.nonlinear_after_manipulation_max", "Nonlinear max size (after manipulation)"),
        ("translation.linear_count", "Linear system count"),
        ("translation.linear_after_manipulation_max", "Linear max size (after manipulation)"),
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
            warnings.append(StructuralWarning(
                field=label,
                reference_value=str(ref_val),
                current_value=str(cur_val),
            ))

    return warnings


def compare_test(
    test: TestModel,
    result: TestResult,
    reference: dict,
    config: Config,
) -> TestComparison:
    """Compare a test's simulation results against its reference."""
    ref_test_id = reference.get("test_id")

    if not result.success:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            test_id=ref_test_id,
            sim_success=False,
            error_message=result.error_message or "Simulation failed",
        )

    structural_warnings = _check_structural_changes(reference, result)

    ref_vars = {v["index"]: v for v in reference.get("variables", [])}
    shared_ref_time = reference.get("time")
    if shared_ref_time is not None:
        shared_ref_time = np.array(shared_ref_time)
    comparisons = []
    all_passed = True

    # Resolve base comparison tolerance: per-test > reference > config > default
    ref_comparison = reference.get("comparison", {})
    base_tolerance = (
        test.comparison_tolerance
        or ref_comparison.get("tolerance")
        or config.tolerance
    )

    # Merge variable overrides: spec overrides take precedence over reference
    ref_var_overrides = ref_comparison.get("variable_overrides", {})
    merged_overrides = {**ref_var_overrides, **test.variable_overrides}

    for var_result in result.variables:
        ref_var = ref_vars.get(var_result.index)
        if ref_var is None:
            comparisons.append(VariableComparison(
                index=var_result.index,
                name=var_result.name,
                passed=False,
                nrmse=float("inf"),
                rmse=float("inf"),
                signal_range=0.0,
                max_abs_error=float("inf"),
                max_abs_error_time=0.0,
                reference_final=float("nan"),
                actual_final=float(var_result.values[-1]) if len(var_result.values) > 0 else float("nan"),
            ))
            all_passed = False
            continue

        if shared_ref_time is not None:
            ref_time = shared_ref_time
        else:
            ref_time = np.array(ref_var["time"])
        ref_values = np.array(ref_var["values"])
        ref_name = ref_var.get("name", ref_var.get("expression", ""))
        # Prefer current run's name; fall back to reference name if it's clean
        # (not a raw Modelica expression like "cat(1, ...)")
        name = var_result.name or ref_name
        if not name or "\n" in name or name.startswith("cat("):
            name = f"x[{var_result.index}]"

        # Resolve per-variable settings
        var_override = merged_overrides.get(name, {})
        tolerance = var_override.get("tolerance", base_tolerance)
        mode = var_override.get("mode", "nrmse")

        if mode == "tube":
            vc = _compare_tube(
                ref_time, ref_values,
                var_result.time, var_result.values,
                var_override,
            )
        elif config.final_only:
            ref_final = ref_values[-1] if len(ref_values) > 0 else 0.0
            act_final = float(var_result.values[-1]) if len(var_result.values) > 0 else 0.0
            vc = _compare_final_values(ref_final, act_final, tolerance)
        else:
            vc = _compare_trajectories(
                ref_time, ref_values,
                var_result.time, var_result.values,
                tolerance,
            )

        vc.index = var_result.index
        vc.name = name
        vc.tolerance_used = tolerance
        comparisons.append(vc)

        if not vc.passed:
            all_passed = False

    return TestComparison(
        model_id=test.model_id,
        passed=all_passed,
        test_id=ref_test_id,
        variables=comparisons,
        warnings=structural_warnings,
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
                passed=True,
                has_reference=False,
                error_message="No reference baseline stored",
            ))
            continue

        comp = compare_test(test, result, reference, config)
        comparisons.append(comp)

    return comparisons
