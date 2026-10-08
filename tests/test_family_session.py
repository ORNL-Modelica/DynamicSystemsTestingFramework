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
from dstf.authoring.tree_pointer import node_or_none
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
    # the flat test's own tree written out, and the ONE op of the gesture
    assert [op.materializes for op in bare.spec.pending] == [True, False]
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


def _named(session, paths) -> tuple[str, ...]:
    """The variables at ``paths``, as a panel reads them when it is built."""
    tree = session.tree(A)
    return tuple((node_or_none(tree, path) or {}).get("variable", "") for path in paths)


def _edit(
    session,
    paths,
    *,
    metric="nrmse",
    params=None,
    window=None,
    against="primary",
    variables=None,
):
    return EditCriteria().act(
        _view(session, leaf_path=paths[0], leaf_paths=paths),
        model_id=A,
        path=paths[0],
        paths=paths,
        variables=_named(session, paths) if variables is None else variables,
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


def test_one_gesture_on_a_fold_is_one_pending_edit(tmp_path):
    """D4: what the header counts — ONE op for the gesture, the whole tree
    it leaves, and a second edit in a row replaces the first: a value typed
    into a folded field is one edit, not one per key and not one per
    element."""
    session = _session(*family_project(tmp_path, 6, tree=_saved_six()))
    members = _members(session, range(1, 7))
    session.apply(_edit(session, members, params={"tolerance": 0.5}))
    assert [(op.op, op.path) for op in session.spec.pending] == [("add", ROOT)]
    session.apply(_edit(session, members, params={"tolerance": 0.25}))
    assert [(op.op, op.path) for op in session.spec.pending] == [("add", ROOT)]
    assert {leaf["tolerance"] for leaf in session.tree(A)["children"][1:]} == {0.25}


def test_on_a_flat_test_the_tree_is_written_out_first_as_for_one_criterion(six):
    """The executing review's M5: a fold edit on a FLAT test staged the
    whole tree as one unmarked add — the preview showed every line of it
    as added, with no note, and an Indices removal showed nothing deleted.
    It is staged as a per-leaf edit is: the implicit tree written out
    first, marked, and then the gesture's one op, so the preview diffs
    against that tree."""
    from dstf.authoring.spec_preview import entry_lines

    members = _members(six, range(1, 7))
    six.apply(_edit(six, members, params={"tolerance": 0.5}))
    assert [(op.op, op.path, op.materializes) for op in six.spec.pending] == [
        ("add", ROOT, True),
        ("add", ROOT, False),
    ]
    lines = entry_lines(six.spec.preview_base(A), six.spec.entry(A))
    kinds = [line.kind for line in lines]
    # six tolerance lines came, and nothing else is news (JSON's comma on
    # the line before each is rewritten: six more pairs)
    assert (
        sum('"tolerance": 0.5' in line.text for line in lines if line.kind == "add")
        == 6
    )
    assert kinds.count("add") == 12 and kinds.count("del") == 6
    # typed over: still those two ops
    six.apply(_edit(six, members, params={"tolerance": 0.25}))
    assert [op.materializes for op in six.spec.pending] == [True, False]
    # an Indices removal on a flat test shows what it removed
    six.discard()
    six.apply(_reindex(six, _members(six, range(1, 7)), "1:5"))
    assert [op.materializes for op in six.spec.pending] == [True, False]
    lines = entry_lines(six.spec.preview_base(A), six.spec.entry(A))
    removed = [line.text for line in lines if line.kind == "del"]
    assert any("pipe.T[6]" in text for text in removed)
    assert not any("pipe.T[" in line.text for line in lines if line.kind == "add")
    # ... and an add over a slice is the tree and the add
    six.discard()
    six.apply(_add(six, metric="tube", indices="1:2"))
    assert [op.materializes for op in six.spec.pending] == [True, False]


def test_a_value_typed_and_cleared_on_a_folded_field_leaves_nothing_pending(six):
    """`Session.stage`'s rule holds for the whole-tree op: the tree put
    back is the one the saved entry already scores by, and is no edit."""
    members = _members(six, range(1, 7))
    six.apply(_edit(six, members, params={"tolerance": 0.5}))
    assert len(six.spec.pending) == 2  # the flat test's tree, and the edit
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
    with pytest.raises(ValueError, match="no longer one folded row"):
        _edit(six, members, params={"tolerance": 0.5})
    assert six.spec.pending == []


# -- the folded editor's Indices (D5) -------------------------------------------------


def _reindex(session, paths, text, variables=None):
    return EditFamily().act(
        _view(session, leaf_path=paths[0], leaf_paths=paths),
        model_id=A,
        paths=paths,
        variables=_named(session, paths) if variables is None else variables,
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


# -- the fix batch of the executing review -------------------------------------------


def _saved_six() -> dict:
    """`and[x, nrmse pipe.T[1..6]]`, written in the spec."""
    return {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *({"metric": "nrmse", "variable": element(i)} for i in range(1, 7)),
        ],
    }


def _leaves_at(session) -> list[tuple[str, dict]]:
    out = []

    def walk(node, path):
        if "metric" in node:
            out.append((path, node))
        for i, child in enumerate(node.get("children") or []):
            walk(child, f"{path}/children/{i}")

    walk(session.tree(A), ROOT)
    return out


def _family_paths(session) -> tuple[str, ...]:
    """Every pipe.T leaf of the tree, wherever it is, in tree order."""
    return tuple(
        path
        for path, leaf in _leaves_at(session)
        if leaf["variable"].startswith("pipe.T[")
    )


def _rest(tree):
    """``tree`` without its pipe.T leaves: what a gesture on the fold must
    leave exactly as it was."""
    if "metric" in tree:
        return None if tree["variable"].startswith("pipe.T[") else tree
    kids = [_rest(child) for child in tree.get("children") or []]
    return {**tree, "children": [kid for kid in kids if kid is not None]}


def test_a_zero_based_family_adds_and_edits_by_its_own_indices(tmp_path):
    """M1: `pipe.T[0]` … `pipe.T[4]` prefilled `0:4`, and Add answered
    "0:4: indices start at 1"; its fold refused its own text, and `1:4`
    was the only way on — which removed pipe.T[0] for good."""
    from dstf.authoring.builtin.edit_tree import _family

    session = _session(*family_project(tmp_path, 5, tracked=(), fail=(), first=0))
    family = _family(_view(session), A, element(2))
    assert family is not None and family.text == "0:4"
    session.apply(_add(session, variable=element(2), indices=family.text))
    assert _variables(session) == ["x", *(element(i) for i in range(5))]
    assert classify(session.comparisons[A]) == PASS
    members = _family_paths(session)
    assert _reindex(session, members, "0:4") is None  # its own text: no edit
    session.apply(_reindex(session, members, "0,2:4"))
    assert _variables(session) == ["x", *(element(i) for i in (0, 2, 3, 4))]
    with pytest.raises(ValueError) as e:
        _reindex(session, _family_paths(session), "0:5")
    assert str(e.value) == "pipe.T[0:4] has 5 elements; 5 is not one of them."
    with pytest.raises(ValueError, match="an index is not negative"):
        _add(session, variable=element(2), indices="-1:2")


@pytest.mark.parametrize(
    "third",
    [
        {
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "pipe.T[3]"}],
        },
        {"metric": "nrmse", "variable": "pipe.T[3]", "tolerance": 0.5},
    ],
)
def test_indices_never_give_an_element_a_second_criterion_beside_its_own(
    tmp_path, third
):
    """M2: pipe.T[3] fails, and is a warning (or has its own tolerance) —
    the test PASSES. Indices 1:6 on the fold of the others added a second,
    hard nrmse on it: FAIL. Refused, naming it; nothing is staged."""
    tree = _saved_six()
    tree["children"][3] = third
    session = _session(*family_project(tmp_path, 6, tree=tree))
    assert classify(session.comparisons[A]) == PASS
    members = _members(session, (1, 2, 4, 5, 6))
    with pytest.raises(ValueError) as e:
        _reindex(session, members, "1:6")
    assert str(e.value) == (
        "pipe.T[3] already has its own nrmse criterion under this group — "
        "edit or remove that one, or leave 3 out"
    )
    assert session.spec.pending == []
    assert classify(session.comparisons[A]) == PASS


def test_an_element_that_has_the_folds_criterion_is_never_given_it_twice(tmp_path):
    """H1 from below: asked to add pipe.T[3] to a fold beside an identical
    leaf on it — a page that showed a stale fold did — nothing is staged,
    and never a second leaf."""
    session = _session(*family_project(tmp_path, 6, tree=_saved_six()))
    members = _members(session, (1, 2, 4, 5, 6))
    assert _reindex(session, members, "1:6") is None
    assert session.spec.pending == []


SHAPES = {
    "a sibling leaf": lambda family: {
        "combinator": "and",
        "children": [
            {"metric": "range", "variable": "x", "min_value": -5, "max_value": 5},
            *family,
            {"metric": "nrmse", "variable": "x", "tolerance": 0.3},
        ],
    },
    "a sibling combinator": lambda family: {
        "combinator": "and",
        "children": [
            {
                "combinator": "k-of-n",
                "k": 1,
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {
                        "combinator": "warn",
                        "children": [
                            {"metric": "range", "variable": "x", "min_value": 0}
                        ],
                    },
                ],
            },
            *family,
            {"combinator": "or", "children": [{"metric": "nrmse", "variable": "x"}]},
        ],
    },
    "the family in a nested combinator": lambda family: {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {
                "combinator": "or",
                "children": [
                    {"metric": "range", "variable": "x", "max_value": 9},
                    {"combinator": "and", "children": [*family]},
                ],
            },
            {"metric": "nrmse", "variable": "x", "tolerance": 0.2},
        ],
    },
}


@pytest.mark.parametrize("shape", SHAPES)
def test_a_gesture_on_a_fold_leaves_the_rest_of_the_tree_as_it_was(tmp_path, shape):
    """M3: the gesture is staged as the WHOLE tree, so everything it does
    not name rides in the op — a sibling criterion, a sibling group and its
    children, the groups around the family. Each as it was, after a folded
    field's edit, an Indices change and an add over a slice."""
    family = [{"metric": "nrmse", "variable": element(i)} for i in (1, 2, 3, 4)]
    tree = SHAPES[shape](family)
    session = _session(*family_project(tmp_path, 8, tracked=(1, 2, 3, 4), tree=tree))
    rest = _rest(session.tree(A))
    assert rest != session.tree(A) and json.dumps(rest).count('"variable": "x"') >= 2

    session.apply(_edit(session, _family_paths(session), params={"tolerance": 0.5}))
    assert _rest(session.tree(A)) == rest
    assert [
        leaf.get("tolerance")
        for _, leaf in _leaves_at(session)
        if leaf["variable"].startswith("pipe.T[")
    ] == [0.5] * 4

    session.apply(_reindex(session, _family_paths(session), "2:3,6"))
    assert _rest(session.tree(A)) == rest
    assert [
        leaf["variable"]
        for _, leaf in _leaves_at(session)
        if leaf["variable"].startswith("pipe.T[")
    ] == [element(i) for i in (2, 3, 6)]

    parent = _family_paths(session)[0].rsplit("/children/", 1)[0]
    session.apply(
        _add(session, path=parent, variable=element(7), metric="range", indices="7:8")
    )
    assert _rest(session.tree(A)) == rest
    assert len(_family_paths(session)) == 5
    # the bytes Save writes hold the rest as the file held it
    session.save()
    assert _rest(session.tree(A)) == rest


def test_a_folded_edit_whose_paths_went_stale_is_refused(tmp_path):
    """M4: an edit staged elsewhere moved the tree under the fold's paths —
    x's range now stands where pipe.T[1] stood — and the edit of "every
    member" overwrote x's criterion with nrmse 0.5. A path whose leaf is
    not the member it was read as refuses the whole edit."""
    session = _session(*family_project(tmp_path, 6, tree=_saved_six()))
    members = _members(session, range(1, 7))
    named = _named(session, members)
    session.stage(
        [
            PatchOp(
                A,
                "add",
                f"{ROOT}/children/1",
                {"metric": "range", "variable": "x", "min_value": -5, "max_value": 5},
            )
        ]
    )
    before = session.tree(A)
    pending = list(session.spec.pending)
    with pytest.raises(ValueError, match="no longer one folded row"):
        _edit(session, members, params={"tolerance": 0.5}, variables=named)
    with pytest.raises(ValueError, match="no longer one folded row"):
        _reindex(session, members, "1:3", variables=named)
    # ... and one that names its members by nothing is refused too
    with pytest.raises(ValueError, match="no longer one folded row"):
        _edit(session, members, params={"tolerance": 0.5}, variables=())
    assert session.tree(A) == before and session.spec.pending == pending


def test_the_same_indices_put_back_are_no_edit(tmp_path):
    """Decided 6: `1:20`, `1:5:20`, `1:20` left the leaves 1, 6, 11, 16, 2,
    3, … and Save rewrote a tree nobody had changed. The group stays in
    index order, so the tree is as saved and nothing is pending."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *({"metric": "nrmse", "variable": element(i)} for i in range(1, 21)),
        ],
    }
    cfg, work = family_project(tmp_path, 20, fail=(6,), tree=tree)
    session = _session(cfg, work)
    saved = (cfg.parent / "test_spec.json").read_bytes()
    session.apply(_reindex(session, _family_paths(session), "1:5:20"))
    assert len(session.spec.pending) == 1
    session.apply(_reindex(session, _family_paths(session), "1:20"))
    assert _variables(session) == ["x", *(element(i) for i in range(1, 21))]
    assert session.spec.pending == []
    session.save()
    assert (cfg.parent / "test_spec.json").read_bytes() == saved


def _notes(session) -> list[str]:
    return [text for op in session.spec.pending for _, text in op.notes]


def test_each_gesture_says_what_it_did_for_the_preview(tmp_path):
    """M5: one line per gesture above the entry's diff, in words."""
    cfg, work = family_project(tmp_path, 20, tracked=range(1, 7), fail=())
    session = _session(cfg, work)
    members = _members(session, range(1, 7))
    session.apply(_edit(session, members, params={"tolerance": 0.05}))
    assert _notes(session) == ["tolerance → 0.05 on 6 criteria of pipe.T[1:6]"]
    # typed over: the line follows, it does not pile up
    session.apply(_edit(session, members, params={"tolerance": 0.5}))
    assert _notes(session) == ["tolerance → 0.5 on 6 criteria of pipe.T[1:6]"]
    session.apply(
        _edit(session, members, params={"tolerance": 0.5}, window={"start": 0.2})
    )
    assert _notes(session) == [
        "tolerance → 0.5 on 6 criteria of pipe.T[1:6]",
        "window start → 0.2 on 6 criteria of pipe.T[1:6]",
    ]
    session.apply(_reindex(session, members, "1:5,9"))
    assert _notes(session)[2:] == [
        "Indices: removed pipe.T[6]",
        "Indices: added pipe.T[9]",
    ]
    session.apply(_add(session, metric="range", indices="1:5:20"))
    assert _notes(session)[4:] == ["added 4 criteria: pipe.T[1:5:16]"]
    # ONE op holds the gestures (and the flat test's tree before it)
    assert [(op.op, op.path, op.materializes) for op in session.spec.pending] == [
        ("add", ROOT, True),
        ("add", ROOT, False),
    ]
    session.discard()
    members = _members(session, range(1, 7))
    session.apply(_edit(session, members, metric="range", params={"min_value": 0}))
    assert _notes(session) == ["mode → range on 6 criteria of pipe.T[1:6]"]
    session.apply(_edit(session, members, metric="range", params={}))
    assert _notes(session) == [
        "mode → range on 6 criteria of pipe.T[1:6]",
        "min_value cleared on 6 criteria of pipe.T[1:6]",
    ]
