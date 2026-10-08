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
(:func:`_written`) — after it is SNAPPED, where it is a value the pointer
placed and the caller says what its axis showed (:func:`_placed`, D149).

A ``kind="grip"`` primitive is one SIDE of a tolerance, and its editor
writes that side through :func:`_set_side`: the value is the distance from
the target to the dragged edge — in ``rel``, over the target's magnitude —
and the holder is the row's own tolerance where the row has one, else the
criterion's. A grip moves on ONE axis; the other reaches the editor as the
grip's own resolved position, never as something to write.

Two more inverses stand beside the editors (D149). :func:`handle_fields`
says which FIELD of the leaf a handle writes on each axis it moves on —
what "arm a field, then click the plot" finds a field's handle by. And a
PLACER takes a Ctrl+click (:class:`RowPlace`) to the leaf with one more row
in its table, settled on the data — a checkpoint on the reference, an event
on the nearest actual event, a peak on the nearest spectral peak, a tube
control point on the tube as it stands — attached to the descriptor as
``place_row``.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .algorithms import (
    _PEAK_MIN_REL_AMPLITUDE,
    _find_event_boundaries,
    _interpolate_tube_widths,
    _peak_window,
)
from .geometry import Primitive, Series, VLine
from .sides import parse_sides
from .snap import snapped
from .types import _EPS


@dataclass(frozen=True, kw_only=True)
class HandleEdit:
    """One drag, as an editor receives it."""

    #: The raw leaf, as ``metrics`` holds it; never mutated.
    leaf: dict
    #: The handle's id without its leaf path: ``"tube/upper/3"``.
    local: str
    #: The dragged position; ``None`` where that axis did not move. For a
    #: grip — which moves on one axis — the other axis carries where the
    #: grip stands (a constant tube's side is measured from the reference
    #: at the grip's own time).
    x: float | None
    y: float | None
    #: The primary baseline ``(time, values)``, full resolution — unwindowed.
    reference: tuple[np.ndarray, np.ndarray] | None
    #: The number a tolerance side is measured from where the leaf's own
    #: geometry holds it: a scalar leaf's baseline scalar (its
    #: ``scalar/reference`` line). ``None`` where there is none.
    target: float | None = None
    #: The ACTUAL series' first and last time as the scorer receives it —
    #: sliced to the leaf's ``window`` — which bounds the time of a
    #: checkpoint (:func:`edit_points_handle`): it is scored against the
    #: run, with a value of its own or the baseline's. ``None`` where the
    #: caller has no actual series: no such bound.
    span: tuple[float, float] | None = None
    #: What the x and the y axis the position was read on SHOWED — each
    #: one's visible span, the page's, as it stood at the press or the
    #: click: what a placed value is snapped by (:func:`_placed`). ``None``
    #: for an axis whose span the caller does not know: nothing placed on
    #: it is snapped.
    visible: tuple[float | None, float | None] = (None, None)


class HandleEditError(ValueError):
    """A drag the leaf cannot take; the message names the handle and why,
    ``reason`` only why — the tool shows it in the open criterion's editor,
    which already says which handle it is."""

    def __init__(self, message: str, *, reason: str | None = None):
        self.reason = reason if reason is not None else message
        super().__init__(message)


def _refuse(edit: HandleEdit, reason: str) -> HandleEditError:
    return HandleEditError(f"{edit.local}: {reason}", reason=reason)


def _unnamed(edit: HandleEdit) -> HandleEditError:
    return HandleEditError(
        f"{edit.local} cannot be moved on the plot",
        reason="this cannot be moved on the plot",
    )


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


def _placed(
    edit: HandleEdit, axis: str, value: float, *, fraction: bool = False
) -> float:
    """A number the POINTER placed, as an editor writes it: a dragged or
    clicked coordinate on ``axis``, or a tolerance side measured from one
    — snapped to a readable step of what that axis showed
    (:func:`~.snap.snapped`; a ``fraction`` of its target to 3 significant
    digits), then written as every number is (:func:`_written`). Snapped
    FIRST: every clamp and refusal reads the value that would be written.
    A caller that names no span (``edit.visible``) is no pointer, and gets
    the 12 digits alone."""
    span = edit.visible[0 if axis == "x" else 1]
    return _written(snapped(float(value), span, fraction=fraction))


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
    if reference is None:
        start, end = _window_ends(edit, leaf)
        lo = -math.inf if start is None else start
        hi = math.inf if end is None else end
        return lo, hi
    inside, _ = _windowed(edit, leaf, reference)
    return float(inside[0]), float(inside[-1])


def _window_ends(edit: HandleEdit, leaf: dict) -> tuple[float | None, float | None]:
    window = leaf.get("window") or {}
    start = window.get("start")
    end = window.get("end")
    if start is not None:
        start = _authored_number(edit, start, "window.start")
    if end is not None:
        end = _authored_number(edit, end, "window.end")
    return start, end


def _windowed(
    edit: HandleEdit, leaf: dict, reference: tuple[np.ndarray, np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """``reference`` as the SCORER receives it: the evaluator slices it to
    the samples inside the leaf's ``window`` (inclusive) first, so whatever
    a mode reads off the reference itself — its final value, its events —
    it reads off this. Refused where the window holds no sample."""
    start, end = _window_ends(edit, leaf)
    ref_time = np.asarray(reference[0], dtype=float)
    mask = np.ones(len(ref_time), dtype=bool)
    if start is not None:
        mask &= ref_time >= start
    if end is not None:
        mask &= ref_time <= end
    if not mask.any():
        raise _refuse(edit, "the leaf's window holds no reference sample")
    return ref_time[mask], np.asarray(reference[1], dtype=float)[mask]


def _measured_reference(
    edit: HandleEdit, leaf: dict, what: str
) -> tuple[np.ndarray, np.ndarray]:
    """The primary baseline a tolerance side is measured from, as the
    scorer receives it (:func:`_windowed`) — refused on a leaf scored
    against another baseline, and where there is none."""
    _refuse_other_baseline(edit, leaf, what)
    reference = _reference(edit)
    if reference is None:
        raise _refuse(edit, f"{what} needs a reference to measure from")
    return _windowed(edit, leaf, reference)


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


def _fraction(edit: HandleEdit, width: float, of: float, zero: str) -> float:
    """``width`` as a fraction of ``|of|`` — a ``rel`` width back from a
    dragged edge, the ONE rule for a tube's and a checkpoint's. Refused
    (``zero`` says of what) where the magnitude is under ``types._EPS``,
    not only at exactly 0: a checkpoint's target of 1e-20 divided, and the
    drag wrote a tolerance of 5e18."""
    if abs(of) < _EPS:
        raise _refuse(edit, zero)
    return width / abs(of)


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
    if segment == "const":  # before `_index`: it is no index
        return _edit_constant_tube(edit, leaf, side)
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
    x = _placed(edit, "x", x)
    t = _written(_clamped_time(edit, leaf, x, reference))
    try:
        _refuse_shared_time(edit, rows, i, t)
        point["time"] = t
        return _set_tube_width(edit, leaf, point, i, side, mode, t, reference)
    except HandleEditError as err:
        if t == x:
            raise
        clamp = f"(x={_num(x)} was clamped to t={_num(t)})"
        raise HandleEditError(
            f"{err} {clamp}", reason=f"{err.reason} {clamp}"
        ) from None


def _edit_constant_tube(edit: HandleEdit, leaf: dict, side: str) -> dict:
    """``tube/upper/const`` and ``tube/lower/const`` — the grips of a tube
    of constant width — → that side of ``tube_abs`` (``band``) or
    ``tube_rel`` (``rel``), by :func:`_set_side`: ``y − ref(x)`` for upper,
    ``ref(x) − y`` for lower, ``x`` being where the grip stands; in ``rel``
    over ``|ref(x)|``, refused where the reference is zero there (below
    ``types._EPS``, the control points' rule). A tube with control points
    has no such grip: its widths are theirs."""
    if leaf.get("tube_points"):
        raise _unnamed(edit)
    mode = leaf.get("tube_width_mode")
    if mode not in ("band", "rel"):
        raise _refuse(edit, f"tube_width_mode {mode!r} has no constant width")
    ref_time, ref_values = _measured_reference(edit, leaf, "its width")
    if edit.y is None or edit.x is None:
        return leaf
    ref_x = float(np.interp(float(edit.x), ref_time, ref_values))
    width = edit.y - ref_x if side == "upper" else ref_x - edit.y
    if mode == "rel":
        zero = f"the reference is zero at t={_num(edit.x)}"
        width = _fraction(edit, width, ref_x, zero)
    key = "tube_abs" if mode == "band" else "tube_rel"
    _set_side(edit, leaf, key, side, width, axis="y", fraction=mode == "rel")
    return leaf


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
    if mode == "abs":
        y = _placed(edit, "y", edit.y)
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
        point[side] = y
        return leaf
    assert reference is not None  # refused above for band / rel
    ref_t = float(np.interp(t, reference[0], reference[1]))
    width = edit.y - ref_t if side == "upper" else ref_t - edit.y
    if mode == "band":
        width = _placed(edit, "y", width)
    if width < 0:
        raise _refuse(edit, f"{side} width {_num(width)} would be negative")
    if mode == "rel":
        width = _fraction(edit, width, ref_t, f"the reference is zero at t={_num(t)}")
        width = _placed(edit, "y", width, fraction=True)
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
    ordered.

    ``scalar/upper`` and ``scalar/lower`` — the two lines of what the
    baseline form allows — → that side of the leaf's ``tolerance``, from
    ``y`` measured against the baseline's scalar (``edit.target``):
    ``y − target`` for upper, ``target − y`` for lower, divided by
    ``|target|`` in ``rel`` (refused where the baseline scalar is zero, or
    where there is none). The side is written by :func:`_set_side`."""
    segments = edit.local.split("/")
    if len(segments) == 2 and segments[1] in ("upper", "lower"):
        return _edit_scalar_side(edit, segments[1])
    return _edit_bounds(edit, strict=True)


def _edit_scalar_side(edit: HandleEdit, side: str) -> dict:
    _check_position(edit)
    _parse(edit, "scalar", 2)
    leaf = copy.deepcopy(edit.leaf)
    if edit.y is None:
        return leaf
    if edit.target is None:
        raise _refuse(edit, "there is no baseline scalar to measure from")
    target = float(edit.target)
    value = edit.y - target if side == "upper" else target - edit.y
    if leaf.get("tolerance_mode", "abs") == "rel":
        # The scorer's condition (`_compare_scalar`'s ``abs_fallback``):
        # only at exactly 0 does it leave the fraction for the absolute
        # test. A tiny baseline is scored, and drawn, as a fraction of it.
        if abs(target) == 0.0:
            raise _refuse(edit, "the baseline scalar is zero")
        value = value / abs(target)
    rel = leaf.get("tolerance_mode", "abs") == "rel"
    _set_side(edit, leaf, "tolerance", side, value, axis="y", fraction=rel)
    return leaf


def _set_side(
    edit: HandleEdit,
    holder: dict,
    key: str,
    side: str,
    value: float,
    *,
    axis: str,
    fraction: bool = False,
    named: str | None = None,
    crosses: str = "target",
) -> None:
    """Write ``value`` as the ``side`` (``"lower"`` / ``"upper"``) of the
    tolerance ``holder[key]`` — the one rule for a dragged tolerance side.

    The shape in the spec is the link state: where ``holder[key]`` is
    absent or a number the result is the NUMBER ``value`` (linked: both
    sides move); where it is a pair only ``side`` changes, and it stays a
    pair. A negative side — one dragged across its target — is refused, in
    the side's own name as its editor's fields say it (``named``: a time
    side is ``before`` or ``early``, not ``lower``) and naming what it
    would cross (``crosses``); a zero one is not. A tolerance the scorer
    would refuse is refused in the scorer's words, and one that is no
    finite number — a rel side over a tiny baseline overflows — is refused
    before it is written.

    ``value`` is a side the pointer placed, measured along ``axis`` — a
    ``fraction`` of its target where the tolerance is relative: it is
    snapped (:func:`_placed`) before anything here reads it."""
    if not math.isfinite(value):
        raise _refuse(edit, "the tolerance would not be a finite number")
    value = _placed(edit, axis, value, fraction=fraction)
    if value < 0:
        raise _refuse(edit, f"the {named or side} side would cross its {crosses}")
    current = holder.get(key)
    if current is None:
        holder[key] = _written(value)
        return
    try:
        sides = parse_sides(current, key)
    except ValueError as err:
        raise _refuse(edit, str(err)) from None
    if isinstance(current, dict):
        pair = {"lower": sides.lower, "upper": sides.upper}
        pair[side] = _written(value)
        holder[key] = pair
    else:
        holder[key] = _written(value)


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
    leaf[f"{which}_value"] = _placed(edit, "y", edit.y)
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
    the baseline, into the span where it has a target (the tube rule: the
    first reference sample ``>= window.start`` to the last ``<=
    window.end``); for one with an explicit ``value``, into the window's
    own ends; and for either, into the actual trajectory's span
    (``edit.span``) besides — the forward code scores and draws a
    checkpoint only where ``t ± time_tolerance`` meets that trajectory, so
    a ring let go a little past the run's end (the time axis runs past the
    data; a run shorter than its baseline ends well inside it) read "not
    scored" and was no longer drawn. So a checkpoint is held to where its
    own span and the run's OVERLAP — a single time where they only touch;
    where they do not (a run that never meets its baseline) to its own
    alone, as with no ``span`` (a caller that has no actual series). A
    checkpoint whose ``time``
    is absent or null — the trace-end sentinel — keeps it: ``x`` is ignored,
    a time is typed in the table. ``y`` sets ``value`` only when the point
    has a non-null one — ``point.get("value") is not None``, the forward
    code's test in ``algorithms._compare_points`` — so a checkpoint that
    follows the baseline (``value`` absent or null) moves in time only and a
    drag never makes it baseline-free. ``points/target/final`` (the final-value check
    of an empty list) is refused: there is no declared point to move.

    ``points/upper|lower|before|after/i`` and ``points/upper|lower/final``
    are the sides of a tolerance: :func:`_edit_points_side`."""
    _check_position(edit)
    _, role, segment = _parse(edit, "points", 3)
    if role in _POINT_SIDES:
        return _edit_points_side(edit, role, segment)
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
        follows = point.get("value") is None
        point["time"] = _checkpoint_time(edit, leaf, _placed(edit, "x", x), follows)
    if edit.y is not None and point.get("value") is not None:
        point["value"] = _placed(edit, "y", edit.y)
    return leaf


def _checkpoint_time(
    edit: HandleEdit, leaf: dict, x: float, follows_baseline: bool
) -> float:
    """``x`` as a checkpoint's time: held to where the checkpoint is scored
    and drawn (:func:`edit_points_handle` says where), then written."""
    reference = _reference(edit) if follows_baseline else None
    lo, hi = _time_span(edit, leaf, reference)
    t = float(min(max(float(x), lo), hi))
    if edit.span is not None:
        first, last = edit.span
        if max(lo, first) <= min(hi, last):
            t = min(max(t, first), last)
    return _written(t)


#: A point's grips: the side each writes, and of which tolerance.
_POINT_SIDES = {
    "upper": ("upper", "tolerance"),
    "lower": ("lower", "tolerance"),
    "before": ("lower", "time_tolerance"),
    "after": ("upper", "time_tolerance"),
}


def _edit_points_side(edit: HandleEdit, role: str, segment: str) -> dict:
    """One side of a checkpoint's tolerance, by :func:`_set_side`.

    ``upper`` / ``lower`` → the value tolerance the row is held to: its own
    ``tolerance`` where the row has one (a ``null`` is an absent key, as
    the scorer reads it), else the CRITERION's — so every row that defers
    to it moves with it. That holds while every deferring row reads it in
    the dragged row's ``tolerance_mode`` (:func:`_deferring_modes`). Where
    they differ — a ``rel`` row and an ``abs`` row both defer — the number
    dragged means a fraction to one and signal units to the other (0.634
    of a target became ±0.634), so the dragged ROW takes a tolerance of
    its own, seeded from the criterion's, and the criterion's stays what
    the other rows read. From ``y``: ``y − target`` for upper, ``target −
    y`` for lower, over ``|target|`` where the row's ``tolerance_mode`` is
    ``rel`` (refused at a target of zero — below ``types._EPS``, the
    tube's rule: :func:`_fraction`). The target is the row's
    ``value``, else the reference at the row's time — refused on a leaf
    scored against another baseline, and with no reference.

    ``before`` / ``after`` → the row's ``time_tolerance``, from ``x``:
    ``time − x`` and ``x − time``. Only a row that holds one has these.

    ``upper/final`` / ``lower/final`` — the final-value check of an empty
    list — → the criterion's ``tolerance``, measured from the reference's
    final value AS WINDOWED (the scorer's)."""
    side, key = _POINT_SIDES[role]
    leaf = copy.deepcopy(edit.leaf)
    if segment == "final":
        if key != "tolerance":
            raise _unnamed(edit)
        _, ref_values = _measured_reference(edit, leaf, "the final value's tolerance")
        if edit.y is None:
            return leaf
        target = float(ref_values[-1])
        value = edit.y - target if side == "upper" else target - edit.y
        _set_side(edit, leaf, "tolerance", side, value, axis="y")
        return leaf
    i = _index(edit, segment, leaf.get("points"))
    point = leaf["points"][i]
    if key == "time_tolerance":
        if point.get("time_tolerance") is None:
            raise _refuse(edit, f"points[{i}] holds no time_tolerance")
        if edit.x is None:
            return leaf
        t = _point_time(edit, leaf, point, i)
        value = t - edit.x if side == "lower" else edit.x - t
        _set_side(
            edit,
            point,
            "time_tolerance",
            side,
            value,
            axis="x",
            named=role,
            crosses="point's time",
        )
        return leaf
    if point.get("value") is not None:
        target = _authored_number(edit, point["value"], f"points[{i}].value")
    else:
        ref_time, ref_values = _measured_reference(edit, leaf, "its tolerance")
        t = _point_time(edit, leaf, point, i)
        target = float(np.interp(t, ref_time, ref_values))
    if edit.y is None:
        return leaf
    value = edit.y - target if side == "upper" else target - edit.y
    rel = point.get("tolerance_mode", "abs") == "rel"
    if rel:
        # The scorer's arithmetic, inverted: each side is that fraction of
        # |target| — by the tube's rule, refused under `_EPS`.
        value = _fraction(edit, value, target, "the target is zero")
    holder = point
    if point.get("tolerance") is None:
        if _defers(leaf, point):
            holder = leaf
        elif leaf.get("tolerance") is not None:
            # It stops deferring: from the tolerance it was held to, so a
            # pair keeps its other side where the box's other edge stands.
            point["tolerance"] = copy.deepcopy(leaf["tolerance"])
    _set_side(edit, holder, "tolerance", side, value, axis="y", fraction=rel)
    return leaf


def _defers(leaf: dict, point: dict) -> bool:
    """Whether a drag on ``point``'s value tolerance writes the
    CRITERION's: the row holds none of its own, and every row that defers
    reads the criterion's in this row's ``tolerance_mode``
    (:func:`_edit_points_side` says why)."""
    return point.get("tolerance") is None and _deferring_modes(leaf) == {
        point.get("tolerance_mode", "abs")
    }


def _deferring_modes(leaf: dict) -> set[str]:
    """The ``tolerance_mode`` of every row of ``leaf`` that holds no
    ``tolerance`` of its own (a ``null`` is an absent key): the modes the
    CRITERION's tolerance is read in — each deferring row reads the one
    number or pair in its own."""
    return {
        row.get("tolerance_mode", "abs")
        for row in leaf.get("points") or []
        if isinstance(row, dict) and row.get("tolerance") is None
    }


def _point_time(edit: HandleEdit, leaf: dict, point: dict, i: int) -> float:
    """Checkpoint ``i``'s time: its own, or — absent or null, the trace-end
    sentinel — the last time of the reference as the scorer receives it."""
    time = point.get("time")
    if time is not None:
        return _authored_number(edit, time, f"points[{i}].time")
    ref_time, _ = _measured_reference(edit, leaf, "the trace's end")
    return float(ref_time[-1])


# -- event-timing --------------------------------------------------------------


def edit_events_handle(edit: HandleEdit) -> dict:
    """``events/early/i`` and ``events/late/i`` — the edges of event ``i``'s
    window — → that side of the tolerance the event is held to, by
    :func:`_set_side`, from ``x``: ``time − x`` for early (``lower``),
    ``x − time`` for late (``upper``); ``y`` is ignored.

    A DECLARED event (the leaf holds ``events``): its time is its row's,
    and the holder its row's ``tolerance`` where the row has one, else the
    criterion's ``time_tolerance``. AUTO-DETECTED events (no ``events``):
    event ``i`` is the ``i``-th duplicated time of the reference as the
    scorer receives it — windowed — found by the scorer's own detection;
    the holder is the criterion's ``time_tolerance``, so every window
    moves. Refused on a leaf scored against another baseline, and with no
    reference. An event's own time has no handle."""
    _check_position(edit)
    _, role, segment = _parse(edit, "events", 3)
    if role not in ("early", "late"):
        raise _unnamed(edit)
    side = "lower" if role == "early" else "upper"
    leaf = copy.deepcopy(edit.leaf)
    holder = leaf
    key = "time_tolerance"
    if leaf.get("events") is not None:
        i = _index(edit, segment, leaf["events"])
        event = leaf["events"][i]
        t = _authored_number(edit, event.get("time"), f"events[{i}].time")
        if event.get("tolerance") is not None:
            holder, key = event, "tolerance"
    else:
        ref_time, _ = _measured_reference(edit, leaf, "an event's window")
        events = [float(ref_time[b[0]]) for b in _find_event_boundaries(ref_time)]
        if not (segment.isascii() and segment.isdigit()):
            raise _unnamed(edit)
        i = int(segment)
        if i >= len(events):
            raise _refuse(edit, f"there is no event {i} to move")
        t = events[i]
    if edit.x is None:
        return leaf
    value = t - edit.x if side == "lower" else edit.x - t
    _set_side(
        edit, holder, key, side, value, axis="x", named=role, crosses="event's time"
    )
    return leaf


# -- dominant-frequency --------------------------------------------------------


def edit_peaks_handle(edit: HandleEdit) -> dict:
    """``peaks/declared/i`` → ``peaks[i]["freq"]`` from ``x``, refused
    ``<= 0``; ``y`` (the spectrum's amplitude) is ignored. An ``abs``
    window keeps its width in Hz and moves with its ring: a ring dragged
    to where that window's lower edge (:func:`~.algorithms._peak_window`,
    the scorer's) is at 0 Hz or below is refused as the grips are — 5 Hz
    with 3 Hz either side, dragged to 2 Hz, was a window from −1 Hz. Where
    that edge was at 0 Hz or below BEFORE the drag (a window written by
    hand: 5 Hz with 9 Hz below it) the ring did not bring it there, and the
    refusal says "already", in the upper grip's words for the same state.
    A ``rel`` window is a fraction of its frequency and no ring brings it
    there.

    ``peaks/lower/i`` and ``peaks/upper/i`` — the edges of the peak's
    window — → that side of ``peaks[i]["tolerance"]``, by
    :func:`_set_side` (a row without one gets it on its first drag): in
    ``rel`` (the default) ``1 − x/freq`` and ``x/freq − 1``, refused at a
    declared frequency of zero; in ``abs`` ``freq − x`` and ``x − freq``.
    A write that would leave the window's LOWER edge at 0 Hz or below — a
    lower side of 1 or more in ``rel``, of the frequency or more in
    ``abs`` — is refused, whichever grip moved: no window reaches 0 Hz. A
    lower edge dragged there is one; so is the UPPER edge of a row holding
    a number, dragged past twice the frequency, since the number is both
    sides (2.61 was written to both). The upper grip of a PAIR writes the
    upper side alone: where the lower one reaches 0 Hz it did so before the
    drag, and the refusal says that — "already", not "would"."""
    _check_position(edit)
    _, role, segment = _parse(edit, "peaks", 3)
    if role not in ("declared", "lower", "upper"):
        raise _unnamed(edit)
    leaf = copy.deepcopy(edit.leaf)
    i = _index(edit, segment, leaf.get("peaks"))
    if role != "declared":
        peak = leaf["peaks"][i]
        if edit.x is None:
            return leaf
        freq = _authored_number(edit, peak.get("freq"), f"peaks[{i}].freq")
        value = freq - edit.x if role == "lower" else edit.x - freq
        rel = peak.get("tolerance_mode", "rel") == "rel"
        if rel:
            if freq == 0.0:
                raise _refuse(edit, "the declared frequency is zero")
            value = value / abs(freq)
        # A pair's upper grip leaves its lower side as it was.
        kept = role == "upper" and isinstance(peak.get("tolerance"), dict)
        _set_side(edit, peak, "tolerance", role, value, axis="x", fraction=rel)
        # `_set_side` wrote what it parsed: a number or a whole pair.
        lower = parse_sides(peak["tolerance"], "tolerance").lower
        if lower >= (1.0 if rel else freq):
            if kept:
                raise _refuse(edit, "the window's lower side already reaches 0 Hz")
            raise _refuse(edit, "the window would reach 0 Hz")
        return leaf
    if edit.x is not None:
        x = _placed(edit, "x", edit.x)
        if x <= 0:
            raise _refuse(edit, f"frequency {_num(x)} must be positive")
        peak = leaf["peaks"][i]
        was = peak.get("freq")
        peak["freq"] = x
        if peak.get("tolerance_mode", "rel") != "rel":
            try:
                low, _high, sides, _mode = _peak_window(peak, i)
            except ValueError as err:
                raise _refuse(edit, str(err)) from None
            if low <= 0:
                # The same width about the frequency it had: where the
                # window's lower edge stood before the ring moved.
                if _is_number(was) and was - sides.lower <= 0:
                    raise _refuse(edit, "the window's lower side already reaches 0 Hz")
                raise _refuse(edit, "the window would reach 0 Hz")
    return leaf


# -- which field a handle writes (D149) ----------------------------------------


def _row_index(segment: str) -> int | None:
    return int(segment) if segment.isascii() and segment.isdigit() else None


def handle_fields(leaf: dict, local: str) -> dict[str, tuple[str | int, ...]]:
    """Which field of ``leaf`` a move of the handle ``local`` writes, per
    axis it moves on: ``{"x": ("points", 3, "time"), "y": ("points", 3,
    "value")}`` for a checkpoint's ring. A field is a path of keys into the
    raw leaf; one side of a tolerance ends in ``"lower"`` or ``"upper"``
    whether the file holds a number or a pair. It is the editors' own
    choice of holder, said as data — a checkpoint's arrow names the
    criterion's ``tolerance`` while its row defers to it, an event's the
    criterion's ``time_tolerance`` — so "arm a field, then click the plot"
    finds the handle that sets a field without a second rule. ``{}`` for a
    local no editor names."""
    parts = local.split("/")
    if len(parts) == 2:
        family, which = parts
        if which in ("min", "max"):
            return {"y": (f"{which}_value",)}
        if family == "scalar" and which in ("upper", "lower"):
            return {"y": ("tolerance", which)}
        return {}
    if len(parts) != 3:
        return {}
    family, role, key = parts
    i = _row_index(key)
    if family == "tube" and role in ("upper", "lower"):
        if key == "const":
            band = leaf.get("tube_width_mode") == "band"
            return {"y": ("tube_abs" if band else "tube_rel", role)}
        if i is not None:
            return {"x": ("tube_points", i, "time"), "y": ("tube_points", i, role)}
    if family == "points" and role == "target" and i is not None:
        return {"x": ("points", i, "time"), "y": ("points", i, "value")}
    if family == "points" and role in _POINT_SIDES:
        side, tolerance = _POINT_SIDES[role]
        if tolerance == "time_tolerance":
            return {} if i is None else {"x": ("points", i, tolerance, side)}
        rows = leaf.get("points") or []
        if i is not None and i < len(rows) and not _defers(leaf, rows[i]):
            return {"y": ("points", i, tolerance, side)}
        return {"y": (tolerance, side)} if key == "final" or i is not None else {}
    if family == "events" and role in ("early", "late") and i is not None:
        side = "lower" if role == "early" else "upper"
        events = leaf.get("events") or []
        own = i < len(events) and isinstance(events[i], dict)
        if own and events[i].get("tolerance") is not None:
            return {"x": ("events", i, "tolerance", side)}
        return {"x": ("time_tolerance", side)}
    if family == "peaks" and i is not None:
        if role == "declared":
            return {"x": ("peaks", i, "freq")}
        if role in ("lower", "upper"):
            return {"x": ("peaks", i, "tolerance", role)}
    return {}


# -- a Ctrl+click adds a row that settles on the data (D149) -------------------


@dataclass(frozen=True, kw_only=True)
class RowPlace:
    """One Ctrl+click on the plot, as a placer receives it. A placer returns
    the leaf with one more row in its table — the input is not mutated —
    and refuses with :class:`HandleEditError` where the click names no
    place for one."""

    #: The raw leaf, as ``metrics`` holds it; never mutated.
    leaf: dict
    #: Per list field of the metric, the row the editor's ``Add row`` would
    #: append to it now: every field of the new row the click does not set.
    blanks: Mapping[str, dict]
    #: The panel that was clicked — ``"time"`` or ``"spectrum"`` — and the
    #: click in that panel's own axes.
    panel: str
    x: float
    y: float
    #: The primary baseline and the run, ``(time, values)``, full
    #: resolution and unwindowed; ``None`` where there is none.
    reference: tuple[np.ndarray, np.ndarray] | None = None
    actual: tuple[np.ndarray, np.ndarray] | None = None
    #: What the plot draws for this leaf: the geometry it was scored with.
    geometry: Sequence[Primitive] = ()
    #: As :attr:`HandleEdit.visible` and :attr:`HandleEdit.span`.
    visible: tuple[float | None, float | None] = (None, None)
    span: tuple[float, float] | None = None


def _place_edit(place: RowPlace, panel: str, noun: str) -> HandleEdit:
    """``place`` as the editors' helpers read a drag — the clamps and the
    refusals of a row added by a click are a dragged row's — refused where
    the click is not on ``panel``, the one a row of this kind is drawn on."""
    edit = HandleEdit(
        leaf=place.leaf,
        local=f"new {noun}",
        x=place.x,
        y=place.y,
        reference=place.reference,
        span=place.span,
        visible=place.visible,
    )
    _check_position(edit)
    if place.panel != panel:
        where = "spectrum" if panel == "spectrum" else "time plot"
        raise _refuse(edit, f"{noun} is added by a Ctrl+click on the {where}")
    return edit


def place_points_row(place: RowPlace) -> dict:
    """A checkpoint at the clicked TIME — snapped, and held to where a
    checkpoint is scored and drawn, as a dragged one is — with no value of
    its own: it follows the baseline there. Where there is no baseline to
    follow it takes the RUN's value at that time as its ``value``, as the
    data has it (not snapped: it was not placed, it was read). Refused on a
    leaf scored against another baseline, as a time drag is."""
    edit = _place_edit(place, "time", "a point")
    leaf = copy.deepcopy(place.leaf)
    _refuse_other_baseline(edit, leaf, "its time")
    row = copy.deepcopy(dict(place.blanks["points"]))
    follows = _reference(edit) is not None
    row["time"] = _checkpoint_time(edit, leaf, _placed(edit, "x", place.x), follows)
    row.pop("value", None)
    if not follows:
        actual = place.actual
        if actual is None or len(actual[0]) == 0:
            raise _refuse(edit, "there is no signal to place a point on")
        row["value"] = _written(float(np.interp(row["time"], actual[0], actual[1])))
    leaf["points"] = [*(leaf.get("points") or []), row]
    return leaf


def place_events_row(place: RowPlace) -> dict:
    """A declared event at the run's event NEAREST the click — the actual
    events the plot draws for the leaf — at that event's own time. Refused
    where the run has none, and where that event is already declared: a
    second row on it would say nothing more."""
    edit = _place_edit(place, "time", "an event")
    leaf = copy.deepcopy(place.leaf)
    actual = [
        p.time
        for p in place.geometry
        if isinstance(p, VLine) and p.kind == "act" and p.panel == "time"
    ]
    if not actual:
        raise _refuse(edit, "the run has no event to place one on")
    t = _written(min(actual, key=lambda at: abs(at - place.x)))
    rows = list(leaf.get("events") or [])
    if any(isinstance(r, dict) and r.get("time") == t for r in rows):
        raise _refuse(edit, f"the nearest event, at t={_num(t)}, is already declared")
    row = copy.deepcopy(dict(place.blanks["events"]))
    row["time"] = t
    leaf["events"] = [*rows, row]
    return leaf


def place_peaks_row(place: RowPlace) -> dict:
    """A declared peak at the peak of the run's spectrum NEAREST the click
    — a local maximum of the spectrum the plot draws, above the noise floor
    the scorer matches peaks over — at that peak's own frequency. A click
    on the time plot is refused: a frequency is read off the spectrum."""
    edit = _place_edit(place, "spectrum", "a peak")
    leaf = copy.deepcopy(place.leaf)
    spectrum = next(
        (
            p
            for p in place.geometry
            if isinstance(p, Series) and p.panel == "spectrum" and p.kind == "act"
        ),
        None,
    )
    peaks: list[float] = []
    if spectrum is not None and len(spectrum.x) >= 3:
        freqs = np.asarray(spectrum.x, dtype=float)
        amps = np.asarray(spectrum.y, dtype=float)
        inner = np.arange(1, len(amps) - 1)
        tops = inner[
            (amps[inner] > amps[inner - 1])
            & (amps[inner] > amps[inner + 1])
            & (amps[inner] >= _PEAK_MIN_REL_AMPLITUDE * float(np.max(amps)))
        ]
        peaks = [float(freqs[k]) for k in tops if freqs[k] > 0]
    if not peaks:
        raise _refuse(edit, "the run's spectrum has no peak to place one on")
    freq = _written(min(peaks, key=lambda f: abs(f - place.x)))
    rows = list(leaf.get("peaks") or [])
    if any(isinstance(r, dict) and r.get("freq") == freq for r in rows):
        raise _refuse(
            edit, f"the nearest peak, at {_num(freq)} Hz, is already declared"
        )
    row = copy.deepcopy(dict(place.blanks["peaks"]))
    row["freq"] = freq
    leaf["peaks"] = [*rows, row]
    return leaf


def place_tube_row(place: RowPlace) -> dict:
    """A control point at the clicked time — snapped, and clamped into the
    span a control point is drawn in — whose widths are the tube's own
    THERE, interpolated as the scorer interpolates them: adding it changes
    nothing until it is dragged. A tube of constant width has no rows, and
    a click adds none: the leaf comes back as it is. Refused where a
    control point already stands at that time, and — like a time drag — on
    a leaf scored against another baseline."""
    leaf = copy.deepcopy(place.leaf)
    rows = leaf.get("tube_points")
    if not rows:
        return leaf
    edit = _place_edit(place, "time", "a control point")
    _refuse_other_baseline(edit, leaf, "its time")
    x = _placed(edit, "x", place.x)
    t = _written(_clamped_time(edit, leaf, x, _reference(edit)))
    if any(isinstance(r, dict) and r.get("time") == t for r in rows):
        raise _refuse(edit, f"a control point already stands at t={_num(t)}")
    try:
        upper, lower = _interpolate_tube_widths(
            np.array([t]), rows, leaf.get("tube_interpolation", "linear")
        )
    except (KeyError, TypeError, ValueError) as err:
        raise _refuse(
            edit, f"the tube's control points cannot be read: {err}"
        ) from None
    row = copy.deepcopy(dict(place.blanks["tube_points"]))
    row.update(time=t, upper=_written(upper[0]), lower=_written(lower[0]))
    leaf["tube_points"] = [*rows, row]
    return leaf


def tube_row_noun(leaf: dict) -> str | None:
    """What a Ctrl+click adds to a tube: a control point, where it has
    any; nothing to one of constant width."""
    return "a control point" if leaf.get("tube_points") else None
