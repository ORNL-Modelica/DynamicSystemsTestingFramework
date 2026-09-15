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
one pixel-sized ring shape per ``kind="handle"`` marker and turns on
``config.edits.shapePosition``. That makes every shape draggable — plotly.js
offers no per-shape switch that works (measured on plotly.js 3.8.2, the
build NiceGUI 3.16 bundles) — and the rings are what an editor attaches to,
since Plotly drags shapes, not trace points.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..comparison.geometry import (
    Band,
    Box,
    HLine,
    Marker,
    Primitive,
    Series,
    VLine,
    Window,
)
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
#: for the per-point NRMSE. Unknown ids ride on the signal-units axis.
ERROR_AXES = {"error/signed": "y3", "error/abs": "y3", "error/nrmse": "y4"}

#: Vertical extent of the two panels when a spectrum is present.
TIME_DOMAIN = (0.45, 1.0)
SPECTRUM_DOMAIN = (0.0, 0.32)

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


def _handle_rings(markers: list[Marker]) -> list[dict]:
    """One draggable ring per handle marker, named with the marker's id.

    The fill is visible on purpose: a shape drags by its painted area, and a
    transparent fill is not guaranteed to receive the pointer.
    """
    return [
        {
            "type": "circle",
            "xref": _xref(m),
            "yref": "y2" if m.panel == "spectrum" else "y",
            "xsizemode": "pixel",
            "ysizemode": "pixel",
            "xanchor": m.x,
            "yanchor": m.y,
            "x0": -7,
            "x1": 7,
            "y0": -7,
            "y1": 7,
            "line": {"color": HANDLE_RING, "width": 2},
            "fillcolor": HANDLE_RING_FILL,
            "name": m.id,
        }
        for m in markers
        if m.kind == "handle"
    ]


def _base_traces(view: VariableView) -> list[dict]:
    traces = []
    if view.act_time is not None and view.act_values is not None:
        traces.append(
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
                    "full_samples": view.full_samples.get("act", len(view.act_time)),
                },
            }
        )
    if view.ref_time is not None and view.ref_values is not None:
        traces.append(
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
                    "full_samples": view.full_samples.get("ref", len(view.ref_time)),
                },
            }
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


def _error_trace(s: Series, ctx: _Ctx) -> dict:
    return {
        "type": "scatter",
        "mode": "lines",
        "x": s.x.tolist(),
        "y": s.y.tolist(),
        "name": _label(s.id),
        "line": {"color": ERROR, "width": 1},
        "opacity": 0.6,
        "yaxis": ERROR_AXES.get(s.id, "y3"),
        "visible": "legendonly",
        "hovertemplate": f"{_label(s.id)}: %{{y:.4g}}<extra></extra>",
        "meta": {
            "id": s.id,
            "kind": "error",
            "samples": len(s.x),
            "full_samples": _full(ctx, s.id, len(s.x)),
        },
    }


def _time_span(view: VariableView) -> tuple[float, float]:
    xs = [a for a in (view.act_time, view.ref_time) if a is not None and len(a)]
    if not xs:
        return 0.0, 1.0
    return float(min(a[0] for a in xs)), float(max(a[-1] for a in xs))


def _layout(view: VariableView, shapes: list[dict], has_errors: bool) -> dict:
    layout: dict = {
        "xaxis": {
            "title": {"text": "Time"},
            "hoverformat": ".6~g",
            "uirevision": "keep",
        },
        "yaxis": {
            "title": {"text": "Value", "standoff": 10},
            "tickformat": ".4g",
            "automargin": True,
            "nticks": 6,
            "uirevision": "keep",
        },
        "margin": {"t": 25, "b": 45, "l": 60, "r": 20},
        "legend": {"x": 0, "y": 1, "bgcolor": "rgba(255,255,255,0.8)"},
        "hovermode": "x unified",
        "shapes": shapes,
        "uirevision": "keep",
    }
    if view.has_spectrum:
        layout["yaxis"]["domain"] = list(TIME_DOMAIN)
        layout["xaxis2"] = {"title": {"text": "Frequency [Hz]"}, "anchor": "y2"}
        layout["yaxis2"] = {
            "title": {"text": "Magnitude"},
            "domain": list(SPECTRUM_DOMAIN),
            "anchor": "x2",
        }
    if has_errors:
        # Both axes are laid out hidden: a laid-out axis with only
        # legend-only traces gets a placeholder range and draws its zero
        # line as a stray black rule across the plot. templates/axes.js
        # shows an axis while a series on it is visible, in both tiers.
        layout["yaxis3"] = _secondary_axis("Error [signal units]")
        layout["yaxis4"] = {
            **_secondary_axis("NRMSE [–]"),
            # A second right axis anchored to x would draw over the first;
            # free + autoshift stacks it outward (plotly.js >= 2.25; the
            # report vendors 2.35.0, NiceGUI 3.16 bundles 3.1.1).
            "anchor": "free",
            "autoshift": True,
        }
    return layout


def _secondary_axis(title: str) -> dict:
    return {
        "title": {"text": title, "font": {"color": ERROR, "size": 10}},
        "tickformat": ".4g",
        "nticks": 4,
        "automargin": True,
        "overlaying": "y",
        "side": "right",
        "showgrid": False,
        "tickfont": {"color": ERROR, "size": 10},
        "zeroline": False,
        "visible": False,
    }


def variable_figure(
    view: VariableView, max_samples: int | None = None, *, editable: bool = False
) -> dict:
    """The one figure both tiers draw. ``max_samples`` decimates the view first.

    ``editable`` adds a draggable ring per handle marker and
    ``config.edits.shapePosition``; the report's figure never passes it.
    """
    if max_samples:
        view = decimate_view(view, max_samples)
    x_min, x_max = _time_span(view)
    ctx = _Ctx(
        x_min=x_min,
        x_max=x_max,
        has_spectrum=view.has_spectrum,
        full_samples=view.full_samples,
    )
    data: list[dict] = _base_traces(view)
    shapes: list[dict] = []
    rings: list[dict] = []
    for leaf in view.leaves:
        window = next((p for p in leaf.geometry if isinstance(p, Window)), None)
        markers: list[Marker] = []
        for prim in leaf.geometry:
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
            data.extend(traces)
            shapes.extend(new_shapes)
        data.extend(_marker_traces(markers, leaf))
        if editable:
            rings.extend(_handle_rings(markers))
    for s in view.errors:
        data.append(_error_trace(s, ctx))
    config: dict = dict(PLOT_CONFIG)
    if editable:
        config["edits"] = {"shapePosition": True}
    return {
        "data": data,
        "layout": _layout(view, shapes + rings, bool(view.errors)),
        "config": config,
    }
