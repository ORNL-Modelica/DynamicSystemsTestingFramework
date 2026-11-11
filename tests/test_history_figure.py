"""`baseline plot`'s figure (``figures.history``): one variable's overlay
across revisions over the change panel — each later revision minus the
first, on the first's grid — as a plain Plotly figure dict, read here
without a browser."""

import math

import numpy as np
import pytest

from dstf.figures.families import HUES, ramp
from dstf.figures.history import (
    LEGEND_ROW,
    TITLE_CHARS,
    WIDTH,
    HistoryTrace,
    history_figure,
)
from dstf.figures.palette import palette_of


def _trace(name, values, change=None, non_finite_at=()):
    t = np.arange(len(values), dtype=float)
    return HistoryTrace(
        name=name,
        time=t,
        values=np.asarray(values, float),
        change_time=None if change is None else t,
        change=None if change is None else np.asarray(change, float),
        non_finite_at=np.asarray(non_finite_at, float),
    )


def _figure(traces, unit="range", **kw):
    kw.setdefault("title", "T · Dymola/linux · a.p")
    kw.setdefault("y_title", "a.p")
    kw.setdefault("first", "2026-04-30 6ad1765")
    kw.setdefault("notes", [])
    return history_figure(traces=traces, unit=unit, **kw)


def _three():
    return [
        _trace("first", [0, 5, 10]),
        _trace("second", [0, 5.5, 10], [0, 5, 0]),
        _trace("newest", [0, 6, 10], [0, 10, 0]),
    ]


def _on(fig, axis):
    return [t for t in fig["data"] if t["yaxis"] == axis]


def test_the_overlay_draws_every_revision_and_the_panel_every_later_one():
    fig = _figure(_three())
    top, panel = _on(fig, "y"), _on(fig, "y2")
    assert [t["name"] for t in top] == ["first", "second", "newest"]
    assert [t["y"] for t in top] == [[0, 5, 10], [0, 5.5, 10], [0, 6, 10]]
    assert [(t["name"], t["y"]) for t in panel] == [
        ("second", [0, 5, 0]),
        ("newest", [0, 10, 0]),
    ]
    # a panel line is its revision's: one legend entry toggles both
    assert all(t["showlegend"] is False for t in panel)
    assert {t["legendgroup"] for t in panel} == {"second", "newest"}


def test_the_first_is_grey_the_later_ones_a_ramp_and_the_newest_heavier():
    fig = _figure(_three())
    top = _on(fig, "y")
    blues = ramp(HUES[0], 2)
    assert [t["line"]["color"] for t in top] == [palette_of("default").context, *blues]
    widths = [t["line"]["width"] for t in top]
    assert widths[2] > widths[1] == widths[0]
    assert [t["line"] for t in _on(fig, "y2")] == [top[1]["line"], top[2]["line"]]


@pytest.mark.parametrize(
    ("unit", "title", "said"),
    [
        ("range", "% of range", "as % of the first's range"),
        ("value", "% of value", "as % of the first's value (a constant)"),
        ("absolute", "difference", "the absolute difference (the first is all zero)"),
    ],
)
def test_the_panel_s_unit_follows_the_scale(unit, title, said):
    fig = _figure(_three(), unit=unit)
    assert fig["layout"]["yaxis2"]["title"]["text"] == title
    caption = fig["layout"]["annotations"][0]["text"]
    assert caption.startswith(
        "each later baseline minus the first (2026-04-30 6ad1765)"
    )
    assert said in caption


def test_a_non_finite_value_is_a_gap_and_a_marker_on_the_panel():
    """plotly.js drops an infinity silently: every non-finite value is a
    gap (null), and each time the difference is not finite is marked."""
    traces = [
        _trace("first", [0, 1, 2, 3]),
        _trace(
            "later", [0, math.inf, math.nan, 3.3], [0, math.nan, math.nan, 10], [1, 2]
        ),
    ]
    fig = _figure(traces)
    top = _on(fig, "y")
    assert top[1]["y"] == [0, None, None, 3.3]
    lines = [t for t in _on(fig, "y2") if t["mode"] == "lines"]
    assert lines[0]["y"] == [0, None, None, 10]
    (marks,) = [t for t in _on(fig, "y2") if t["mode"] == "markers"]
    assert (marks["x"], marks["y"]) == ([1.0, 2.0], [0, 0])
    assert marks["marker"]["color"] == top[1]["line"]["color"]


def test_the_image_grows_with_the_legend_s_rows_and_its_notes():
    two = _figure(_three()[:2])
    three = _figure(_three())
    noted = _figure(_three(), notes=["one note", "another"])
    assert three["layout"]["height"] - two["layout"]["height"] == LEGEND_ROW
    assert noted["layout"]["height"] > three["layout"]["height"]
    assert three["layout"]["width"] == WIDTH
    notes = noted["layout"]["annotations"][-1]
    assert notes["text"] == "one note<br>another"


def test_a_long_title_and_y_title_are_cut_with_an_ellipsis():
    long = "x" * 300
    fig = _figure(_three(), title=long, y_title=long)
    title = fig["layout"]["title"]["text"]
    assert len(title) == TITLE_CHARS and title.endswith("…")
    assert fig["layout"]["yaxis"]["title"]["text"].endswith("…")
    short = _figure(_three())
    assert short["layout"]["title"]["text"] == "T · Dymola/linux · a.p"


def test_the_figure_is_static_and_carries_no_template():
    fig = _figure(_three())
    assert fig["config"]["staticPlot"] is True
    assert "template" not in fig["layout"]
    # the panel shares the overlay's x axis, titled once, under the panel
    assert {t["xaxis"] for t in fig["data"]} == {"x"}
    assert fig["layout"]["xaxis"]["anchor"] == "y2"
