"""A running test's page as data (D151): reader snapshots to the series a
plot draws, and the words of every state — no Session, no UI, no file."""

from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest

from dstf.authoring import live_rows
from dstf.authoring.live_rows import (
    NOT_SCORED,
    WAITING,
    LiveTrace,
    Partial,
    progress,
    seconds,
    spanned,
    stopped_marker,
    unscored_view,
)
from dstf.backends.common.live_mat import (
    GONE,
    RUNNING,
    STARTING,
    LiveSeries,
    LiveSnapshot,
)
from dstf.backends.types import VariableText
from dstf.comparison.tree_spec import parse_metric_tree


def _snap(state=RUNNING, run=0, rows=0, time=None, **series) -> LiveSnapshot:
    return LiveSnapshot(
        state=state,
        run=run,
        rows=rows,
        time=time,
        series=MappingProxyType(
            {
                name: LiveSeries(np.asarray(t, dtype=float), np.asarray(v, dtype=float))
                for name, (t, v) in series.items()
            }
        ),
    )


def _xy(trace: LiveTrace, name: str) -> tuple[list[float], list[float]]:
    time, values = trace.series(name)
    return time.tolist(), values.tolist()


# -- snapshots to series ---------------------------------------------------------


def test_a_trace_starts_gone_then_starting_then_running():
    trace = LiveTrace(cap=100)
    assert (trace.state, trace.time, trace.series("x")) == (GONE, None, None)
    trace.take(_snap(GONE))
    assert trace.state == GONE
    trace.take(_snap(STARTING))
    assert (trace.state, trace.time) == (STARTING, None)
    trace.take(_snap(RUNNING, rows=2, time=0.5, x=([0.0, 0.5], [1.0, 2.0])))
    assert (trace.state, trace.time) == (RUNNING, 0.5)
    assert _xy(trace, "x") == ([0.0, 0.5], [1.0, 2.0])


def test_samples_accumulate_poll_by_poll_and_an_empty_poll_adds_nothing():
    trace = LiveTrace(cap=100)
    trace.take(_snap(rows=2, time=1.0, x=([0.0, 1.0], [0.0, 10.0])))
    assert trace.moved("x") is True
    trace.take(_snap(rows=2, time=1.0, x=([], [])))
    assert trace.moved("x") is False
    trace.take(_snap(rows=3, time=2.0, x=([2.0], [20.0])))
    assert trace.moved("x") is True
    assert _xy(trace, "x") == ([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
    assert trace.samples("x") == 3


def test_a_new_run_drops_every_sample_of_the_one_before():
    """`run` going up is the reader starting over on another file (a rerun
    wiped the directory): nothing of the old run is drawn under the new."""
    trace = LiveTrace(cap=100)
    trace.take(_snap(run=0, rows=2, time=1.0, x=([0.0, 1.0], [5.0, 6.0])))
    trace.take(_snap(GONE, run=1))
    assert (trace.state, trace.time, trace.series("x")) == (GONE, None, None)
    trace.take(_snap(run=1, rows=1, time=0.0, x=([0.0], [9.0])))
    assert _xy(trace, "x") == ([0.0], [9.0])
    # ... and a reader that started over between two RUNNING polls
    trace.take(_snap(run=2, rows=1, time=0.25, x=([0.25], [7.0])))
    assert _xy(trace, "x") == ([0.25], [7.0])


def test_a_name_first_asked_late_has_its_own_series():
    trace = LiveTrace(cap=100)
    trace.take(_snap(rows=2, time=1.0, x=([0.0, 1.0], [0.0, 1.0])))
    assert trace.series("y") is None
    # the reader serves a late name its rows from the start
    trace.take(
        _snap(rows=3, time=2.0, x=([2.0], [2.0]), y=([0.0, 1.0, 2.0], [3, 4, 5]))
    )
    assert _xy(trace, "x") == ([0.0, 1.0, 2.0], [0.0, 1.0, 2.0])
    assert _xy(trace, "y") == ([0.0, 1.0, 2.0], [3.0, 4.0, 5.0])


def test_a_long_series_is_drawn_under_the_cap_and_ends_on_the_newest_sample():
    trace = LiveTrace(cap=40)
    time = np.arange(1000.0)
    for first in range(0, 1000, 50):
        chunk = time[first : first + 50]
        trace.take(_snap(rows=first + 50, time=chunk[-1], x=(chunk, np.sin(chunk))))
        drawn_t, drawn_v = trace.series("x")
        assert len(drawn_t) == len(drawn_v) <= 40
        assert drawn_t[0] == 0.0 and drawn_t[-1] == chunk[-1]
        assert np.all(np.diff(drawn_t) > 0)
    assert trace.samples("x") == 1000


def test_the_decimation_is_the_figure_layer_s_and_not_run_on_every_poll(monkeypatch):
    """LTTB costs 12 ms whatever it is given: a series past the cap is
    decimated once and its new samples drawn after that, until they are a
    quarter of the cap."""
    calls = []
    lttb = live_rows.lttb
    monkeypatch.setattr(
        live_rows, "lttb", lambda t, v, n: calls.append(len(t)) or lttb(t, v, n)
    )
    trace = LiveTrace(cap=40)
    time = np.arange(400.0)
    trace.take(_snap(rows=100, time=99.0, x=(time[:100], time[:100])))
    trace.series("x")
    assert calls == [100]
    for n in range(100, 109):  # nine more samples, one a poll
        trace.take(_snap(rows=n + 1, time=float(n), x=(time[n : n + 1], [float(n)])))
        assert trace.series("x")[0][-1] == n
    assert calls == [100]
    trace.take(_snap(rows=140, time=139.0, x=(time[109:140], time[109:140])))
    assert len(trace.series("x")[0]) <= 40 and calls == [100, 140]


def test_what_a_trace_holds_is_bounded_and_stays_evenly_spaced():
    """An hour of a fast run is millions of rows a name: past the bound the
    samples are thinned to every second one, the ones still to come too."""
    trace = LiveTrace(cap=10, bound=64)
    time = np.arange(1000.0)
    for first in range(0, 1000, 7):
        chunk = time[first : first + 7]
        trace.take(_snap(rows=first + 7, time=chunk[-1], x=(chunk, chunk * 2)))
        assert trace.held("x") <= 65  # the bound, and the newest sample
    assert trace.samples("x") == 1000
    held_t, held_v = trace._names["x"].arrays()
    steps = np.diff(held_t[:-1])
    assert len(set(steps.tolist())) == 1 and steps[0] >= 16  # one stride
    assert held_t[-1] == 999.0 and np.array_equal(held_v, held_t * 2)


# -- the words ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (42.0, "42"),
        (19.2, "19.2"),
        (5.851, "5.85"),
        (100.0, "100"),
        (1234.56, "1235"),
        (0.0, "0"),
        (0.00125, "0.00125"),
        (86400.0, "86400"),
    ],
)
def test_seconds_reads_three_figures_and_never_an_exponent(value, text):
    assert seconds(value) == text


def test_progress_says_how_far_of_how_far_or_that_it_is_starting():
    assert progress(GONE, None, 100.0) == "starting"
    assert progress(STARTING, None, 100.0) == "starting"
    # a file with its header and no row yet
    assert progress(RUNNING, None, 100.0) == "starting"
    assert progress(RUNNING, 42.0, 100.0) == "t = 42 / 100 s"
    assert progress(RUNNING, 42.0, None) == "t = 42 s"


@pytest.mark.parametrize(
    ("how", "sentence"),
    [
        (live_rows.CANCELLED, "Cancelled at t = 19.2 of 200 s — not scored"),
        (live_rows.TIMED_OUT, "Timed out at t = 19.2 of 200 s — not scored"),
        (live_rows.FAILED, "Failed at t = 19.2 of 200 s — not scored"),
    ],
)
def test_a_partial_result_says_how_it_ended_and_where(how, sentence):
    partial = Partial(how, 19.2, 200.0)
    assert partial.sentence == sentence
    assert partial.marker == "stopped at t = 19.2 s"
    assert Partial(how, 19.2, None).sentence == sentence.replace(" of 200 s", " s")


def test_ended_reads_how_an_unfinished_run_ended():
    assert live_rows.how_ended(cancelled=True, timed_out=False) == live_rows.CANCELLED
    assert live_rows.how_ended(cancelled=False, timed_out=True) == live_rows.TIMED_OUT
    assert live_rows.how_ended(cancelled=False, timed_out=False) == live_rows.FAILED
    # a stop asked of a run that then timed out is a cancel (the pool's rule)
    assert live_rows.how_ended(cancelled=True, timed_out=True) == live_rows.CANCELLED


# -- the criteria, unscored --------------------------------------------------------

TREE = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x", "tolerance": 0.01},
        {
            "combinator": "warn",
            "children": [{"metric": "range", "variable": "y", "min_value": 0.0}],
        },
    ],
}


def test_an_unscored_view_is_the_tree_with_the_word_and_no_verdict():
    texts = {"x": VariableText(label="a.Q", text="a.Q (x)")}
    view = unscored_view(parse_metric_tree(TREE), texts, WAITING)
    assert view["unscored"] == WAITING and "passed" not in view
    first, warn = view["children"]
    assert first["path"] == "/metrics/children/0"
    assert (first["metric"], first["variable"]) == ("nrmse", "x")
    # what its row reads, and what it says where a verdict's readout goes
    assert (first["label"], first["criterion"]) == ("a.Q (x)", WAITING)
    assert "passed" not in first and first["advisory"] is False
    (ranged,) = warn["children"]
    assert (ranged["label"], ranged["criterion"]) == ("y", WAITING)
    assert ranged["advisory"] is True and "passed" not in warn
    assert unscored_view(parse_metric_tree(TREE), {}, NOT_SCORED)["unscored"] == (
        NOT_SCORED
    )


# -- the figure ----------------------------------------------------------------------

FIGURE = {
    "data": [{"x": [0, 1], "y": [0, 1]}],
    "layout": {
        "xaxis": {"title": {"text": "time"}, "uirevision": "keep"},
        "yaxis": {"uirevision": "keep"},
        "yaxis3": {"uirevision": "error/abs"},
        "xaxis2": {"title": {"text": "Hz"}, "uirevision": "keep"},
        "uirevision": "keep",
        "shapes": [{"type": "rect", "name": "a"}],
    },
}


def test_spanned_sets_the_time_axis_to_the_whole_run_on_a_copy():
    figure = spanned(FIGURE, 100.0, "run 1")
    assert figure["layout"]["xaxis"]["range"] == [0.0, 100.0]
    assert "range" not in figure["layout"]["xaxis2"]  # the spectrum's is not time
    assert "range" not in FIGURE["layout"]["xaxis"]
    # no stop time, or none ahead of the start: the axis stays the data's
    for unknown in (None, 0.0):
        assert "range" not in spanned(FIGURE, unknown, "run 1")["layout"]["xaxis"]


@pytest.mark.parametrize("stop_time", [100.0, None, 0.0])
def test_a_spanned_figure_is_another_revision_of_the_plot(stop_time):
    """The time axis's range changes when a run starts and when it ends.
    Under the figure's own revision the browser would then reset that axis
    and keep a zoom's y range: every axis the figure pinned moves to the
    run's revision, so the plot starts over whole — and keeps a zoom made
    mid-run, the revision standing for as long as the run. With no stop
    time known too: the figure was handed back under the RESULT's
    revision, so a zoom made on the last result held the growing trace."""
    layout = spanned(FIGURE, stop_time, "run 3")["layout"]
    assert layout["uirevision"] == "run 3"
    for axis in ("xaxis", "yaxis", "xaxis2"):
        assert layout[axis]["uirevision"] == "run 3"
    assert layout["yaxis3"]["uirevision"] == "error/abs"  # the error axes' own
    assert FIGURE["layout"]["uirevision"] == "keep"
    assert FIGURE["layout"]["yaxis"]["uirevision"] == "keep"


def test_no_two_runs_share_a_revision_and_none_is_the_figure_layer_s():
    revisions = [live_rows.run_revision(n) for n in range(1, 50)]
    assert len(set(revisions)) == len(revisions) and "keep" not in revisions


def test_the_stopped_marker_is_a_line_and_its_words_in_the_given_colour():
    partial = Partial(live_rows.CANCELLED, 5.851, 200.0)
    figure = stopped_marker(FIGURE, partial, "#9e9e9e")
    assert len(FIGURE["layout"]["shapes"]) == 1  # a copy
    rect, line = figure["layout"]["shapes"]
    assert rect == {"type": "rect", "name": "a"}
    assert (line["type"], line["x0"], line["x1"]) == ("line", 5.851, 5.851)
    assert (line["xref"], line["yref"], line["y0"], line["y1"]) == ("x", "paper", 0, 1)
    assert line["line"]["color"] == "#9e9e9e" and line["name"] == live_rows.STOPPED
    assert line["editable"] is False
    (note,) = figure["layout"]["annotations"]
    assert note["text"] == "stopped at t = 5.85 s" and note["x"] == 5.851
    assert note["font"]["color"] == "#9e9e9e"
