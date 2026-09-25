"""The Variables drawer's rows (3c; DESIGN.md "The Variables drawer"): the
groups, the folds, the filter, the cap, and each row's state on the
selected plot — computed without a page."""

from __future__ import annotations

import re

import pytest

from dstf.authoring.variable_rows import cap, matcher, variable_groups
from dstf.discovery.plots import PlotSpec, SeriesSpec

NAMES = [
    "x",
    "y",
    "a0",
    "z",
    "pipe.T[1]",
    "pipe.T[2]",
    "pipe.T[3]",
    "port[2, 1].C[1]",
    "port[2, 1].C[2]",
    "CPUtime",
]


def _l(*plots):
    return tuple(PlotSpec(series=tuple(SeriesSpec(v) for v in p)) for p in plots)


def _groups(**kw):
    args = dict(
        names=NAMES,
        tracked=["x", "y"],
        diagnostics=["CPUtime", "EventCounter"],
        labels={},
        layout=_l(["x"], ["y", "pipe.T[1]"]),
        selected=0,
        scored={"x": 1, "y": 2},
        keep=lambda text: True,
    )
    args.update(kw)
    return variable_groups(**args)


def test_groups_are_tracked_then_by_prefix_then_top_level_then_diagnostics():
    assert [g.title for g in _groups()] == [
        "Tracked",
        "pipe",
        "port",
        "(top level)",
        "Diagnostics",
    ]


def test_a_family_is_one_row_over_its_members():
    pipe = _groups()[1]
    (row,) = pipe.rows
    assert row.members == ("pipe.T[1]", "pipe.T[2]", "pipe.T[3]")
    assert row.text == "pipe.T[1:3]"
    port = _groups()[2].rows[0]
    assert port.text == "port[2, 1].C[1:2]"


def test_a_row_knows_the_selected_plot_and_the_others():
    tracked = {r.members[0]: r for r in _groups()[0].rows}
    assert tracked["x"].on_selected == 1 and tracked["x"].elsewhere == ()
    assert tracked["y"].on_selected == 0 and tracked["y"].elsewhere == (1,)
    pipe = _groups()[1].rows[0]
    assert (pipe.on_selected, pipe.elsewhere) == (0, (1,))


def test_a_scored_variable_on_its_last_plot_is_stranded_there():
    tracked = {r.members[0]: r for r in _groups()[0].rows}
    assert tracked["x"].stranded == ("x",)  # plot 0 is x's only plot
    assert tracked["y"].stranded == ()  # y is not on plot 0 at all
    moved = {r.members[0]: r for r in _groups(selected=1)[0].rows}
    assert moved["y"].stranded == ("y",)
    assert tracked["x"].criteria == 1
    # pipe.T[1] is on plot 1 alone too, and no criterion scores it
    assert _groups(selected=1)[1].rows[0].stranded == ()


def test_no_selected_plot_means_nothing_on_it():
    rows = _groups(layout=(), selected=None)[0].rows
    assert all(r.on_selected == 0 and r.stranded == () for r in rows)


def test_labels_are_read_and_the_name_rides_beside():
    labels = {"x": "a.Q"}
    row = _groups(labels=labels)[0].rows[0]
    assert (row.text, row.name) == ("a.Q", "x")
    assert _groups()[0].rows[0].name is None
    keep = matcher("a.Q", regex=False, case=False)
    (group,) = _groups(labels=labels, keep=keep)
    assert [r.members for r in group.rows] == [("x",)]


def test_the_filter_keeps_members_and_folds_over_what_it_kept():
    keep = matcher("pipe.T[?]", regex=False, case=False)
    groups = _groups(keep=keep)
    assert [g.title for g in groups] == ["pipe"]
    keep2 = matcher("T[2]", regex=False, case=False)
    (row,) = _groups(keep=keep2)[0].rows
    assert row.members == ("pipe.T[2]",) and row.text == "pipe.T[2]"


@pytest.mark.parametrize(
    ("needle", "regex", "case", "text", "hit"),
    [
        ("pipe", False, False, "PIPE.t[1]", True),  # substring, any case
        ("pipe", False, True, "PIPE.t[1]", False),
        ("pipe.T[*]", False, False, "pipe.T[12]", True),  # brackets literal
        ("pipe.T*", False, False, "a.pipe.T[1]", False),  # a glob is anchored
        ("^port", True, False, "port[2, 1].C[1]", True),
        ("", False, False, "anything", True),
    ],
)
def test_matcher(needle, regex, case, text, hit):
    assert matcher(needle, regex=regex, case=case)(text) is hit


def test_a_bad_regex_raises_for_the_drawer_to_say_so():
    with pytest.raises(re.error):
        matcher("(", regex=True, case=False)


def test_cap_counts_top_level_rows_and_says_what_it_left_out():
    many = [f"v{i}" for i in range(250)]
    groups = _groups(names=many, tracked=[], layout=(), selected=None, scored={})
    kept, shown, total = cap(groups, 200)
    assert (shown, total) == (200, 250)
    assert sum(len(g.rows) for g in kept) == 200
