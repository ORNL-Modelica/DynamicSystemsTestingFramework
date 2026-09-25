"""The Variables drawer's rows (3c; DESIGN.md "The Variables drawer"): the
groups, the folds, the filter, the cap, and each row's state on the
selected plot — computed without a page."""

from __future__ import annotations

import re

import pytest

from dstf.authoring import variable_rows
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
        texts={},
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
    row = _groups(labels=labels, texts=labels)[0].rows[0]
    assert (row.text, row.name) == ("a.Q", "x")
    assert _groups()[0].rows[0].name is None
    keep = matcher("a.Q", regex=False, case=False)
    (group,) = _groups(labels=labels, texts=labels, keep=keep)
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


def test_a_mixed_row_is_an_add_so_nothing_on_it_is_stranded():
    """A row only partly on the selected plot adds its missing members on a
    click, so no removal is on offer and nothing is stranded — under a
    layout of one plot per scored variable, every folded row is mixed."""
    fam = ["pipe.T[1]", "pipe.T[2]", "pipe.T[3]"]
    mixed = _groups(
        names=fam, tracked=[], layout=_l(["pipe.T[2]"]), scored={"pipe.T[2]": 1}
    )[0].rows[0]
    assert (mixed.on_selected, mixed.stranded, mixed.criteria) == (1, (), 0)
    full = _groups(names=fam, tracked=[], layout=_l(fam), scored={"pipe.T[2]": 1})[
        0
    ].rows[0]
    assert (full.on_selected, full.stranded, full.criteria) == (3, ("pipe.T[2]",), 1)


# a tracked UnitTests family whose expressions are pipe.T[i], beside the
# result file's own pipe.T[i]: two rows, one key, two ids
_PIPE = [f"pipe.T[{i}]" for i in (1, 2, 3)]
_UNIT = {f"unitTests.x[{i}]": f"pipe.T[{i}]" for i in (1, 2, 3)}


def test_a_row_id_is_unique_where_its_key_is_not():
    groups = _groups(
        names=[*_UNIT, *_PIPE],
        tracked=list(_UNIT),
        labels=_UNIT,
        texts=_UNIT,
        layout=(),
        selected=None,
        scored={},
    )
    rows = [r for g in groups for r in g.rows]
    assert [r.key for r in rows] == ["pipe.T[]", "pipe.T[]"]
    assert [r.id for r in rows] == ["0/Tracked/pipe.T[]", "1/pipe/pipe.T[]"]


def test_a_row_id_is_unique_where_group_title_and_key_are_not():
    """A tracked family whose expressions read ``Tracked.a[i]`` beside the
    result file's own ``Tracked.a[i]``, untracked: both groups are titled
    "Tracked" and both rows key ``Tracked.a[]``; only the rank tells them
    apart."""
    unit = {f"unitTests.x[{i}]": f"Tracked.a[{i}]" for i in (1, 2)}
    groups = _groups(
        names=[*unit, *unit.values()],
        tracked=list(unit),
        labels=unit,
        texts=unit,
        layout=(),
        selected=None,
        scored={},
    )
    assert [(g.rank, g.title) for g in groups] == [(0, "Tracked"), (1, "Tracked")]
    rows = [r for g in groups for r in g.rows]
    assert [r.key for r in rows] == ["Tracked.a[]", "Tracked.a[]"]
    assert len({r.id for r in rows}) == 2


def _reference(names, layout, selected, scored):
    """Each lone row's state on the selected plot, the slow plain way."""
    out = {}
    for n in names:
        plots = [i for i, p in enumerate(layout) if any(s.var == n for s in p.series)]
        on = selected in plots
        stuck = (n,) if on and n in scored and plots == [selected] else ()
        out[n] = (int(on), tuple(i for i in plots if i != selected), stuck)
    return out


def test_the_layout_is_indexed_once_not_scanned_per_name(monkeypatch):
    def scan(*_):
        raise AssertionError("plots_showing scanned the layout for one name")

    monkeypatch.setattr(variable_rows, "plots_showing", scan, raising=False)
    names = [f"v{i}" for i in range(40)]
    layout = _l(
        names[0:10],
        [*names[5:15], "v5"],  # a variable twice on one plot is one plot
        names[12:13],
        ["v0", "v39"],
    )
    scored = {n: 1 for n in names[::3]}
    for selected in (None, 0, 1, 2, 3):
        groups = _groups(
            names=names, tracked=[], layout=layout, selected=selected, scored=scored
        )
        got = {
            r.members[0]: (r.on_selected, r.elsewhere, r.stranded)
            for g in groups
            for r in g.rows
        }
        assert got == _reference(names, layout, selected, scored)


def test_cap_counts_rows_across_groups_not_groups():
    names = [
        *(f"a.v{i}" for i in range(5)),
        *(f"b.v{i}" for i in range(300)),
        *(f"c.v{i}" for i in range(10)),
    ]
    groups = _groups(names=names, tracked=[], layout=(), selected=None, scored={})
    kept, shown, total = cap(groups, 200)
    assert [(g.title, len(g.rows)) for g in kept] == [("a", 5), ("b", 195)]
    assert (shown, total) == (200, 315)


def test_a_prefix_named_like_a_group_is_its_own_group():
    groups = _groups(names=["x", "Tracked.a", "Diagnostics.b", "CPUtime"])
    assert [(g.rank, g.title) for g in groups] == [
        (0, "Tracked"),
        (1, "Diagnostics"),
        (1, "Tracked"),
        (3, "Diagnostics"),
    ]
    assert [r.members for r in groups[0].rows] == [("x",)]
    ids = [r.id for g in groups for r in g.rows]
    assert len(ids) == len(set(ids))


def test_the_fold_is_over_the_texts_and_the_row_reads_the_label():
    """x={pipe.T[1], pipe.T[1]}: two variables, one label. Their texts carry
    the name, so they fold into no family (the report's plot-title rule);
    each row reads the plain label with its name beside it."""
    labels = {"u1": "pipe.T[1]", "u2": "pipe.T[1]"}
    texts = {"u1": "pipe.T[1] (u1)", "u2": "pipe.T[1] (u2)"}
    (group,) = _groups(
        names=["u1", "u2"],
        tracked=["u1", "u2"],
        labels=labels,
        texts=texts,
        layout=(),
        selected=None,
        scored={},
    )
    assert [(r.members, r.text, r.name) for r in group.rows] == [
        (("u1",), "pipe.T[1]", "u1"),
        (("u2",), "pipe.T[1]", "u2"),
    ]


def test_prefix_groups_sort_without_case():
    groups = _groups(names=["alpha.x", "Zeta.y", "$DER.z"], tracked=[])
    assert [g.title for g in groups] == ["$DER", "alpha", "Zeta"]
