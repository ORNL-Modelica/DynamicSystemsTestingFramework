"""Compare simulation results against stored references."""

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import numpy as np

from ..discovery.test_registry import TestModel
from ..simulators import TestResult
from ..storage.reference_store import ReferenceStore

if TYPE_CHECKING:
    # Annotation-only — runtime import is inside compare_test() to break the
    # comparator ↔ metric_tree cycle (metric_tree imports VariableComparison).
    from .metric_tree import MetricResult

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 1e-4

# Machine epsilon guard — signals with range below this are treated as constant
_EPS = 100 * np.finfo(np.float64).eps


@dataclass
class VariableComparison:
    """Comparison result for a single tracked variable.

    Conforms to the ``MetricResult`` contract in docs/extensibility.md:
    carries a ``passed`` flag, a numeric score (``nrmse`` for NRMSE mode,
    ``tube_points_inside`` for tube mode), and a structured ``diagnostics``
    bag for metric-specific extras (e.g. event-timing deltas, spectral
    peaks) that future metrics may attach without widening the schema.
    """
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
    # Open-ended structured extras — future metrics attach here instead of
    # growing this dataclass (event-timing, spectral, domain-specific scores).
    diagnostics: dict = field(default_factory=dict)


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
    # Phase 3.1: MetricTree root for this test. Today populated as the
    # implicit flat-AND over per-variable comparisons (matches `passed`
    # exactly). Phase 3.2+ replaces with user-authored trees from
    # test_spec.json. None when no comparison ran (sim failure, no baseline).
    metric_tree: Optional["MetricResult"] = None


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

    # NRMSE: normalize by range, or by magnitude for constant signals.
    # For constants with large magnitude (e.g., 37e9), raw RMSE is misleading
    # because float32 quantization alone can produce errors of hundreds.
    # Normalizing by magnitude gives a meaningful relative metric.
    is_constant = signal_range < _EPS
    if is_constant:
        ref_magnitude = float(np.max(np.abs(ref_values)))
        nrmse = rmse / ref_magnitude if ref_magnitude > _EPS else rmse
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


def _compare_points(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    points: Optional[list[dict]] = None,
    tolerance: float = 1e-4,
) -> VariableComparison:
    """Compare actual vs reference at declared time points.

    When ``points`` is None or empty, falls back to the legacy final-
    value check (act[-1] vs ref[-1] with ``tolerance`` as absolute
    delta). When ``points`` is a non-empty list, declared-points
    handling lands in Task 3 of the points-mode plan; for now, this
    branch raises NotImplementedError so downstream callers see a
    clear error if they hit the unimplemented path.
    """
    if not points:
        # Implicit final-only — preserved behavior.
        if len(ref_values) == 0 or len(act_values) == 0:
            return VariableComparison(
                index=0, name="", passed=False,
                nrmse=float("inf"), rmse=float("inf"),
                signal_range=0.0,
                max_abs_error=float("inf"),
                max_abs_error_time=0.0,
                reference_final=float("nan"),
                actual_final=float("nan"),
                mode="points",
                diagnostics={"error": "empty trajectory"},
            )
        ref_final = float(ref_values[-1])
        act_final = float(act_values[-1])
        delta = abs(act_final - ref_final)
        passed = delta < tolerance
        return VariableComparison(
            index=0, name="", passed=passed,
            nrmse=delta, rmse=delta, signal_range=0.0,
            max_abs_error=delta,
            max_abs_error_time=float(ref_time[-1]) if len(ref_time) else 0.0,
            reference_final=ref_final, actual_final=act_final,
            mode="points",
            diagnostics={"tolerance": tolerance, "delta": delta},
        )
    raise NotImplementedError(
        "Declared-points scoring lands in Task 3 of the points-mode plan."
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
    if is_constant:
        ref_magnitude = float(np.max(np.abs(ref_values)))
        nrmse = rmse / ref_magnitude if ref_magnitude > _EPS else rmse
    else:
        nrmse = rmse / signal_range

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


def _compare_range(
    act_time: np.ndarray,
    act_values: np.ndarray,
    min_value: Optional[float],
    max_value: Optional[float],
) -> VariableComparison:
    """Check that every sample of the actual signal lies within [min, max].

    Reference data is not used — the bounds come from the spec itself.
    This gives the MetricTree a leaf type that works without a stored
    baseline (the "is this variable always within safe limits" pattern).
    """
    above = (
        np.maximum(act_values - max_value, 0.0)
        if max_value is not None
        else np.zeros_like(act_values)
    )
    below = (
        np.maximum(min_value - act_values, 0.0)
        if min_value is not None
        else np.zeros_like(act_values)
    )
    violations = np.maximum(above, below)

    n_total = len(act_values)
    n_inside = int(np.sum(violations <= 0))
    fraction_inside = n_inside / n_total if n_total > 0 else 1.0

    worst_idx = int(np.argmax(violations)) if n_total > 0 else 0
    max_violation = float(violations[worst_idx]) if n_total > 0 else 0.0
    worst_time = float(act_time[worst_idx]) if n_total > 0 else 0.0

    passed = max_violation <= 0.0
    act_range = float(np.max(act_values) - np.min(act_values)) if n_total > 0 else 0.0
    act_final = float(act_values[-1]) if n_total > 0 else 0.0

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        # Signal-free metric: use max_violation as the score ("0 = all in bounds,
        # larger = farther out of bounds"). nrmse/rmse are overloaded here for
        # reporting uniformity.
        nrmse=max_violation,
        rmse=float(np.sqrt(np.mean(violations ** 2))) if n_total > 0 else 0.0,
        signal_range=act_range,
        max_abs_error=max_violation,
        max_abs_error_time=worst_time,
        reference_final=float("nan"),
        actual_final=act_final,
        is_constant=act_range < _EPS,
        mode="range",
        # Repurpose the tube fields for consistent reporting — "inside" reads
        # as "inside bounds" here.
        tube_points_inside=fraction_inside,
        tube_worst_violation=max_violation,
        tube_worst_violation_time=worst_time,
        # Preserve the declared bounds so the reporter panel can pre-fill
        # them (6.1.5 — auto-derived mode UI reads mode_values from here).
        diagnostics={"min_value": min_value, "max_value": max_value},
    )


def _compare_event_timing(
    ref_time: np.ndarray,
    act_time: np.ndarray,
    time_tolerance: float = 1e-3,
    count_must_match: bool = True,
    declared_events: Optional[list[dict]] = None,
) -> VariableComparison:
    """Compare event instants between reference and actual signals (4.C.1).

    Two paths:

    * ``declared_events is None`` (default): events are auto-detected
      from duplicate-time samples in BOTH arrays, then paired by index.
      Pass when ``count_must_match`` is satisfied and every pair's time-
      delta is within ``time_tolerance``.

    * ``declared_events is not None``: the declared list IS the reference
      event set. The actual signal is still auto-detected; each declared
      event claims the nearest actual event within its own tolerance
      (``event["tolerance"]`` if set, else ``time_tolerance``). An
      unclaimed declared event fails. Unclaimed actual events only fail
      if ``count_must_match`` is set (and declared count != actual count).

    The score (``nrmse`` field — repurposed for reporting uniformity) is
    the max event-time delta across matched pairs.
    """
    act_boundaries = _find_event_boundaries(act_time)
    act_events = [float(act_time[b[0]]) for b in act_boundaries]

    if declared_events is None:
        # Legacy auto-detect path — unchanged behavior.
        ref_boundaries = _find_event_boundaries(ref_time)
        ref_events = [float(ref_time[b[0]]) for b in ref_boundaries]

        n = min(len(ref_events), len(act_events))
        max_delta = 0.0
        delta_at = 0.0
        for i in range(n):
            d = abs(ref_events[i] - act_events[i])
            if d > max_delta:
                max_delta = d
                delta_at = ref_events[i]

        counts_match = len(ref_events) == len(act_events)
        passed = max_delta <= time_tolerance and (counts_match or not count_must_match)

        return VariableComparison(
            index=0, name="", passed=passed,
            nrmse=max_delta, rmse=max_delta, signal_range=0.0,
            max_abs_error=max_delta, max_abs_error_time=delta_at,
            reference_final=float("nan"), actual_final=float("nan"),
            mode="event-timing",
            diagnostics={
                "ref_event_count": len(ref_events),
                "act_event_count": len(act_events),
                "max_time_delta": max_delta,
                "time_tolerance": time_tolerance,
                "counts_match": counts_match,
            },
        )

    # Declared-events path.
    declared_count = len(declared_events)
    actual_count = len(act_events)
    counts_match = declared_count == actual_count
    max_delta = 0.0
    delta_at = 0.0
    # Track which actual events have been claimed so we don't double-match.
    claimed = [False] * actual_count
    all_matched = True
    for e in declared_events:
        target = float(e["time"])
        tol = float(e.get("tolerance") if e.get("tolerance") is not None else time_tolerance)
        # Find nearest unclaimed actual event within tolerance.
        best_idx = -1
        best_d = float("inf")
        for j, at in enumerate(act_events):
            if claimed[j]:
                continue
            d = abs(at - target)
            if d <= tol and d < best_d:
                best_d = d
                best_idx = j
        if best_idx < 0:
            all_matched = False
            continue
        claimed[best_idx] = True
        if best_d > max_delta:
            max_delta = best_d
            delta_at = target

    passed = all_matched and (counts_match or not count_must_match)

    return VariableComparison(
        index=0, name="", passed=passed,
        nrmse=max_delta, rmse=max_delta, signal_range=0.0,
        max_abs_error=max_delta, max_abs_error_time=delta_at,
        reference_final=float("nan"), actual_final=float("nan"),
        mode="event-timing",
        diagnostics={
            "ref_event_count": declared_count,
            "act_event_count": actual_count,
            "max_time_delta": max_delta,
            "time_tolerance": time_tolerance,
            "counts_match": counts_match,
        },
    )


def _compute_fft_spectrum(
    t: np.ndarray, v: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs, magnitude) for a time-series via uniform-grid FFT.

    Resamples to a uniform grid sized at the **next power of 2** above
    ``max(len(t), 64)``, strips DC, takes the real-FFT. The pow-2 choice
    matches the JS-side radix-2 implementation (D76) — both sides produce
    bit-identical bin frequencies, so the CLI's `paired_peaks` agrees
    with the browser's live scorer on a self-regression (no more
    index-PASS vs per-test-FAIL disagreement).
    """
    import math
    if len(t) == 0:
        return np.array([]), np.array([])
    n0 = max(len(t), 64)
    n = 1 << int(math.ceil(math.log2(n0))) if n0 > 1 else 64
    t_uniform = np.linspace(t[0], t[-1], n)
    unique_t, unique_idx = np.unique(t, return_index=True)
    v_uniform = np.interp(t_uniform, unique_t, v[unique_idx])
    v_uniform = v_uniform - np.mean(v_uniform)
    dt = (t_uniform[-1] - t_uniform[0]) / (n - 1)
    if dt <= 0:
        return np.array([]), np.array([])
    spectrum = np.abs(np.fft.rfft(v_uniform))
    freqs = np.fft.rfftfreq(n, d=dt)
    return freqs, spectrum


def _find_top_n_peaks(
    freqs: np.ndarray,
    spectrum: np.ndarray,
    n_peaks: int,
    min_frequency: float = 0.0,
) -> list[tuple[float, float]]:
    """Return the ``n_peaks`` largest local maxima as ``[(freq, amplitude), ...]``
    sorted by **frequency** (ascending).

    Local maxima are indices strictly greater than both immediate neighbors
    above ``min_frequency``. The amplitude filter (pick top-N by magnitude)
    runs first so spectral noise doesn't get counted; the frequency-sort
    runs second so pairing between reference and actual is predictable for
    tests with known frequencies (e.g., PRBS).

    Used by the reporter's "Detect peaks from reference" button to
    bootstrap a declared-peaks table; not consulted during pass/fail
    evaluation (that path now uses declared peaks).
    """
    if len(freqs) < 3 or n_peaks < 1:
        return []
    interior = np.arange(1, len(spectrum) - 1)
    is_peak = (
        (spectrum[interior] > spectrum[interior - 1])
        & (spectrum[interior] > spectrum[interior + 1])
    )
    peak_idx = interior[is_peak]
    floor = max(min_frequency, 0.0)
    peak_idx = peak_idx[freqs[peak_idx] > floor]
    if len(peak_idx) == 0:
        return []
    sorted_by_amp = peak_idx[np.argsort(-spectrum[peak_idx])]
    top_n = sorted_by_amp[: n_peaks]
    top_n_sorted = top_n[np.argsort(freqs[top_n])]
    return [(float(freqs[i]), float(spectrum[i])) for i in top_n_sorted]


def _find_strongest_peak_in_window(
    freqs: np.ndarray,
    spectrum: np.ndarray,
    lo: float,
    hi: float,
) -> Optional[tuple[float, float]]:
    """Return the ``(freq, amplitude)`` of the strongest local maximum in
    ``[lo, hi]``, or ``None`` if no local maximum exists in that window.

    Used by the declared-peaks pass/fail path. A peak qualifies if it's a
    local max (greater than both neighbors) AND its frequency sits in
    ``[lo, hi]``. Returning ``None`` means the leaf fails that peak.
    """
    if len(freqs) < 3:
        return None
    interior = np.arange(1, len(spectrum) - 1)
    in_window = (freqs[interior] >= lo) & (freqs[interior] <= hi)
    is_local_max = (
        (spectrum[interior] > spectrum[interior - 1])
        & (spectrum[interior] > spectrum[interior + 1])
    )
    candidate_idx = interior[in_window & is_local_max]
    if len(candidate_idx) == 0:
        return None
    best = candidate_idx[int(np.argmax(spectrum[candidate_idx]))]
    return (float(freqs[best]), float(spectrum[best]))


# Cap spectrum samples embedded into the HTML report's interactive.js —
# fine enough to see peaks, small enough to not bloat the payload.
_SPECTRUM_EMBED_CAP = 512


def _compare_dominant_frequency(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    peaks: Optional[list[dict]] = None,
) -> VariableComparison:
    """Compare declared frequency peaks between reference and actual (4.C.2).

    Takes a list of user-declared peaks of the form::

        [{"freq": 1.0, "tolerance": 0.02, "tolerance_mode": "rel"},
         {"freq": 7.0, "tolerance": 0.5,  "tolerance_mode": "abs"}, ...]

    For each declared peak the algorithm looks for the strongest local
    maximum in ``act_spectrum`` within the peak's tolerance window
    (rel: ``[f*(1-tol), f*(1+tol)]``; abs: ``[f-tol, f+tol]``). The
    leaf passes iff every declared peak has a match in its window.
    Unmatched declared peaks fail with ``matched_hz=None`` and a reason
    of "no peak in tolerance window".

    When ``peaks`` is empty (or omitted), the leaf fails with a hint
    pointing users to the reporter's "Detect peaks from reference"
    button — the declared-peaks contract is explicit by design.

    The top-N-by-amplitude algorithm of the pre-D75 implementation is
    retained as ``_find_top_n_peaks`` — the reporter uses it to
    populate a declared-peaks table from the reference spectrum when
    users don't yet know their peak frequencies. Pass/fail logic no
    longer calls it.
    """
    # The actual-side FFT is always required — without it we can't match
    # any peaks. The reference-side FFT is only required when no peaks
    # are declared (so the reporter can seed a table from the ref
    # spectrum's top-N). With declared peaks, an empty reference is OK
    # (baseline-free path, idea #59 / D83).
    if len(act_values) < 4:
        return VariableComparison(
            index=0, name="", passed=False,
            nrmse=float("inf"), rmse=0.0, signal_range=0.0,
            max_abs_error=0.0, max_abs_error_time=0.0,
            reference_final=float("nan"), actual_final=float("nan"),
            mode="dominant-frequency",
            diagnostics={"reason": "signal too short for FFT (need >=4 samples)"},
        )

    has_ref = len(ref_values) >= 4
    if not has_ref and not peaks:
        return VariableComparison(
            index=0, name="", passed=False,
            nrmse=float("inf"), rmse=0.0, signal_range=0.0,
            max_abs_error=0.0, max_abs_error_time=0.0,
            reference_final=float("nan"), actual_final=float("nan"),
            mode="dominant-frequency",
            diagnostics={"reason": "signal too short for FFT (need >=4 samples)"},
        )

    if has_ref:
        ref_freqs, ref_spectrum = _compute_fft_spectrum(ref_time, ref_values)
        # Reference peak detection — embedded so the reporter's "Detect"
        # button can seed a fresh declared-peaks table without recomputing.
        # N=10 gives room for up to 10 tracked modes without bloat.
        detected_ref_peaks = _find_top_n_peaks(ref_freqs, ref_spectrum, 10)
    else:
        ref_freqs = np.array([])
        ref_spectrum = np.array([])
        detected_ref_peaks = []

    act_freqs, act_spectrum = _compute_fft_spectrum(act_time, act_values)

    # Downsample spectra for embedding. 512 bins is ample for visual
    # inspection; full-resolution would inflate every dominant-frequency
    # leaf's payload.
    def _embed(freqs: np.ndarray, mag: np.ndarray) -> tuple[list[float], list[float]]:
        if len(freqs) <= _SPECTRUM_EMBED_CAP:
            return freqs.tolist(), mag.tolist()
        idx = np.linspace(0, len(freqs) - 1, _SPECTRUM_EMBED_CAP).astype(int)
        return freqs[idx].tolist(), mag[idx].tolist()

    ref_f_embed, ref_m_embed = _embed(ref_freqs, ref_spectrum)
    act_f_embed, act_m_embed = _embed(act_freqs, act_spectrum)

    peaks = peaks or []
    if not peaks:
        return VariableComparison(
            index=0, name="", passed=False,
            nrmse=float("inf"), rmse=0.0, signal_range=0.0,
            max_abs_error=0.0, max_abs_error_time=0.0,
            reference_final=float("nan"), actual_final=float("nan"),
            mode="dominant-frequency",
            diagnostics={
                "reason": (
                    "no peaks declared — use the reporter's "
                    "'Detect peaks from reference' button to seed a table "
                    "from the reference spectrum, then commit the spec."
                ),
                "peaks_declared": [],
                "paired_peaks": [],
                "detected_reference_peaks_hz": [f for f, _ in detected_ref_peaks],
                "ref_spectrum_freq": ref_f_embed,
                "ref_spectrum_mag": ref_m_embed,
                "act_spectrum_freq": act_f_embed,
                "act_spectrum_mag": act_m_embed,
            },
        )

    paired: list[dict] = []
    all_passed = True
    max_rel_err = 0.0
    max_delta = 0.0
    for declared in peaks:
        f_decl = float(declared.get("freq", 0.0))
        tol = float(declared.get("tolerance", 0.01))
        mode = declared.get("tolerance_mode", "rel")
        if mode == "rel":
            lo, hi = f_decl * (1.0 - tol), f_decl * (1.0 + tol)
        else:  # "abs"
            lo, hi = f_decl - tol, f_decl + tol

        match = _find_strongest_peak_in_window(act_freqs, act_spectrum, lo, hi)
        if match is None:
            paired.append({
                "declared_hz": f_decl,
                "matched_hz": None,
                "delta": None,
                "passed": False,
                "tolerance": tol,
                "tolerance_mode": mode,
                "reason": "no peak in tolerance window",
            })
            all_passed = False
            max_rel_err = float("inf")
        else:
            matched_hz, _amp = match
            delta = abs(matched_hz - f_decl)
            rel_err = delta / f_decl if f_decl > 0 else delta
            if rel_err > max_rel_err:
                max_rel_err = rel_err
            if delta > max_delta:
                max_delta = delta
            paired.append({
                "declared_hz": f_decl,
                "matched_hz": matched_hz,
                "delta": delta,
                "passed": True,  # match-in-window guarantees pass for this peak
                "tolerance": tol,
                "tolerance_mode": mode,
            })

    act_range = float(np.max(act_values) - np.min(act_values))
    return VariableComparison(
        index=0, name="", passed=all_passed,
        nrmse=max_rel_err,  # repurposed as "max relative error across declared peaks"
        rmse=max_rel_err,
        signal_range=act_range,
        max_abs_error=max_delta,
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode="dominant-frequency",
        diagnostics={
            "peaks_declared": list(peaks),
            "paired_peaks": paired,
            "detected_reference_peaks_hz": [f for f, _ in detected_ref_peaks],
            "max_rel_error": max_rel_err,
            # Spectrum arrays for the reporter's subplot.
            "ref_spectrum_freq": ref_f_embed,
            "ref_spectrum_mag": ref_m_embed,
            "act_spectrum_freq": act_f_embed,
            "act_spectrum_mag": act_m_embed,
        },
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
    test: TestModel, default_tolerance: float,
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
            comparisons.append(TestComparison(
                model_id=test.model_id,
                passed=False,
                sim_success=False,
                error_message="No simulation results found",
            ))
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
            comparisons.append(TestComparison(
                model_id=test.model_id,
                passed=True,
                has_reference=False,
                error_message="No reference baseline stored",
            ))
            continue

        comp = compare_test(
            test, result, reference if reference is not None else {},
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
