"""Per-mode comparison algorithms — pure compute, no orchestration.

Lives separately from :mod:`.comparator` (orchestrates compare_test /
compare_all) and :mod:`.types` (result dataclasses) to make per-mode
edits easier to review and reason about without scrolling through the
test-suite-level orchestration.

These functions are also the Python source-of-truth for
``interactive.js`` MODE_SCORERS — the JS counterparts mirror the math
here for live-edit recompute. Drift is caught by
``tests/test_scorer_parity.py``; cross-reference markers at each public
``_compare_*`` function point at the JS line.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .types import VariableComparison, _EPS


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
    # parity-test: live-preview JS counterpart at
    # src/dstf/reporting/templates/interactive.js MODE_SCORERS['nrmse']
    # (around line 113). The two implementations MUST agree on pass/fail
    # for any (ref, act, tolerance); drift is caught by
    # tests/test_scorer_parity.py. Update both when changing the math.
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


def _min_delta_in_box(
    act_time: np.ndarray,
    act_values: np.ndarray,
    t_lo: float,
    t_hi: float,
    target: float,
) -> tuple[float, float]:
    """Find the smallest |act(t) - target| for t in [t_lo, t_hi].

    Evaluates at every act_time sample inside the window plus the
    interpolated endpoints t_lo and t_hi — so a curve that enters the
    box between samples is still detected. Because the act trace is
    piecewise-linear between samples, the minimum on any segment is
    either at an endpoint or at a zero-crossing of ``act - target``;
    if any segment crosses the target value, min delta is 0.

    Returns (min_delta, t_at_min). When [t_lo, t_hi] is empty / outside
    the trajectory, returns (inf, t_lo) — caller decides whether that
    counts as a fail or a skip.
    """
    if len(act_time) == 0 or t_hi < t_lo:
        return float("inf"), t_lo
    # Build the ordered list of (t, v) eval points: interpolated
    # endpoints t_lo / t_hi (if inside trajectory) + interior samples.
    candidates: list[tuple[float, float]] = []
    if act_time[0] <= t_lo <= act_time[-1]:
        candidates.append((t_lo, float(np.interp(t_lo, act_time, act_values))))
    for i, t in enumerate(act_time):
        if t_lo <= t <= t_hi:
            candidates.append((float(t), float(act_values[i])))
    if act_time[0] <= t_hi <= act_time[-1]:
        candidates.append((t_hi, float(np.interp(t_hi, act_time, act_values))))
    if not candidates:
        return float("inf"), t_lo
    # Sort by time so adjacent-pair zero-crossing detection is well-defined.
    candidates.sort(key=lambda tv: tv[0])
    best_delta = float("inf")
    best_t = t_lo
    for t, v in candidates:
        d = abs(v - target)
        if d < best_delta:
            best_delta = d
            best_t = t
    # Zero-crossing check on each linear segment (between adjacent eval
    # points). If sign of (v - target) flips, the curve passes through
    # the target value somewhere inside the segment → delta = 0 there.
    for (t1, v1), (t2, v2) in zip(candidates, candidates[1:]):
        d1 = v1 - target
        d2 = v2 - target
        if d1 == 0 or d2 == 0:
            continue  # Already captured by endpoint scan.
        if (d1 > 0) != (d2 > 0):
            # Linear interp solve: t* = t1 + d1 / (d1 - d2) * (t2 - t1).
            denom = d1 - d2
            if denom != 0:
                t_star = t1 + d1 / denom * (t2 - t1)
                best_delta = 0.0
                best_t = t_star
                break
    return best_delta, best_t


def _compare_points(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    points: Optional[list[dict]] = None,
    tolerance: float = 1e-4,
) -> VariableComparison:
    # parity-test: live-preview JS counterpart at
    # src/dstf/reporting/templates/interactive.js MODE_SCORERS['points']
    # (around line 145). Drift is caught by tests/test_scorer_parity.py.
    """Compare actual vs reference at declared time points.

    When ``points`` is None or empty, falls back to the legacy final-
    value check (act[-1] vs ref[-1] with ``tolerance``). When ``points``
    is a non-empty list, each entry is a checkpoint:

      ``time`` — absolute time, or None for "trace's final time".
      ``value`` — absolute target. If absent, target = ref(time).
      ``tolerance`` — per-point y-tolerance (defaults to ``tolerance``).
      ``tolerance_mode`` — "abs" (default) | "rel" (scale by |target|).
      ``time_tolerance`` — x-tolerance for the box check (Task 4 of
        the points-mode plan; defaults to 0 = strict-time).

    Pass iff every scored point's delta is within its y-limit.
    """
    if not points:
        # Implicit final-only — legacy behavior.
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

    # Declared-points path.
    trace_end = (
        float(ref_time[-1]) if len(ref_time)
        else float(act_time[-1]) if len(act_time)
        else 0.0
    )
    scored = 0
    failed = 0
    worst_delta = 0.0
    worst_t = 0.0
    for point in points:
        t = point.get("time")
        if t is None:
            t = trace_end
        else:
            t = float(t)
        # Resolve target.
        explicit_value = point.get("value")
        if explicit_value is not None:
            target = float(explicit_value)
        else:
            if len(ref_time) == 0:
                # Ref-relative point with no reference data is skipped.
                # The ``is_baseline_free`` invariant should prevent us
                # from getting here, but we guard anyway.
                continue
            target = float(np.interp(t, ref_time, ref_values))
        # Resolve tolerance + mode.
        per_tol = point.get("tolerance")
        per_tol = float(per_tol) if per_tol is not None else float(tolerance)
        mode = point.get("tolerance_mode", "abs")
        y_limit = per_tol * abs(target) if mode == "rel" else per_tol
        if len(act_time) == 0:
            continue
        x_tol = point.get("time_tolerance", 0)
        x_tol = float(x_tol) if x_tol is not None else 0.0
        t_lo = max(t - x_tol, float(act_time[0]))
        t_hi = min(t + x_tol, float(act_time[-1]))
        if t_hi < t_lo:
            # Fully clipped (time outside trajectory + box doesn't reach in).
            continue
        delta, t_at_min = _min_delta_in_box(
            act_time, act_values, t_lo, t_hi, target,
        )
        scored += 1
        if delta > worst_delta:
            worst_delta = delta
            worst_t = t_at_min
        if delta > y_limit:
            failed += 1

    passed = scored > 0 and failed == 0
    return VariableComparison(
        index=0, name="", passed=passed,
        nrmse=worst_delta, rmse=worst_delta, signal_range=0.0,
        max_abs_error=worst_delta, max_abs_error_time=worst_t,
        reference_final=float(ref_values[-1]) if len(ref_values) else float("nan"),
        actual_final=float(act_values[-1]) if len(act_values) else float("nan"),
        mode="points",
        diagnostics={
            "scored_points": scored,
            "failed_points": failed,
            "worst_delta": worst_delta,
            "worst_time": worst_t,
        },
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
    # parity-test: live-preview JS counterpart at
    # src/dstf/reporting/templates/interactive.js MODE_SCORERS['tube']
    # (around line 210). The tube interpolation logic is the trickiest of
    # the live-preview modes — both implementations need to handle the
    # same width-mode dispatch (rel/band/absolute), the same tube_points
    # interpolation, and the same min_width clamping. Drift is caught by
    # tests/test_scorer_parity.py.
    """Compare using a tolerance tube around the reference trajectory.

    Tube width modes:
    - "band": widths are constant offsets in signal units (ref ± width)
    - "rel":  widths are fractions of |reference| (ref ± frac * |ref|)
    - "abs":  upper/lower are literal y-axis bounds (not offsets) —
              requires tube_points with explicit upper/lower

    Supports symmetric (upper == lower) and asymmetric tubes.
    Constant-tube shorthand (tube_abs / tube_rel) supports only
    'band' and 'rel'; 'abs' must use tube_points.

    A point passes if: tube_lower <= actual <= tube_upper.
    The test passes if ALL points are inside the tube (strict).
    """
    act_interp = np.interp(ref_time, act_time, act_values)

    tube_width_mode = tube_config.get("tube_width_mode")
    if tube_width_mode not in ("band", "rel", "abs"):
        raise ValueError(
            f"tube_width_mode must be 'band', 'rel', or 'abs'; "
            f"got {tube_width_mode!r}"
        )
    min_width = tube_config.get("tube_min_width", 0.0)

    tube_points = tube_config.get("tube_points")
    if tube_points:
        interpolation = tube_config.get("tube_interpolation", "linear")
        raw_upper, raw_lower = _interpolate_tube_widths(
            ref_time, tube_points, interpolation,
        )
    else:
        # Constant-tube shorthand — only 'band' and 'rel' supported.
        # 'abs' (literal y-bounds) needs both bounds explicitly, so it
        # must use tube_points.
        const_abs = tube_config.get("tube_abs", 0.0)
        const_rel = tube_config.get("tube_rel", 0.0)
        if tube_width_mode == "band":
            raw_upper = np.full_like(ref_time, const_abs)
            raw_lower = raw_upper.copy()
        elif tube_width_mode == "rel":
            raw_upper = np.full_like(ref_time, const_rel)
            raw_lower = raw_upper.copy()
        else:  # abs
            raise ValueError(
                "tube_width_mode='abs' requires tube_points; "
                "constant-tube shorthand supports only 'band' and 'rel'."
            )

    if tube_width_mode == "abs":
        # Literal y-axis bounds.
        tube_upper = raw_upper
        tube_lower = raw_lower
    else:
        # 'rel' or 'band' — raw values are offsets from reference.
        if tube_width_mode == "rel":
            upper_width = raw_upper * np.abs(ref_values)
            lower_width = raw_lower * np.abs(ref_values)
        else:  # band
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
    # parity-test: live-preview JS counterpart at
    # src/dstf/reporting/templates/interactive.js MODE_SCORERS['range']
    # (around line 198). Drift is caught by tests/test_scorer_parity.py.
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
    # parity-test: live-preview JS counterpart at
    # src/dstf/reporting/templates/interactive.js MODE_SCORERS['dominant-frequency']
    # (around line 276). Both sides resample to a power of 2 above
    # max(N, 64) before the FFT so bin frequencies are bit-identical,
    # which is what makes the parity feasible at all. Drift is caught
    # by tests/test_scorer_parity.py.
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
