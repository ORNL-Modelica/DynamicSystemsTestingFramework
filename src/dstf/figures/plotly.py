"""Render a :class:`~.view.VariableView` to a Plotly figure dict.

Plain dicts, JSON-ready, no ``plotly.py``. One builder per primitive type in
:data:`TRACE_BUILDERS`; assembly adds the actual / reference / overlay
traces, the error axis and the spectrum panel. No mode name appears here —
a new metric is drawn by emitting primitives, a new primitive by adding one
builder.

Every trace carries the primitive id in ``meta`` (grouped marker traces
carry the ids in ``customdata`` and the family in ``meta``); every shape
carries it as ``name``. Those are the handles on-plot editing attaches to.

The editable figure (``editable=True``, the tool's; never the report's) adds
one pixel-sized ring shape (a ``path``) per ``kind="handle"`` marker and one
arrow per ``kind="grip"`` primitive — a side of a tolerance — with an
invisible point that pads the axis it moves along (:func:`_headroom`), turns on
``config.edits.shapePosition`` and turns OFF ``scrollZoom`` (:func:`_config`). That makes every shape draggable — plotly.js
offers no per-shape switch that works (measured on plotly.js 3.1.1, the
build NiceGUI 3.16 bundles; the bundle's "3.8.2" string is d3's) — and the
rings are what an editor attaches to, since Plotly drags shapes, not trace
points. So the editable figure also SAYS which of its shapes an editor
moves, in ``layout.meta`` (:data:`POINTER`): the page takes the pointer off
every other one.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from ..comparison.geometry import (
    Band,
    Box,
    HLine,
    Marker,
    Primitive,
    Series,
    VLine,
    Window,
    grip_axis,
    grip_group,
    grip_label,
    grip_outward,
)
from .families import HUES, family_label, group_families, ramp
from .view import LeafView, VariableView, decimate_view

PLOT_CONFIG = {"responsive": True, "displaylogo": False, "scrollZoom": True}

ACTUAL = "#2196F3"
REFERENCE = "#FF9800"
PASS = "rgba(76,175,80,0.15)"
PASS_LINE = "rgba(76,175,80,0.6)"
FAIL = "rgba(244,67,54,0.15)"
FAIL_LINE = "rgba(244,67,54,0.8)"
MUTED = "rgba(117,117,117,0.35)"
WINDOW_FILL = "rgba(33,150,243,0.08)"
WINDOW_LINE = "rgba(33,150,243,0.4)"
ERROR = "#f44336"
#: Which right-hand axis an error series rides on — one axis per unit family
#: (brief §3.4): signal units for the signed and absolute error, dimensionless
#: for the per-point NRMSE. A lookup, not a default: an error series whose
#: family is not here raises rather than being drawn against the wrong unit.
ERROR_AXES = {"error/signed": "y3", "error/abs": "y3", "error/nrmse": "y4"}

#: Vertical extent of the two panels when a spectrum is present.
TIME_DOMAIN = (0.45, 1.0)
SPECTRUM_DOMAIN = (0.0, 0.32)

#: Where the legend sits, on both tiers: above the plot area, one row that
#: wraps to more when its entries don't fit (a report legend of 8 entries is
#: 2 rows at 900 px). Plotly grows the top margin to fit it (measured: 25 ->
#: 41 px for one row), so it never sits on the trace start or on a ring (the
#: 2026-09-21 board, option b).
LEGEND = {
    "orientation": "h",
    "x": 0,
    "xanchor": "left",
    "y": 1,
    "yanchor": "bottom",
    "bgcolor": "rgba(0,0,0,0)",
}


@dataclass(frozen=True)
class Visibility:
    """What the tool's page chose to show on one plot (legend b).

    ``hidden``: the leaf paths whose drawing is off. ``error``: the one error
    series shown — a key of :data:`ERROR_AXES` — or ``None``. A figure built
    WITH a Visibility puts only signals in its legend: the criteria are
    toggled from their rows and the errors from one select. A figure built
    without one (the report) keeps every legend entry."""

    hidden: frozenset[str] = frozenset()
    error: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.hidden, str):
            raise TypeError(
                "hidden takes a collection of leaf paths, not a str "
                "(a str matches by substring)"
            )
        if self.error is not None and self.error not in ERROR_AXES:
            raise ValueError(f"error {self.error!r} is none of {', '.join(ERROR_AXES)}")


_LABELS = {
    "tube/band": "Tube",
    "tube/upper": "Tube upper points",
    "tube/lower": "Tube lower points",
    "points/target": "Checkpoints",
    "points/box": "Checkpoint boxes",
    "range/min": "Min bound",
    "range/max": "Max bound",
    "scalar/actual": "Actual (scalar)",
    "scalar/reference": "Reference (scalar)",
    "scalar/min": "Min bound",
    "scalar/max": "Max bound",
    "events/ref": "Reference events",
    "events/act": "Actual events",
    "events/window": "Event windows",
    "spectrum/ref": "Reference spectrum",
    "spectrum/act": "Actual spectrum",
    "peaks/declared": "Declared peaks",
    "peaks/matched": "Matched peaks",
    "peaks/detected": "Reference peaks",
    "peaks/window": "Peak windows",
    "error/signed": "Signed error",
    "error/abs": "Abs error",
    "error/nrmse": "NRMSE (per-point)",
}

_MARKER_SYMBOLS = {
    "tube/upper": "triangle-up",
    "tube/lower": "triangle-down",
    "points/target": "diamond",
    "peaks/declared": "diamond",
    "peaks/matched": "x",
    "peaks/detected": "circle-open",
}


@dataclass(frozen=True)
class _Ctx:
    x_min: float
    x_max: float
    has_spectrum: bool
    #: Pre-decimation sample counts by primitive id (see ``decimate_view``).
    full_samples: dict[str, int]
    #: The spectrum panel's x range where the layout SETS one
    #: (:func:`_spectrum_range`), else None: what :func:`_grip_way` reads.
    spectrum_range: tuple[float, float] | None = None


def _full(ctx: _Ctx, prim_id: str, n_now: int) -> int:
    return ctx.full_samples.get(prim_id, n_now)


def family(prim: Primitive, leaf: LeafView) -> str:
    """``tube/upper`` from ``/metrics/children/0/tube/upper/3``."""
    local = (
        prim.id[len(leaf.path) + 1 :]
        if prim.id.startswith(leaf.path + "/")
        else prim.id
    )
    return "/".join(local.split("/")[:2])


def _label(fam: str) -> str:
    return _LABELS.get(fam, fam)


def as_text(label: str) -> str:
    """A label as Plotly must receive it to PRINT it.

    Trace names, hover templates and axis titles are rendered through
    plotly.js's small HTML subset (``<b>``, ``<br>``, ``<sub>`` …), so a
    label holding ``<`` is read as markup: an expression the model wrote as
    ``a<b`` lost its tail, and one holding ``<br>`` broke the hover over two
    lines. Escaped here — plotly.js decodes the entities back, so an
    ordinary label is unchanged on the page.

    Applied to every LABEL that enters a trace name, a hover or a title,
    and to nothing that enters ``meta.id``, ``legendgroup`` or a shape
    name: those carry NAMES, which the page, the picker and every test
    look traces up by.
    """
    return html.escape(label, quote=False)


def hover_of(label: str) -> dict:
    """The trace keys that make a hover READ ``label``, plus the value.

    A hovertemplate is a format string: plotly.js substitutes every
    ``%{…}`` in it from the trace's data, and there is no escape for a
    literal one. So the label does not enter the template at all — it rides
    in the trace's ``meta`` and the template references it as
    ``%{meta.label}``.

    NOT ``text``: plotly.js substitutes ``%{text}`` only from a PER-POINT
    array. A scalar string is left alone, so every labelled hover on both
    tiers rendered the literal ``%{text}: 1.234`` — read off a real mouse
    hover in Chromium, which is the only place it shows (the figure dict
    looked right). An array would be the label copied once per sample into
    the page; ``meta`` is one object per trace, and ``%{meta.label}``
    substitutes from it.

    The ``meta`` here is MERGED into the trace's own, which already carries
    its id and its sample counts — :func:`with_hover` does that, and a
    plain spread beside a ``"meta"`` key would not.
    """
    return {
        "meta": {"label": as_text(label)},
        # `.4~g` like the y axes' `hoverformat` (`_Y_LABELS`): a template
        # overrides it, so it says the same itself.
        "hovertemplate": "%{meta.label}: %{y:.4~g}<extra></extra>",
    }


def with_hover(trace: dict, label: str | None) -> dict:
    """``trace`` with a hover reading ``label``; ``None`` leaves it alone.

    The one way :func:`hover_of` is applied: its ``meta`` folds into the
    trace's own rather than replacing it, so the hover cannot cost a trace
    its ``meta.id`` — the key the page, the overlay picker and every test
    address a trace by.
    """
    if label is None:
        return trace
    hover = hover_of(label)
    trace["hovertemplate"] = hover["hovertemplate"]
    trace["meta"] = {**trace.get("meta", {}), **hover["meta"]}
    return trace


def _axes(prim: Primitive) -> dict:
    return {"xaxis": "x2", "yaxis": "y2"} if prim.panel == "spectrum" else {}


def _xref(prim: Primitive) -> str:
    return "x2" if prim.panel == "spectrum" else "x"


def _paper_span(prim: Primitive, ctx: _Ctx) -> tuple[float, float]:
    if not ctx.has_spectrum:
        return (0.0, 1.0)
    return SPECTRUM_DOMAIN if prim.panel == "spectrum" else TIME_DOMAIN


def _window_span(window: Window | None, ctx: _Ctx) -> tuple[float, float]:
    if window is None:
        return ctx.x_min, ctx.x_max
    return (
        ctx.x_min if window.start is None else float(window.start),
        ctx.x_max if window.end is None else float(window.end),
    )


def _band(prim: Band, leaf: LeafView, window: Window | None, ctx: _Ctx):
    color = PASS if leaf.passed else FAIL
    return [
        {
            "type": "scatter",
            "x": [*prim.time.tolist(), *prim.time[::-1].tolist()],
            "y": [*prim.upper.tolist(), *prim.lower[::-1].tolist()],
            "fill": "toself",
            "fillcolor": color,
            "line": {"color": "rgba(0,0,0,0)", "width": 0},
            "name": _label(family(prim, leaf)),
            "hoverinfo": "skip",
            "showlegend": True,
            "meta": {
                "id": prim.id,
                "kind": prim.kind,
                # A band is a closed polygon (2N points for N samples), so the
                # banner cannot read its sample count off the trace's x length.
                "samples": len(prim.time),
                "full_samples": _full(ctx, prim.id, len(prim.time)),
            },
            **_axes(prim),
        }
    ], []


def _box(prim: Box, leaf: LeafView, window: Window | None, ctx: _Ctx):
    full_height = prim.y0 is None or prim.y1 is None
    y0, y1 = _paper_span(prim, ctx) if full_height else (prim.y0, prim.y1)
    ok = prim.passed is not False
    return [], [
        {
            "type": "rect",
            "xref": _xref(prim),
            "yref": "paper"
            if full_height
            else ("y2" if prim.panel == "spectrum" else "y"),
            "x0": prim.t0,
            "x1": prim.t1,
            "y0": y0,
            "y1": y1,
            "fillcolor": (PASS if ok else FAIL)
            if not full_height
            else "rgba(117,117,117,0.09)",
            "line": {
                "color": PASS_LINE if ok else FAIL_LINE,
                "width": 1,
                "dash": "dot",
            },
            "name": prim.id,
        }
    ]


def _hline(prim: HLine, leaf: LeafView, window: Window | None, ctx: _Ctx):
    ok = prim.passed is not False
    color = (
        (PASS_LINE if ok else FAIL_LINE)
        if prim.kind in ("handle", "result", "act")
        else MUTED
    )
    x0, x1 = _window_span(window, ctx)
    shapes = [
        {
            "type": "line",
            "xref": _xref(prim),
            "yref": "y2" if prim.panel == "spectrum" else "y",
            "x0": x0,
            "x1": x1,
            "y0": prim.value,
            "y1": prim.value,
            "line": {"color": color, "width": 1.5, "dash": "dash"},
            "name": prim.id,
        }
    ]
    if window is not None:
        shapes.append(
            {
                "type": "line",
                "xref": "paper",
                "yref": "y2" if prim.panel == "spectrum" else "y",
                "x0": 0,
                "x1": 1,
                "y0": prim.value,
                "y1": prim.value,
                "line": {"color": MUTED, "width": 1, "dash": "dot"},
                "name": f"{prim.id}/outside",
            }
        )
    anchor = {
        "type": "scatter",
        "x": [ctx.x_min],
        "y": [prim.value],
        "mode": "markers",
        "marker": {"opacity": 0},
        "showlegend": False,
        "hoverinfo": "skip",
        "meta": {"anchor_for": prim.id},
        **_axes(prim),
    }
    return [anchor], shapes


def _vline(prim: VLine, leaf: LeafView, window: Window | None, ctx: _Ctx):
    y0, y1 = _paper_span(prim, ctx)
    ref = prim.kind == "ref"
    return [], [
        {
            "type": "line",
            "xref": _xref(prim),
            "yref": "paper",
            "x0": prim.time,
            "x1": prim.time,
            "y0": y0,
            "y1": y1,
            "line": {
                "color": "#757575" if ref else "#1976D2",
                "width": 1 if ref else 1.5,
                "dash": "dash" if ref else "solid",
            },
            "name": prim.id,
        }
    ]


def _series(prim: Series, leaf: LeafView, window: Window | None, ctx: _Ctx):
    fam = family(prim, leaf)
    color = REFERENCE if prim.kind == "ref" else ACTUAL
    return [
        {
            "type": "scatter",
            "mode": "lines",
            "x": prim.x.tolist(),
            "y": prim.y.tolist(),
            "name": _label(fam),
            "line": {
                "color": color,
                "width": 1.2,
                "dash": "dash" if prim.kind == "ref" else "solid",
            },
            "meta": {
                "id": prim.id,
                "kind": prim.kind,
                "samples": len(prim.x),
                "full_samples": _full(ctx, prim.id, len(prim.x)),
            },
            **_axes(prim),
        }
    ], []


def _window(prim: Window, leaf: LeafView, window: Window | None, ctx: _Ctx):
    x0, x1 = _window_span(prim, ctx)
    y0, y1 = _paper_span(prim, ctx)
    return [], [
        {
            "type": "rect",
            "xref": "x",
            "yref": "paper",
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "fillcolor": WINDOW_FILL,
            "line": {"color": WINDOW_LINE, "width": 1, "dash": "dot"},
            "name": prim.id,
        }
    ]


Builder = Callable[
    [Primitive, LeafView, "Window | None", _Ctx], "tuple[list[dict], list[dict]]"
]

#: One builder per primitive type. ``Marker`` is grouped per family in
#: :func:`variable_figure` rather than built one trace at a time.
TRACE_BUILDERS: dict[type, Builder] = {
    Band: _band,  # type: ignore[dict-item]
    Box: _box,  # type: ignore[dict-item]
    HLine: _hline,  # type: ignore[dict-item]
    VLine: _vline,  # type: ignore[dict-item]
    Series: _series,  # type: ignore[dict-item]
    Window: _window,  # type: ignore[dict-item]
}


def _marker_traces(markers: list[Marker], leaf: LeafView) -> list[dict]:
    by_family: dict[str, list[Marker]] = {}
    for m in markers:
        by_family.setdefault(family(m, leaf), []).append(m)
    traces = []
    for fam, group in by_family.items():
        # One colour PER marker: a leaf with one passed and one failed
        # checkpoint must not paint both red (the boxes beside them carry
        # their own verdicts, and the two would contradict).
        ok_colour = HANDLE_RING if group[0].kind == "handle" else ACTUAL
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "x": [m.x for m in group],
                "y": [m.y for m in group],
                "customdata": [m.id for m in group],
                "name": _label(fam),
                "marker": {
                    "symbol": _MARKER_SYMBOLS.get(fam, "circle"),
                    "size": 10,
                    "color": [
                        FAIL_LINE if m.passed is False else ok_colour for m in group
                    ],
                    "line": {"color": "white", "width": 1},
                },
                "hoverinfo": "x+y",
                "showlegend": True,
                "meta": {"family": fam, "kind": group[0].kind},
                **_axes(group[0]),
            }
        )
    return traces


HANDLE_RING = "#2e7d32"
HANDLE_RING_FILL = "rgba(46,125,50,0.25)"


#: A ring, in pixels about its anchor: a circle of radius 7 as four cubic
#: arcs. A ``path``, not a ``circle``: plotly.js gives a pixel-sized circle
#: or rect larger than 10 px in both dimensions RESIZE zones, so a ring
#: pressed 3 px off its centre was resized and staged nothing; a path has
#: none and moves from wherever it is pressed (Chromium, plotly.js 3.1.1,
#: 2026-10-02).
RING_PATH = (
    "M 7,0 C 7,3.866 3.866,7 0,7 C -3.866,7 -7,3.866 -7,0 "
    "C -7,-3.866 -3.866,-7 0,-7 C 3.866,-7 7,-3.866 7,0 Z"
)


def _handle_rings(markers: list[Marker]) -> list[dict]:
    """One draggable ring per handle marker, named with the marker's id.

    The fill is visible on purpose: a shape drags by its painted area, and a
    transparent fill is not guaranteed to receive the pointer.
    """
    return [
        {
            "type": "path",
            "xref": _xref(m),
            "yref": "y2" if m.panel == "spectrum" else "y",
            "xsizemode": "pixel",
            "ysizemode": "pixel",
            "xanchor": m.x,
            "yanchor": m.y,
            "path": RING_PATH,
            "line": {"color": HANDLE_RING, "width": 2},
            "fillcolor": HANDLE_RING_FILL,
            "name": m.id,
        }
        for m in markers
        if m.kind == "handle"
    ]


#: A grip's colour: an orange apart from the rings' green — the theme table's
#: ``no-ref`` dark, as a literal (a figure's shapes follow no page theme;
#: `test_the_grip_colour_is_a_token_of_the_theme_table` pins the two equal).
GRIP = "#E65100"

#: A grip, in pixels about its anchor (positive y is UP), by the axis it
#: moves on and the way it is drawn (:func:`_grip_way` — outward, but for
#: an arrow that would leave a set range): a triangle whose apex stands
#: 10 px off the edge it is anchored on and points at it. It clears a ring of
#: radius 7 on that edge whatever the box's size — rings on the edges buried
#: the target under a box of 16 px or less, and a sideways drag on the
#: checkpoint then rewrote its tolerance — and, a ``path``, it never resizes.
_GRIP_PATHS = {
    ("y", 1): "M 0,10 L 6,17 L -6,17 Z",
    ("y", -1): "M 0,-10 L 6,-17 L -6,-17 Z",
    ("x", -1): "M -10,0 L -17,6 L -17,-6 Z",
    ("x", 1): "M 10,0 L 17,6 L 17,-6 Z",
}


#: An arrow's far end, px from its anchor (:data:`_GRIP_PATHS`), and the
#: narrowest axis the figure plans for, px: the figure layer does not know
#: how wide the page draws a plot, and an arrow that would pass the end of
#: a set range on an axis this narrow is drawn inward (:func:`_grip_way`).
#: On a wider plot some arrows that had the room are drawn inward too; on
#: a narrower one, one that had not is still clipped.
GRIP_FAR_END = 17
NARROWEST_AXIS = 500


def _grip_way(grip: Marker | VLine, ctx: _Ctx) -> int:
    """``+1`` or ``-1``: the way along its axis ``grip``'s arrow is DRAWN
    from its anchor — outward, away from its target
    (:func:`~dstf.comparison.geometry.grip_outward`), except where the axis
    it moves on has a SET range and the arrow's far end would pass the end
    of it: the plot area clips shapes, and with a peak window's lower edge
    next to 0 Hz the arrow was drawn left of the panel, where a press
    landed on the axis. There it is drawn on the inside of its edge,
    mirrored about the same anchor.

    The one axis whose range the layout sets is the spectrum's x
    (:func:`_spectrum_range`); every other is autoranged, with room for
    the arrows (:func:`_headroom`), and its arrows are always outward."""
    way = grip_outward(grip)
    if grip.panel != "spectrum" or grip_axis(grip) != "x":
        return way
    if ctx.spectrum_range is None:
        return way
    low, high = ctx.spectrum_range
    at = grip.time if isinstance(grip, VLine) else grip.x
    room = (at - low) if way < 0 else (high - at)
    needed = GRIP_FAR_END / NARROWEST_AXIS * (high - low)
    return -way if room < needed else way


def _grip_shapes(grips: list[Primitive], ctx: _Ctx) -> list[dict]:
    """One draggable arrow per grip, named with the grip's id and anchored
    at the tolerance edge it stands for. A marker's is anchored in data on
    both axes; a line's — the edge of a window that spans its panel — in
    data on x and on the PAPER at the panel's mid-height. It is drawn the
    way :func:`_grip_way` says.

    The page shows a grip only while the pointer is near its checkpoint
    (``authoring/shell/grips.js``); the figure holds them all, always — a
    drag names a shape by its index."""
    shapes = []
    for grip in grips:
        if isinstance(grip, Marker):
            anchor = {
                "xanchor": grip.x,
                "yref": "y2" if grip.panel == "spectrum" else "y",
                "yanchor": grip.y,
            }
        elif isinstance(grip, VLine):
            y0, y1 = _paper_span(grip, ctx)
            anchor = {"xanchor": grip.time, "yref": "paper", "yanchor": (y0 + y1) / 2}
        else:
            raise TypeError(
                f"no grip shape for {type(grip).__name__} (id {grip.id!r}): "
                "a grip is a Marker or a VLine"
            )
        shapes.append(
            {
                "type": "path",
                "xref": _xref(grip),
                "xsizemode": "pixel",
                "ysizemode": "pixel",
                **anchor,
                "path": _GRIP_PATHS[grip_axis(grip), _grip_way(grip, ctx)],
                "line": {"color": GRIP, "width": 1},
                "fillcolor": GRIP,
                "name": grip.id,
            }
        )
    return shapes


#: A headroom point's diameter (:func:`_headroom`). plotly.js pads an
#: autoranged axis ``size / 1.6`` px past a marker, plus its usual 5 % of
#: the axis; an arrow's far edge stands 17 px past its anchor. At 24 that
#: leaves 9.7 px of plot beyond the arrow at rest on the 234 px value axis
#: of a card and 4.4 px on the 129 px time panel of a split one — the arrow
#: whole, with a small margin, and no more: at 72 the pad was half of the
#: 234 px axis, and on a split plot opening a criterion took the trace from
#: 92.7 to 35.4 px per unit (Chromium, plotly.js 3.1.1; the Test page's
#: browser tests hold the pixels).
HEADROOM_SIZE = 24


def _mid_value(view: VariableView) -> float | None:
    """The middle of the values ``view`` draws, or None when it draws none."""
    values = [
        np.asarray(v, dtype=float)
        for v in (view.act_values, view.ref_values)
        if v is not None and len(v)
    ]
    finite = [v[np.isfinite(v)] for v in values]
    finite = [v for v in finite if len(v)]
    if not finite:
        return None
    low = min(float(v.min()) for v in finite)
    high = max(float(v.max()) for v in finite)
    return (low + high) / 2


def _headroom(
    grips: list[Primitive], leaf: LeafView, view: VariableView, ctx: _Ctx
) -> list[dict]:
    """Invisible points for the open leaf's grips, so that an autoranged
    axis leaves room past the outermost of them: the plot area clips
    shapes, and an arrow on its edge was not painted. The :func:`_hline`
    anchor idiom, with a size (:data:`HEADROOM_SIZE`).

    A point pads BOTH axes by its size, and a grip moves along ONE: so on
    the time panel each stands at its grip's own coordinate on that axis
    and at the MIDDLE of the other, where its size reaches neither end — a
    value's arrow (``upper`` / ``lower``) at mid-time, padding the value
    axis alone; a time's (a checkpoint's ``before`` / ``after``, the edge
    of a window that spans its panel) at the middle of the series' values,
    padding the time axis alone, and nowhere when there are no values.
    Standing at its anchor, the arrow of a checkpoint near either end of
    the run pushed the time axis out as well.

    On the spectrum panel a marker's stands at its own anchor and a line's
    has none: that x range is set (:func:`_spectrum_range`), not
    autoranged."""
    by_panel: dict[str, tuple[Primitive, list[float], list[float]]] = {}
    mid_time = (ctx.x_min + ctx.x_max) / 2
    mid_value = _mid_value(view)
    for grip in grips:
        assert isinstance(grip, (Marker, VLine))  # `_grip_shapes`
        if grip.panel != "time":
            if not isinstance(grip, Marker):
                continue
            x, y = grip.x, grip.y
        elif grip_axis(grip) == "y":
            assert isinstance(grip, Marker)  # a line moves on x
            x, y = mid_time, grip.y
        elif mid_value is not None:
            x, y = (grip.time if isinstance(grip, VLine) else grip.x), mid_value
        else:
            continue
        _, xs, ys = by_panel.setdefault(grip.panel, (grip, [], []))
        xs.append(float(x))
        ys.append(float(y))
    return [
        {
            "type": "scatter",
            "x": xs,
            "y": ys,
            "mode": "markers",
            "marker": {"opacity": 0, "size": HEADROOM_SIZE},
            "showlegend": False,
            "hoverinfo": "skip",
            "meta": {"headroom_for": leaf.path},
            **_axes(first),
        }
        for first, xs, ys in by_panel.values()
    ]


def _base_traces(view: VariableView) -> list[dict]:
    traces = []

    # A one-variable plot names its traces "Actual" / "Reference" — the
    # overlay picker and report.js find them by those names, and the title
    # above says which variable it is. When the label is not the name the
    # hover says the label too: on a `unitTests.x[3]` card, "Actual" alone
    # names nothing a person recognises. A variable whose label IS its name
    # gets no hover it did not have.
    def _hover(suffix: str = "") -> str | None:
        if view.label == view.name:
            return None
        return f"{view.label}{suffix}"

    if view.act_time is not None and view.act_values is not None:
        traces.append(
            with_hover(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": "Actual",
                    "x": view.act_time.tolist(),
                    "y": view.act_values.tolist(),
                    "line": {"color": ACTUAL, "width": 1.5},
                    "meta": {
                        "id": "actual",
                        "kind": "act",
                        "samples": len(view.act_time),
                        "full_samples": view.full_samples.get(
                            "act", len(view.act_time)
                        ),
                    },
                },
                _hover(),
            )
        )
    if view.ref_time is not None and view.ref_values is not None:
        traces.append(
            with_hover(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": "Reference",
                    "x": view.ref_time.tolist(),
                    "y": view.ref_values.tolist(),
                    "line": {"color": REFERENCE, "width": 1.5, "dash": "dash"},
                    "meta": {
                        "id": "reference",
                        "kind": "ref",
                        "samples": len(view.ref_time),
                        "full_samples": view.full_samples.get(
                            "ref", len(view.ref_time)
                        ),
                    },
                },
                _hover(" (reference)"),
            )
        )
    for ov in view.overlays:
        if ov.role == "soft_check":
            color, dash = "#7B1FA2", "dot"
        elif ov.kind == "sibling-backend":
            color, dash = "#1976D2", "dash"
        else:
            color, dash = "#388E3C", "dashdot"
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                # The page's overlay picker finds a trace by this exact
                # name (report.js overlayTraceName); keep the two in step.
                "name": f"Overlay: {ov.role}/{ov.name}",
                "x": ov.time.tolist(),
                "y": ov.values.tolist(),
                "line": {"color": color, "width": 1.2, "dash": dash},
                "opacity": 0.85,
                "visible": "legendonly",
                "meta": {
                    "id": f"overlay/{ov.role}/{ov.name}",
                    "kind": "overlay",
                    "role": ov.role,
                    "samples": len(ov.time),
                    "full_samples": (
                        len(ov.time) if ov.full_samples is None else ov.full_samples
                    ),
                },
            }
        )
    return traces


def _error_trace(s: Series, ctx: _Ctx, visibility: Visibility | None) -> dict:
    trace = {
        "type": "scatter",
        "mode": "lines",
        "x": s.x.tolist(),
        "y": s.y.tolist(),
        "name": _label(s.id),
        "line": {"color": ERROR, "width": 1},
        "opacity": 0.6,
        "yaxis": ERROR_AXES[s.id],
        "visible": "legendonly",
        # `.4~g` like the y axes' `hoverformat` (`_Y_LABELS`).
        "hovertemplate": f"{_label(s.id)}: %{{y:.4~g}}<extra></extra>",
        "meta": {
            "id": s.id,
            "kind": "error",
            "samples": len(s.x),
            "full_samples": _full(ctx, s.id, len(s.x)),
        },
    }
    if visibility is not None:
        trace["showlegend"] = False
        trace["visible"] = s.id == visibility.error
    return trace


def _time_span(view: VariableView) -> tuple[float, float]:
    xs = [a for a in (view.act_time, view.ref_time) if a is not None and len(a)]
    if not xs:
        return 0.0, 1.0
    return float(min(a[0] for a in xs)), float(max(a[-1] for a in xs))


#: How every y axis prints its numbers, on both tiers: Plotly's automatic
#: tick labels with `e` exponents, and 4 significant digits on a hover with
#: the trailing zeros trimmed (d3 `.4~g`: 0.6 reads `0.6`, 2.5e-5 reads
#: `0.000025`, where `.4g` printed `0.6000` and `0.00002500`; the same in
#: plotly.js 2.35.0 and 3.1.1). A trace's own hovertemplate overrides it,
#: so `hover_of` and `_error_trace` say `.4~g` too. A fixed `.4g`
#: tickformat printed `0.2000` for 0.2 and `0.00002000` for an error around
#: 1e-5, the size a passing test's error usually is (54 px against 23 for
#: `2e−5`).
_Y_LABELS = {"exponentformat": "e", "hoverformat": ".4~g"}


#: How far past its peak of interest the spectrum's x axis runs.
SPECTRUM_HEADROOM = 3.0


def _spectrum_range(view: VariableView) -> list[float] | None:
    """``[0, 3 x the peak of interest]``, clamped to Nyquist but never short
    of the peak. The peak of interest is the highest declared or matched
    peak (its window's upper edge where it has one), else the STRONGEST
    detected one: the reference's ten detected peaks include leakage
    sidelobes, and on a 2 Hz sine the highest of them put the axis at
    40.8 Hz. None — autorange — with no peak above zero.

    Autoranged, the axis ran 0..Nyquist, and Nyquist follows the sample
    count, not the physics: TRANSFORM's peaks near 1.2e-5 Hz sat in the
    leftmost ~10 px of an axis to ~1.7e-3 Hz (2026-09-26)."""
    declared: list[float] = []
    detected: list[tuple[float, float]] = []  # (frequency, magnitude)
    nyquist = 0.0
    for leaf in view.leaves:
        for p in leaf.geometry:
            if p.panel != "spectrum":
                continue
            if isinstance(p, Series):
                if len(p.x):
                    nyquist = max(nyquist, float(np.nanmax(p.x)))
            elif isinstance(p, Marker):
                if p.kind == "detected":
                    detected.append((float(p.x), float(p.y)))
                else:
                    declared.append(float(p.x))
            elif isinstance(p, Box):
                declared.append(float(p.t1))
    if declared:
        top = max(declared)
    elif detected:
        top = max(detected, key=lambda d: d[1])[0]
    else:
        return None
    if not top > 0:
        return None
    hi = SPECTRUM_HEADROOM * top
    # A declared peak above Nyquist stays on the axis.
    return [0.0, max(min(hi, nyquist), top) if nyquist > 0 else hi]


def _set_spectrum_range(view: VariableView) -> tuple[float, float] | None:
    """The spectrum's x range as :func:`_layout` sets it for ``view``, or
    None where it sets none (no spectrum panel, or an autoranged one)."""
    found = _spectrum_range(view) if view.has_spectrum else None
    return None if found is None else (found[0], found[1])


def _layout(view: VariableView, shapes: list[dict], has_errors: bool) -> dict:
    # Area shapes under lines: under `edits.shapePosition` every shape takes
    # a drag and plotly.js has no per-shape switch that works (`layer` only
    # restacks — measured in Chromium on 3.1.1, 2026-09-26), so the one that
    # is on top wins. A window's or a box's rect drawn after a bound line
    # swallowed every drag on the bound inside it.
    shapes = [s for s in shapes if s.get("type") == "rect"] + [
        s for s in shapes if s.get("type") != "rect"
    ]
    layout: dict = {
        "xaxis": {
            "title": {"text": "Time"},
            "hoverformat": ".6~g",
            "uirevision": "keep",
        },
        "yaxis": {
            "title": {"text": "Value", "standoff": 10},
            **_Y_LABELS,
            "automargin": True,
            "nticks": 6,
            "uirevision": "keep",
        },
        "margin": {"t": 25, "b": 45, "l": 60, "r": 20},
        "legend": dict(LEGEND),
        # The hover toolbar stands on the right edge: laid across the top it
        # covered the legend above the plot area once that wrapped to a
        # second row (the owner's 2026-09-24 ruling, both tiers).
        "modebar": {"orientation": "v"},
        "hovermode": "x unified",
        "shapes": shapes,
        "uirevision": "keep",
    }
    if view.has_spectrum:
        layout["yaxis"]["domain"] = list(TIME_DOMAIN)
        layout["xaxis2"] = {
            "title": {"text": "Frequency [Hz]"},
            "anchor": "y2",
            # A zoom into the spectrum survives the page's redraws, like
            # the time axis's.
            "uirevision": "keep",
        }
        spectrum_range = _spectrum_range(view)
        if spectrum_range is not None:
            layout["xaxis2"]["range"] = spectrum_range
        layout["yaxis2"] = {
            "title": {"text": "Magnitude"},
            **_Y_LABELS,
            "domain": list(SPECTRUM_DOMAIN),
            "anchor": "x2",
        }
    if has_errors:
        # Both axes are laid out hidden: a laid-out axis with only
        # legend-only traces gets a placeholder range and draws its zero
        # line as a stray black rule across the plot. In the report,
        # templates/axes.js shows an axis while a series on it is visible,
        # following the select and legend clicks; the tool's figure, built
        # with a Visibility, sets its one chosen axis visible itself
        # (`variable_figure`) and loads no axis script.
        layout["yaxis3"] = _secondary_axis("Error [signal units]")
        layout["yaxis4"] = {
            **_secondary_axis("NRMSE [–]"),
            # The report's legend can show signed and NRMSE together, and a
            # second right axis anchored to x would draw over the first;
            # free + autoshift stacks it outward (plotly.js >= 2.25; the
            # report vendors 2.35.0, NiceGUI 3.16 bundles 3.1.1). The tool
            # shows one at a time and anchors it (`variable_figure`).
            "anchor": "free",
            "autoshift": True,
        }
    return layout


def _secondary_axis(title: str) -> dict:
    return {
        "title": {"text": title, "font": {"color": ERROR, "size": 10}},
        **_Y_LABELS,
        "nticks": 4,
        "automargin": True,
        "overlaying": "y",
        "side": "right",
        "showgrid": False,
        "tickfont": {"color": ERROR, "size": 10},
        "zeroline": False,
        "visible": False,
    }


#: The key of the editable figure's ``layout.meta`` that says which of its
#: shapes take the pointer — read by the page's ``grips.js``, which derives
#: nothing from a shape's name:
#:
#: * ``grips``: per grip shape, by name, the ``group`` it shows with
#:   (:func:`~dstf.comparison.geometry.grip_group`), the ``axis`` it moves
#:   on (the page's cursor over it), ``outward`` — ``+1`` or ``-1``, the
#:   way along that axis its arrow is DRAWN from its anchor
#:   (:func:`_grip_way`), which is where the page takes the pointer to be
#:   on it — and the ``label`` it carries
#:   as its tooltip (:func:`~dstf.comparison.geometry.grip_label` — the
#:   words are Python's);
#: * ``targets``: per ring a group's grips stand around, by name, that
#:   group — a checkpoint's ring is one of the places its arrows are near;
#: * ``draggable``: the names an editor moves — the ringed leaves' rings,
#:   grips and ``kind="handle"`` lines. Every OTHER shape of the figure (a
#:   box, a window, a result line, a leaf that is not open) the page makes
#:   inert: under ``edits.shapePosition`` Plotly would drag it all the same.
#:
#: IN the figure, not beside it: whatever figure the browser holds, this is
#: the map of that figure — the two cannot arrive out of step.
POINTER = "dstf"


def _no_pointer() -> dict:
    return {"grips": {}, "targets": {}, "draggable": []}


def drags_of(geometry: Iterable[Primitive]) -> Literal["rings", "lines", ""]:
    """What the editable figure gives a ringed leaf of ``geometry`` to be
    dragged by, in a word — :func:`_leaf_traces`' own rule, for a caller
    that has to SAY it (the editor's hint line): ``"rings"`` where it draws
    a ring (a handle marker) or an arrow (a grip), with or without lines;
    ``"lines"`` where the handle lines are all an editor moves; ``""``
    where nothing is. Read off the geometry, so the words follow what a
    metric draws and no list of metric names."""
    lines = False
    for prim in geometry:
        if prim.kind == "grip" or (isinstance(prim, Marker) and prim.kind == "handle"):
            return "rings"
        lines = lines or (isinstance(prim, HLine) and prim.kind == "handle")
    return "lines" if lines else ""


def _leaf_traces(
    view: VariableView,
    ctx: _Ctx,
    *,
    editable: bool,
    rings_for: Collection[str] | None = None,
    visibility: Visibility | None = None,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Every leaf of one view, drawn: ``(traces, shapes, rings, pointer)``
    — the rings holding the open leaves' grips too, ``pointer`` what
    :data:`POINTER` says of them.

    The one loop both figure builders run — so a new primitive type, or a
    change to what a handle ring is, lands in a single place. A
    ``kind="grip"`` primitive is never a trace and never one of the
    figure's own shapes: it is drawn with its leaf's rings or not at all,
    so a figure that is not editable is what the same geometry without its
    grips draws.
    """
    if isinstance(rings_for, str):
        raise TypeError(
            "rings_for takes a collection of leaf paths, not a str "
            "(a str matches by substring)"
        )
    data: list[dict] = []
    shapes: list[dict] = []
    rings: list[dict] = []
    pointer = _no_pointer()
    for leaf in view.leaves:
        window = next((p for p in leaf.geometry if isinstance(p, Window)), None)
        markers: list[Marker] = []
        grips: list[Primitive] = []
        leaf_data: list[dict] = []
        leaf_shapes: list[dict] = []
        lines: list[str] = []  # the shapes of this leaf's handle lines
        for prim in leaf.geometry:
            if prim.kind == "grip":
                grips.append(prim)
                continue
            if isinstance(prim, Marker):
                markers.append(prim)
                continue
            builder = TRACE_BUILDERS.get(type(prim))
            if builder is None:
                raise TypeError(
                    f"no figure builder for {type(prim).__name__} "
                    f"(id {prim.id!r}); add one to TRACE_BUILDERS"
                )
            traces, new_shapes = builder(prim, leaf, window, ctx)
            leaf_data.extend(traces)
            leaf_shapes.extend(new_shapes)
            if isinstance(prim, HLine) and prim.kind == "handle":
                lines.extend(s["name"] for s in new_shapes)
        leaf_data.extend(_marker_traces(markers, leaf))
        ringed = editable and (rings_for is None or leaf.path in rings_for)
        leaf_rings = _handle_rings(markers) + _grip_shapes(grips, ctx) if ringed else []
        if ringed:
            leaf_data.extend(_headroom(grips, leaf, view, ctx))
            for grip in grips:
                assert isinstance(grip, (Marker, VLine))  # `_grip_shapes`
                pointer["grips"][grip.id] = {
                    "group": grip_group(grip),
                    "axis": grip_axis(grip),
                    "outward": _grip_way(grip, ctx),
                    "label": grip_label(grip),
                }
            groups = {said["group"] for said in pointer["grips"].values()}
            for m in markers:
                if m.kind == "handle" and grip_group(m) in groups:
                    pointer["targets"][m.id] = grip_group(m)
            pointer["draggable"] += [s["name"] for s in leaf_rings] + lines
        for trace in leaf_data:
            trace["meta"] = {**trace.get("meta", {}), "leaf": leaf.path}
        if visibility is not None:
            for trace in leaf_data:
                trace["showlegend"] = False
            if leaf.path in visibility.hidden:
                # `visible: false`, never removed: a shape's INDEX is what a
                # drag's relayout names, and the hook resolves it against
                # the full list (the 2026-09-24 spike, fact 2-3).
                for item in (*leaf_data, *leaf_shapes, *leaf_rings):
                    item["visible"] = False
        data.extend(leaf_data)
        shapes.extend(leaf_shapes)
        rings.extend(leaf_rings)
    return data, shapes, rings, pointer


def _config(editable: bool) -> dict:
    """The figure's ``config``, the same for both builders. The tool's plots
    are stacked full-width down the Test page, where ``scrollZoom`` costs the
    page its wheel: one notch puts a plot under the cursor and the page never
    scrolls again, so the cards below are never drawn (measured in Chromium).
    The report draws one figure per page and keeps it. It does not depend on
    ``rings_for`` (decision 7): opening a criterion stays a `Plotly.react`."""
    config: dict = dict(PLOT_CONFIG)
    if editable:
        config["edits"] = {"shapePosition": True}
        config["scrollZoom"] = False
    return config


def variable_figure(
    view: VariableView,
    max_samples: int | None = None,
    *,
    editable: bool = False,
    rings_for: Collection[str] | None = None,
    visibility: Visibility | None = None,
) -> dict:
    """The one figure both tiers draw. ``max_samples`` decimates the view first.

    ``editable`` adds a draggable ring per handle marker, an arrow per grip
    and ``config.edits.shapePosition``; the report's figure never passes it.
    ``rings_for``: the leaf paths whose handles get a draggable ring, and
    whose grips their arrows; ``None`` rings every leaf. The Test page names the open criterion, so two leaves'
    rings never overlap; ``config`` does not depend on it. A path is matched
    whole, and a bare ``str`` is a ``TypeError`` — its membership test is by
    substring, which would ring leaves nobody named.

    ``visibility`` is what the tool's page chose: with one, only signals are
    in the legend, a hidden leaf's traces and shapes are ``visible: False``
    (kept in place, so shape indices do not move), and only the chosen error
    series shows, with its axis. Without one, the figure is the report's.
    """
    if max_samples:
        view = decimate_view(view, max_samples)
    x_min, x_max = _time_span(view)
    ctx = _Ctx(
        x_min=x_min,
        x_max=x_max,
        has_spectrum=view.has_spectrum,
        full_samples=view.full_samples,
        spectrum_range=_set_spectrum_range(view),
    )
    data: list[dict] = _base_traces(view)
    leaf_data, shapes, rings, pointer = _leaf_traces(
        view, ctx, editable=editable, rings_for=rings_for, visibility=visibility
    )
    data.extend(leaf_data)
    for s in view.errors:
        data.append(_error_trace(s, ctx, visibility))
    config: dict = _config(editable)
    layout = _layout(view, shapes + rings, bool(view.errors))
    if editable:
        layout["meta"] = {POINTER: pointer}
    if visibility is not None and view.errors:
        shown = ERROR_AXES.get(visibility.error or "")
        for axis in ("y3", "y4"):
            key = axis.replace("y", "yaxis")  # "y3" -> "yaxis3"
            layout[key]["visible"] = axis == shown
            # A hidden axis left at automargin kept the right margin it had
            # grown while shown, through every later react (Chromium: 55 px
            # after Abs error, 103 after NRMSE, against 20). The report keeps
            # both at True: its axes.js flips `visible` in the page.
            layout[key]["automargin"] = axis == shown
            # A box zoom made while the axis hides scales it too (it
            # overlays `y`), and the figure's `uirevision: "keep"` kept
            # that range: Abs error then drew as a flat line on the stale
            # [-0.49, 3.51] (the TRANSFORM gate, 2026-09-25). Keyed by the
            # chosen series, a new choice autoranges the axis, and a zoom
            # made while it shows is kept through a redraw.
            layout[key]["uirevision"] = visibility.error or "none"
        # One error axis at a time here, so NRMSE's sits on the plot's edge
        # like the other's: left free + autoshift, it floated off the plot
        # once Abs error had shown.
        layout["yaxis4"]["anchor"] = "x"
        del layout["yaxis4"]["autoshift"]
    return {"data": data, "layout": layout, "config": config}


#: A context series' line colour: the theme table's ``text-muted``, as a
#: literal — a figure's traces are data, and follow no page theme.
#: `test_the_context_colour_is_the_themes_muted_text` pins the two equal.
CONTEXT_COLOR = "#888888"


def _context_trace(view: VariableView) -> dict | None:
    """One context series: grey and thinner, on the subject's axes, with no
    reference and no criteria geometry — drawn beside the plot's subject,
    never scored there. ``None`` for a series the result lacks."""
    if not _draws_actual(view):
        return None
    assert view.act_time is not None and view.act_values is not None
    return with_hover(
        {
            "type": "scatter",
            "mode": "lines",
            "name": f"{as_text(view.label)} · context",
            "xaxis": "x",
            "yaxis": "y",
            "x": view.act_time.tolist(),
            "y": view.act_values.tolist(),
            "line": {"color": CONTEXT_COLOR, "width": 1.0},
            "meta": {
                "id": f"context/{view.name}",
                "kind": "context",
                "samples": len(view.act_time),
                "full_samples": view.full_samples.get("act", len(view.act_time)),
            },
        },
        view.label,
    )


def plot_figure(
    views: Sequence[VariableView],
    max_samples: int | None = None,
    *,
    context: Sequence[VariableView] = (),
    editable: bool = False,
    labels: Mapping[str, str] | None = None,
    rings_for: Collection[str] | None = None,
    visibility: Visibility | None = None,
) -> dict:
    """The figure for one plot of a test: ``views`` are its SUBJECT (one
    variable, or the members of one array family), ``context`` the grey
    series drawn beside it on the same y axis (:func:`_context_trace`).
    Context changes nothing the subject draws — a one-variable subject
    keeps its overlays and error traces — and brings no leaves: a context
    variable's criteria are on its own plot."""
    figure = _subject_figure(
        views,
        max_samples,
        editable=editable,
        labels=labels,
        rings_for=rings_for,
        visibility=visibility,
    )
    for view in context:
        if max_samples:
            view = decimate_view(view, max_samples)
        trace = _context_trace(view)
        if trace is not None:
            figure["data"].append(trace)
    return figure


def _subject_figure(
    views: Sequence[VariableView],
    max_samples: int | None = None,
    *,
    editable: bool = False,
    labels: Mapping[str, str] | None = None,
    rings_for: Collection[str] | None = None,
    visibility: Visibility | None = None,
) -> dict:
    """The figure of a plot's subject (DESIGN.md "Plots are modular").

    One view is :func:`variable_figure`, unchanged. Several draw every
    series in its family's hue (:mod:`.families`) — actual solid, reference
    dashed in the same colour and out of the legend, a family folded to one
    legend entry — with the geometry of every leaf of every series, and NO
    overlays or error traces: both are per-variable pickers keyed by trace
    name. ``labels`` overrides a family's legend text by family key.
    ``rings_for`` and ``visibility`` are :func:`variable_figure`'s, reaching
    every series' leaves; a plot of several has no error traces, so
    ``visibility.error`` has nothing to act on there.
    """
    if len(views) == 1:
        return variable_figure(
            views[0],
            max_samples,
            editable=editable,
            rings_for=rings_for,
            visibility=visibility,
        )
    if max_samples:
        views = [decimate_view(v, max_samples) for v in views]
    by_name = {v.name: v for v in views}
    texts = series_texts(views)
    spans = [
        _time_span(v) for v in views if v.act_time is not None or v.ref_time is not None
    ]
    x_min = min((s[0] for s in spans), default=0.0)
    x_max = max((s[1] for s in spans), default=1.0)
    data: list[dict] = []
    shapes: list[dict] = []
    rings: list[dict] = []
    pointer = _no_pointer()
    has_spectrum = any(v.has_spectrum for v in views)
    # Families fold over what each series READS as: `unitTests.x[1..3]` is a
    # position in a component, not an array — three unrelated expressions
    # under one legend entry and one colour ramp said they were one
    # quantity. Two series sharing one expression read as `label (name)`
    # (:func:`series_texts`), which belongs to no family either, so they
    # stay two legend entries and two hues.
    for h, (key, members) in enumerate(group_families(list(by_name), texts)):
        colors = ramp(HUES[h % len(HUES)], len(members))
        label = (labels or {}).get(key) or family_label(key, members, texts)
        # The legend entry rides on the first member that DRAWS: a family whose
        # lowest index has no result would otherwise have none.
        lead = next((n for n in members if _draws_actual(by_name[n])), None)
        for name, color in zip(members, colors):
            data.extend(
                _series_traces(
                    by_name[name],
                    color,
                    key,
                    label,
                    texts[name],
                    first=name == lead,
                )
            )
    # One view stands in for the layout, carrying EVERY view's leaves, so
    # `_layout` sees the spectrum panel wherever it comes from.
    if views:
        all_leaves = tuple(leaf for v in views for leaf in v.leaves)
        layout_view = replace(views[0], leaves=all_leaves)
    else:
        layout_view = VariableView(name="")
    spectrum_range = _set_spectrum_range(layout_view)
    for view in views:
        ctx = _Ctx(
            x_min=x_min,
            x_max=x_max,
            has_spectrum=has_spectrum,
            full_samples=view.full_samples,
            spectrum_range=spectrum_range,
        )
        leaf_data, leaf_shapes, leaf_rings, leaf_pointer = _leaf_traces(
            view, ctx, editable=editable, rings_for=rings_for, visibility=visibility
        )
        data.extend(leaf_data)
        shapes.extend(leaf_shapes)
        rings.extend(leaf_rings)
        pointer["grips"].update(leaf_pointer["grips"])
        pointer["targets"].update(leaf_pointer["targets"])
        pointer["draggable"] += leaf_pointer["draggable"]
    config: dict = _config(editable)
    layout = _layout(layout_view, shapes + rings, False)
    if editable:
        layout["meta"] = {POINTER: pointer}
    return {"data": data, "layout": layout, "config": config}


def _draws_actual(view: VariableView) -> bool:
    return view.act_time is not None and view.act_values is not None


def series_texts(views: Sequence[VariableView]) -> dict[str, str]:
    """``{name: what that series reads as}`` on a plot drawing several.

    Each view's own ``label``, which is already what its TEST's page reads
    for it (:func:`~dstf.backends.types.variable_texts`, applied where the
    views are built). Disambiguating HERE, over the plot's series alone,
    was a scope of its own: a variable read ``a.Q`` in this legend and
    ``a.Q (unitTests.x[1])`` in its criteria row on the same page.
    """
    return {v.name: v.label for v in views}


def _series_traces(
    view: VariableView,
    color: str,
    group: str,
    label: str,
    text: str,
    *,
    first: bool,
) -> list[dict]:
    traces = []
    # Read as `text` — the label, with the name beside it when another
    # variable of this TEST carries the same label — and keyed by the name:
    # `meta.id` is what the page, the picker and every test address a trace
    # by.
    #
    # Spelled out, not `_draws_actual(view)`: mypy narrows the arrays here.
    if view.act_time is not None and view.act_values is not None:
        traces.append(
            with_hover(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": as_text(label) if first else as_text(text),
                    "legendgroup": group,
                    "showlegend": first,
                    "x": view.act_time.tolist(),
                    "y": view.act_values.tolist(),
                    "line": {"color": color, "width": 1.5},
                    "meta": {
                        "id": f"actual/{view.name}",
                        "kind": "act",
                        "samples": len(view.act_time),
                        "full_samples": view.full_samples.get(
                            "act", len(view.act_time)
                        ),
                    },
                },
                text,
            )
        )
    if view.ref_time is not None and view.ref_values is not None:
        traces.append(
            with_hover(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": f"{as_text(text)} (reference)",
                    "legendgroup": group,
                    "showlegend": False,
                    "x": view.ref_time.tolist(),
                    "y": view.ref_values.tolist(),
                    "line": {"color": color, "width": 1.2, "dash": "dash"},
                    "opacity": 0.7,
                    "meta": {
                        "id": f"reference/{view.name}",
                        "kind": "ref",
                        "samples": len(view.ref_time),
                        "full_samples": view.full_samples.get(
                            "ref", len(view.ref_time)
                        ),
                    },
                },
                f"{text} (reference)",
            )
        )
    return traces
