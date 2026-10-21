"""Per-mode comparison algorithms — pure compute, no orchestration.

Lives separately from :mod:`.comparator` (orchestrates compare_test /
compare_all) and :mod:`.types` (result dataclasses) to make per-mode
edits easier to review and reason about without scrolling through the
test-suite-level orchestration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .geometry import Band, Box, HLine, Marker, Primitive, Series, VLine
from .sides import Sides, as_written, parse_number, parse_sides
from .types import _EPS, VariableComparison


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


def _no_data_failure(mode: str, reason: str) -> VariableComparison:
    """Hard-fail sentinel for comparisons with nothing to compare.

    Review 2026-07-06 (Theme 1): an empty reference slice, empty actual
    trajectory, or a window that excludes every sample must FAIL with a
    diagnostic — a vacuous pass here would let a dead simulation or a
    mis-specified window report green in a regression framework.
    """
    return VariableComparison(
        index=0,
        name="",
        passed=False,
        nrmse=float("inf"),
        mode=mode,
        diagnostics={"error": reason},
    )


def actual_on_reference_grid(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The actual trajectory read on the reference grid, event-aware.

    Returns ``(times, ref_on_times, act_on_times)``. Modelica writes an
    event as two samples at one instant; the reference is split there and
    the actual is deduplicated per segment — first value at a duplicate
    time for a segment ending at an event, last value for one starting at
    an event — so the pre-event actual is never paired with the post-event
    reference. This is THE interpolation the nrmse score uses; the figure
    layer's error series reads the same function, so the error drawn is
    the error scored (an event never shows as a spike the verdict lacks).
    """
    boundaries = _find_event_boundaries(ref_time)
    segments = _split_segments(ref_time, ref_values, boundaries)

    act_time_pre, act_values_pre = _dedup_time_series(
        act_time, act_values, keep="first"
    )
    act_time_post, act_values_post = _dedup_time_series(
        act_time, act_values, keep="last"
    )

    all_times = []
    all_ref = []
    all_act = []
    n_segments = len(segments)
    for seg_idx, (seg_time, seg_values) in enumerate(segments):
        if len(seg_time) == 0:
            continue
        is_last_segment = seg_idx == n_segments - 1
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
        all_times.append(seg_time)
        all_ref.append(seg_values)
        all_act.append(seg_actual)

    if not all_times:
        empty = np.array([], dtype=float)
        return empty, empty, empty
    return np.concatenate(all_times), np.concatenate(all_ref), np.concatenate(all_act)


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
    if len(ref_time) == 0:
        return _no_data_failure(
            "nrmse", "no reference samples to compare (empty baseline or window)"
        )
    if len(act_time) == 0:
        return _no_data_failure(
            "nrmse", "no actual samples to compare (empty trajectory or window)"
        )

    error_times, ref_on_grid, act_on_grid = actual_on_reference_grid(
        ref_time, ref_values, act_time, act_values
    )
    if len(error_times) == 0:
        # All segments empty — can happen with pathological duplicate-only
        # time vectors. Same rule as the guards above: nothing compared
        # means FAIL, not a vacuous pass (review 2026-07-06, finding 1).
        return _no_data_failure(
            "nrmse", "no comparable samples after event segmentation"
        )
    abs_error = np.abs(act_on_grid - ref_on_grid)

    rmse = float(np.sqrt(np.mean(abs_error**2)))

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
        is_constant=bool(is_constant),
    )


def _box_candidates(
    act_time: np.ndarray,
    act_values: np.ndarray,
    t_lo: float,
    t_hi: float,
) -> list[tuple[float, float]]:
    """The time-ordered ``(t, v)`` points a box check evaluates: the
    interpolated ends ``t_lo`` / ``t_hi`` (where inside the trajectory) and
    every sample between them. Empty for an empty box."""
    if len(act_time) == 0 or t_hi < t_lo:
        return []
    candidates: list[tuple[float, float]] = []
    if act_time[0] <= t_lo <= act_time[-1]:
        candidates.append((t_lo, float(np.interp(t_lo, act_time, act_values))))
    for i, t in enumerate(act_time):
        if t_lo <= t <= t_hi:
            candidates.append((float(t), float(act_values[i])))
    if act_time[0] <= t_hi <= act_time[-1]:
        candidates.append((t_hi, float(np.interp(t_hi, act_time, act_values))))
    # A NaN sample is no evidence of anything: it cannot be the closest
    # approach, and treating it as one side of a sign change made a curve
    # 3 units off everywhere "cross" the target and pass. Drop it; a window
    # holding nothing but NaN then reports no comparable sample (inf).
    candidates = [(t, v) for t, v in candidates if not np.isnan(v)]
    # Sort by time so adjacent-pair crossing detection is well-defined.
    candidates.sort(key=lambda tv: tv[0])
    return candidates


def _violation_in_box(
    act_time: np.ndarray,
    act_values: np.ndarray,
    t_lo: float,
    t_hi: float,
    target: float,
    lower: float,
    upper: float,
) -> tuple[float, float]:
    """How far the piecewise-linear actual stays outside the band
    ``-lower <= act - target <= upper`` over ``[t_lo, t_hi]``, and the value
    that comes closest to it.

    The violation is ``0.0`` when a candidate (:func:`_box_candidates`) lies
    in the band, or two adjacent finite candidates lie on opposite sides of
    it — the curve crossed it between them; else the smallest distance to the
    band; ``inf`` for a box with no candidate.

    Judged on ``d = v - target``, never on the edges ``target ± limit``: the
    two differ by a rounding step at the edge (``1.0 + 0.1`` is ``1.1``
    while ``1.1 - 1.0`` exceeds ``0.1``), and with equal sides this must be
    ``|d| <= limit`` to the bit — the verdict a number always gave. A band
    inverted by a negative side (``-lower > upper``) holds nothing and is
    crossed by nothing, as a negative tolerance never passed.
    """
    candidates = _box_candidates(act_time, act_values, t_lo, t_hi)
    best, closest = float("inf"), float("nan")
    for _, v in candidates:
        d = v - target
        if -lower <= d <= upper:
            return 0.0, v
        gap = d - upper if d > upper else -lower - d
        # An infinite sample's gap is inf, never below the starting best: it
        # still names the closest value. A NaN's gap is NaN and never wins.
        if gap < best or (gap == best and math.isnan(closest)):
            best, closest = gap, v
    if -lower <= upper:
        for (_, v1), (_, v2) in zip(candidates, candidates[1:]):
            # A jump to an infinite value is no curve through the band.
            if not (math.isfinite(v1) and math.isfinite(v2)):
                continue
            d1, d2 = v1 - target, v2 - target
            if (d1 < -lower and d2 > upper) or (d1 > upper and d2 < -lower):
                return 0.0, target
    return best, closest


def _point_tolerances(
    points: list[dict] | None, tolerance: Sides
) -> list[tuple[Sides, Sides]]:
    """Each declared point's ``(tolerance, time_tolerance)``: its own, else
    the leaf's ``tolerance`` (value) or none at all (time); a ``null`` is an
    absent key. A malformed one is a ``ValueError`` naming the row —
    ``PointsMode.__init__`` calls this too, because ``resolve_mode``'s
    callers are where a ``ValueError`` becomes the leaf's config error on
    both scoring paths."""
    out = []
    for i, point in enumerate(points or []):
        value, time = point.get("tolerance"), point.get("time_tolerance")
        out.append(
            (
                tolerance
                if value is None
                else parse_sides(value, f"points[{i}].tolerance"),
                Sides(0.0, 0.0)
                if time is None
                else parse_sides(time, f"points[{i}].time_tolerance"),
            )
        )
    return out


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
    candidates = _box_candidates(act_time, act_values, t_lo, t_hi)
    if not candidates:
        return float("inf"), t_lo
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
        if not (math.isfinite(v1) and math.isfinite(v2)):
            continue  # A jump to an infinite value crosses nothing.
        if (d1 > 0) != (d2 > 0):
            # Linear interp solve: t* = t1 + d1 / (d1 - d2) * (t2 - t1).
            denom = d1 - d2
            if denom != 0:
                t_star = t1 + d1 / denom * (t2 - t1)
                best_delta = 0.0
                best_t = t_star
                break
    return best_delta, best_t


def _signed_delta_in_box(
    act_time: np.ndarray,
    act_values: np.ndarray,
    t_lo: float,
    t_hi: float,
    target: float,
    delta: float,
) -> float:
    """``actual − target`` at the value :func:`_min_delta_in_box` found
    ``delta`` (its magnitude) at: the first candidate that far from the
    target, as that scan keeps the first. ``0.0`` where the curve crosses
    the target; ``delta`` itself where no candidate is that far (a box with
    no comparable sample)."""
    if delta == 0.0:
        return 0.0
    for _, v in _box_candidates(act_time, act_values, t_lo, t_hi):
        if abs(v - target) == delta:
            return v - target
    return delta


def _reported_point(
    tolerance: Sides,
    reported: tuple[float, Sides, bool, float] | None,
    worst_delta: float,
) -> dict:
    """What `summarize_points` says of the REPORTED point — the one furthest
    outside its own band, the largest |delta|'s when none is — as
    ``(actual − target, the sides it was judged on, whether they are
    relative, its time)``; ``None`` when no point was scored.

    Recorded wherever the leaf's own sentence — ``worst_delta``, the largest
    |delta| of any point, beside the leaf's sides — would not be that
    point's: its sides are not the leaf's (a pair or a number of its own),
    or they are RELATIVE (the band its delta was held to is those sides
    times |target|, the leaf's number or not), or another point's delta is
    the larger (a passing one, inside wider sides of its own); and where
    the leaf's are a pair, when its signed delta says which side applies.
    A leaf of one number whose reported point defers to it, absolute and
    of the largest delta, carries what it always carried."""
    if reported is None:
        return {} if tolerance.equal else {"worst_signed_delta": 0.0}
    signed_delta, sides, relative, time = reported
    its_own = sides != tolerance or relative or abs(signed_delta) != worst_delta
    if tolerance.equal and not its_own:
        return {}
    return {
        "worst_signed_delta": signed_delta,
        "worst_sides": as_written(sides),
        "worst_sides_relative": relative,
        "worst_point_time": time,
    }


def _compare_points(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    points: list[dict] | None = None,
    tolerance: Sides | float = 1e-4,
) -> VariableComparison:
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

    Every tolerance is a number (both sides equal) or a lower/upper pair.
    With ``d = actual − target``, a declared point passes iff the actual
    curve meets ``−lower <= d <= upper`` somewhere in its time box
    ``[time − lower, time + upper]`` (``lower``: how much earlier); the
    final-value check passes iff ``−lower < d < upper`` (strict).
    """
    tolerance = parse_sides(tolerance, "tolerance")
    if len(act_values) == 0:
        # An empty trajectory, or a window that holds none of its samples:
        # nothing to check, with rows declared as without — not scored, as
        # a range is (`_no_data_failure`), never a row that failed.
        return _no_data_failure(
            "points", "no actual samples to check (empty trajectory or window)"
        )
    if not points:
        # Implicit final-only — legacy behavior.
        if len(ref_values) == 0:
            return VariableComparison(
                index=0,
                name="",
                passed=False,
                nrmse=float("inf"),
                mode="points",
                diagnostics={"error": "empty trajectory"},
            )
        ref_final = float(ref_values[-1])
        act_final = float(act_values[-1])
        d = act_final - ref_final
        delta = abs(d)
        passed = -tolerance.lower < d < tolerance.upper
        t_final = float(ref_time[-1]) if len(ref_time) else 0.0
        return VariableComparison(
            index=0,
            name="",
            passed=passed,
            nrmse=delta,
            max_abs_error=delta,
            reference_final=ref_final,
            actual_final=act_final,
            mode="points",
            geometry=(
                Marker(
                    id="points/target/final",
                    kind="handle",
                    x=t_final,
                    y=ref_final,
                    passed=passed,
                ),
                Box(
                    id="points/box/final",
                    kind="handle",
                    t0=t_final,
                    t1=t_final,
                    y0=ref_final - tolerance.lower,
                    y1=ref_final + tolerance.upper,
                    passed=passed,
                ),
                Marker(
                    id="points/upper/final",
                    kind="grip",
                    x=t_final,
                    y=ref_final + tolerance.upper,
                ),
                Marker(
                    id="points/lower/final",
                    kind="grip",
                    x=t_final,
                    y=ref_final - tolerance.lower,
                ),
            ),
        )

    # Declared-points path.
    trace_end = (
        float(ref_time[-1])
        if len(ref_time)
        else float(act_time[-1])
        if len(act_time)
        else 0.0
    )
    scored = 0
    failed = 0
    worst_delta = 0.0
    worst_t = 0.0
    # With uneven sides the largest |delta| need not be the point that
    # breaks its band: +0.04 under an upper side of 0.05 passes while −0.02
    # under a lower side of 0.01 fails. The point the sentence reports is
    # the one that lies FURTHEST OUTSIDE its own band — `violation`, in the
    # band's units, so a `rel` point's is on the scaled band; the largest
    # |delta|'s when every point is inside. Of equals, the first. Each is
    # kept as `_reported_point` takes it.
    worst_outside = 0.0
    outside: tuple[float, Sides, bool, float] | None = None
    largest: tuple[float, Sides, bool, float] | None = None
    # Review 2026-07-06 (finding 2): a checkpoint the simulation never
    # reached — or whose ref-relative target lies beyond the stored
    # baseline — FAILS instead of being skipped. Skipping let truncated
    # simulations pass their tail checkpoints. Reasons are collected for
    # the report.
    geometry: list[Primitive] = []
    point_failures: list[dict] = []
    # The `point_failures` of checkpoints that were never SCORED (each
    # `continue` below). The first is the failure the sentence reports,
    # before any scored point: no delta of another row explains it.
    unscored: list[dict] = []
    row_tolerances = _point_tolerances(points, tolerance)
    for i, point in enumerate(points):
        t = point.get("time")
        t = trace_end if t is None else float(t)
        # Resolve target.
        explicit_value = point.get("value")
        if explicit_value is not None:
            target = float(explicit_value)
        else:
            if len(ref_time) == 0:
                # Ref-relative point with no reference data cannot score.
                # The ``is_baseline_free`` invariant should prevent us
                # from getting here, but if it happens it is a failure,
                # not a skip.
                failed += 1
                unscored.append(
                    {"time": t, "reason": "no reference data for ref-relative point"}
                )
                point_failures.append(unscored[-1])
                continue
            if t < float(ref_time[0]) or t > float(ref_time[-1]):
                # np.interp would silently clamp to the nearest endpoint,
                # scoring against a target the baseline never defined.
                failed += 1
                unscored.append(
                    {"time": t, "reason": "checkpoint time outside reference range"}
                )
                point_failures.append(unscored[-1])
                continue
            target = float(np.interp(t, ref_time, ref_values))
        # Resolve tolerance + mode.
        per_tol, x_tol = row_tolerances[i]
        mode = point.get("tolerance_mode", "abs")
        # ``rel``: each side is that fraction of |target|.
        lower_limit = per_tol.lower * abs(target) if mode == "rel" else per_tol.lower
        upper_limit = per_tol.upper * abs(target) if mode == "rel" else per_tol.upper
        t_lo = max(t - x_tol.lower, float(act_time[0]))
        t_hi = min(t + x_tol.upper, float(act_time[-1]))
        if t_hi < t_lo:
            # Time box entirely outside the actual trajectory — the
            # simulation never reached this checkpoint.
            failed += 1
            unscored.append(
                {"time": t, "reason": "checkpoint time outside actual trajectory"}
            )
            point_failures.append(unscored[-1])
            continue
        delta, t_at_min = _min_delta_in_box(
            act_time,
            act_values,
            t_lo,
            t_hi,
            target,
        )
        scored += 1
        if largest is None or delta > worst_delta:
            largest = (
                _signed_delta_in_box(act_time, act_values, t_lo, t_hi, target, delta),
                per_tol,
                mode == "rel",
                t,
            )
        if delta > worst_delta:
            worst_delta = delta
            worst_t = t_at_min
        # ``delta`` (the closest approach to the target itself) stays the
        # reported score; the verdict is the band's.
        violation, closest = _violation_in_box(
            act_time, act_values, t_lo, t_hi, target, lower_limit, upper_limit
        )
        point_passed = violation == 0.0
        if violation > worst_outside:
            worst_outside = violation
            outside = (
                _signed_delta_in_box(act_time, act_values, t_lo, t_hi, target, delta),
                per_tol,
                mode == "rel",
                t,
            )
        lo, hi = target - lower_limit, target + upper_limit
        geometry.append(
            Marker(
                id=f"points/target/{i}",
                kind="handle",
                x=t,
                y=target,
                passed=point_passed,
            )
        )
        geometry.append(
            Box(
                id=f"points/box/{i}",
                kind="handle",
                t0=t - x_tol.lower,
                t1=t + x_tol.upper,
                y0=lo,
                y1=hi,
                passed=point_passed,
            )
        )
        # One grip per side of the box, on its edge. The time grips only
        # where the row HOLDS a time tolerance: a row without one has no
        # width in time to drag.
        geometry.append(Marker(id=f"points/upper/{i}", kind="grip", x=t, y=hi))
        geometry.append(Marker(id=f"points/lower/{i}", kind="grip", x=t, y=lo))
        if point.get("time_tolerance") is not None:
            geometry.append(
                Marker(
                    id=f"points/before/{i}",
                    kind="grip",
                    x=t - x_tol.lower,
                    y=target,
                )
            )
            geometry.append(
                Marker(
                    id=f"points/after/{i}",
                    kind="grip",
                    x=t + x_tol.upper,
                    y=target,
                )
            )
        if not point_passed:
            failed += 1
            if per_tol.equal:
                reason = f"delta {delta:.6g} exceeds limit {upper_limit:.6g}"
            elif np.isnan(target):
                reason = "the target is not a number"
            elif np.isnan(closest):
                # No sample compares (every one is NaN): there is no
                # closest value to name. A row with no time tolerance has
                # no box to name either.
                in_box = x_tol.lower != 0 or x_tol.upper != 0
                reason = "no comparable sample" + (" in the time box" if in_box else "")
            else:
                reason = f"closest value {closest:.6g} is outside [{lo:.6g}, {hi:.6g}]"
            point_failures.append({"time": t, "reason": reason})

    passed = scored > 0 and failed == 0
    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        nrmse=worst_delta,
        max_abs_error=worst_delta,
        mode="points",
        diagnostics={
            "scored_points": scored,
            "failed_points": failed,
            "worst_delta": worst_delta,
            "worst_time": worst_t,
            "point_failures": point_failures,
            # The first, and how many: `failed_points` counts them beside
            # the scored rows that fail.
            **(
                {"unscored_point": unscored[0], "unscored_points": len(unscored)}
                if unscored
                else {}
            ),
            **_reported_point(
                tolerance, outside if outside is not None else largest, worst_delta
            ),
        },
        geometry=tuple(geometry),
    )


def _interpolate_tube_widths(
    eval_time: np.ndarray,
    tube_points: list[dict],
    interpolation: str = "linear",
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate tube upper/lower widths at evaluation times.

    Control-point format: ``{"time": t, "upper": u, "lower": l}``. A
    symmetric tube sets ``upper == lower``; either side may be omitted and
    defaults to 0.

    Returns (upper_widths, lower_widths) arrays matching eval_time.
    Before first point: hold. After last: hold.
    """
    if not tube_points:
        return np.zeros_like(eval_time), np.zeros_like(eval_time)

    pts = sorted(tube_points, key=lambda p: p["time"])
    ctrl_times = np.array([p["time"] for p in pts])

    if not any("upper" in p or "lower" in p for p in pts):
        raise ValueError(
            "tube_points entries need 'upper' and/or 'lower' widths; "
            f"got keys {sorted(pts[0])}"
        )
    ctrl_upper = np.array([p.get("upper", 0.0) for p in pts])
    ctrl_lower = np.array([p.get("lower", 0.0) for p in pts])

    if len(pts) == 1 or interpolation == "constant":
        indices = np.searchsorted(ctrl_times, eval_time, side="right") - 1
        indices = np.clip(indices, 0, len(pts) - 1)
        return ctrl_upper[indices], ctrl_lower[indices]

    upper_widths = np.interp(eval_time, ctrl_times, ctrl_upper)
    lower_widths = np.interp(eval_time, ctrl_times, ctrl_lower)
    return upper_widths, lower_widths


def _tube_bounds(
    eval_time: np.ndarray,
    ref_at_eval: np.ndarray,
    tube_config: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """Upper and lower band edges at ``eval_time``.

    Split out so the envelope is built by ONE piece of code whichever points it
    is asked about — the union grid, or the reference grid. ``_compare_tube``
    scores both and takes the stricter verdict, and that argument only holds if
    the two bands are constructed identically.
    """
    tube_width_mode = tube_config.get("tube_width_mode")
    if tube_width_mode not in ("band", "rel", "abs"):
        raise ValueError(
            f"tube_width_mode must be 'band', 'rel', or 'abs'; got {tube_width_mode!r}"
        )
    tube_points = tube_config.get("tube_points")
    if tube_points:
        raw_upper, raw_lower = _interpolate_tube_widths(
            eval_time,
            tube_points,
            tube_config.get("tube_interpolation", "linear"),
        )
    else:
        # Constant-tube shorthand — only 'band' and 'rel' supported. 'abs'
        # (literal y-bounds) needs both bounds explicitly, so it must use
        # tube_points. A number is both sides; a pair is the width above
        # (``upper``) and below (``lower``) the reference.
        const_abs = parse_sides(tube_config.get("tube_abs", 0.0), "tube_abs")
        const_rel = parse_sides(tube_config.get("tube_rel", 0.0), "tube_rel")
        if tube_width_mode == "band":
            raw_upper = np.full_like(eval_time, const_abs.upper)
            raw_lower = np.full_like(eval_time, const_abs.lower)
        elif tube_width_mode == "rel":
            raw_upper = np.full_like(eval_time, const_rel.upper)
            raw_lower = np.full_like(eval_time, const_rel.lower)
        else:  # abs
            raise ValueError(
                "tube_width_mode='abs' requires tube_points; "
                "constant-tube shorthand supports only 'band' and 'rel'."
            )

    if tube_width_mode == "abs":
        return raw_upper, raw_lower  # literal y-axis bounds

    if tube_width_mode == "rel":
        upper_width = raw_upper * np.abs(ref_at_eval)
        lower_width = raw_lower * np.abs(ref_at_eval)
    else:  # band
        upper_width = raw_upper
        lower_width = raw_lower

    # One number, whoever built the config: a negative one is no floor.
    min_width = parse_number(tube_config.get("tube_min_width", 0.0), "tube_min_width")
    if min_width > 0:
        upper_width = np.maximum(upper_width, min_width)
        lower_width = np.maximum(lower_width, min_width)

    return ref_at_eval + upper_width, ref_at_eval - lower_width


def _tube_eval_grid(
    ref_time: np.ndarray,
    act_time: np.ndarray,
    ctrl_times: np.ndarray | None,
) -> np.ndarray:
    """Breakpoints of both curves, so the band is enforced between samples.

    A tube is a continuous envelope; the reference grid is only the coarse
    basis on which it is declared. Evaluating on ``ref_time`` alone cannot see
    an excursion that lives between two baseline samples, and rounds away the
    band's own corners when a ``tube_points`` control point falls off that grid
    (declare an upper bound of 0.10 at t=1.5 against a baseline sampling
    0,1,2,3 and the reconstructed bound there is 0.25).

    EXACTNESS, AND ITS LIMITS. For ``band`` and ``abs`` width modes with linear
    width interpolation, checking every breakpoint is exactly as strict as
    checking the envelope continuously: between two consecutive breakpoints
    neither curve has a knot, so both are linear, their difference is linear,
    and a linear function attains its extremum at an endpoint. Verified over
    20,000 random reference/actual/band combinations against a dense scan.

    That argument does NOT cover three configurations, because each puts a kink
    in the violation function at a time this grid does not carry, leaving it
    concave on a segment so the endpoints understate the true maximum:

    * ``rel`` width mode -- ``|ref|`` kinks at every reference zero crossing,
      which is where a relative band pinches to zero width.
    * ``tube_min_width`` -- ``max(width, min_width)`` kinks where the clamp
      starts binding.
    * ``tube_interpolation="constant"`` -- a step width means the segment left
      of a widening step is checked with the new, wider width.

    Each of those kinks is computable in closed form and adding them here makes
    the guarantee unconditional; that is a separate change. Until then this
    grid is strictly stricter than evaluating on ``ref_time``, but exhaustive
    only for the two modes named above.

    DUPLICATE TIMESTAMPS, AND A KNOWN GAP. A discontinuity is stored as two
    samples at one instant. ``np.unique`` collapses it and ``np.interp`` then
    yields the post-event value, so the pre-event band centre is not checked.
    Evaluating ``ref_values`` by index instead does check both, but pairs the
    pre-event CENTRE against the post-event ACTUAL, which false-FAILS a perfect
    reproduction of the baseline -- so that is not a fix, and keeping it
    alongside this grid would preserve the false failure. Away from a duplicated
    instant the two approaches agree exactly, because interpolating at a node
    returns the node.

    The residual gap is therefore narrow: an actual whose value is wrong ONLY at
    a duplicated timestamp and correct at every surrounding sample. Handling a
    jump properly -- pairing pre with pre and post with post, and widening to the
    union of both bands where only one series carries the duplicate -- is a
    separate change with its own design question. ``TestKnownGapAtDiscontinuities``
    pins the current behaviour so that change has a target.

    The grid is clipped to the reference's span because that is where the band
    is declared -- an actual sample past the end of the baseline has no
    envelope to be judged against, which also keeps the evaluated span
    identical to what this function used before.
    """
    parts = [np.asarray(ref_time, dtype=float), np.asarray(act_time, dtype=float)]
    if ctrl_times is not None and len(ctrl_times) > 0:
        parts.append(np.asarray(ctrl_times, dtype=float))
    grid = np.unique(np.concatenate(parts))
    return grid[(grid >= ref_time[0]) & (grid <= ref_time[-1])]


def _tube_geometry(
    grid: np.ndarray,
    tube_lower: np.ndarray,
    tube_upper: np.ndarray,
    tube_points: list[dict] | None,
) -> tuple[Primitive, ...]:
    """The band as scored, plus one handle pair per authored control point.

    Handles sit at the RESOLVED band edge at the control point's time — for
    a ``rel`` tube that is ``ref ± rel·|ref|`` there, not the authored
    fraction — so an editor drags what the user sees. Control-point times
    are on the grid by construction (``_tube_eval_grid``), so the lookup is
    exact. Ids follow the spec's array order: ``tube/upper/3`` addresses
    ``tube_points/3`` in a patch.

    A tube of constant width (``tube_abs`` / ``tube_rel``, no control
    points) has one grip per side instead, ``tube/upper/const`` and
    ``tube/lower/const``, on the band's edges at the grid's middle sample.
    """
    out: list[Primitive] = [
        Band(id="tube/band", time=grid, lower=tube_lower, upper=tube_upper)
    ]
    if not tube_points:
        mid = len(grid) // 2
        for side, edge in (("upper", tube_upper), ("lower", tube_lower)):
            out.append(
                Marker(
                    id=f"tube/{side}/const",
                    kind="grip",
                    x=float(grid[mid]),
                    y=float(edge[mid]),
                )
            )
    for i, pt in enumerate(tube_points or []):
        t = float(pt["time"])
        if t < grid[0] or t > grid[-1]:
            continue  # off the span: no band edge exists there to anchor a handle on
        out.append(
            Marker(
                id=f"tube/upper/{i}",
                kind="handle",
                x=t,
                y=float(np.interp(t, grid, tube_upper)),
            )
        )
        out.append(
            Marker(
                id=f"tube/lower/{i}",
                kind="handle",
                x=t,
                y=float(np.interp(t, grid, tube_lower)),
            )
        )
    return tuple(out)


def _compare_tube(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    tube_config: dict,
) -> VariableComparison:
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
    if len(ref_time) == 0:
        return _no_data_failure(
            "tube", "no reference samples to compare (empty baseline or window)"
        )
    if len(act_time) == 0:
        return _no_data_failure(
            "tube", "no actual samples to compare (empty trajectory or window)"
        )
    # Width-mode validation and the min-width floor live in _tube_bounds, which
    # is the single place the envelope is constructed.
    tube_points = tube_config.get("tube_points")
    ctrl_times = (
        np.array([p["time"] for p in tube_points], dtype=float) if tube_points else None
    )

    # The envelope is continuous: evaluate every breakpoint of either curve.
    grid = _tube_eval_grid(ref_time, act_time, ctrl_times)
    ref_on_grid = np.interp(grid, ref_time, ref_values)
    act_on_grid = np.interp(grid, act_time, act_values)
    tube_upper, tube_lower = _tube_bounds(grid, ref_on_grid, tube_config)

    # Check: tube_lower <= actual <= tube_upper
    violations = np.maximum(act_on_grid - tube_upper, tube_lower - act_on_grid)

    n_total = len(violations)
    if n_total == 0:
        return _no_data_failure(
            "tube", "no evaluable samples (non-finite or disjoint time span)"
        )
    n_inside = int(np.sum(violations <= 0))
    fraction_inside = n_inside / n_total

    worst_idx = int(np.argmax(violations))
    worst_violation = float(max(violations[worst_idx], 0.0))
    worst_violation_time = float(grid[worst_idx])

    passed = n_inside == n_total

    # The largest deviation and the finals the summary reports stay on the
    # REFERENCE grid, as `_compare_trajectories` reads the same pair: the
    # verdict moved to the union grid above, and decides by the band alone.
    act_interp = np.interp(ref_time, act_time, act_values)
    abs_error = np.abs(act_interp - ref_values)
    max_abs_idx = int(np.argmax(abs_error))

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        # A tube is scored by the fraction inside (`tube_points_inside`, what
        # a weighted group reads); its `nrmse` is its worst violation, as a
        # range's is, and no surface shows it as the tube's number.
        nrmse=worst_violation,
        max_abs_error=float(abs_error[max_abs_idx]),
        max_abs_error_time=float(ref_time[max_abs_idx]),
        reference_final=float(ref_values[-1]),
        actual_final=float(act_interp[-1]),
        mode="tube",
        tube_points_inside=fraction_inside,
        tube_worst_violation=worst_violation,
        tube_worst_violation_time=worst_violation_time,
        geometry=_tube_geometry(grid, tube_lower, tube_upper, tube_points),
    )


def _compare_range(
    act_time: np.ndarray,
    act_values: np.ndarray,
    min_value: float | None,
    max_value: float | None,
) -> VariableComparison:
    """Check that every sample of the actual signal lies within [min, max].

    Reference data is not used — the bounds come from the spec itself.
    This gives the MetricTree a leaf type that works without a stored
    baseline (the "is this variable always within safe limits" pattern).
    """
    if len(act_values) == 0:
        return _no_data_failure(
            "range", "no actual samples to check (empty trajectory or window)"
        )
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

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        # Signal-free metric: use max_violation as the score ("0 = all in bounds,
        # larger = farther out of bounds").
        nrmse=max_violation,
        mode="range",
        # Repurpose the tube fields for consistent reporting — "inside" reads
        # as "inside bounds" here.
        tube_points_inside=fraction_inside,
        tube_worst_violation=max_violation,
        tube_worst_violation_time=worst_time,
        # Preserve the declared bounds so the reporter can report them.
        diagnostics={"min_value": min_value, "max_value": max_value},
        geometry=tuple(
            HLine(
                id=f"range/{label}",
                kind="handle",
                value=float(bound),
                passed=bool(np.all(viol <= 0)),
            )
            for label, bound, viol in (
                ("min", min_value, below),
                ("max", max_value, above),
            )
            if bound is not None
        ),
    )


#: Series reductions a ``scalar`` leaf may declare. Closed set in v1 — the
#: quantity field takes either one of these or a stats path, and grows an
#: ``{"expr": ...}`` shape later without breaking either.
SCALAR_REDUCTIONS = ("max", "min", "mean", "sample_mean", "final", "integral")


def _reduce_series(time: np.ndarray, values: np.ndarray, reduction: str) -> float:
    """Reduce a (already windowed) series to the number a scalar leaf scores.

    ``mean`` is the TIME-WEIGHTED mean (integral / time span), not the
    sample mean: a solver that emits a finer output grid, or extra samples
    around events, must not move the score of an identical trajectory. It
    degrades to the sample mean when the span is zero (a single sample, or
    the 1-sample arrays a stats-path leaf is handed).

    ``sample_mean`` is the plain arithmetic mean of the samples — it DOES
    move with the output grid, and exists for parity with tools that report
    that number.

    ``integral`` is the trapezoid rule spelled out rather than
    ``np.trapezoid`` — that spelling only exists from numpy 2.0 and the
    package supports 1.20+.
    """
    if reduction == "final":
        return float(values[-1])
    if reduction == "max":
        return float(np.max(values))
    if reduction == "min":
        return float(np.min(values))
    if reduction == "mean":
        span = float(time[-1] - time[0]) if len(time) > 1 else 0.0
        if span <= 0.0:
            return float(np.mean(values))
        return _trapezoid(time, values) / span
    if reduction == "sample_mean":
        return float(np.mean(values))
    if reduction == "integral":
        return _trapezoid(time, values)
    # Unreachable — ScalarMode validates the reduction at construction time.
    raise ValueError(f"unknown scalar reduction {reduction!r}")


def _trapezoid(time: np.ndarray, values: np.ndarray) -> float:
    """Trapezoid-rule integral of ``values`` over ``time``."""
    if len(values) < 2:
        return 0.0
    return float(np.sum(np.diff(time) * (values[:-1] + values[1:]) / 2.0))


def _scalar_geometry(
    act_scalar: float,
    min_value: float | None,
    max_value: float | None,
    *,
    ref_scalar: float | None,
    passed: bool,
    allowed: tuple[float, float] | None = None,
) -> tuple[Primitive, ...]:
    """The scalar and what it was judged against, as horizontal lines.

    A scalar leaf on a statistics path has no trajectory at all; these
    lines are then the whole figure. ``allowed`` is what the baseline form
    lets the scalar be, ``(lowest, highest)``: drawn, and dragged, as the
    bounds form's bounds are.
    """
    out: list[Primitive] = [
        HLine(id="scalar/actual", kind="act", value=float(act_scalar), passed=passed)
    ]
    if ref_scalar is not None:
        out.append(HLine(id="scalar/reference", kind="ref", value=float(ref_scalar)))
    if min_value is not None:
        out.append(HLine(id="scalar/min", kind="handle", value=float(min_value)))
    if max_value is not None:
        out.append(HLine(id="scalar/max", kind="handle", value=float(max_value)))
    if allowed is not None:
        lowest, highest = allowed
        out.append(HLine(id="scalar/upper", kind="handle", value=float(highest)))
        out.append(HLine(id="scalar/lower", kind="handle", value=float(lowest)))
    return tuple(out)


def _compare_scalar(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    *,
    reduction: str,
    tolerance: Sides | float,
    tolerance_mode: str,
    min_value: float | None,
    max_value: float | None,
) -> VariableComparison:
    """Score one number: reduced series (or resolved stats path) vs baseline.

    Two forms, selected by whether bounds are declared:

    * **bounds** (``min_value`` / ``max_value`` set) — baseline-free; pass
      iff the actual scalar lies within the inclusive bounds. The score is
      the distance outside them (0.0 inside), mirroring ``_compare_range``.
    * **baseline** — ``d = act - ref``; ``abs`` passes when
      ``-lower <= d <= upper``, ``rel`` when ``d / |ref|`` lies in that
      range, ``tolerance`` being a number (both sides) or a lower/upper
      pair. A zero-magnitude reference makes the relative criterion
      degenerate, so ``rel`` falls back to the absolute comparison when
      ``|ref| == 0``. The score stays the magnitude of the deviation.

    The evaluator hands stats-path leaves 1-sample arrays, so the same
    reduction path serves both quantity shapes.
    """
    if len(act_values) == 0:
        return _no_data_failure(
            "scalar", "no actual samples to reduce (empty trajectory or window)"
        )
    act_scalar = _reduce_series(act_time, act_values, reduction)
    # A non-finite reduction is nothing to score: a NaN among the samples, or
    # a window that left the reduction undefined. Fail with the reason rather
    # than carrying nan into the verdict — every surface reads
    # ``diagnostics["error"]`` when the numbers are not there.
    if not np.isfinite(act_scalar):
        return _no_data_failure(
            "scalar",
            f"{reduction} of the actual samples is not finite",
        )

    diagnostics: dict = {
        "reduction": reduction,
        "tolerance_mode": tolerance_mode,
        "min_value": min_value,
        "max_value": max_value,
    }

    if min_value is not None or max_value is not None:
        above = max(act_scalar - max_value, 0.0) if max_value is not None else 0.0
        below = max(min_value - act_scalar, 0.0) if min_value is not None else 0.0
        violation = max(above, below)
        return VariableComparison(
            index=0,
            name="",
            passed=violation <= 0.0,
            # Signal-free metric: nrmse carries the score ("0 = in bounds,
            # larger = farther out"), as in _compare_range.
            nrmse=violation,
            actual_final=act_scalar,
            mode="scalar",
            tube_worst_violation=violation,
            diagnostics=diagnostics,
            geometry=_scalar_geometry(
                act_scalar,
                min_value,
                max_value,
                ref_scalar=None,
                passed=violation <= 0.0,
            ),
        )

    if len(ref_values) == 0:
        return _no_data_failure(
            "scalar", "no reference samples to reduce (empty baseline or window)"
        )
    ref_scalar = _reduce_series(ref_time, ref_values, reduction)
    if not np.isfinite(ref_scalar):
        return _no_data_failure(
            "scalar",
            f"{reduction} of the reference samples is not finite",
        )
    tolerance = parse_sides(tolerance, "tolerance")
    d = act_scalar - ref_scalar
    # A zero-magnitude reference makes the relative criterion degenerate —
    # fall back to the absolute test and record that it fired, so the report
    # can say which criterion actually decided the verdict.
    abs_fallback = tolerance_mode == "rel" and abs(ref_scalar) == 0.0
    # ``rel``: each side is that fraction of |ref| — the plain side under
    # the fallback.
    scale = abs(ref_scalar) if tolerance_mode == "rel" and not abs_fallback else 1.0
    signed = d / abs(ref_scalar) if tolerance_mode == "rel" and not abs_fallback else d
    # What a ``weighted`` combinator reads stays a magnitude; the verdict is
    # each side's.
    score = abs(signed)
    passed = bool(-tolerance.lower <= signed <= tolerance.upper)
    diagnostics["abs_fallback"] = abs_fallback

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        nrmse=score,
        reference_final=ref_scalar,
        actual_final=act_scalar,
        # NOT is_constant: that flag means "the reference SIGNAL has zero
        # range", which the console renders as "(constant signal)". A scalar
        # leaf never compares signals, so the flag would be a false claim.
        mode="scalar",
        diagnostics=diagnostics,
        geometry=_scalar_geometry(
            act_scalar,
            None,
            None,
            ref_scalar=ref_scalar,
            passed=passed,
            allowed=(
                ref_scalar - tolerance.lower * scale,
                ref_scalar + tolerance.upper * scale,
            ),
        ),
    )


def _event_geometry(
    ref_events: list[float],
    act_events: list[float],
    tolerances: list[Sides],
    matched: Sequence[bool | None],
) -> tuple[Primitive, ...]:
    """Reference (or declared) events with their tolerance windows, and the
    actual events. Windows span the panel height (``y0``/``y1`` ``None``)
    and run from ``lower`` before the event to ``upper`` after it.

    ``matched[i]`` is ``None`` for a window that was never scored — an
    auto-detected reference event beyond the paired count contributes
    nothing to the verdict (only ``count_must_match`` can fail the leaf on
    it), so drawing it failed would contradict a passing leaf."""
    out: list[Primitive] = []
    for i, (t, tol, ok) in enumerate(zip(ref_events, tolerances, matched)):
        out.append(VLine(id=f"events/ref/{i}", kind="ref", time=float(t)))
        out.append(
            Box(
                id=f"events/window/{i}",
                kind="handle",
                t0=float(t - tol.lower),
                t1=float(t + tol.upper),
                y0=None,
                y1=None,
                passed=None if ok is None else bool(ok),
            )
        )
        out.append(
            VLine(id=f"events/early/{i}", kind="grip", time=float(t - tol.lower))
        )
        out.append(VLine(id=f"events/late/{i}", kind="grip", time=float(t + tol.upper)))
    for j, t in enumerate(act_events):
        out.append(VLine(id=f"events/act/{j}", kind="act", time=float(t)))
    return tuple(out)


def _event_tolerances(events: list[dict] | None, time_tolerance: Sides) -> list[Sides]:
    """Each declared event's window sides: its own ``tolerance``, else the
    leaf's ``time_tolerance`` (a ``null`` is an absent key). ``lower`` is how
    much EARLIER the actual event may come, ``upper`` how much LATER. A
    malformed one is a ``ValueError`` naming the row;
    ``EventTimingMode.__init__`` calls this too, so it is the leaf's config
    error on both scoring paths."""
    return [
        time_tolerance
        if event.get("tolerance") is None
        else parse_sides(event["tolerance"], f"events[{k}].tolerance")
        for k, event in enumerate(events or [])
    ]


def _signed_time_delta(time_tolerance: Sides, max_signed: float) -> dict:
    """``actual − reference`` (late positive) of the pair that lies furthest
    outside its window — of the largest |Δt| when none does — under
    ``max_time_delta_signed``, for a sentence whose sides differ
    (`summarize_event_timing`): recorded only there, so a leaf with a
    number carries what it always carried."""
    return {} if time_tolerance.equal else {"max_time_delta_signed": max_signed}


def _reported_event(
    time_tolerance: Sides, reported: tuple[float, Sides, float] | None
) -> dict:
    """What `summarize_event_timing` says of the REPORTED declared event —
    the matched pair of the largest |Δt| — as ``(actual − declared, the
    sides of its window, its declared time)``; ``None`` when no declared
    event was found.

    As `_reported_point`: recorded where that event's window is not the
    leaf's — a pair or a number of its own — or the leaf's is a pair, so
    the sentence names the window the Δt was held to. A leaf of one number
    whose reported event defers to it carries what it always carried."""
    if reported is None:
        return _signed_time_delta(time_tolerance, 0.0)
    signed_delta, sides, time = reported
    if sides == time_tolerance and time_tolerance.equal:
        return {}
    return {
        "max_time_delta_signed": signed_delta,
        "worst_sides": as_written(sides),
        "worst_event_time": time,
    }


def _compare_event_timing(
    ref_time: np.ndarray,
    act_time: np.ndarray,
    time_tolerance: Sides | float = 1e-3,
    count_must_match: bool = True,
    declared_events: list[dict] | None = None,
) -> VariableComparison:
    """Compare event instants between reference and actual signals (4.C.1).

    Two paths:

    * ``declared_events is None`` (default): events are auto-detected
      from duplicate-time samples in BOTH arrays, then paired by index.
      Pass when ``count_must_match`` is satisfied and every pair's
      ``d = actual − reference`` lies in ``[−lower, upper]`` of
      ``time_tolerance`` (a number is both sides; ``lower`` is how much
      earlier, ``upper`` how much later).

    * ``declared_events is not None``: the declared list IS the reference
      event set. The actual signal is still auto-detected; declared
      windows (``event["tolerance"]`` if set, else ``time_tolerance``) are
      then matched optimally — sorted by right endpoint, each claiming the
      smallest unclaimed actual event inside it. An
      unclaimed declared event fails. Unclaimed actual events only fail
      if ``count_must_match`` is set (and declared count != actual count).

    The score (``nrmse`` field — repurposed for reporting uniformity) is
    the max event-time delta across matched pairs.
    """
    time_tolerance = parse_sides(time_tolerance, "time_tolerance")
    # An empty trajectory, or a window that holds none of its samples, has
    # no events to find and none to miss: not scored (`_no_data_failure`).
    # Auto-detected, the reference's events were paired with none and the
    # leaf passed on nothing; declared, each read as an event not found.
    if len(act_time) == 0:
        return _no_data_failure(
            "event-timing", "no actual samples to check (empty trajectory or window)"
        )
    if declared_events is None and len(ref_time) == 0:
        return _no_data_failure(
            "event-timing",
            "no reference samples to compare (empty baseline or window)",
        )
    act_boundaries = _find_event_boundaries(act_time)
    act_events = [float(act_time[b[0]]) for b in act_boundaries]

    if declared_events is None:
        # Legacy auto-detect path — unchanged behavior.
        ref_boundaries = _find_event_boundaries(ref_time)
        ref_events = [float(ref_time[b[0]]) for b in ref_boundaries]

        n = min(len(ref_events), len(act_events))
        max_delta = 0.0
        max_signed = 0.0
        delta_at = 0.0
        # With uneven sides the largest |Δt| need not be the pair that
        # breaks the window (0.04 late under an upper side of 0.05 passes,
        # 0.03 early under a lower side of 0.01 fails): the signed Δt the
        # sentence prints is the pair's FURTHEST OUTSIDE it, and
        # `max_signed` (the largest |Δt|) when every pair is inside.
        max_outside = 0.0
        outside_signed = 0.0
        # ``d > 0`` is LATE: the actual event came after the reference's.
        in_window = []
        for i in range(n):
            d = act_events[i] - ref_events[i]
            in_window.append(-time_tolerance.lower <= d <= time_tolerance.upper)
            if abs(d) > max_delta:
                max_delta = abs(d)
                max_signed = d
                delta_at = ref_events[i]
            if not in_window[-1]:
                outside = (
                    d - time_tolerance.upper
                    if d > time_tolerance.upper
                    else -time_tolerance.lower - d
                )
                if outside > max_outside:
                    max_outside = outside
                    outside_signed = d

        counts_match = len(ref_events) == len(act_events)
        # A window inverted by a negative side holds nothing — with no pair
        # to score as well, as a negative number never passed
        # (``max_delta <= time_tolerance`` with ``max_delta`` 0).
        holds = -time_tolerance.lower <= time_tolerance.upper
        passed = holds and all(in_window) and (counts_match or not count_must_match)

        return VariableComparison(
            index=0,
            name="",
            passed=passed,
            nrmse=max_delta,
            max_abs_error_time=delta_at,
            mode="event-timing",
            diagnostics={
                "ref_event_count": len(ref_events),
                "act_event_count": len(act_events),
                "time_tolerance": as_written(time_tolerance),
                "counts_match": counts_match,
                **_signed_time_delta(
                    time_tolerance, outside_signed if max_outside > 0 else max_signed
                ),
            },
            geometry=_event_geometry(
                ref_events,
                act_events,
                [time_tolerance] * len(ref_events),
                [in_window[i] if i < n else None for i in range(len(ref_events))],
            ),
        )

    # Declared-events path.
    declared_count = len(declared_events)
    actual_count = len(act_events)
    counts_match = declared_count == actual_count
    max_delta = 0.0
    delta_at = 0.0
    # The pair the sentence reports: the largest |Δt|'s, of equals the
    # first claimed — kept as `_reported_event` takes it.
    largest: tuple[float, Sides, float] | None = None
    # Optimal 1-D interval matching (review 2026-07-06, greedy-stealing
    # finding): each declared event defines a window [time-lower, time+upper].
    # Sorting windows by right endpoint and assigning each the SMALLEST
    # unclaimed actual event inside it is a maximum matching for interval
    # graphs — a feasible assignment is never missed because an earlier
    # declared event claimed the wrong actual event.
    claimed = [False] * actual_count
    matched_flags = [False] * declared_count
    windows = []
    declared_times: list[float] = []
    declared_tols = _event_tolerances(declared_events, time_tolerance)
    for k, e in enumerate(declared_events):
        target = float(e["time"])
        tol = declared_tols[k]
        declared_times.append(target)
        windows.append((target - tol.lower, target + tol.upper, target, k))
    windows.sort(key=lambda w: w[1])
    for lo, hi, target, k in windows:
        best_idx = -1
        for j, at in enumerate(act_events):
            if not claimed[j] and lo <= at <= hi:
                best_idx = j
                break  # act_events is time-sorted → first hit is smallest
        if best_idx < 0:
            continue
        claimed[best_idx] = True
        matched_flags[k] = True
        # A pair exists only INSIDE its window (the claim above), so none
        # lies outside its band: the signed Δt is the largest |Δt|'s.
        signed_d = act_events[best_idx] - target
        d = abs(signed_d)
        if largest is None or d > max_delta:
            largest = (signed_d, declared_tols[k], target)
        if d > max_delta:
            max_delta = d
            delta_at = target

    # A declared event that claimed nothing fails the leaf, and the score
    # above says nothing of it (it is the matched pairs'): the count is what
    # the sentence then says (`summarize_event_timing`); WHICH ones is the
    # geometry's — a window that is not ``passed``.
    unmatched = matched_flags.count(False)
    passed = unmatched == 0 and (counts_match or not count_must_match)

    return VariableComparison(
        index=0,
        name="",
        passed=passed,
        nrmse=max_delta,
        max_abs_error_time=delta_at,
        mode="event-timing",
        diagnostics={
            "ref_event_count": declared_count,
            "act_event_count": actual_count,
            "time_tolerance": as_written(time_tolerance),
            "counts_match": counts_match,
            **_reported_event(time_tolerance, largest),
            "unmatched_events": unmatched,
            "declared_events": declared_count,
        },
        geometry=_event_geometry(
            declared_times, act_events, declared_tols, matched_flags
        ),
    )


def _compute_fft_spectrum(
    t: np.ndarray,
    v: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs, magnitude) for a time-series via uniform-grid FFT.

    Resamples to a uniform grid sized at the **next power of 2** above
    ``max(len(t), 64)``, strips DC, takes the real-FFT. Bin frequencies
    therefore depend on the sample COUNT: the same signal at a different
    resolution lands its peaks on a different grid.
    """
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


def _local_maxima(spectrum: np.ndarray) -> np.ndarray:
    """The indices of ``spectrum``'s local maxima (a flat top counts once).

    A run of equal values whose outer neighbours are both strictly lower,
    touching neither end of the array, is one maximum, reported at the
    run's middle index rounded down — ``scipy.signal.find_peaks``' rule on
    finite input (on an infinite run they differ: ``[1, inf, inf, 1]`` gives
    no maximum here, ``[1]`` there). A resonance between two bins can land
    on both with the same magnitude: strictly greater than both neighbours
    then found nothing there. Its callers hold at least three values.
    """
    starts = np.concatenate(([0], np.flatnonzero(np.diff(spectrum) != 0) + 1))
    ends = np.concatenate((starts[1:], [len(spectrum)])) - 1
    values = spectrum[starts]
    inner = np.arange(1, len(starts) - 1)
    peak = (values[inner - 1] < values[inner]) & (values[inner + 1] < values[inner])
    return (starts[inner[peak]] + ends[inner[peak]]) // 2


def _find_top_n_peaks(
    freqs: np.ndarray,
    spectrum: np.ndarray,
    n_peaks: int,
    min_frequency: float = 0.0,
) -> list[tuple[float, float]]:
    """Return the ``n_peaks`` largest local maxima as ``[(freq, amplitude), ...]``
    sorted by **frequency** (ascending).

    Local maxima (a flat top counts once: :func:`_local_maxima`) above
    ``min_frequency``. The amplitude filter (pick top-N by magnitude)
    runs first so spectral noise doesn't get counted; the frequency-sort
    runs second so pairing between reference and actual is predictable for
    tests with known frequencies (e.g., PRBS).

    Drawn on the spectrum plot ("Reference peaks") so a spec author can
    read the reference's own peaks off a failing run and declare them;
    not consulted during pass/fail evaluation (that path uses declared
    peaks).
    """
    if len(freqs) < 3 or n_peaks < 1:
        return []
    peak_idx = _local_maxima(spectrum)
    floor = max(min_frequency, 0.0)
    peak_idx = peak_idx[freqs[peak_idx] > floor]
    if len(peak_idx) == 0:
        return []
    sorted_by_amp = peak_idx[np.argsort(-spectrum[peak_idx])]
    top_n = sorted_by_amp[:n_peaks]
    top_n_sorted = top_n[np.argsort(freqs[top_n])]
    return [(float(freqs[i]), float(spectrum[i])) for i in top_n_sorted]


def _find_strongest_peak_in_window(
    freqs: np.ndarray,
    spectrum: np.ndarray,
    lo: float,
    hi: float,
    *,
    exclude: set[float] | None = None,
    min_amplitude: float = 0.0,
) -> tuple[float, float] | None:
    """Return the ``(freq, amplitude)`` of the strongest local maximum in
    ``[lo, hi]``, or ``None`` if no local maximum exists in that window.

    Used by the declared-peaks pass/fail path. A peak qualifies if it's a
    local maximum (a flat top counts once: :func:`_local_maxima`) AND its
    frequency sits in ``[lo, hi]``. Returning ``None`` means the leaf fails
    that peak.

    ``exclude`` holds bin frequencies already claimed by other declared
    peaks; ``min_amplitude`` filters spectral noise (review 2026-07-06:
    without both, two declared peaks could match the same actual peak,
    or a claimed-peak fix would just re-match machine-noise sidelobes).
    """
    if len(freqs) < 3:
        return None
    maxima = _local_maxima(spectrum)
    candidate_idx = maxima[(freqs[maxima] >= lo) & (freqs[maxima] <= hi)]
    if min_amplitude > 0.0:
        candidate_idx = candidate_idx[spectrum[candidate_idx] >= min_amplitude]
    if exclude:
        candidate_idx = np.array(
            [i for i in candidate_idx if float(freqs[i]) not in exclude], dtype=int
        )
    if len(candidate_idx) == 0:
        return None
    best = candidate_idx[int(np.argmax(spectrum[candidate_idx]))]
    return (float(freqs[best]), float(spectrum[best]))


# A local maximum below this fraction of the spectrum's global max is
# treated as noise, not a matchable mode. Declared peaks describe
# significant resonances; letting near-zero FFT sidelobes satisfy a
# declared peak defeats the check (see _compare_dominant_frequency).
_PEAK_MIN_REL_AMPLITUDE = 0.01


def _frequency_geometry(
    ref_freqs: np.ndarray,
    ref_spectrum: np.ndarray,
    act_freqs: np.ndarray,
    act_spectrum: np.ndarray,
    detected_ref_peaks: list[tuple[float, float]],
    peaks: list[dict],
    paired: list[dict],
) -> tuple[Primitive, ...]:
    """Both spectra, the reference's detected peaks, and per declared peak
    its handle, its acceptance window and (when found) its match. All on
    the spectrum panel.

    The spectra are the FULL-resolution arrays the peaks were found in, not
    an index-strided subset: a stride skips the peak bin for any signal over
    a few hundred samples, and a marker placed at full-resolution amplitude
    then floats above a curve with no peak in it. The figure layer
    decimates with LTTB, which keeps peaks."""
    out: list[Primitive] = []
    if len(ref_freqs):
        out.append(
            Series(
                id="spectrum/ref",
                kind="ref",
                panel="spectrum",
                x=ref_freqs,
                y=ref_spectrum,
            )
        )
    out.append(
        Series(
            id="spectrum/act",
            kind="act",
            panel="spectrum",
            x=act_freqs,
            y=act_spectrum,
        )
    )
    for k, (f, amp) in enumerate(detected_ref_peaks):
        out.append(
            Marker(
                id=f"peaks/detected/{k}",
                kind="detected",
                panel="spectrum",
                x=float(f),
                y=float(amp),
            )
        )

    def _amp_at(freq: float) -> float:
        return (
            float(np.interp(freq, act_freqs, act_spectrum)) if len(act_freqs) else 0.0
        )

    for i, (declared, pair) in enumerate(zip(peaks, paired)):
        f_decl = float(declared.get("freq", 0.0))
        lo, hi, _, _ = _peak_window(declared, i)
        ok = bool(pair["passed"])
        out.append(
            Marker(
                id=f"peaks/declared/{i}",
                kind="handle",
                panel="spectrum",
                x=f_decl,
                y=_amp_at(f_decl),
                passed=ok,
            )
        )
        out.append(
            Box(
                id=f"peaks/window/{i}",
                kind="handle",
                panel="spectrum",
                t0=lo,
                t1=hi,
                y0=None,
                y1=None,
                passed=ok,
            )
        )
        for side, edge in (("lower", lo), ("upper", hi)):
            out.append(
                VLine(id=f"peaks/{side}/{i}", kind="grip", panel="spectrum", time=edge)
            )
        if pair.get("matched_hz") is not None:
            mh = float(pair["matched_hz"])
            out.append(
                Marker(
                    id=f"peaks/matched/{i}",
                    kind="matched",
                    panel="spectrum",
                    x=mh,
                    y=_amp_at(mh),
                    passed=True,
                )
            )
    return tuple(out)


def _peak_tolerance(declared: dict, i: int) -> Sides:
    """Declared peak ``i``'s tolerance: a number (both sides) or a
    lower/upper pair; none (or ``null``) is 1 % each side. A malformed one
    is a ``ValueError`` naming the row — ``DominantFrequencyMode.__init__``
    calls this too, so it is the leaf's config error on both scoring paths."""
    raw = declared.get("tolerance")
    if raw is None:
        return Sides(0.01, 0.01)
    return parse_sides(raw, f"peaks[{i}].tolerance")


def _peak_window(declared: dict, i: int) -> tuple[float, float, Sides, str]:
    """Declared peak ``i``'s acceptance window ``(lo, hi)``, its sides and
    its tolerance mode — the ONE place the window is computed: the scorer
    matches in it and the figure draws it. ``rel``: ``f·(1 − lower)`` to
    ``f·(1 + upper)``; ``abs``: ``f − lower`` to ``f + upper``."""
    f = float(declared.get("freq", 0.0))
    tol = _peak_tolerance(declared, i)
    mode = declared.get("tolerance_mode", "rel")
    if mode == "rel":
        return f * (1.0 - tol.lower), f * (1.0 + tol.upper), tol, mode
    return f - tol.lower, f + tol.upper, tol, mode


def _compare_dominant_frequency(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    peaks: list[dict] | None = None,
) -> VariableComparison:
    """Compare declared frequency peaks between reference and actual (4.C.2).

    Takes a list of user-declared peaks of the form::

        [{"freq": 1.0, "tolerance": 0.02, "tolerance_mode": "rel"},
         {"freq": 7.0, "tolerance": 0.5,  "tolerance_mode": "abs"}, ...]

    For each declared peak the algorithm looks for the strongest local
    maximum in ``act_spectrum`` within the peak's tolerance window
    (rel: ``[f*(1-lower), f*(1+upper)]``; abs: ``[f-lower, f+upper]``, a
    number being both sides — :func:`_peak_window`). The
    leaf passes iff every declared peak has a match in its window.
    Unmatched declared peaks fail with ``matched_hz=None`` and a reason
    of "no peak in tolerance window".

    When ``peaks`` is empty (or omitted), the leaf fails, and its summary
    points the author at the leaf's ``peaks`` list and at the reference's
    peaks marked on the spectrum plot — the declared-peaks contract is
    explicit by design.

    The top-N-by-amplitude algorithm of the pre-D75 implementation is
    retained as ``_find_top_n_peaks`` — it finds those marked peaks, so an
    author who doesn't yet know their peak frequencies can take them
    from the reference spectrum. Pass/fail logic no longer calls it.
    """
    # The actual-side FFT is always required — without it we can't match
    # any peaks. The reference-side FFT is only required when no peaks
    # are declared (the failure then marks the ref spectrum's top-N on
    # its plot). With declared peaks, an empty reference is OK
    # (baseline-free path, idea #59 / D83).
    if len(act_values) < 4:
        # Nothing to take a spectrum of: not scored, with the reason where
        # every surface reads one (``diagnostics["error"]``, the mode
        # contract) — under ``reason`` the summary said "No peaks declared".
        return _no_data_failure(
            "dominant-frequency", "signal too short for FFT (need >=4 samples)"
        )

    has_ref = len(ref_values) >= 4
    if not has_ref and not peaks:
        # No peaks declared, and no baseline to take a spectrum of (an empty
        # one, or a window that holds none of it): nothing to compare — not
        # scored, as D163 has it, where it failed as "No peaks declared".
        return _no_data_failure(
            "dominant-frequency",
            "no reference samples to take a spectrum of (empty baseline or window)",
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

    peaks = peaks or []
    if not peaks:
        return VariableComparison(
            index=0,
            name="",
            passed=False,
            nrmse=float("inf"),
            mode="dominant-frequency",
            diagnostics={"paired_peaks": []},
            geometry=_frequency_geometry(
                ref_freqs,
                ref_spectrum,
                act_freqs,
                act_spectrum,
                detected_ref_peaks,
                [],
                [],
            ),
        )

    paired: list[dict] = []
    all_passed = True
    max_rel_err = 0.0
    # Each declared peak must claim a DISTINCT actual peak (review
    # 2026-07-06: without claiming, a two-resonance system regressing to
    # one resonance passed both declared peaks against the single
    # surviving peak). The amplitude floor keeps the claiming from
    # falling through to machine-noise sidelobes: a "peak" below
    # _PEAK_MIN_REL_AMPLITUDE of the spectrum maximum is not a mode.
    claimed_hz: set[float] = set()
    amp_floor = (
        _PEAK_MIN_REL_AMPLITUDE * float(np.max(act_spectrum))
        if len(act_spectrum)
        else 0.0
    )
    for i, declared in enumerate(peaks):
        f_decl = float(declared.get("freq", 0.0))
        lo, hi, tol, mode = _peak_window(declared, i)

        match = _find_strongest_peak_in_window(
            act_freqs,
            act_spectrum,
            lo,
            hi,
            exclude=claimed_hz,
            min_amplitude=amp_floor,
        )
        if match is not None:
            claimed_hz.add(match[0])
        if match is None:
            paired.append(
                {
                    "declared_hz": f_decl,
                    "matched_hz": None,
                    "delta": None,
                    "passed": False,
                    "tolerance": as_written(tol),
                    "tolerance_mode": mode,
                    "reason": "no peak in tolerance window",
                }
            )
            all_passed = False
            max_rel_err = float("inf")
        else:
            matched_hz, _amp = match
            delta = abs(matched_hz - f_decl)
            rel_err = delta / f_decl if f_decl > 0 else delta
            if rel_err > max_rel_err:
                max_rel_err = rel_err
            paired.append(
                {
                    "declared_hz": f_decl,
                    "matched_hz": matched_hz,
                    "delta": delta,
                    "passed": True,  # match-in-window guarantees pass for this peak
                    "tolerance": as_written(tol),
                    "tolerance_mode": mode,
                }
            )

    return VariableComparison(
        index=0,
        name="",
        passed=all_passed,
        nrmse=max_rel_err,  # repurposed as "max relative error across declared peaks"
        mode="dominant-frequency",
        diagnostics={
            "paired_peaks": paired,
        },
        geometry=_frequency_geometry(
            ref_freqs,
            ref_spectrum,
            act_freqs,
            act_spectrum,
            detected_ref_peaks,
            peaks,
            paired,
        ),
    )
