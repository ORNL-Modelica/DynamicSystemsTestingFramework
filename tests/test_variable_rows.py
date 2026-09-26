"""The Variables drawer's rows (3c; DESIGN.md "The Variables drawer"): the
groups, the folds, the filter, the cap, and each row's state on the
selected plot — computed without a page."""

from __future__ import annotations

import itertools
import re
import time

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
        ("", False, False, "anything", True),
    ],
)
def test_matcher(needle, regex, case, text, hit):
    assert matcher(needle, regex=regex, case=case)(text) is hit


def _regex_glob(needle: str, case: bool):
    """The glob as a spec's ``variables`` list reads it (`_pattern_to_regex`,
    anchored): what the drawer's glob must mean, exactly."""
    from dstf.backends.types import _pattern_to_regex

    glob = re.compile(_pattern_to_regex(needle).pattern, 0 if case else re.IGNORECASE)
    return lambda text: glob.fullmatch(text) is not None


def test_the_glob_means_what_the_specs_regex_means():
    """Every glob of up to four characters over a small alphabet against
    every text of up to four: the drawer's glob, which cannot backtrack,
    keeps exactly what the spec's anchored regex keeps — a case toggle, a
    ``.`` literal, a ``?`` that is one character but never a newline and a
    ``*`` that never spans one, as the regex's ``.`` does not."""
    needles = [
        "".join(p)
        for n in range(1, 5)
        for p in itertools.product("aA.?*\n", repeat=n)
        if "*" in p or "?" in p
    ]
    texts = [
        "".join(p) for n in range(5) for p in itertools.product("aAb.\n", repeat=n)
    ]
    for case in (False, True):
        for needle in needles:
            ours = matcher(needle, regex=False, case=case)
            theirs = _regex_glob(needle, case)
            for text in texts:
                assert ours(text) is theirs(text), (needle, text, case)


def test_a_glob_cannot_backtrack():
    """``*a*a*a*a*a*a*a*b`` over one 60-character name took over 20 s as a
    regex, on the UI event loop, blocking every tab."""
    keep = matcher("*a*a*a*a*a*a*a*b", regex=False, case=False)
    start = time.perf_counter()
    assert not keep("a" * 60)
    assert time.perf_counter() - start < 0.5


def test_a_bad_regex_raises_for_the_drawer_to_say_so():
    regex = pytest.importorskip("regex")
    with pytest.raises(regex.error):
        matcher("(", regex=True, case=False)


def test_a_regex_searches_with_case_on_and_off():
    pytest.importorskip("regex")
    name = "port[2, 1].C[1]"
    assert matcher("^port", regex=True, case=False)(name)
    assert matcher("^PORT", regex=True, case=False)(name)
    assert not matcher("^PORT", regex=True, case=True)(name)
    assert not matcher("^port", regex=True, case=True)("a.port")
    assert matcher(r"C\[1\]$", regex=True, case=True)(name)


_BACKTRACK = """
import sys, time
import dstf
from dstf.authoring.variable_rows import matcher
assert dstf.__file__ == sys.argv[1], (dstf.__file__, sys.argv[1])
keep = matcher(r"^(a|aa)+$", regex=True, case=False)
start = time.process_time()
try:
    for _ in range(3):
        keep("a" * 60 + "b")
except TimeoutError:
    pass
print(time.process_time() - start)
"""


def test_a_regex_that_backtracks_finishes_within_the_budget():
    """`^(a|aa)+$` over a name that almost matches backtracks exponentially:
    the filter FINISHES near its budget — timed out, or answered if a later
    `regex` optimises the pattern away (it already does `(a+)+$`) — instead
    of freezing the tool. In a child process, so a filter that no longer
    stops fails on the timeout: without one this search was killed at 736 s
    and would have run for days. The timeout is generous because the
    child's import alone takes ~2 s here. The child times the filter in
    process-CPU seconds, the unit `regex` counts a search's timeout in: on
    2 loaded cores its wall time passed 1.5 budgets in 7 runs of 20."""
    pytest.importorskip("regex")
    import subprocess
    import sys

    import dstf

    done = subprocess.run(
        [sys.executable, "-c", _BACKTRACK, dstf.__file__],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert float(done.stdout) < 1.5 * variable_rows.REGEX_BUDGET


def test_a_plain_regex_over_a_big_result_does_not_run_out_of_time():
    """A search costs ~2.5 us a name: at a 0.25 s budget, 100,000 names
    timed out `T` itself, and TRANSFORM's largest result has 76,992."""
    pytest.importorskip("regex")
    names = [f"pipe{i}.T[{i % 20}]" for i in range(100_000)]
    keep = matcher(r"pipe\d+\.T\[3\]$", regex=True, case=False)
    kept = [n for n in names if keep(n)]
    assert len(kept) == 5_000 and kept[0] == "pipe3.T[3]"


def test_a_plain_regex_keeps_its_time_while_another_thread_is_busy():
    """With the GIL released on every search, a search waits on whatever
    else the tool runs (its overlay index json.loads TRANSFORM's refs for
    ~15 s after a page opens): ~1 ms a name, not ~2.5 us, and `T` over
    TRANSFORM's names ran out of time after ~2,000 of them."""
    pytest.importorskip("regex")
    import threading

    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(1000))

    busy = threading.Thread(target=spin, daemon=True)
    names = [f"pipe{i}.T[{i % 20}]" for i in range(50_000)]
    keep = matcher(r"pipe\d+\.T\[3\]$", regex=True, case=False)
    busy.start()
    try:
        kept = [n for n in names if keep(n)]
    finally:
        stop.set()
        busy.join()
    assert len(kept) == 2_500 and kept[0] == "pipe3.T[3]"


def test_the_budget_is_one_for_every_name_of_a_filter(monkeypatch):
    """ONE budget from the filter's first name, not one per name: 1 s a
    name over 6,093 names is 100 minutes. The clock is faked to read 0.4
    budgets later at each name while every search is instant, so a budget
    per name would never run out; the one total budget runs out at the 4th
    name, which a fresh filter keeps."""
    pytest.importorskip("regex")
    b = variable_rows.REGEX_BUDGET
    ticks = iter([0.0, 0.4 * b, 0.8 * b, 1.2 * b, 1.2 * b])
    monkeypatch.setattr(variable_rows, "monotonic", lambda: next(ticks))
    keep = matcher("x", regex=True, case=False)
    assert [keep("x") for _ in range(3)] == [True, True, True]
    with pytest.raises(TimeoutError):
        keep("x")
    assert matcher("x", regex=True, case=False)("x")


def test_a_search_is_given_the_time_left_not_the_whole_budget(monkeypatch):
    """The first name took 96% of the budget: the second search stops at
    the 4% left, well before a whole budget would. 32 a's, in this process:
    given no timeout the search runs ~0.8 s, 4x the bound, and ends — 60
    a's would run for days."""
    pytest.importorskip("regex")
    ticks = iter([0.0, 0.96 * variable_rows.REGEX_BUDGET])
    monkeypatch.setattr(variable_rows, "monotonic", lambda: next(ticks))
    keep = matcher(r"^(a|aa)+$", regex=True, case=False)
    assert keep("aa")
    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        keep("a" * 32 + "b")
    assert time.perf_counter() - start < 0.2


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
        names[5:15],
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


def test_the_filter_asks_the_label_not_the_text():
    """A row READS the plain label, so the filter asks that and the name —
    never the disambiguated text, whose ``(u1)`` the row does not show. A
    needle only the text matches keeps nothing."""
    labels = {"u1": "pipe.T[1]", "u2": "pipe.T[1]"}
    texts = {"u1": "pipe.T[1] (u1)", "u2": "pipe.T[1] (u2)"}
    args = dict(
        names=["u1", "u2"],
        tracked=[],
        labels=labels,
        texts=texts,
        layout=(),
        selected=None,
        scored={},
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
