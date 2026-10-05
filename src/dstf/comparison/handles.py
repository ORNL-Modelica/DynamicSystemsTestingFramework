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

A ``kind="grip"`` primitive is one SIDE of a tolerance, and its editor
writes that side through :func:`_set_side`: the value is the distance from
the target to the dragged edge — in ``rel``, over the target's magnitude —
and the holder is the row's own tolerance where the row has one, else the
criterion's. A grip moves on ONE axis; the other reaches the editor as the
grip's own resolved position, never as something to write.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import numpy as np

from .algorithms import _find_event_boundaries
from .sides import parse_sides
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
    t = _written(_clamped_time(edit, leaf, x, reference))
    try:
        _refuse_shared_time(edit, rows, i, t)
        point["time"] = t
        return _set_tube_width(edit, leaf, point, i, side, mode, t, reference)
    except HandleEditError as err:
        if t == _written(x):
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
    _set_side(edit, leaf, "tube_abs" if mode == "band" else "tube_rel", side, width)
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
        width = _fraction(edit, width, ref_t, f"the reference is zero at t={_num(t)}")
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
    _set_side(edit, leaf, "tolerance", side, value)
    return leaf


def _set_side(
    edit: HandleEdit,
    holder: dict,
    key: str,
    side: str,
    value: float,
    *,
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
    before it is written."""
    if not math.isfinite(value):
        raise _refuse(edit, "the tolerance would not be a finite number")
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
        follows_baseline = point.get("value") is None
        reference = _reference(edit) if follows_baseline else None
        point["time"] = _written(_clamped_time(edit, leaf, x, reference))
    if edit.y is not None and point.get("value") is not None:
        point["value"] = _written(edit.y)
    return leaf


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
        _set_side(edit, leaf, "tolerance", side, value)
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
    if point.get("tolerance_mode", "abs") == "rel":
        # The scorer's arithmetic, inverted: each side is that fraction of
        # |target| — by the tube's rule, refused under `_EPS`.
        value = _fraction(edit, value, target, "the target is zero")
    holder = point
    if point.get("tolerance") is None:
        if _deferring_modes(leaf) == {point.get("tolerance_mode", "abs")}:
            holder = leaf
        elif leaf.get("tolerance") is not None:
            # It stops deferring: from the tolerance it was held to, so a
            # pair keeps its other side where the box's other edge stands.
            point["tolerance"] = copy.deepcopy(leaf["tolerance"])
    _set_side(edit, holder, "tolerance", side, value)
    return leaf


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
    _set_side(edit, holder, key, side, value, named=role, crosses="event's time")
    return leaf


# -- dominant-frequency --------------------------------------------------------


def edit_peaks_handle(edit: HandleEdit) -> dict:
    """``peaks/declared/i`` → ``peaks[i]["freq"]`` from ``x``, refused
    ``<= 0``; ``y`` (the spectrum's amplitude) is ignored.

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
    sides (2.61 was written to both)."""
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
        _set_side(edit, peak, "tolerance", role, value)
        # `_set_side` wrote what it parsed: a number or a whole pair.
        lower = parse_sides(peak["tolerance"], "tolerance").lower
        if lower >= (1.0 if rel else freq):
            raise _refuse(edit, "the window would reach 0 Hz")
        return leaf
    if edit.x is not None:
        if edit.x <= 0:
            raise _refuse(edit, f"frequency {_num(edit.x)} must be positive")
        leaf["peaks"][i]["freq"] = _written(edit.x)
    return leaf
