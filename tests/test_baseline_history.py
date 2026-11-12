"""`baseline plot`'s facts (``storage.baseline_history``): a test's stored
baseline at several revisions, each variable's series at each revision
kept, and each later one minus the FIRST as `baseline diff` measures a
change — so a panel's peak is the Change that command prints."""

import json
import math

import numpy as np
import pytest

from conftest import RESAVED_OLD, GitRepo, resaved_single
from dstf.storage.baseline_diff import DiffError, diff_baselines
from dstf.storage.baseline_history import MAX_KEPT, baseline_history

LINUX = "Dymola/linux"


def _ref(model_id, variables, time=(0.0, 1.0, 2.0), **fields) -> str:
    """A baseline file's text: ``variables`` as (name, expression, values)."""
    doc = {
        "model_id": model_id,
        "status": "active",
        "simulation": {"stop_time": 2.0},
        **fields,
        "time": list(time),
        "variables": [
            {"index": i, "name": n, "expression": e, "values": list(v)}
            for i, (n, e, v) in enumerate(variables, start=1)
        ],
    }
    return json.dumps(doc)


@pytest.fixture
def lib(tmp_path):
    repo = GitRepo(tmp_path / "lib")
    (repo.top / "refs").mkdir()
    return repo


def _write(repo, text, path=f"{LINUX}/ref_a.json"):
    target = repo.top / "refs" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commits(lib, *steps, model_id="Lib.A"):
    """One commit per step: the variables of Lib.A's baseline in LINUX."""
    out = []
    for i, variables in enumerate(steps):
        _write(lib, _ref(model_id, variables))
        out.append(lib.commit(f"step {i}"))
    return out


def _one(history):
    (test,) = history.tests
    (folder,) = test.folders
    return test, folder


def _panel(line):
    return line.difference.scaled


def test_each_later_revision_is_measured_against_the_first_not_the_one_before(lib):
    revs = _commits(
        lib,
        [("x", "", [0, 5, 10])],
        [("x", "", [0, 5.5, 10])],
        [("x", "", [0, 6, 10])],
    )
    history = baseline_history(lib.top / "refs", revs)
    test, folder = _one(history)
    assert test.model_id == "Lib.A"
    assert [r.commit.sha for r in folder.kept] == revs
    (x,) = folder.variables
    first, second, third = x.lines
    assert first.difference is None
    # 0.5 and 1.0 of old's range 10, at t=1: the second against the FIRST
    np.testing.assert_allclose(_panel(second), [0, 5, 0])
    np.testing.assert_allclose(_panel(third), [0, 10, 0])
    assert third.change.largest == pytest.approx(10.0)


def _many(lib):
    """Two revisions of one baseline whose variables change in every way a
    change is measured: a range, a constant's value, a series of zeros, a
    value that turned NaN, one renamed and one added."""
    return _commits(
        lib,
        [
            ("x", "", [0, 5, 10]),
            ("c", "", [300, 300, 300]),
            ("z", "", [0, 0, 0]),
            ("n", "", [1, 2, 3]),
            ("PHS.p", "", [10, 20, 30]),
        ],
        [
            ("x", "", [0, 5.5, 10]),
            ("c", "", [303, 300, 300]),
            ("z", "", [0, 0.5, 0]),
            ("n", "", [1, math.nan, 3.5]),
            ("unitTests.x[1]", "PHS.p", [10, 21, 30]),
            ("added", "", [1, 1, 1]),
        ],
    )


def test_a_panel_s_peak_is_baseline_diff_s_change_for_every_variable(lib):
    """The invariant the plan names: for two revisions, the largest finite
    point of the panel is the Change `baseline diff` prints, on the same
    scale — the range's, the value's, or absolute."""
    revs = _many(lib)
    root = lib.top / "refs"
    (diffed,) = diff_baselines(root, revs[0], revs[1]).tests
    changes = {v.name: v.change for v in diffed.baselines[LINUX].variables}
    _, folder = _one(baseline_history(root, revs))
    peaks = {}
    for v in folder.variables:
        if len(v.lines) < 2:
            continue
        d = v.lines[1].difference
        peaks[v.lines[1].name] = (float(np.max(np.abs(d.scaled[d.finite]))), d.scale)
    assert peaks == {
        name: (c.largest, c.scale) for name, c in changes.items() if c is not None
    }
    assert peaks["x"] == (pytest.approx(5.0), "range")
    assert peaks["c"] == (pytest.approx(1.0), "value")
    assert peaks["z"] == (0.5, "absolute")


def test_a_non_finite_point_is_counted_and_kept_off_the_peak(lib):
    revs = _many(lib)
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    n = next(v for v in folder.variables if v.label == "n")
    line = n.lines[1]
    assert (line.change.non_finite, line.change.non_finite_at) == (1, 1.0)
    assert list(line.difference.finite) == [True, False, True]
    # the finite points still read: 0.5 of the range 2 at t=2
    assert line.change.largest == pytest.approx(25.0)


def test_a_renamed_variable_is_one_history_and_says_its_rename(lib):
    revs = _many(lib)
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    p = next(v for v in folder.variables if v.label == "PHS.p")
    assert [line.name for line in p.lines] == ["PHS.p", "unitTests.x[1]"]
    assert p.renames == [(folder.kept[1].commit.short, "PHS.p", "unitTests.x[1]")]
    np.testing.assert_allclose(_panel(p.lines[1]), [0, 5, 0])


def test_a_variable_added_later_starts_where_it_appears(lib):
    revs = _commits(
        lib,
        [("x", "", [0, 1, 2])],
        [("x", "", [0, 1, 3]), ("y", "", [0, 10, 20])],
        [("x", "", [0, 1, 4]), ("y", "", [0, 12, 20])],
    )
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    y = next(v for v in folder.variables if v.label == "y")
    assert [line.revision.commit.sha for line in y.lines] == revs[1:]
    assert y.lines[0].difference is None
    # measured against the first revision that holds it
    np.testing.assert_allclose(_panel(y.lines[1]), [0, 10, 0])


def test_a_revision_equal_in_values_to_the_last_kept_is_skipped(lib):
    """Equal by label too: a revision that only renamed the variables (the
    values the same) draws the same line twice, and is said instead."""
    revs = _commits(
        lib,
        [("PHS.p", "", [0, 1, 2])],
        [("unitTests.x[1]", "PHS.p", [0, 1, 2])],
        [("unitTests.x[1]", "PHS.p", [0, 1, 3])],
    )
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    assert [r.commit.sha for r in folder.kept] == [revs[0], revs[2]]
    assert [(r.commit.sha, to.commit.sha) for r, to in folder.equal] == [
        (revs[1], revs[0])
    ]


def test_a_revision_is_skipped_as_equal_to_the_last_kept_one(lib):
    """Measured against the last revision KEPT: not the first (the third
    equals the second, not the first), and not the revision just before
    (the fifth follows one that cannot be read)."""
    revs = _commits(
        lib, [("x", "", [0, 1, 2])], [("x", "", [0, 1, 3])], [("x", "", [0, 1, 3])]
    )
    _write(lib, "{ not json")
    broken = lib.commit("broken")
    revs += [broken, *_commits(lib, [("x", "", [0, 1, 3])])]
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    assert [r.commit.sha for r in folder.kept] == revs[:2]
    assert [(r.commit.sha, to.commit.sha) for r, to in folder.equal] == [
        (revs[2], revs[1]),
        (revs[4], revs[1]),
    ]


def test_a_rename_at_a_skipped_revision_is_said_at_that_revision(lib):
    """The revision that only renamed is skipped as equal; the rename is
    its, not the next drawn revision's."""
    revs = _commits(
        lib,
        [("PHS.p", "", [0, 1, 2])],
        [("unitTests.x[1]", "PHS.p", [0, 1, 2])],
        [("unitTests.x[1]", "PHS.p", [0, 1, 3])],
    )
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    (p,) = folder.variables
    assert p.renames == [(revs[1][:7], "PHS.p", "unitTests.x[1]")]
    assert [line.name for line in p.lines] == ["PHS.p", "unitTests.x[1]"]


def test_a_rename_at_a_revision_left_out_is_said_at_that_revision(lib):
    """Over twelve kept, the ones left out are followed too: a rename at
    one of them is its own."""
    # 100, 110, then a step of 0.001 at the rename (the smallest move: the
    # one left out), then steps of 50
    ends = [100, 110, 110.001, *(110 + 50 * k for k in range(1, MAX_KEPT - 1))]
    steps = [
        [("PHS.p", "", [0, 100, end])]
        if k < 2
        else [("unitTests.x[1]", "PHS.p", [0, 100, end])]
        for k, end in enumerate(ends)
    ]
    revs = _commits(lib, *steps)
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    assert [r.commit.sha for r in folder.thinned] == [revs[2]]
    (p,) = folder.variables
    assert p.renames == [(revs[2][:7], "PHS.p", "unitTests.x[1]")]


def test_a_variable_moved_only_where_a_later_line_differs_from_its_first(lib):
    """What `baseline plot` draws: a variable whose change against its
    first line is not nothing at some later line, or whose difference is
    not finite. One added, removed or equal throughout did not move."""
    revs = _commits(
        lib,
        [("same", "", [1, 2, 3]), ("gone", "", [1, 1, 1]), ("back", "", [0, 1, 2])],
        [("same", "", [1, 2, 3]), ("new", "", [5, 5, 5]), ("back", "", [0, 1, 9])],
        [
            ("same", "", [1, 2, 3]),
            ("new", "", [5, 5, 5]),
            ("back", "", [0, 1, 2]),
            ("nan", "", [math.nan, 1, 1]),
        ],
    )
    history = baseline_history(lib.top / "refs", revs)
    _, folder = _one(history)
    moved = {v.label: v.moved for v in folder.variables}
    # back moved at the second and moved back at the third: it moved
    assert moved == {
        "same": False,
        "gone": False,
        "back": True,
        "new": False,
        "nan": False,
    }
    assert history.images == 1


def _docs(lib, *docs):
    """One commit per document of Lib.A's baseline in LINUX."""
    out = []
    for i, doc in enumerate(docs):
        _write(lib, json.dumps(doc))
        out.append(lib.commit(f"step {i}"))
    return out


def test_a_change_within_the_saved_digits_did_not_move(lib):
    """A real baseline accepted again at single precision, its numbers
    kept: each line differs from the first by the first file's rounding
    (its true size kept), and nothing is drawn."""
    history = baseline_history(lib.top / "refs", _docs(lib, *resaved_single()))
    _, folder = _one(history)
    assert {v.label: v.moved for v in folder.variables} == {
        n: False for n in RESAVED_OLD
    }
    assert history.images == 0
    change = folder.variables[0].lines[1].change
    assert change.within_saved_digits is True
    assert change.largest == pytest.approx(3.747e-6, rel=1e-3)


def _with(doc, value, precision, time=None):
    """``doc`` with every value ``value``, ``precision`` recorded, and
    ``time`` where given."""
    out = json.loads(json.dumps(doc))
    for v in out["variables"]:
        v["values"] = [value] * len(v["values"])
    out["sampling"] = {**out["sampling"], "precision": precision}
    if time is not None:
        out["time"] = time
    return out


def test_each_line_is_held_to_its_own_file_and_the_first_s(lib):
    """The first file spelled at 7 digits: a later single-precision line
    within a step of it is its rounding, whatever a double revision moved
    between them."""
    old, single = resaved_single()
    double = _with(single, 231.4, "double")
    history = baseline_history(lib.top / "refs", _docs(lib, old, double, single))
    _, folder = _one(history)
    x1 = folder.variables[0]
    assert [line.change.within_saved_digits for line in x1.lines[1:]] == [False, True]


def test_lines_of_files_that_record_their_sampling_are_never_rounding(lib):
    """Single, then the same numbers in a double file (skipped as equal),
    then a double within a 7th digit of the first: both files spell their
    runs' own numbers, so that is a move, drawn."""
    _, single = resaved_single()
    exact = [0.0, 1.0, 2.0, 3.0]  # the same times in either precision
    first = _with(single, 231.300491, "single", exact)
    same = _with(single, float(np.float32(231.300491)), "double", exact)
    moved = _with(single, 231.30049, "double", exact)
    revs = _docs(lib, first, same, moved)
    history = baseline_history(lib.top / "refs", revs)
    _, folder = _one(history)
    assert [r.commit.sha for r, _ in folder.equal] == [revs[1]]
    x1 = folder.variables[0]
    assert (x1.lines[1].change.within_saved_digits, x1.moved) == (False, True)


def test_a_value_turned_not_finite_is_a_move(lib):
    revs = _commits(lib, [("x", "", [0, 1, 2])], [("x", "", [0, math.nan, 2])])
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    (x,) = folder.variables
    assert (x.lines[1].change.largest, x.moved) == (0.0, True)


def test_a_folder_with_one_distinct_revision_draws_nothing(lib):
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 2])]))
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 2])]), "Dymola/windows/ref_a.json")
    one = lib.commit("one")
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 2.5])]))
    _write(
        lib,
        _ref("Lib.A", [("x", "", [0, 1, 2])], date_added="2026-02-02"),
        "Dymola/windows/ref_a.json",
    )
    two = lib.commit("two")
    (test,) = baseline_history(lib.top / "refs", [one, two]).tests
    assert [f.folder for f in test.folders] == [LINUX]


def test_a_test_absent_at_the_first_revision_starts_at_its_first(lib):
    _write(lib, _ref("Lib.B", [("x", "", [0, 1, 2])]), f"{LINUX}/ref_b.json")
    zero = lib.commit("no Lib.A")
    revs = _commits(lib, [("x", "", [0, 1, 2])], [("x", "", [0, 1, 3])])
    history = baseline_history(lib.top / "refs", [zero, *revs])
    test, folder = _one(history)
    assert test.model_id == "Lib.A"
    assert [r.commit.sha for r in folder.absent] == [zero]
    assert [r.commit.sha for r in folder.kept] == revs


def test_time_grids_of_different_lengths_are_read_on_the_first_s(lib):
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 2])]))
    one = lib.commit("three points")
    _write(
        lib,
        _ref("Lib.A", [("x", "", [0, 0.6, 1.2, 1.8, 2.4])], time=(0, 0.5, 1, 1.5, 2)),
    )
    two = lib.commit("five points")
    _, folder = _one(baseline_history(lib.top / "refs", [one, two]))
    (x,) = folder.variables
    assert len(x.lines[1].time) == 5
    # the second read on the first's grid: 1.2 - 1 at t=1, 2.4 - 2 at t=2
    np.testing.assert_allclose(_panel(x.lines[1]), [0, 10, 20])


def test_a_revision_that_cannot_be_read_is_skipped_with_its_reason(lib):
    revs = _commits(lib, [("x", "", [0, 1, 2])])
    _write(lib, "{ not json")
    broken = lib.commit("broken")
    revs += _commits(lib, [("x", "", [0, 1, 3])])
    _, folder = _one(baseline_history(lib.top / "refs", [revs[0], broken, revs[1]]))
    ((revision, why),) = folder.unreadable
    assert revision.commit.sha == broken
    assert why.startswith("Expecting property name")
    assert [r.commit.sha for r in folder.kept] == revs


def test_by_default_the_tests_drawn_are_those_changed_first_to_last(lib):
    """A test that moved and moved back is not listed by `baseline diff`
    first -> last; named, it is drawn, and marked as not listed."""
    _write(lib, _ref("Lib.B", [("x", "", [0, 1, 2])]), f"{LINUX}/ref_b.json")
    revs = _commits(lib, [("x", "", [0, 1, 2])])
    _write(lib, _ref("Lib.B", [("x", "", [0, 1, 5])]), f"{LINUX}/ref_b.json")
    revs += _commits(lib, [("x", "", [0, 1, 3])])
    _write(lib, _ref("Lib.B", [("x", "", [0, 1, 2])]), f"{LINUX}/ref_b.json")
    revs += _commits(lib, [("x", "", [0, 1, 4])])
    root = lib.top / "refs"
    assert [t.model_id for t in baseline_history(root, revs).tests] == ["Lib.A"]
    named = baseline_history(root, revs, tests=["B", "Lib.A"])
    assert [(t.model_id, t.listed) for t in named.tests] == [
        ("Lib.A", True),
        ("Lib.B", False),
    ]


def test_a_test_is_named_by_its_short_name_or_its_model_id(lib):
    revs = _commits(lib, [("x", "", [0, 1, 2])], [("x", "", [0, 1, 3])])
    root = lib.top / "refs"
    for name in ("A", "Lib.A"):
        assert [
            t.model_id for t in baseline_history(root, revs, tests=[name]).tests
        ] == ["Lib.A"]


def test_a_name_that_matches_no_test_is_one_sentence(lib):
    revs = _commits(lib, [("x", "", [0, 1, 2])], [("x", "", [0, 1, 3])])
    with pytest.raises(DiffError) as e:
        baseline_history(lib.top / "refs", revs, tests=["A", "Nope", "Gone"])
    assert str(e.value) == (
        "--test: no baseline at these revisions is of a test named Nope, Gone "
        "(a short name such as A, or a full model id)"
    )


def test_a_folder_that_holds_no_baseline_is_one_sentence(lib):
    revs = _commits(lib, [("x", "", [0, 1, 2])], [("x", "", [0, 1, 3])])
    with pytest.raises(DiffError) as e:
        baseline_history(lib.top / "refs", revs, folders=["OpenModelica/linux"])
    assert str(e.value) == (
        "--folder OpenModelica/linux: no baseline in it at these revisions "
        "(they hold Dymola/linux)"
    )


def test_a_folder_restricts_what_is_drawn(lib):
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 2])]), "Dymola/windows/ref_a.json")
    revs = _commits(lib, [("x", "", [0, 1, 2])])
    _write(lib, _ref("Lib.A", [("x", "", [0, 1, 5])]), "Dymola/windows/ref_a.json")
    revs += _commits(lib, [("x", "", [0, 1, 3])])
    root = lib.top / "refs"
    (test,) = baseline_history(root, revs).tests
    assert [f.folder for f in test.folders] == ["Dymola/linux", "Dymola/windows"]
    (test,) = baseline_history(root, revs, folders=["Dymola/windows"]).tests
    assert [f.folder for f in test.folders] == ["Dymola/windows"]


def test_over_twelve_kept_the_first_the_last_and_the_largest_movers_are(lib):
    """A step of k at revision k: the ten largest middle steps are the ten
    last middles, and the two smallest are left out and said."""
    n = MAX_KEPT + 2
    value, steps = 0.0, []
    for k in range(n):
        value += k
        steps.append([("x", "", [0, 100, 100 + value])])
    revs = _commits(lib, *steps)
    _, folder = _one(baseline_history(lib.top / "refs", revs))
    assert len(folder.kept) == MAX_KEPT
    assert [r.commit.sha for r in folder.thinned] == revs[1:3]
    assert [r.commit.sha for r in folder.kept] == [revs[0], *revs[3:]]
