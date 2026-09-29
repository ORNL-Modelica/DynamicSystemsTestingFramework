"""The Variables drawer's rows (3c; DESIGN.md "The Variables drawer"): the
groups, the folds, the filter, the cap, and each row's state on the
selected plot — computed without a page."""

from __future__ import annotations

import pytest

from dstf.authoring import variable_rows
from dstf.authoring.needle import matcher
from dstf.authoring.variable_rows import cap, variable_groups
from dstf.discovery.plots import ResolvedPlot, derive_plots

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
    """Plots from ``(subject, context)`` pairs, a bare list a subject alone;
    a subject no tracked name scores is a watch plot here only when said —
    the rows read the subject and the context, never ``watch``."""
    out = []
    for i, plot in enumerate(plots):
        subject, context = plot if isinstance(plot, tuple) else (plot, [])
        out.append(
            ResolvedPlot(index=i, subject=tuple(subject), context=tuple(context))
        )
    return tuple(out)


def _groups(**kw):
    args = dict(
        names=NAMES,
        tracked=["x", "y"],
        diagnostics=["CPUtime", "EventCounter"],
        labels={},
        plots=_l((["x"], ["z"]), (["y"], ["pipe.T[1]"])),
        selected=0,
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


def _by_name(groups) -> dict:
    return {r.members[0]: r for g in groups for r in g.rows}


def test_a_row_knows_the_selected_plots_context_and_the_others():
    """`on_selected` counts the members the selected plot draws as CONTEXT
    — what its check adds or takes off; `elsewhere` every other plot that
    draws a member, as subject or context."""
    rows = _by_name(_groups())
    assert (rows["x"].on_selected, rows["x"].elsewhere) == (0, ())
    assert (rows["z"].on_selected, rows["z"].elsewhere) == (1, ())
    assert (rows["y"].on_selected, rows["y"].elsewhere) == (0, (1,))
    pipe = rows["pipe.T[1]"]
    assert (pipe.on_selected, pipe.elsewhere) == (0, (1,))


def test_a_row_says_whether_it_is_a_plots_subject_and_whose():
    """The selected plot's subject is its check's grey "on"; any plot's
    subject is its "+ new plot"'s grey "already"."""
    rows = _by_name(_groups())
    assert (rows["x"].is_subject, rows["x"].subject_of) == (True, 0)
    assert (rows["y"].is_subject, rows["y"].subject_of) == (False, 1)
    assert (rows["z"].is_subject, rows["z"].subject_of) == (False, None)
    moved = _by_name(_groups(selected=1))
    assert moved["y"].is_subject and not moved["x"].is_subject
    # a family is a subject when any member is: its members are one plot's
    family = _by_name(_groups(plots=_l(["pipe.T[2]", "pipe.T[3]"])))["pipe.T[1]"]
    assert (family.is_subject, family.subject_of) == (True, 0)


def test_no_selected_plot_means_nothing_on_it():
    rows = _groups(plots=(), selected=None)[0].rows
    assert all(
        r.on_selected == 0 and not r.is_subject and r.subject_of is None for r in rows
    )


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


def test_cap_counts_top_level_rows_and_says_what_it_left_out():
    many = [f"v{i}" for i in range(250)]
    groups = _groups(names=many, tracked=[], plots=(), selected=None)
    kept, shown, total = cap(groups, 200)
    assert (shown, total) == (200, 250)
    assert sum(len(g.rows) for g in kept) == 200


def test_a_family_row_counts_the_members_in_the_context():
    """A family partly in the selected plot's context is mixed: its check
    adds the rest."""
    fam = ["pipe.T[1]", "pipe.T[2]", "pipe.T[3]"]
    mixed = _groups(names=fam, tracked=[], plots=_l((["x"], ["pipe.T[2]"])))[0]
    assert mixed.rows[0].on_selected == 1
    full = _groups(names=fam, tracked=[], plots=_l((["x"], fam)))[0].rows[0]
    assert (full.on_selected, full.is_subject) == (3, False)


# the tracked elements of an array beside the untracked rest of it: two
# rows, one key, two ids
_PIPE = [f"pipe.T[{i}]" for i in (1, 2, 3, 4)]
_UNIT = {f"unitTests.x[{i}]": f"pipe.T[{i}]" for i in (1, 2, 3)}


def test_a_row_id_is_unique_where_its_key_is_not():
    groups = _groups(
        names=_PIPE,
        tracked=_PIPE[:2],
        plots=(),
        selected=None,
    )
    rows = [r for g in groups for r in g.rows]
    assert [r.key for r in rows] == ["pipe.T[]", "pipe.T[]"]
    assert [r.id for r in rows] == ["0/Tracked/pipe.T[]", "1/pipe/pipe.T[]"]


def test_a_row_id_is_unique_where_group_title_and_key_are_not():
    """The tracked ``Tracked.a[1:2]`` beside the untracked ``Tracked.a[3:4]``:
    both groups are titled "Tracked" and both rows key ``Tracked.a[]``; only
    the rank tells them apart."""
    names = [f"Tracked.a[{i}]" for i in (1, 2, 3, 4)]
    groups = _groups(
        names=names,
        tracked=names[:2],
        plots=(),
        selected=None,
    )
    assert [(g.rank, g.title) for g in groups] == [(0, "Tracked"), (1, "Tracked")]
    rows = [r for g in groups for r in g.rows]
    assert [r.key for r in rows] == ["Tracked.a[]", "Tracked.a[]"]
    assert len({r.id for r in rows}) == 2


def _reference(names, plots, selected):
    """Each lone row's state against the plots, the slow plain way."""
    out = {}
    for n in names:
        showing = [i for i, p in enumerate(plots) if n in p.series]
        chosen = plots[selected] if selected is not None else None
        out[n] = (
            int(chosen is not None and n in chosen.context),
            tuple(i for i in showing if i != selected),
            chosen is not None and n in chosen.subject,
            next((i for i, p in enumerate(plots) if n in p.subject), None),
        )
    return out


def test_each_rows_state_is_the_plain_reading_of_the_plots():
    """The plots are indexed once for all the names (scanning every plot
    per name cost 395 ms over 6,093 names and 300 plots); what the index
    says is what reading each plot for each name says."""
    names = [f"v{i}" for i in range(40)]
    plots = _l(
        (names[0:1], names[1:10]),
        (names[5:6], names[6:15]),
        (names[12:13], []),
        (["v39"], ["v0"]),
    )
    for selected in (None, 0, 1, 2, 3):
        groups = _groups(names=names, tracked=[], plots=plots, selected=selected)
        got = {
            r.members[0]: (r.on_selected, r.elsewhere, r.is_subject, r.subject_of)
            for g in groups
            for r in g.rows
        }
        assert got == _reference(names, plots, selected)


def test_cap_counts_rows_across_groups_not_groups():
    names = [
        *(f"a.v{i}" for i in range(5)),
        *(f"b.v{i}" for i in range(300)),
        *(f"c.v{i}" for i in range(10)),
    ]
    groups = _groups(names=names, tracked=[], plots=(), selected=None)
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


def test_rows_fold_as_the_plots_do_unit_tests_elements_never():
    """The owner's ruling O3, as `derive_plots` folds: by RESULT-FILE NAME,
    and a UnitTests element never — three whose expressions read
    ``pipe.T[1:3]`` are three plots, and three rows, each reading its
    expression with its name beside it. The result's own ``pipe.T[1:3]``
    folds by name into one."""
    unit = list(_UNIT)
    (tracked, pipe) = _groups(
        names=[*unit, *_PIPE[:3]],
        tracked=unit,
        labels=_UNIT,
        unit_tests=unit,
        plots=derive_plots(None, (), tracked=unit, unit_tests=unit),
        selected=0,
    )
    assert [(r.members, r.text, r.name) for r in tracked.rows] == [
        ((n,), _UNIT[n], n) for n in unit
    ]
    assert [r.members for r in pipe.rows] == [tuple(_PIPE[:3])]
    # two variables reading one label are two rows too
    (group,) = _groups(
        names=["u1", "u2"],
        tracked=["u1", "u2"],
        labels={"u1": "pipe.T[1]", "u2": "pipe.T[1]"},
        plots=(),
        selected=None,
    )
    assert [(r.members, r.text, r.name) for r in group.rows] == [
        (("u1",), "pipe.T[1]", "u1"),
        (("u2",), "pipe.T[1]", "u2"),
    ]


def test_the_filter_asks_the_label_not_the_text():
    """A row READS the plain label, so the filter asks that and the name —
    never the disambiguated text, whose ``(u1)`` the row does not show. A
    needle only the text matches keeps nothing."""
    labels = {"u1": "pipe.T[1]", "u2": "pipe.T[1]"}
    args = dict(
        names=["u1", "u2"],
        tracked=[],
        labels=labels,
        plots=(),
        selected=None,
    )
    assert _groups(**args, keep=matcher("(u1)", regex=False, case=False)) == []
    (group,) = _groups(**args, keep=matcher("u1", regex=False, case=False))
    assert [r.members for r in group.rows] == [("u1",)]


def test_prefix_groups_sort_without_case():
    groups = _groups(names=["alpha.x", "Zeta.y", "$DER.z"], tracked=[])
    assert [g.title for g in groups] == ["$DER", "alpha", "Zeta"]


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("'a.b'.c", (1, "'a.b'")),
        ("'a[1]'.c", (1, "'a[1]'")),
        ("'a.b'", (2, "(top level)")),
        ("port[2, 1].C[1]", (1, "port")),
        ("'it\\'s.x'.c", (1, "'it\\'s.x'")),
        ("'a.b'.'c.d'.x", (1, "'a.b'")),
    ],
)
def test_a_dot_or_bracket_inside_a_quoted_identifier_is_not_a_separator(name, group):
    """`'a.b'.c` is component `'a.b'`'s `c`: it was grouped under `'a`."""
    assert variable_rows._group(name, set(), set()) == group


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("der(pipe.T[1])", (1, "pipe")),
        ("der(der(x.v))", (1, "x")),
        ("der(x)", (2, "(top level)")),
        ("der('a.b'.c)", (1, "'a.b'")),
    ],
)
def test_a_derivative_is_grouped_under_the_component_it_differentiates(name, group):
    """A result file names a derivative `der(pipe.T[1])`: it was grouped
    under a prefix `der(pipe`."""
    assert variable_rows._group(name, set(), set()) == group


def test_a_tracked_derivative_is_tracked_by_its_full_name():
    assert variable_rows._group("der(x.v)", {"der(x.v)"}, set()) == (0, "Tracked")
    assert variable_rows._group("der(x.v)", {"x.v"}, set()) == (1, "x")


# -- the drawer's group, sort and names menus (step T, Q7) --------------------


def _titles(groups) -> list[tuple[int, str]]:
    return [(g.rank, g.title) for g in groups]


def _members(group) -> list[tuple[str, ...]]:
    return [r.members for r in group.rows]


def test_flat_is_one_group_over_every_name_families_still_folded():
    (group,) = _groups(group="flat")
    assert (group.rank, group.title) == (1, "All variables")
    assert _members(group) == [
        ("x",),
        ("y",),
        ("a0",),
        ("z",),
        ("pipe.T[1]", "pipe.T[2]", "pipe.T[3]"),
        ("port[2, 1].C[1]", "port[2, 1].C[2]"),
        ("CPUtime",),
    ]


def test_tracked_first_is_tracked_then_everything_else_diagnostics_too():
    tracked, other = _groups(group="tracked")
    assert _titles([tracked, other]) == [(0, "Tracked"), (1, "Other variables")]
    assert _members(tracked) == [("x",), ("y",)]
    assert ("CPUtime",) in _members(other)
    assert ("pipe.T[1]", "pipe.T[2]", "pipe.T[3]") in _members(other)
    assert ("a0",) in _members(other)


def test_flat_folds_a_tracked_family_apart_from_the_results_own_names():
    """The tracked ``pipe.T[1:2]`` beside the untracked ``pipe.T[3:4]``:
    under Flat one group holds both, and one row over all four hid which of
    them the test tracks. The tracked and the untracked fold apart, in
    result-file order, and the tracked family's id is marked — both key
    ``pipe.T[]``. The whitespace-free id is the row's marker
    (`variables_drawer.marker`)."""
    args = dict(tracked=_PIPE[:2], plots=(), selected=None)
    (group,) = _groups(names=_PIPE, group="flat", **args)
    assert _members(group) == [tuple(_PIPE[:2]), tuple(_PIPE[2:])]
    assert [(r.text, r.file_text) for r in group.rows] == [
        ("pipe.T[1:2]", "pipe.T[1:2]"),
        ("pipe.T[3:4]", "pipe.T[3:4]"),
    ]
    ids = [r.id for r in group.rows]
    assert ids == ["1/All variables/tracked:pipe.T[]", "1/All variables/pipe.T[]"]
    assert len({"".join(i.split()) for i in ids}) == 2
    # the result file's order decides which family comes first
    interleaved = [_PIPE[2], *_PIPE[:2], _PIPE[3]]
    (group,) = _groups(names=interleaved, group="flat", **args)
    assert _members(group) == [tuple(_PIPE[2:]), tuple(_PIPE[:2])]
    # the other two groupings keep the rows they had
    rows = {
        how: [
            (r.id, r.members)
            for g in _groups(names=_PIPE, group=how, **args)
            for r in g.rows
        ]
        for how in ("component", "tracked")
    }
    assert rows == {
        "component": [
            ("0/Tracked/pipe.T[]", tuple(_PIPE[:2])),
            ("1/pipe/pipe.T[]", tuple(_PIPE[2:])),
        ],
        "tracked": [
            ("0/Tracked/pipe.T[]", tuple(_PIPE[:2])),
            ("1/Other variables/pipe.T[]", tuple(_PIPE[2:])),
        ],
    }


def test_the_name_order_sorts_each_group_by_what_it_reads():
    (group,) = _groups(group="flat", order="name")
    assert [r.text for r in group.rows] == [
        "a0",
        "CPUtime",
        "pipe.T[1:3]",
        "port[2, 1].C[1:2]",
        "x",
        "y",
        "z",
    ]
    # the file order is the result's first appearance
    (group,) = _groups(group="flat")
    assert [r.text for r in group.rows][:4] == ["x", "y", "a0", "z"]


def test_a_rows_file_text_is_its_name_folded():
    """``x={pipe.T[3], pipe.T[5]}``: two rows reading their expressions,
    each ``unitTests.x[i]`` by result-file name; a family of the result's
    own reads its names folded either way."""
    names = ["unitTests.x[1]", "unitTests.x[2]", "z[1]", "z[2]"]
    labels = {"unitTests.x[1]": "pipe.T[3]", "unitTests.x[2]": "pipe.T[5]"}
    (group,) = _groups(
        names=names,
        tracked=names[:2],
        labels=labels,
        unit_tests=names[:2],
        plots=(),
        selected=None,
        group="flat",
    )
    assert [(r.text, r.file_text) for r in group.rows] == [
        ("pipe.T[3]", "unitTests.x[1]"),
        ("pipe.T[5]", "unitTests.x[2]"),
        ("z[1:2]", "z[1:2]"),
    ]


def test_the_name_order_sorts_by_what_the_names_menu_shows():
    """``unitTests.x[1]`` reads ``a.Q``: A→Z puts it first by expression,
    after ``b`` and ``m`` by result-file name — the order follows what is
    shown."""
    names = ["b", "unitTests.x[1]", "m"]
    labels = {"unitTests.x[1]": "a.Q"}
    args = dict(
        names=names,
        tracked=[],
        labels=labels,
        plots=(),
        selected=None,
        group="flat",
        order="name",
    )
    for read in ("expression", "both"):
        (group,) = _groups(**args, read=read)
        assert [r.members[0] for r in group.rows] == ["unitTests.x[1]", "b", "m"]
    (group,) = _groups(**args, read="file")
    assert [r.members[0] for r in group.rows] == ["b", "m", "unitTests.x[1]"]
