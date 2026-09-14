"""The inverse of each mode's handles: a dragged position back to a raw leaf.

A mode emits its editable points as ``kind="handle"`` primitives whose ids
the evaluator prefixes with the leaf's RFC 6902 path. An editor here takes
the raw leaf (as the spec's ``metrics`` holds it), the handle's id WITHOUT
that path (its ``local``: ``"tube/upper/3"``) and the position it was
dragged to, and returns the leaf the drag means. It is attached to the
mode's :class:`~.registry.MetricDescriptor` as ``edit_handle``, beside
``summarize``; a third-party metric supplies its own.

Every editor deep-copies the leaf and returns the copy — the input is not
mutated — and refuses with :class:`HandleEditError`, naming the handle and
the reason, rather than return a leaf the drag cannot mean: a non-finite
or non-numeric position, a local it does not name, or a rule below broken. The locals are
the ids the modes emit (``algorithms._tube_geometry`` and its siblings).
Every number an editor writes is rounded to 12 significant digits
(:func:`_written`).
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import numpy as np

from .types import _EPS


@dataclass(frozen=True, kw_only=True)
class HandleEdit:
    """One drag, as an editor receives it."""

    #: The raw leaf, as ``metrics`` holds it; never mutated.
    leaf: dict
    #: The handle's id without its leaf path: ``"tube/upper/3"``.
    local: str
    #: The dragged position; ``None`` where that axis did not move.
    x: float | None
    y: float | None
    #: The primary baseline ``(time, values)``, full resolution — unwindowed.
    reference: tuple[np.ndarray, np.ndarray] | None


class HandleEditError(ValueError):
    """A drag the leaf cannot take; the message names the handle and why."""


def _refuse(edit: HandleEdit, reason: str) -> HandleEditError:
    return HandleEditError(f"{edit.local}: {reason}")


def _unnamed(edit: HandleEdit) -> HandleEditError:
    return HandleEditError(f"{edit.local} cannot be moved on the plot")


def _num(value: float) -> str:
    """A number for a refusal message, to 12 significant digits: enough
    that a value just outside a bound never prints as a value on it (``:g``
    prints ``2.0000001`` as ``2``), few enough that float noise from the
    width arithmetic does not (``repr`` prints ``-1.5270000000000001``)."""
    return f"{float(value):.12g}"


def _written(value: float) -> float:
    """A number an editor WRITES into the leaf, to 12 significant digits —
    the precision :func:`_num` prints: the arithmetic from a drag's position
    (``0.8 - 0.5``) otherwise lands ``0.30000000000000004`` in the spec file
    the author reads and version-controls."""
    return float(f"{float(value):.12g}")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_position(edit: HandleEdit) -> None:
    for axis, value in (("x", edit.x), ("y", edit.y)):
        if value is None:
            continue
        if not _is_number(value):
            raise _refuse(edit, f"{axis} = {value!r} is not a number")
        if not math.isfinite(value):
            raise _refuse(edit, f"{axis} = {value!r} is not a finite number")


def _authored_number(edit: HandleEdit, value: object, what: str) -> float:
    """A number the leaf's author wrote, or a refusal naming it."""
    if not _is_number(value):
        raise _refuse(edit, f"{what} is {value!r}, not a number")
    return float(value)  # type: ignore[arg-type]


def _parse(edit: HandleEdit, family: str, parts: int) -> list[str]:
    """Split ``edit.local`` into ``parts`` segments under ``family``, or
    refuse it as a local this editor does not name."""
    segments = edit.local.split("/")
    if len(segments) != parts or segments[0] != family:
        raise _unnamed(edit)
    return segments


def _index(edit: HandleEdit, segment: str, rows: object) -> int:
    """The list index a local's last segment addresses in ``rows``: a
    canonical non-negative integer naming an existing dict entry. ASCII
    digits only — ``str.isdigit`` also accepts ``"²"``, which ``int``
    refuses."""
    if not (segment.isascii() and segment.isdigit()) or str(int(segment)) != segment:
        raise _unnamed(edit)
    i = int(segment)
    if not isinstance(rows, list) or i >= len(rows) or not isinstance(rows[i], dict):
        raise _refuse(edit, f"there is no entry {i} to move")
    return i


def _reference(edit: HandleEdit) -> tuple[np.ndarray, np.ndarray] | None:
    """``edit.reference``, or ``None`` when either array is empty: an empty
    baseline has nothing to measure from, and reading it as one would reach
    numpy's own errors (``np.interp`` on no samples) instead of a refusal."""
    if edit.reference is None:
        return None
    ref_time, ref_values = edit.reference
    if len(ref_time) == 0 or len(ref_values) == 0:
        return None
    return edit.reference


def _time_move(edit: HandleEdit, t: float) -> float | None:
    """``edit.x`` as a time move from the handle's current time ``t``, or
    ``None`` when it is not one. The ring always sends its ``x``, and a
    vertical drag sends it back off ``t`` by the data→pixel→data round trip;
    read as a move, that offset was clamped (moving a value checkpoint drawn
    outside the span) or refused (an ``against``-other leaf, a time shared
    with another point), so a vertical drag changed or lost a time. Decided
    before any clamp, ``against`` or shared-time check.

    The tolerance is pixel-scale, not float noise: a vertical drag in a real
    browser on a TRANSFORM baseline (time 0..10, 2000 samples, ~800 px plot)
    sent ``4.99998293759`` for a point at 5 — about 1.7e-6 of the span, far
    above ``1e-9``. ``1e-5`` of the reference's time span is well under one
    pixel at default zoom and still under one at 100x zoom, so no real drag
    falls inside it; the float-noise term remains the floor, and is all
    there is with no reference."""
    if edit.x is None:
        return None
    tol = 1e-9 * max(1.0, abs(t))
    reference = _reference(edit)
    if reference is not None:
        ref_time = reference[0]
        tol = max(tol, 1e-5 * float(ref_time[-1] - ref_time[0]))
    if abs(edit.x - t) <= tol:
        return None
    return float(edit.x)


def _refuse_other_baseline(edit: HandleEdit, leaf: dict, what: str) -> None:
    """A leaf scored against a named baseline takes its bounds from that
    baseline; the plot's reference is the primary one, so ``what`` would be
    measured from the wrong curve."""
    against = leaf.get("against", "primary")
    if against != "primary":
        raise _refuse(
            edit,
            f"this leaf scores against {against!r}; the plot's reference is "
            f"the primary baseline, so {what} cannot be dragged",
        )


def _time_span(
    edit: HandleEdit, leaf: dict, reference: tuple[np.ndarray, np.ndarray] | None
) -> tuple[float, float]:
    """Where the forward geometry draws a time handle: the evaluator slices
    the reference to the samples inside the leaf's ``window`` (inclusive),
    so the span runs from the first reference sample ``>= window.start`` to
    the last ``<= window.end``, either end absent meaning the reference's
    own. With no reference, only the window's own ends apply."""
    window = leaf.get("window") or {}
    start = window.get("start")
    end = window.get("end")
    if start is not None:
        start = _authored_number(edit, start, "window.start")
    if end is not None:
        end = _authored_number(edit, end, "window.end")
    if reference is None:
        lo = -math.inf if start is None else start
        hi = math.inf if end is None else end
        return lo, hi
    ref_time = np.asarray(reference[0], dtype=float)
    mask = np.ones(len(ref_time), dtype=bool)
    if start is not None:
        mask &= ref_time >= start
    if end is not None:
        mask &= ref_time <= end
    inside = ref_time[mask]
    if len(inside) == 0:
        raise _refuse(edit, "the leaf's window holds no reference sample")
    return float(inside[0]), float(inside[-1])


def _clamped_time(
    edit: HandleEdit,
    leaf: dict,
    x: float,
    reference: tuple[np.ndarray, np.ndarray] | None,
) -> float:
    """``x`` clamped into :func:`_time_span` over ``reference`` (``None``:
    the window's own ends alone). Clamped, not refused: a real drag past the
    span's edge lands on the edge, where the handle is still drawn."""
    lo, hi = _time_span(edit, leaf, reference)
    return float(min(max(float(x), lo), hi))


# -- tube --------------------------------------------------------------------


def edit_tube_handle(edit: HandleEdit) -> dict:
    """``tube/upper/i`` and ``tube/lower/i`` → ``tube_points[i]``.

    The index addresses ``tube_points`` in authored order, as
    ``algorithms._tube_geometry`` enumerates it. ``x`` sets the point's
    ``time`` — unless it lies within a sub-pixel tolerance of the current
    time, which is no time move at all (:func:`_time_move`) — clamped into the span
    where the handle is drawn (the first reference sample
    ``>= window.start`` to the last ``<= window.end``, either end absent
    meaning the reference's own; with no reference, the window's ends
    alone). ``y`` sets that side's width at the point's (new)
    time ``t``, where ``ref(t)`` interpolates the reference:

    * ``abs`` — the value itself; refused when upper would fall below the
      point's lower, or lower above its upper (a missing side reads as
      ``0.0``, as ``_interpolate_tube_widths`` reads it). Needs no reference.
    * ``band`` — ``y − ref(t)`` for upper, ``ref(t) − y`` for lower.
    * ``rel`` — the band width divided by ``|ref(t)|``; refused where the
      reference is zero (below ``types._EPS``).

    ``band`` and ``rel`` refuse a negative width, a missing reference, and a
    leaf whose ``against`` is not ``"primary"`` (the view's reference is the
    primary one); ``abs`` refuses only a time drag on such a leaf (the span
    would come from the wrong reference). A point sharing its time with
    another is refused, and so is a time drag landing on another point's
    time: the forward interpolation reads the later point there, so the
    handle would not follow the edit. A refusal after a clamp says so,
    naming the clamped time. A width under ``tube_min_width`` is
    written as dragged; the band then draws at the minimum, so the handle
    lands on the band's real edge.
    """
    _check_position(edit)
    _, side, segment = _parse(edit, "tube", 3)
    if side not in ("upper", "lower"):
        raise _unnamed(edit)
    leaf = copy.deepcopy(edit.leaf)
    i = _index(edit, segment, leaf.get("tube_points"))
    rows: list = leaf["tube_points"]
    point = rows[i]
    mode = leaf.get("tube_width_mode")
    if mode not in ("abs", "band", "rel"):
        raise _refuse(edit, f"tube_width_mode {mode!r} has no draggable widths")
    reference = _reference(edit)
    if mode != "abs":
        _refuse_other_baseline(edit, leaf, "its widths")
        if reference is None:
            raise _refuse(edit, f"a {mode} tube needs a reference to measure from")

    t = _authored_number(edit, point.get("time"), f"tube_points[{i}].time")
    _refuse_shared_time(edit, rows, i, t)
    x = _time_move(edit, t)
    if x is None:
        return _set_tube_width(edit, leaf, point, i, side, mode, t, reference)
    _refuse_other_baseline(edit, leaf, "its time")
    t = _written(_clamped_time(edit, leaf, x, reference))
    try:
        _refuse_shared_time(edit, rows, i, t)
        point["time"] = t
        return _set_tube_width(edit, leaf, point, i, side, mode, t, reference)
    except HandleEditError as err:
        if t == _written(x):
            raise
        raise HandleEditError(
            f"{err} (x={_num(x)} was clamped to t={_num(t)})"
        ) from None


def _set_tube_width(
    edit: HandleEdit,
    leaf: dict,
    point: dict,
    i: int,
    side: str,
    mode: str,
    t: float,
    reference: tuple[np.ndarray, np.ndarray] | None,
) -> dict:
    """``edit.y`` written as ``point``'s ``side`` width at time ``t``."""
    if edit.y is None:
        return leaf
    y = float(edit.y)
    if mode == "abs":
        if side == "upper":
            lower = _authored_number(
                edit, point.get("lower", 0.0), f"tube_points[{i}].lower"
            )
            if y < lower:
                raise _refuse(
                    edit, f"upper {_num(y)} would fall below lower {_num(lower)}"
                )
        else:
            upper = _authored_number(
                edit, point.get("upper", 0.0), f"tube_points[{i}].upper"
            )
            if y > upper:
                raise _refuse(
                    edit, f"lower {_num(y)} would rise above upper {_num(upper)}"
                )
        point[side] = _written(y)
        return leaf
    assert reference is not None  # refused above for band / rel
    ref_t = float(np.interp(t, reference[0], reference[1]))
    width = y - ref_t if side == "upper" else ref_t - y
    if width < 0:
        raise _refuse(edit, f"{side} width {_num(width)} would be negative")
    if mode == "rel":
        if abs(ref_t) < _EPS:
            raise _refuse(edit, f"the reference is zero at t={_num(t)}")
        width = width / abs(ref_t)
    point[side] = _written(width)
    return leaf


def _refuse_shared_time(edit: HandleEdit, rows: list, i: int, t: float) -> None:
    """Refuse when another control point sits at ``t``."""
    for j, other in enumerate(rows):
        if j != i and isinstance(other, dict) and other.get("time") == t:
            a, b = sorted((i, j))
            raise _refuse(
                edit,
                f"control points {a} and {b} share time {_num(t)}; "
                "separate them in the table",
            )


# -- range / scalar bounds -----------------------------------------------------


def edit_range_handle(edit: HandleEdit) -> dict:
    """``range/min`` and ``range/max`` → ``min_value`` / ``max_value``, from
    ``y``; ``x`` is ignored. Refused when the result has ``min_value >
    max_value``; equal bounds are accepted."""
    return _edit_bounds(edit, strict=False)


def edit_scalar_handle(edit: HandleEdit) -> dict:
    """``scalar/min`` and ``scalar/max`` → ``min_value`` / ``max_value``, from
    ``y``; ``x`` is ignored. Refused when the result has ``min_value >=
    max_value`` — ``modes._validate_scalar_config`` requires them strictly
    ordered."""
    return _edit_bounds(edit, strict=True)


def _edit_bounds(edit: HandleEdit, *, strict: bool) -> dict:
    """The shared rule; ``<metric>`` in the local id is the leaf's own
    metric."""
    _check_position(edit)
    family = edit.leaf.get("metric")
    if not isinstance(family, str):
        raise _unnamed(edit)
    _, which = _parse(edit, family, 2)
    if which not in ("min", "max"):
        raise _unnamed(edit)
    leaf = copy.deepcopy(edit.leaf)
    if edit.y is None:
        return leaf
    leaf[f"{which}_value"] = _written(edit.y)
    lo, hi = leaf.get("min_value"), leaf.get("max_value")
    if lo is not None and hi is not None:
        lo = _authored_number(edit, lo, "min_value")
        hi = _authored_number(edit, hi, "max_value")
        if lo > hi:
            raise _refuse(
                edit, f"min_value {_num(lo)} would exceed max_value {_num(hi)}"
            )
        if strict and lo == hi:
            raise _refuse(
                edit,
                f"min_value {_num(lo)} would equal max_value {_num(hi)}; "
                "a scalar leaf needs min_value < max_value",
            )
    return leaf


# -- points --------------------------------------------------------------------


def edit_points_handle(edit: HandleEdit) -> dict:
    """``points/target/i`` → ``points[i]``.

    ``x`` sets ``time`` — unless it lies within a sub-pixel tolerance of the
    current time (:func:`_time_move`) — and is refused on a leaf whose ``against``
    is not ``"primary"``. The time is clamped: for a checkpoint that follows
    the baseline, into the span where it is drawn (the tube rule: the first
    reference sample ``>= window.start`` to the last ``<= window.end``); for
    one with an explicit ``value``, only into the window's own ends — the
    forward code draws it wherever ``t ± time_tolerance`` meets the actual
    trajectory, which the editor does not have. A checkpoint whose ``time``
    is absent or null — the trace-end sentinel — keeps it: ``x`` is ignored,
    a time is typed in the table. ``y`` sets ``value`` only when the point
    has a non-null one — ``point.get("value") is not None``, the forward
    code's test in ``algorithms._compare_points`` — so a checkpoint that
    follows the baseline (``value`` absent or null) moves in time only and a
    drag never makes it baseline-free. ``points/target/final`` (the final-value check
    of an empty list) is refused: there is no declared point to move."""
    _check_position(edit)
    _, role, segment = _parse(edit, "points", 3)
    if role != "target":
        raise _unnamed(edit)
    if segment == "final":
        raise _refuse(edit, "there is no declared point to move (final-value check)")
    leaf = copy.deepcopy(edit.leaf)
    i = _index(edit, segment, leaf.get("points"))
    point = leaf["points"][i]
    time = point.get("time")
    x = None
    if time is not None:
        x = _time_move(edit, _authored_number(edit, time, f"points[{i}].time"))
    if x is not None:
        _refuse_other_baseline(edit, leaf, "its time")
        follows_baseline = point.get("value") is None
        reference = _reference(edit) if follows_baseline else None
        point["time"] = _written(_clamped_time(edit, leaf, x, reference))
    if edit.y is not None and point.get("value") is not None:
        point["value"] = _written(edit.y)
    return leaf


# -- dominant-frequency --------------------------------------------------------


def edit_peaks_handle(edit: HandleEdit) -> dict:
    """``peaks/declared/i`` → ``peaks[i]["freq"]`` from ``x``, refused
    ``<= 0``; ``y`` (the spectrum's amplitude) is ignored."""
    _check_position(edit)
    _, role, segment = _parse(edit, "peaks", 3)
    if role != "declared":
        raise _unnamed(edit)
    leaf = copy.deepcopy(edit.leaf)
    i = _index(edit, segment, leaf.get("peaks"))
    if edit.x is not None:
        if edit.x <= 0:
            raise _refuse(edit, f"frequency {_num(edit.x)} must be positive")
        leaf["peaks"][i]["freq"] = _written(edit.x)
    return leaf
