"""One variable's stored baseline across revisions, as a Plotly figure dict
(``dstf baseline plot``): the overlay of every revision on top, and under
it the change panel — each later revision minus the first, on the first's
grid, as a percent of the first's range (``baseline diff``'s Change
measure; of its value for a constant, the absolute difference where it is
all zero). Plain dicts, no ``plotly.py``; the image is drawn from it by
``reporting.images``.

Light only, the default theme's palette (an image has no theme): the first
revision in the grey of a context series, the later ones along the first
hue's ramp, the newest drawn heavier. The legend lists the revisions one
per row under the x axis, and the image grows taller with its rows and the
notes under it, so nothing collides at any count. A value that is not
finite is a gap (plotly.js drops an infinity silently), and each time the
difference is not finite is marked on the panel's zero line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .families import ramp
from .palette import palette_of

#: The image's width, in pixels.
WIDTH = 1000
#: The title's height above the plot, the two panels', and the x axis's
#: ticks and title under them. Margins are fixed (no automargin), so the
#: legend and the notes are placed by these numbers alone: measured in
#: Chromium, the legend's top 5 px under the x title, the notes' under the
#: legend's bottom, at 2, 5 and 12 revisions.
_TOP, _PLOT, _X_AXIS = 46, 470, 60
#: One legend row, the legend's own padding, one line of the notes.
LEGEND_ROW, _LEGEND_PAD, _NOTE_LINE = 21, 10, 16
#: The longest title (an ellipsis included) that fits the image at 14 px,
#: and the longest y axis title along the overlay's height.
TITLE_CHARS, _Y_TITLE_CHARS = 120, 44
#: The overlay's and the panel's share of the plot's height.
_OVERLAY, _PANEL = (0.42, 1.0), (0.0, 0.34)
_LINE, _NEWEST = 1.6, 2.6
_PANEL_TITLE = {"range": "% of range", "value": "% of value", "absolute": "difference"}
_PANEL_SAID = {
    "range": "as % of the first's range",
    "value": "as % of the first's value (a constant)",
    "absolute": "the absolute difference (the first is all zero)",
}
#: The words for what each line is (``baseline plot``'s baselines, ``plot``'s
#: runs): the panel's caption, and the notes naming a line not held or the
#: first that holds one (``{}``: the line's short name).
KIND_WORDS = {
    "baseline": {
        "each later": "each later baseline",
        "not held": "Not stored at {}.",
        "first held": "First stored at {}",
    },
    "run": {
        "each later": "each later run",
        "not held": "Not in run {}.",
        "first held": "First in run {}",
    },
}
_GRID = "#eeeeee"
_MUTED = "#555555"


@dataclass(frozen=True)
class HistoryTrace:
    """One revision's line of the variable: its legend name, its series,
    and — for each revision after the first — its difference from the
    first on the first's grid, with the times that difference is not
    finite."""

    name: str
    time: np.ndarray
    values: np.ndarray
    change_time: np.ndarray | None = None
    change: np.ndarray | None = None
    non_finite_at: np.ndarray | None = None


def cut(text: str, limit: int) -> str:
    """``text`` at most ``limit`` characters, an ellipsis ending a cut one."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _gaps(values: np.ndarray) -> list[float | None]:
    """A series as plotly.js reads it: every non-finite value a gap."""
    return [float(v) if np.isfinite(v) else None for v in np.asarray(values, float)]


def history_figure(
    *,
    title: str,
    y_title: str,
    traces: Sequence[HistoryTrace],
    unit: str,
    first: str,
    notes: Sequence[str],
    kind: str = "baseline",
) -> dict:
    """The figure of one variable: ``traces`` oldest first, the first the
    one every other is measured against. ``unit`` is the difference's
    scale (``range``, ``value`` or ``absolute``); ``first`` names the first
    revision in the panel's caption, ``kind`` what each line is there
    ("baseline", "run"); ``notes`` are lines under the legend (revisions
    skipped, a rename, the points not finite)."""
    palette = palette_of("default")
    colours = [palette.context, *ramp(palette.hues[0], max(len(traces) - 1, 1))]
    data: list[dict] = []
    for i, trace in enumerate(traces):
        line = {
            "color": colours[i],
            "width": _NEWEST if i == len(traces) - 1 and i else _LINE,
        }
        common = {"name": trace.name, "legendgroup": trace.name, "xaxis": "x"}
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [float(t) for t in trace.time],
                "y": _gaps(trace.values),
                "line": line,
                "yaxis": "y",
                **common,
            }
        )
        if trace.change is None or trace.change_time is None:
            continue
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [float(t) for t in trace.change_time],
                "y": _gaps(trace.change),
                "line": line,
                "yaxis": "y2",
                "showlegend": False,
                **common,
            }
        )
        marks = trace.non_finite_at
        if marks is not None and len(marks):
            data.append(
                {
                    "type": "scatter",
                    "mode": "markers",
                    "x": [float(t) for t in marks],
                    "y": [0] * len(marks),
                    "marker": {"symbol": "x", "size": 9, "color": colours[i]},
                    "yaxis": "y2",
                    "showlegend": False,
                    **common,
                }
            )

    legend_h = LEGEND_ROW * len(traces) + _LEGEND_PAD
    notes_h = _NOTE_LINE * len(notes)
    bottom = _X_AXIS + legend_h + notes_h + 12
    height = _TOP + _PLOT + bottom
    annotations = [
        {
            "text": f"{KIND_WORDS[kind]['each later']} minus the first ({first}), "
            f"{_PANEL_SAID[unit]}: baseline diff's Change measure",
            "xref": "paper",
            "yref": "paper",
            "x": 0,
            "y": _PANEL[1],
            "xanchor": "left",
            "yanchor": "bottom",
            "showarrow": False,
            "font": {"size": 11, "color": _MUTED},
        }
    ]
    if notes:
        annotations.append(
            {
                "text": "<br>".join(notes),
                "xref": "paper",
                "yref": "paper",
                "x": 0,
                "y": -(_X_AXIS + legend_h) / _PLOT,
                "xanchor": "left",
                "yanchor": "top",
                "align": "left",
                "showarrow": False,
                "font": {"size": 11, "color": _MUTED},
            }
        )
    layout = {
        "width": WIDTH,
        "height": height,
        "paper_bgcolor": "white",
        "plot_bgcolor": "white",
        "margin": {"l": 76, "r": 20, "t": _TOP, "b": bottom},
        "title": {"text": cut(title, TITLE_CHARS), "x": 0.01, "font": {"size": 14}},
        "showlegend": True,
        "legend": {
            "orientation": "v",
            "x": 0,
            "xanchor": "left",
            # in the image's own height: a legend under the plot otherwise
            # moves the plot's margins
            "yref": "container",
            "y": (bottom - _X_AXIS) / height,
            "yanchor": "top",
            "font": {"size": 11},
            "tracegroupgap": 0,
        },
        "xaxis": {
            "anchor": "y2",
            "title": {"text": "time (s)"},
            "gridcolor": _GRID,
            "automargin": False,
        },
        "yaxis": {
            "domain": list(_OVERLAY),
            "title": {"text": cut(y_title, _Y_TITLE_CHARS)},
            "gridcolor": _GRID,
            "automargin": False,
        },
        "yaxis2": {
            "domain": list(_PANEL),
            "title": {"text": _PANEL_TITLE[unit]},
            "gridcolor": _GRID,
            "automargin": False,
            "zeroline": True,
            "zerolinecolor": "#bbbbbb",
        },
        "annotations": annotations,
    }
    return {"data": data, "layout": layout, "config": {"staticPlot": True}}
