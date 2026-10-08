"""A criterion over an array family, against the Session (D152): the add
form's Indices, the folded row's editor and its Indices — what each stages,
what it refuses, and what the header then counts."""

from __future__ import annotations

import json

import pytest

from dstf.authoring import capabilities as caps
from dstf.authoring.builtin import register_builtins
from dstf.authoring.builtin.edit_criteria import EditCriteria
from dstf.authoring.builtin.edit_family import EMPTY, EditFamily
from dstf.authoring.builtin.edit_tree import EditTree
from dstf.authoring.family_rows import Fold, fold_rows, tally
from dstf.authoring.session import Selection, Session, SessionView
from dstf.authoring.spec_document import PatchOp
from dstf.discovery.patch_apply import PatchError
from dstf.reporting.status import FAIL, PASS, classify
from dstf.reporting.tree_rows import criteria_rows
from family_project import element, family_project
from test_session import A, _unit_tests_project

ROOT = "/metrics"


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


def _session(cfg, work) -> Session:
    s = Session()
    s.open(cfg, work_dir=work)
    return s


@pytest.fixture
def bare(tmp_path):
    """A tracks x alone; its result file and baseline hold pipe.T[1..20]."""
    return _session(*family_project(tmp_path, 20, tracked=(), fail=(6,)))


@pytest.fixture
def six(tmp_path):
    """A tracks x and pipe.T[1..6], one nrmse each (flat: no saved tree);
    pipe.T[3] fails."""
    return _session(*family_project(tmp_path, 6))


def _view(session, **selection) -> SessionView:
    return SessionView(session, Selection(tests=frozenset({A}), **selection))


def _add(session, path=ROOT, variable="pipe.T[1]", metric="nrmse", **more):
    return EditTree().act(
        _view(session),
        action="add",
        model_id=A,
        path=path,
        variable=variable,
        metric=metric,
        **more,
    )


def _variables(session) -> list[str]:
    return [child["variable"] for child in session.tree(A)["children"]]


def _members(session, indices) -> tuple[str, ...]:
    """The paths of pipe.T's leaves at ``indices``, in tree order."""
    wanted = {element(i) for i in indices}
    return tuple(
        f"{ROOT}/children/{k}"
        for k, child in enumerate(session.tree(A)["children"])
        if child.get("variable") in wanted
    )


def _folds(session) -> list[Fold]:
    view = session.tree_view(A)
    subject = [n for plot in session.plots(A) for n in plot.subject]
    return [e for e in fold_rows(criteria_rows(view, subject)) if isinstance(e, Fold)]


# -- the add form's Indices (R1) -----------------------------------------------------


def test_add_with_indices_stages_one_criterion_per_chosen_element_in_order(bare):
    patch = _add(bare, indices="1:5:20")
    (op,) = patch.ops  # ONE op: the whole tree those adds give
    assert (op.op, op.path) == ("add", ROOT)
    bare.apply(patch)
    assert _variables(bare) == ["x", element(1), element(6), element(11), element(16)]
    # literal leaves, every one the same criterion
    leaves = bare.tree(A)["children"][1:]
    assert all(
        leaf == {"metric": "nrmse", "variable": leaf["variable"]} for leaf in leaves
    )
    assert len(bare.spec.pending) == 1
    # scored at once: the file holds them, and so does the baseline
    by_name = {vc.name: vc.passed for vc in bare.comparisons[A].variables}
    assert by_name == {
        "x": True,
        element(1): True,
        element(6): False,
        element(11): True,
        element(16): True,
    }


def test_the_leaves_land_where_one_would_have(bare):
    """Under a combinator at its end; right after the leaf the `+` was
    clicked on; beside a root leaf as an `and` of them all."""
    bare.apply(_add(bare, indices="1:2"))
    bare.apply(_add(bare, path=f"{ROOT}/children/0", indices="7,9"))
    assert _variables(bare) == ["x", element(7), element(9), element(1), element(2)]
    bare.discard()
    bare.stage([PatchOp(A, "add", ROOT, {"metric": "nrmse", "variable": "x"})])
    bare.apply(_add(bare, indices="3:4"))
    assert bare.tree(A)["combinator"] == "and"
    assert _variables(bare) == ["x", element(3), element(4)]


def test_a_mixture_is_added_by_index_whatever_order_it_was_written_in(bare):
    bare.apply(_add(bare, indices="10:2:14, 1:3"))
    assert _variables(bare)[1:] == [element(i) for i in (1, 2, 3, 10, 12, 14)]


def test_a_seeded_metric_is_seeded_once_so_the_leaves_are_one_criterion(bare):
    """A range's bounds come from the data: seeded per element they would
    be twenty criteria, and nothing would fold."""
    # What ONE criterion on the first chosen element is seeded with.
    single = _add(bare, variable=element(2), metric="range").ops[0].value
    bare.apply(_add(bare, metric="range", indices="2:4"))
    leaves = bare.tree(A)["children"][1:]
    assert [leaf["variable"] for leaf in leaves] == [element(2), element(3), element(4)]
    (bounds,) = {(leaf["min_value"], leaf["max_value"]) for leaf in leaves}
    assert bounds == (single["min_value"], single["max_value"])
    (fold,) = _folds(bare)
    assert fold.name == "range · pipe.T[2:4]"


def test_one_chosen_element_is_one_add_op_as_before(bare):
    (op,) = _add(bare, indices="3").ops
    assert (op.op, op.path) == ("add", f"{ROOT}/children/-")
    assert op.value == {"metric": "nrmse", "variable": element(3)}
    # ... and with no Indices text the variable picked is the one scored
    (op,) = _add(bare, variable=element(4)).ops
    assert op.value == {"metric": "nrmse", "variable": element(4)}


@pytest.mark.parametrize(
    ("indices", "says"),
    [
        ("1:25", "pipe.T[1:20] has 20 elements; 21:25 are not among them."),
        ("21", "pipe.T[1:20] has 20 elements; 21 is not one of them."),
        ("", "Indices is empty"),
        ("5:1", "5:1 ends before it starts."),
        ("1,1", "1 is written twice."),
    ],
)
def test_a_refused_indices_text_stages_nothing(bare, indices, says):
    with pytest.raises(ValueError) as e:
        _add(bare, indices=indices)
    assert says in str(e.value)
    assert bare.spec.pending == []


def test_a_variable_that_is_no_element_takes_no_indices(bare):
    with pytest.raises(ValueError, match="x is no element of an array"):
        _add(bare, variable="x", indices="1:3")


def test_a_unit_tests_element_takes_no_indices(tmp_path):
    """D140: each is a test quantity of its own — no family, no field."""
    from dstf.authoring.builtin.edit_tree import _family

    session = _session(*_unit_tests_project(tmp_path))
    names = session.variable_names(A)
    units = [n for n in names if n.startswith("unitTests.x[")]
    assert len(units) >= 2  # by their names alone they would be a family
    assert _family(_view(session), A, units[0]) is None
    with pytest.raises(ValueError, match="is no element of an array"):
        _add(session, variable=units[0], indices="1:2")


# -- the folded row's editor (R2) ----------------------------------------------------


def _edit(
    session, paths, *, metric="nrmse", params=None, window=None, against="primary"
):
    return EditCriteria().act(
        _view(session, leaf_path=paths[0], leaf_paths=paths),
        model_id=A,
        path=paths[0],
        paths=paths,
        metric=metric,
        variable=element(1),
        params=params or {},
        against=against,
        window=window or {},
    )


def test_the_folded_edit_reaches_every_member_and_nothing_else(six):
    members = _members(six, range(1, 7))
    before_x = six.tree(A)["children"][0]
    patch = _edit(six, members, params={"tolerance": 0.5})
    (op,) = patch.ops
    assert (op.op, op.path) == ("add", ROOT)
    six.apply(patch)
    tree = six.tree(A)
    assert tree["children"][0] == before_x
    assert tree["children"][1:] == [
        {"metric": "nrmse", "variable": element(i), "tolerance": 0.5}
        for i in range(1, 7)
    ]
    assert classify(six.comparisons[A]) == PASS  # pipe.T[3] passes at 0.5


def test_a_folded_edit_of_some_members_leaves_the_others(six):
    members = _members(six, (2, 4))
    six.apply(_edit(six, members, params={"tolerance": 0.5}))
    tolerances = [leaf.get("tolerance") for leaf in six.tree(A)["children"]]
    assert tolerances == [None, None, 0.5, None, 0.5, None, None]
    assert [f.name for f in _folds(six)] == [
        "nrmse · pipe.T[1,3,5,6]",  # a list: its runs are too short to fold
        "nrmse · pipe.T[2:2:4]",
    ]


def test_one_gesture_on_a_fold_is_one_pending_edit(six):
    """D4: what the header counts. A flat test materializes nothing first —
    the op is the whole tree, as a move's is — and a second edit in a row
    replaces the first: a value typed into a folded field is one edit, not
    one per key and not one per element."""
    members = _members(six, range(1, 7))
    six.apply(_edit(six, members, params={"tolerance": 0.5}))
    assert [(op.op, op.path) for op in six.spec.pending] == [("add", ROOT)]
    six.apply(_edit(six, members, params={"tolerance": 0.25}))
    assert [(op.op, op.path) for op in six.spec.pending] == [("add", ROOT)]
    assert {leaf["tolerance"] for leaf in six.tree(A)["children"][1:]} == {0.25}


def test_a_value_typed_and_cleared_on_a_folded_field_leaves_nothing_pending(six):
    """`Session.stage`'s rule holds for the whole-tree op: the tree put
    back is the one the saved entry already scores by, and is no edit."""
    members = _members(six, range(1, 7))
    six.apply(_edit(six, members, params={"tolerance": 0.5}))
    assert len(six.spec.pending) == 1
    six.apply(_edit(six, members))
    assert six.spec.pending == []
    assert classify(six.comparisons[A]) == FAIL  # as saved


def test_typed_and_cleared_over_a_saved_tree_leaves_nothing_pending(tmp_path):
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": element(i), "tolerance": 0.1}
            for i in (1, 2, 3)
        ],
    }
    session = _session(*family_project(tmp_path, 3, tree=tree))
    members = _members(session, (1, 2, 3))
    session.apply(_edit(session, members, params={"tolerance": 0.7}))
    assert len(session.spec.pending) == 1
    session.apply(_edit(session, members, params={"tolerance": 0.1}))
    assert session.spec.pending == []


@pytest.mark.parametrize(
    ("more", "error", "says"),
    [
        ({"params": {"tolerance": -1}}, ValueError, "tolerance"),
        ({"window": {"start": 0.8, "end": 0.2}}, PatchError, "window"),
    ],
)
def test_a_refused_folded_edit_stages_nothing_for_any_member(six, more, error, says):
    members = _members(six, range(1, 7))
    before = six.tree(A)
    with pytest.raises(error) as e:
        six.apply(_edit(six, members, **more))
    assert says in str(e.value)
    assert six.spec.pending == []
    assert six.tree(A) == before


def test_a_member_that_is_gone_refuses_the_whole_edit(six):
    members = (*_members(six, (1, 2)), f"{ROOT}/children/40")
    with pytest.raises(ValueError, match="names no criterion"):
        _edit(six, members, params={"tolerance": 0.5})
    assert six.spec.pending == []


# -- the folded editor's Indices (D5) -------------------------------------------------


def _reindex(session, paths, text):
    return EditFamily().act(
        _view(session, leaf_path=paths[0], leaf_paths=paths),
        model_id=A,
        paths=paths,
        indices=text,
    )


@pytest.fixture
def some(tmp_path):
    """A tracks x and pipe.T[1..4] of a 10-element family, one loosened
    nrmse each under an explicit tree, then z's stand-in: x again."""
    tree = {
        "combinator": "and",
        "children": [
            *(
                {"metric": "nrmse", "variable": element(i), "tolerance": 0.5}
                for i in (1, 2, 3, 4)
            ),
            {"metric": "nrmse", "variable": "x"},
        ],
    }
    return _session(*family_project(tmp_path, 10, tracked=(1, 2, 3, 4), tree=tree))


def test_indices_grow_adds_leaves_with_the_groups_parameters_after_its_last(some):
    members = _members(some, (1, 2, 3, 4))
    patch = _reindex(some, members, "1:4,7:8")
    (op,) = patch.ops
    assert (op.op, op.path) == ("add", ROOT)
    some.apply(patch)
    assert _variables(some) == [*(element(i) for i in (1, 2, 3, 4, 7, 8)), "x"]
    assert some.tree(A)["children"][4] == {
        "metric": "nrmse",
        "variable": element(7),
        "tolerance": 0.5,
    }
    assert len(some.spec.pending) == 1
    (fold,) = _folds(some)
    assert fold.name == "nrmse · pipe.T[1:4,7,8]"


def test_indices_shrink_removes_those_leaves(some):
    some.apply(_reindex(some, _members(some, (1, 2, 3, 4)), "2:3"))
    assert _variables(some) == [element(2), element(3), "x"]
    assert len(some.spec.pending) == 1


def test_indices_unchanged_stage_nothing(some):
    assert _reindex(some, _members(some, (1, 2, 3, 4)), " 1:4 ") is None
    assert _reindex(some, _members(some, (1, 2, 3, 4)), "4,1:3") is None


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("1:11", "pipe.T[1:10] has 10 elements; 11 is not one of them."),
        ("", EMPTY),
        (" , ", EMPTY),
        ("1:", "'1:' is not an index or a range"),
    ],
)
def test_a_refused_indices_edit_stages_nothing(some, text, says):
    with pytest.raises(ValueError) as e:
        _reindex(some, _members(some, (1, 2, 3, 4)), text)
    assert says in str(e.value)
    assert some.spec.pending == []


def test_removing_every_element_is_refused_with_the_way_to_remove_it(some):
    with pytest.raises(ValueError) as e:
        _reindex(some, _members(some, (1, 2, 3, 4)), "")
    assert "remove" in str(e.value) and "unfold" in str(e.value)


def test_a_member_the_last_run_did_not_record_stays_an_element(tmp_path):
    """D142, unchanged: a criterion on an element the result lacks is a
    criterion all the same — the family is what the file has AND what the
    fold already holds, so applying the Indices as they are is no refusal."""
    tree = {
        "combinator": "and",
        "children": [{"metric": "nrmse", "variable": element(i)} for i in (2, 3, 9)],
    }
    session = _session(*family_project(tmp_path, 4, tracked=(2, 3), tree=tree))
    members = _members(session, (2, 3, 9))
    assert _reindex(session, members, "2,3,9") is None
    with pytest.raises(ValueError) as e:
        _reindex(session, members, "2,3,8")
    assert str(e.value) == "pipe.T[1:4,9] has 5 elements; 8 is not one of them."


# -- Save, and what the row counts ----------------------------------------------------


def test_save_writes_literal_leaves_and_a_reload_folds_them_again(tmp_path):
    cfg, work = family_project(tmp_path, 20, tracked=(), fail=(6,))
    session = _session(cfg, work)
    session.apply(_add(session, metric="tube", indices="1:5:20"))
    session.save()
    entry = next(
        t
        for t in json.loads((cfg.parent / "test_spec.json").read_text())["tests"]
        if t["model"] == A
    )
    # Nothing new is stored: one literal leaf per element, no glob, no slice.
    assert sorted(entry) == ["metrics", "model", "simulation", "source", "variables"]
    assert [leaf["variable"] for leaf in entry["metrics"]["children"][1:]] == [
        element(1),
        element(6),
        element(11),
        element(16),
    ]
    assert "[1:5:16]" not in json.dumps(entry) and "*" not in json.dumps(
        entry["metrics"]
    )
    again = _session(cfg, work)
    assert again.spec.pending == []
    (fold,) = _folds(again)
    assert fold.name == "tube · pipe.T[1:5:16]"


def test_the_rows_count_is_its_members_verdicts(tmp_path):
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.tree_card import leaf_status

    session = _session(*family_project(tmp_path, 20, fail=(3, 17)))
    (fold,) = _folds(session)
    statuses = [leaf_status(leaf) for leaf in fold.leaves]
    scored = {vc.name: vc.passed for vc in session.comparisons[A].variables}
    assert statuses == ["pass" if scored[v] else "fail" for v in fold.variables]
    assert tally(statuses) == "18 pass · 2 fail"


def test_the_report_goes_on_listing_one_criteria_row_per_leaf(tmp_path):
    """D6: the fold is the tool's alone. The report draws the same walk
    (`criteria_rows`) and its page lists every leaf of the family's plot."""
    from dstf.cli.main import main

    cfg, work = family_project(tmp_path, 6)
    main(["--config", str(cfg), "compare", "--work-dir", str(work), "--report"])
    pages = [
        p.read_text(encoding="utf-8") for p in work.glob("reports/*/interactive.html")
    ]
    (page,) = [text for text in pages if "pipe.T[1:6]" in text]
    assert page.count('class="criteria-row"') == 7  # x, and the six elements
    for i in range(1, 7):
        assert page.count(f'class="criteria-row" data-variable="{element(i)}"') == 1
    session = _session(cfg, work)
    rows = criteria_rows(session.tree_view(A), [element(i) for i in range(1, 7)])
    assert [row["variable"] for row in rows] == [element(i) for i in range(1, 7)]
