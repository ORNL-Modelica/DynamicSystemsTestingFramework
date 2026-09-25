"""The capabilities the tool ships (§4.6 table, §4.7): `act` and `available`
over the fixture project, and each panel alone in NiceGUI's in-process user
simulation through `capability_host` — no shell, no browser."""

from __future__ import annotations

import asyncio
import copy

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

from capability_host import host  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.accept import Accept  # noqa: E402
from dstf.authoring.builtin.edit_criteria import EditCriteria  # noqa: E402
from dstf.authoring.builtin.edit_tree import EditTree  # noqa: E402
from dstf.authoring.builtin.rerun import Rerun  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import (  # noqa: E402
    LOOSEN,
    A,
    B,
    C,
    D,
    _Gate,
    _project,
    _two_var_project,
)


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it."""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def session(tmp_path):
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


@pytest.fixture
def two_var_session(tmp_path):
    """A's tree is `and[nrmse x, nrmse y]`: a second variable is what tells
    a form that reads the selection apart from one that takes the first."""
    cfg, work = _two_var_project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


#: The page's selection the leaf panels are built for (D129).
LEAF0 = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0")


async def test_edit_criteria_stages_on_change(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        # derive_schema's label for NrmseConfig.tolerance.
        await user.should_see("Tolerance")
        form_tol = next(
            c
            for c in user.find(ui.number).elements
            if "olerance" in str(c.props.get("label", ""))
        )
        form_tol.set_value(0.5)
        assert len(session.spec.pending) == 2  # materialization + the replace
        assert session.spec.pending[-1].op == "replace"
        assert session.spec.pending[-1].path == "/metrics/children/0"
        assert session.spec.pending[-1].value["tolerance"] == 0.5
        from dstf.reporting.status import PASS, classify

        assert classify(session.comparisons[A]) == PASS  # rescored live


async def test_edit_criteria_switches_mode_and_offers_against_and_window(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("mode")
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        assert set(mode.options) >= {"nrmse", "tube", "range"}
        mode.set_value("range")
        # RangeConfig.max_value's label is "Upper bound (optional)".
        await user.should_see("Upper bound")
        against = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "against"
        )
        assert against.options == ["primary"]
        start = next(
            c
            for c in user.find(ui.number).elements
            if c.props.get("label") == "window start"
        )
        start.set_value(0.25)
        leaf = session.spec.pending[-1].value
        assert leaf["metric"] == "range" and leaf["window"]["start"] == 0.25


def _ordered(element):
    """Every element under ``element``, in document order."""
    for slot in element.slots.values():
        for child in slot.children:
            yield child
            yield from _ordered(child)


def _labels(element) -> list[str]:
    """The labelled controls under ``element``, in document order."""
    return [
        str(e.props["label"])
        for e in _ordered(element)
        if isinstance(e, (ui.select, ui.number, ui.input, ui.textarea))
        and e.props.get("label")
    ]


async def test_the_criterion_editors_fields_are_one_row_with_the_mode_first(session):
    """The board: the plot and its editor have to fit a screen together, so
    the fields sit in ONE wrapping row — and the mode select, which decides
    what the other fields ARE, comes first. It was created after the form's
    slot, so it rendered below the fields it controls; and each field is
    wide enough for its label ("Width mode" was clipped to "Wid…")."""
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _figure):
        await user.should_see("Tolerance")
        (row,) = [e for e in user.find(ui.row).elements if "dstf-fields" in e.classes]
        assert "wrap" in row.classes
        labels = _labels(row)
        assert labels[0] == "mode"
        assert "Tolerance" in labels
        assert labels[-3:] == ["window start", "window end", "against"]
        fields = [
            e
            for e in _ordered(row)
            if isinstance(e, (ui.select, ui.number, ui.input)) and e.props.get("label")
        ]
        assert fields and all("dstf-field" in e.classes for e in fields)


async def test_a_list_field_takes_its_own_line_of_the_editors_row(session):
    """A mode with a list field (tube's control points) shows it as JSON: a
    multi-line control belongs on its own full-width line of the wrapping
    row, not squeezed between two numbers."""
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _figure):
        await user.should_see("mode")
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("tube")
        await user.should_see("Width mode")
        (row,) = [e for e in user.find(ui.row).elements if "dstf-fields" in e.classes]
        assert _labels(row)[0] == "mode" and "Width mode" in _labels(row)
        (points,) = [
            e
            for e in _ordered(row)
            if isinstance(e, ui.textarea) and e.props.get("label") == "Control points"
        ]
        assert "dstf-field-wide" in points.classes
        # In SOURCE order it sits between two numbers ("Min width" before,
        # "Interpolation" after), which broke the row into three lines. The
        # stylesheet is what puts it last of the row.
        assert _labels(row).index("Control points") < len(_labels(row)) - 1
        from dstf.authoring.shell.style import STYLE

        assert ".dstf-fields .dstf-field-wide { order: 1; }" in STYLE


def test_edit_criteria_is_greyed_while_the_test_is_locked(session):
    from dstf.authoring.capabilities import Job

    view = SessionView(session, LEAF0)
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert EditCriteria().available(view) == f"rerun is running on {A}"
    gate.release.set()
    assert job.done.wait(5)
    assert EditCriteria().available(view) is True


async def test_a_mode_switch_starts_from_the_new_modes_defaults(session):
    """Set a tolerance, switch to range, set a bound: the leaf carries no
    stale nrmse tolerance."""
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("Tolerance")
        tol = next(
            c
            for c in user.find(ui.number).elements
            if str(c.props.get("label")) == "Tolerance"
        )
        tol.set_value(0.5)
        assert len(session.spec.pending) == 2
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("range")
        await user.should_see("Upper bound")
        upper = next(
            c
            for c in user.find(ui.number).elements
            if "Upper" in str(c.props.get("label"))
        )
        upper.set_value(2.0)
        leaf = session.spec.pending[-1].value
        assert leaf == {"metric": "range", "variable": "x", "max_value": 2.0}


def _session_with_soft_check(tmp_path, *ops):
    """A fresh Session per simulation (a page cannot outlive its client):
    A has a soft_check `om_cross`, plus whatever ops are staged."""
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    t = [i / 4 for i in range(5)]
    s.project.store.add_soft_check(
        A, "om_cross", t, [{"index": 1, "name": "x", "values": t}]
    )
    s._reload_results()
    if ops:
        s.stage(list(ops))
    return s


WARN_LEAF = {
    "combinator": "warn",
    "children": [{"metric": "nrmse", "variable": "x", "against": "om_cross"}],
}

#: The leaf beneath the `warn` the two soft-check tests open the panel on.
UNDER_WARN = Selection(
    tests=frozenset({A}), variable="x", leaf_path="/metrics/children/1/children/0"
)


async def _against_select(user):
    await user.should_see("against")
    return next(
        c for c in user.find(ui.select).elements if c.props.get("label") == "against"
    )


async def test_against_offers_no_soft_check_outside_warn(tmp_path):
    """D66: a soft_check is a valid target only beneath a `warn`."""
    s = _session_with_soft_check(tmp_path)
    async with host(s, EditCriteria(), LEAF0) as (user, errors, _):
        assert (await _against_select(user)).options == ["primary"]


async def test_against_offers_the_soft_check_under_warn(tmp_path):
    from dstf.authoring.spec_document import PatchOp

    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", WARN_LEAF)
    )
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert (
            against.options == ["primary", "om_cross"] and against.value == "om_cross"
        )


async def test_against_keeps_a_value_the_store_no_longer_knows(tmp_path):
    """The leaf one opens this panel to repair — its baseline was removed
    after the spec was written; the validator refuses to STAGE such a leaf,
    so it can only arrive from disk — must not crash the panel."""
    import json

    cfg, work = _project(tmp_path)
    doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
    entry = next(e for e in doc["tests"] if e["model"] == A)
    entry["metrics"] = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {
                "combinator": "warn",
                "children": [{"metric": "nrmse", "variable": "x", "against": "ghost"}],
            },
        ],
    }
    (cfg.parent / "test_spec.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )
    s = Session()
    s.open(cfg, work_dir=work)
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert "ghost" in against.options and against.value == "ghost"


#  -- Edit tree, as a node's controls and a variable's add form ------------

ROOT = "/metrics"

#: A tree two levels deep, the shapes a cluster's forms each need: a root
#: leaf, a `k-of-n` of two (a combinator unwrap must refuse) and a `warn` of
#: one (the combinator unwrap takes).
NESTED = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        {
            "combinator": "k-of-n",
            "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "range", "variable": "x", "max_value": 9.0},
            ],
        },
        {
            "combinator": "warn",
            "children": [{"metric": "range", "variable": "x", "max_value": 5.0}],
        },
    ],
}


def _node(model, path, **selection):
    return Selection(tests=frozenset({model}), node_path=path, **selection)


async def _settled(predicate, tries: int = 40) -> None:
    """Refreshes are deferred; wait for a staged op to land."""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


#: The k-of-n field's label (the board's words).
K = "k — how many must pass"
#: Every per-kind field of a combinator form.
FIELDS = {K, "Weights, one per child", "Threshold", "Direction"}


def _labelled(user, kind, label):
    return [e for e in user.find(kind=kind).elements if e.props.get("label") == label]


def _labels_shown(user) -> set[str]:
    """The labels of every field on the page — a kind's fields that are not
    shown are not built at all."""
    out: set[str] = set()
    for kind in (ui.select, ui.number, ui.input):
        try:
            elements = user.find(kind=kind).elements
        except AssertionError:
            continue
        out |= {str(e.props.get("label")) for e in elements}
    return out


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


def _none(user, marker: str) -> bool:
    try:
        user.find(marker=marker)
    except AssertionError:
        return True
    return False


def _tips(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.tooltip)]


def _shown_tips(element) -> list[str]:
    """The tooltips a hover over ``element`` would raise: a hidden one says
    nothing."""
    return [
        e.text for e in element.descendants() if isinstance(e, ui.tooltip) and e.visible
    ]


def _by_label(select, label: str):
    """The option VALUE a person picks by reading ``label``."""
    (value,) = [v for v, text in select.options.items() if text == label]
    return value


def test_edit_tree_needs_one_test_and_a_node_or_a_variable(session):
    cap = EditTree()
    assert cap.available(SessionView(session, Selection(tests=frozenset({A})))) == (
        "select a tree node or a variable"
    )
    assert cap.available(SessionView(session, _node(A, ROOT))) is True
    assert (
        cap.available(
            SessionView(session, Selection(tests=frozenset({A}), variable="x"))
        )
        is True
    )


def test_available_answers_the_lock_and_the_missing_tree_first(session):
    """Both hold with nothing selected in the tree: a page whose selection is
    only a test must read why it cannot edit, not "select a tree node"."""
    from dstf.authoring.capabilities import Job

    cap = EditTree()
    only_b = SessionView(session, Selection(tests=frozenset({B})))
    assert cap.available(only_b) == "no tree to edit (nothing scored)"
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
    try:
        assert cap.available(SessionView(session, Selection(tests=frozenset({D})))) == (
            f"rerun is running on {D}"
        )
        assert cap.available(SessionView(session, _node(D, ROOT))) == (
            f"rerun is running on {D}"
        )
    finally:
        gate.release.set()


def test_the_caption_at_a_variable_names_it(session):
    view = SessionView(session, Selection(tests=frozenset({A}), variable="x"))
    assert EditTree().caption(view) == "Add a criterion for x"
    assert EditTree().caption(SessionView(session, _node(A, ROOT))) == "Edit tree"


def test_the_caption_reads_the_scope_the_selection_carries(two_var_session):
    """The caption reads `Selection.texts` — the page's one read of the
    test's label scope, handed down with the selection — and derives
    nothing: a caption that built the scope for itself did it twice per
    card, which was the Test page's whole rebuild cost (40 cards: 82 scope
    builds, 0.37 s).

    A selection carrying no scope says the NAME. That also settles what a
    label off `next(iter(sel.tests))` used to do: A and B both track `x`
    here, each with its own expression, and a frozenset has no order to
    pick by — VARIABLE placement is single-test anyway (`available`
    refuses anything else)."""
    from test_session import with_expressions

    with_expressions(two_var_session, A, {"x": "a.Q"})
    with_expressions(two_var_session, B, {"x": "b.Q"})
    scope = two_var_session.variable_texts(A)
    one = SessionView(
        two_var_session,
        Selection(tests=frozenset({A}), variable="x", texts=scope),
    )
    assert EditTree().caption(one) == "Add a criterion for a.Q"
    bare = SessionView(two_var_session, Selection(tests=frozenset({A}), variable="x"))
    assert EditTree().caption(bare) == "Add a criterion for x"
    two = SessionView(two_var_session, Selection(tests=frozenset({A, B}), variable="x"))
    assert EditTree().available(two) == "select exactly one test"
    assert EditTree().caption(two) == "Add a criterion for x"


def test_the_caption_at_a_node_is_the_label_even_with_a_variable(session):
    """A criteria row's selection carries both: at a node the panel is that
    node's controls, so the caption is the plain label."""
    view = SessionView(session, _node(A, ROOT, variable="x"))
    assert EditTree().caption(view) == "Edit tree"


def test_add_beside_a_root_leaf_wraps_the_root_in_and(session):
    # A's implicit tree is `and[nrmse x]`, never a root leaf: a root leaf has
    # to be staged.
    session.stage([PatchOp(A, "add", "/metrics", {"metric": "nrmse", "variable": "x"})])
    view = SessionView(session, _node(A, ROOT))
    assert "combinator" not in session.tree(A)
    outcome = EditTree().act(
        view, action="add", model_id=A, path=ROOT, variable="x", metric="range"
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", ROOT)
    assert op.value["combinator"] == "and"
    assert [c.get("metric", "nrmse") for c in op.value["children"]] == [
        "nrmse",
        "range",
    ]
    session.apply(outcome)
    assert len(session.tree(A)["children"]) == 2


def test_add_beside_a_leaf_appends_to_its_parent(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    view = SessionView(session, _node(D, "/metrics/children/1"))
    outcome = EditTree().act(
        view,
        action="add",
        model_id=D,
        path="/metrics/children/1",
        variable="x",
        metric="nrmse",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/-")


def test_add_beside_a_nested_leaf_appends_to_its_own_parent(session):
    """Two levels deep: the parent is everything before the LAST
    `/children/` — read from the first, the leaf would join the root."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    path = "/metrics/children/1/children/0"
    outcome = EditTree().act(
        SessionView(session, _node(D, path)),
        action="add",
        model_id=D,
        path=path,
        variable="x",
        metric="range",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/1/children/-")


def test_add_under_a_combinator_is_unchanged(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    outcome = EditTree().act(
        SessionView(session, _node(D, ROOT)),
        action="add",
        model_id=D,
        path=ROOT,
        variable="x",
        metric="nrmse",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/-")


async def test_the_node_panel_is_the_cluster_and_its_forms_stage(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _figure):
        for marker in (f"add:{leaf}", f"wrap:{leaf}", f"remove:{leaf}"):
            await user.should_see(marker=marker)
        await user.should_not_see(marker=f"edit:{leaf}")  # a leaf has no combinator
        user.find(marker=f"wrap:{leaf}").click()  # the row is built on the click
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "combinator" in session.tree(D)["children"][1])
        assert errors == {}


async def test_a_cluster_builds_a_form_only_when_its_icon_is_clicked(session):
    """Cost (the executing review): a cluster whose three forms are built
    with it is ~28 elements, and a tree row holds one per node — 40 leaves
    made a 2387-element page with the tree open. No form exists until its
    icon is clicked; the click opens it as a row (moved from the menu,
    whose content was built when the menu opened)."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        await user.should_see(marker=f"add:{leaf}")
        await user.should_not_see(kind=ui.select)  # no form is built yet
        await user.should_not_see(marker=f"apply-add:{leaf}")
        user.find(marker=f"add:{leaf}").click()
        await user.should_see(marker=f"apply-add:{leaf}")
        await user.should_see(kind=ui.select)  # the add form's two selects
        assert len(user.find(marker=f"apply-add:{leaf}").elements) == 1


async def test_the_root_has_no_remove_and_a_combinator_has_edit(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _errors, _figure):
        await user.should_see(marker=f"edit:{ROOT}")
        await user.should_not_see(marker=f"remove:{ROOT}")


async def test_remove_stages_at_once(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    n = len(session.tree(D)["children"])
    async with host(session, EditTree(), _node(D, "/metrics/children/1")) as (
        user,
        _e,
        _f,
    ):
        user.find(marker="remove:/metrics/children/1").click()
        await _settled(lambda: len(session.tree(D)["children"]) == n - 1)


async def test_unwrap_lives_inside_edit(session):
    """Board 1C: no unwrap icon of its own (it was drawn on a one-child
    combinator only); Unwrap is a button of the Edit row, and on the k-of-n
    of two it is enabled — its children take its place in its parent."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _figure):
        await user.should_see(marker=f"edit:{k_of_n}")
        await user.should_not_see(marker=f"unwrap:{k_of_n}")
        user.find(marker=f"edit:{k_of_n}").click()
        (unwrap,) = user.find(marker=f"unwrap-in-edit:{k_of_n}").elements
        assert unwrap.enabled


async def test_unwrap_in_edit_stages_and_closes_the_row(session):
    """Unwrap is the Edit row's button now, not an icon that acted at once;
    it stages the unwrap and the row goes."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    warn = "/metrics/children/2"
    async with host(session, EditTree(), _node(D, warn)) as (user, errors, _figure):
        user.find(marker=f"edit:{warn}").click()
        user.find(marker=f"unwrap-in-edit:{warn}").click()
        await _settled(lambda: "combinator" not in session.tree(D)["children"][2])
        assert session.tree(D)["children"][2]["max_value"] == 5.0  # the child kept
        assert errors == {}
        await _settled(lambda: _none(user, f"unwrap-in-edit:{warn}"))


async def test_the_edit_form_is_prefilled_and_stages_an_edit(session):
    """`tune` opens the form on THAT node's kind and fields; applying it
    stages an edit — a wrap would have nested the node under a new one."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        user.find(marker=f"edit:{k_of_n}").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        (kind,) = _labelled(user, ui.select, "Combinator")
        (k,) = _labelled(user, ui.number, K)
        assert (kind.value, k.value) == ("k-of-n", 2)
        kind.set_value("or")
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(lambda: session.tree(D)["children"][1]["combinator"] == "or")
        node = session.tree(D)["children"][1]
        assert "k" not in node and len(node["children"]) == 2  # edited, not wrapped
        assert errors == {}


async def test_the_cluster_add_form_starts_from_the_leafs_own_variable(
    two_var_session,
):
    """Add beside a criterion on `y` offers `y`, and the leaf it stages
    scores `y` — the variable select's first option is `x`."""
    session = two_var_session
    leaf = "/metrics/children/1"
    assert session.tree(A)["children"][1]["variable"] == "y"
    async with host(session, EditTree(), _node(A, leaf)) as (user, errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        assert var.value == "y"
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(A)["children"]) == 3)
        assert session.tree(A)["children"][-1]["variable"] == "y"
        assert errors == {}


async def test_the_add_form_offers_a_variable_whose_criterion_was_removed(
    two_var_session,
):
    """A one-way door (the Task 5-6 review): the add forms read what was
    SCORED, so removing y's last criterion left no way to add one back
    until Discard. A test's variables are the result's, staged edit or not
    — `Session.tracked`'s union, for the same reason."""
    session = two_var_session
    session.stage([PatchOp(A, "remove", "/metrics/children/1")])
    assert [c["variable"] for c in session.tree(A)["children"]] == ["x"]
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        # Dict options: the VALUE a leaf is written against is the name, the
        # text is what a person reads — here the same, neither has an
        # expression.
        assert var.options == {"x": "x", "y": "y"}


async def test_the_add_form_offers_a_variable_with_an_awkward_expression(tmp_path):
    """TRANSFORM has ~70 tests whose UnitTests variables carry a `cat(...)`
    expression. The add forms read `Session.tracked`'s list, so they offer
    the name the result file gives the variable — listing it under two
    spellings offered the same trajectory twice, the second time under a
    name no leaf can be written against. An AUTHORED tree, because such a
    test had no implicit one to edit: its spec names the variable as the
    file spells it."""
    import json

    from test_session import (
        AWKWARD_EXPRESSION,
        UT1,
        _awkward_expression_project,
        with_expressions,
    )

    cfg, work = _awkward_expression_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = {
                "combinator": "and",
                "children": [{"metric": "nrmse", "variable": "y"}],
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)
    with_expressions(session, A, {UT1: AWKWARD_EXPRESSION})
    assert session.tracked(A) == ["y", UT1]
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        # The VARIABLE panel is the add form itself, drawn open.
        (var,) = user.find(marker="apply-add:y:variable").elements
        # The option's VALUE is the result-file name — what the leaf is
        # written against — and its TEXT is the expression the model wrote,
        # which is the only way to tell twelve `unitTests.x[i]` apart.
        assert var.options == {"y": "y", UT1: AWKWARD_EXPRESSION}


async def test_the_cluster_add_form_on_a_nested_leaf_lands_under_its_parent(session):
    """The form's `under` is the node the cluster was drawn for, not the
    root: the new leaf joins the k-of-n."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    leaf = "/metrics/children/1/children/0"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(D)["children"][1]["children"]) == 3)
        assert len(session.tree(D)["children"]) == 3  # the root is untouched
        assert errors == {}


async def test_a_form_opened_after_the_session_moved_shows_the_node_as_it_is(session):
    """The cluster stands while the Session moves under it (the Task 4
    review's probe): a form built when the cluster was drawn showed the k
    the node had then, and an apply from it staged that k back over the one
    already staged. The row is built on the icon's click, from the tree as
    it is at that click."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    assert session.tree(D)["children"][1]["k"] == 2
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        session.stage([PatchOp(D, "replace", f"{k_of_n}/k", 1)])
        user.find(marker=f"edit:{k_of_n}").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 1  # the node as it is now
        staged = len(session.spec.pending)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        # An Apply stages only what moved (the Task 6 review), and the form
        # shows the node as it is: nothing is staged — a form built at the
        # first open would have staged its k of 2 back.
        await _settled(lambda: _none(user, "host-row:title"))
        await asyncio.sleep(0.1)
        assert len(session.spec.pending) == staged
        assert session.tree(D)["children"][1]["k"] == 1  # not the k of the first open
        assert errors == {}


async def test_a_form_opened_on_a_node_that_is_gone_says_so_and_offers_no_form(session):
    """The node was removed under a cluster that is still drawn: the row
    reads the tree at THIS click, so it carries the sentence `available`
    would give rather than a form staging against a path that names
    nothing."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    # The LAST child: removing any other only shifts the rest up, and the
    # path would still name a node — a different one.
    leaf = f"/metrics/children/{len(TREE['children']) - 1}"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        session.stage([PatchOp(D, "remove", leaf)])
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(f"{leaf} names no node of {D}")
        await user.should_not_see(marker=f"apply-wrap:{leaf}")


async def test_a_forms_controls_fill_its_row(session):
    """Every dense control fills the row it is drawn in. (A menu at a row's
    right end came out 88 px wide with its labels cropped, and its column
    asked for a width; a row under the node's row is the page's width.)"""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        assert "dense" in var.props and "w-full" in var.classes


async def test_a_cluster_tooltip_sits_above_its_button(session):
    """Chromium (the review): the default tooltip opens below the icon, over
    what the same click opened below the row (a menu then, a form row
    now)."""
    async with host(session, EditTree(), _node(A, ROOT)) as (user, _errors, _figure):
        (button,) = user.find(marker=f"add:{ROOT}").elements
        (tip,) = [e for e in button.descendants() if isinstance(e, ui.tooltip)]
        assert tip.props.get("anchor") == "top middle"
        assert tip.props.get("self") == "bottom middle"


async def test_a_form_is_rebuilt_on_every_open(session):
    """A form built once carries the values the node had then: a combinator
    edited and then Discarded reopened on the k that is no longer there.
    Each open is the icon's click; each close, Cancel."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _figure):
        user.find(marker=f"edit:{k_of_n}").click()
        (first,) = user.find(marker=f"apply-edit:{k_of_n}").elements
        user.find(marker=f"cancel:{k_of_n}").click()
        user.find(marker=f"edit:{k_of_n}").click()
        (second,) = user.find(marker=f"apply-edit:{k_of_n}").elements
        assert second is not first  # one apply button, and a new one
        session.stage([PatchOp(D, "replace", f"{k_of_n}/k", 1)])
        user.find(marker=f"cancel:{k_of_n}").click()
        user.find(marker=f"edit:{k_of_n}").click()
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 1  # the node as it is now, not as it was


def test_a_node_path_that_names_no_node_says_so(session):
    """A stale selection (the node was removed under it) reaches `available`
    and `act`: both name it, rather than an IndexError out of the page
    build."""
    cap = EditTree()
    gone = "/metrics/children/7"
    view = SessionView(session, _node(A, gone))
    assert cap.available(view) == f"{gone} names no node of {A}"
    with pytest.raises(ValueError, match=f"^{gone} names no node of {A}$"):
        cap.act(view, action="wrap", model_id=A, path=gone, kind="and")
    # A pointer that resolves to something that is not a node at all.
    word = "/metrics/combinator"
    assert cap.available(SessionView(session, _node(A, word))) == (
        f"{word} names no node of {A}"
    )


async def test_a_node_and_a_variable_together_draw_the_nodes_controls(session):
    """A criteria row's selection carries both; the cluster wins, so the
    row's `+` is the node's, not a second "Add a criterion for x"."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf, variable="x")) as (
        user,
        _errors,
        _figure,
    ):
        await user.should_see(marker=f"add:{leaf}")
        await user.should_not_see(marker="apply-add:x")


async def test_applying_a_form_closes_its_row(session):
    """The act is staged and the row is gone — a menu then, a row now: left
    open it would offer a form for a node that has just changed."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker=f"apply-wrap:{leaf}")
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "combinator" in session.tree(D)["children"][1])
        assert _none(user, f"apply-wrap:{leaf}")
        assert _none(user, "host-row:title")


async def test_the_variable_panel_adds_a_criterion_for_it(two_var_session):
    session = two_var_session
    sel = Selection(tests=frozenset({A}), variable="y")
    n = len(session.tree(A)["children"])
    async with host(session, EditTree(), sel) as (user, errors, _figure):
        # The VARIABLE panel is the add form itself, drawn open: no row.
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(session.tree(A)["children"]) == n + 1)
        assert errors == {}
        # The variable the footer belongs to, not the first tracked name.
        assert session.tree(A)["children"][-1]["variable"] == "y"


def test_edit_tree_needs_exactly_one_test(session):
    view = SessionView(session, Selection(tests=frozenset({A, B})))
    assert EditTree().available(view) == "select exactly one test"


def test_edit_tree_reaches_nested_nodes(session):
    """The pointer walker consumed `children` and then indexed the LIST with
    the word — every non-root wrap/edit raised TypeError. `act` is pure, so
    this pins it without a page."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [PatchOp(A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "x"})]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    cap = EditTree()
    # wrap a child, not the root: a `replace` at that path
    outcome = cap.act(
        view, action="wrap", model_id=A, path="/metrics/children/1", kind="warn"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.path == "/metrics/children/1"
    assert (
        op.value["combinator"] == "warn" and op.value["children"][0]["variable"] == "x"
    )
    session.apply(outcome)
    tree = session.tree(A)
    assert tree["children"][1]["combinator"] == "warn"
    # edit the nested combinator: k-of-n on the warn's child list is refused
    # by the parser (warn needs one child), so change the ROOT via its path
    # and a nested leaf's removal through a nested pointer.
    outcome = cap.act(
        view, action="remove", model_id=A, path="/metrics/children/1/children/0"
    )
    assert outcome.ops[0].path == "/metrics/children/1/children/0"


def test_edit_tree_unwraps_a_single_child_combinator(session):
    """The browser editor's unwrap: warn(leaf) becomes leaf, params kept;
    remove would have lost the leaf."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {
                    "combinator": "warn",
                    "children": [
                        {"metric": "nrmse", "variable": "x", "tolerance": 0.3}
                    ],
                },
            )
        ]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    outcome = EditTree().act(
        view, action="unwrap", model_id=A, path="/metrics/children/1"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.value == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    session.apply(outcome)
    assert session.tree(A)["children"][1] == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    # The root's two children have no parent to move into (several
    # children take their node's place since the form-row plan's Task 6).
    with pytest.raises(ValueError, match="the root has no parent"):
        EditTree().act(view, action="unwrap", model_id=A, path="/metrics")


# -- the forms as rows under the node (board 1A-1D) -------------------------


async def test_wrap_opens_a_row_titled_for_the_node_with_only_its_kinds_fields(
    session,
):
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker="host-row:title")
        assert _one(user, "host-row:title").text == "Wrap nrmse · x in a combinator"
        (kind,) = _labelled(user, ui.select, "Combinator")
        assert kind.value == "and"
        assert not _labels_shown(user) & FIELDS
        await user.should_see(
            "The criterion becomes the only child of a new and; add siblings to "
            "it with its +."
        )
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        assert _labels_shown(user) & FIELDS == {K}
        await user.should_see(
            "The criterion becomes the only child of a new k-of-n; add siblings "
            "to it with its +."
        )
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        assert _labels_shown(user) & FIELDS == FIELDS - {K}
        await user.should_see(
            "The criterion becomes the only child of a new weighted node, with "
            "the one weight above."
        )
        kind.set_value("or")
        await user.should_see(
            "The criterion becomes the only child of a new or; add siblings to "
            "it with its +."
        )
        kind.set_value("warn")
        await user.should_see(
            "A failure of this criterion becomes a warning; the test still passes."
        )
        assert not _labels_shown(user) & FIELDS


async def test_a_kinds_typed_value_is_kept_for_a_return_to_it(session):
    """Changing the select rebuilds the fields column; a value typed for a
    kind is there again when the user comes back to that kind."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        (k,) = _labelled(user, ui.number, K)
        k.set_value(3)
        kind.set_value("and")
        await _settled(lambda: K not in _labels_shown(user))
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 3


async def test_a_wrapped_combinator_reads_as_a_node(session):
    """For a combinator being wrapped, "criterion" reads "node"."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        user.find(marker=f"wrap:{k_of_n}").click()
        await user.should_see(
            "The node becomes the only child of a new and; add siblings to it "
            "with its +."
        )
        assert _one(user, "host-row:title").text == (
            "Wrap k-of-n · 2 criteria · k=2 in a combinator"
        )


async def test_edit_carries_unwrap_and_the_nodes_direction(session):
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 1.0],
        "threshold": 0.5,
        "direction": "greater",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    node = "/metrics/children/0"
    async with host(session, EditTree(), _node(D, node)) as (user, errors, _f):
        user.find(marker=f"edit:{node}").click()
        await user.should_see(marker=f"unwrap-in-edit:{node}")
        assert _one(user, "host-row:title").text == "Edit this combinator"
        (direction,) = _labelled(user, ui.select, "Direction")
        # Pinned, and chosen BY LABEL below: a test that set the value
        # "less" passed against a select that wrote its labels as values.
        assert direction.options == {
            "less": "lower is better",
            "greater": "higher is better",
        }
        assert direction.options[direction.value] == "higher is better"
        await user.should_see("Changes this node's kind and fields; its children stay.")
        direction.set_value(_by_label(direction, "lower is better"))
        staged = len(session.spec.pending)
        user.find(marker=f"apply-edit:{node}").click()
        await _settled(lambda: len(session.spec.pending) > staged)
        ops = session.spec.pending[staged:]
        assert any(
            op.op == "replace" and op.path == f"{node}/direction" and op.value == "less"
            for op in ops
        ), ops
        assert errors == {}


async def test_the_edit_rows_buttons_read_unwrap_cancel_apply(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        user.find(marker=f"edit:{k_of_n}").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        row = _one(user, f"apply-edit:{k_of_n}").parent_slot.parent
        assert [b.text for b in row.default_slot.children] == [
            "Unwrap",
            "Cancel",
            "Apply",
        ]


def test_unwrap_of_several_children_moves_them_into_the_parent(session):
    a = {"metric": "nrmse", "variable": "x"}
    b = {"metric": "range", "variable": "x", "max_value": 9.0}
    c = {"metric": "range", "variable": "x", "max_value": 5.0}
    tree = {
        "combinator": "and",
        "children": [{"combinator": "k-of-n", "k": 1, "children": [a, b]}, c],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    node = "/metrics/children/0"
    outcome = EditTree().act(
        SessionView(session, _node(D, node)), action="unwrap", model_id=D, path=node
    )
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("remove", node, None),
        ("add", "/metrics/children/0", a),
        ("add", "/metrics/children/1", b),
    ]
    session.apply(outcome)
    assert session.tree(D) == {"combinator": "and", "children": [a, b, c]}


async def test_unwrap_is_disabled_on_a_root_with_several_children(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _errors, _f):
        user.find(marker=f"edit:{ROOT}").click()
        await user.should_see(marker=f"unwrap-in-edit:{ROOT}")
        button = _one(user, f"unwrap-in-edit:{ROOT}")
        assert not button.enabled
        assert _tips(button) == ["the root has no parent to take its children"]


async def test_plus_is_grey_under_warn_and_weighted(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    for path in ("/metrics/children/2", "/metrics/children/2/children/0"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            plus = _one(user, f"add:{path}")
            assert not plus.enabled
            assert _tips(plus) == ["a warn takes exactly one child"]
    # Beside a leaf under an `and`, and under a k-of-n: `+` works.
    for path in ("/metrics/children/0", "/metrics/children/1"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            assert _one(user, f"add:{path}").enabled
    weighted = {
        "combinator": "weighted",
        "weights": [1.0],
        "threshold": 0.5,
        "children": [{"metric": "nrmse", "variable": "x"}],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    for path in ("/metrics/children/0", "/metrics/children/0/children/0"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            plus = _one(user, f"add:{path}")
            assert not plus.enabled
            assert _tips(plus) == [
                "a weighted node has one weight per child; a child cannot be "
                "added alone"
            ]


async def test_add_reveals_the_new_criterion(session):
    three = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
            {"metric": "range", "variable": "x", "max_value": 5.0},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, three)])
    reveals: list[str] = []
    async with host(session, EditTree(), _node(D, ROOT), reveals) as (user, e, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert _one(user, "host-row:title").text == (
            "Add a criterion under and · 3 criteria"
        )
        await user.should_see(
            "Added with the metric's defaults as the last child; its editor "
            "opens under its plot."
        )
        user.find(marker=f"apply-add:{ROOT}").click()
        await _settled(lambda: len(session.tree(D)["children"]) == 4)
        assert reveals == ["/metrics/children/3"] and e == {}
    # Beside a ROOT leaf: the root becomes an `and` of both.
    session.stage([PatchOp(D, "add", ROOT, {"metric": "nrmse", "variable": "x"})])
    reveals.clear()
    async with host(session, EditTree(), _node(D, ROOT), reveals) as (user, e, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert _one(user, "host-row:title").text == ("Add a criterion beside nrmse · x")
        user.find(marker=f"apply-add:{ROOT}").click()
        await _settled(lambda: "combinator" in session.tree(D))
        assert reveals == ["/metrics/children/1"] and e == {}


async def test_cancel_stages_nothing(session):
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker=f"cancel:{leaf}")
        user.find(marker=f"cancel:{leaf}").click()
        await _settled(lambda: _none(user, f"cancel:{leaf}"))
        assert _none(user, "host-row:title")  # `close` emptied the row
        assert session.spec.pending == []


# -- the Task 6 review's corrections ----------------------------------------


async def test_an_untouched_weighted_wrap_waits_for_a_typed_threshold(session):
    """A weighted wrap's Threshold starts EMPTY and Wrap is grey, saying
    why, until a number is typed; the one weight starts as 1, one per child
    (the wrapped node). Before, an untouched weighted Wrap was refused every
    time (no weights), and threshold 0 with `less` always fails."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        (threshold,) = _labelled(user, ui.number, "Threshold")
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        assert threshold.value is None
        assert weights.value == "1"
        wrap = _one(user, f"apply-wrap:{leaf}")
        assert not wrap.enabled
        assert _shown_tips(wrap) == ["enter a threshold"]
        threshold.set_value(0.25)
        await _settled(lambda: wrap.enabled)
        assert _shown_tips(wrap) == []
        # Another kind needs no threshold; back to weighted, still typed.
        kind.set_value("and")
        await _settled(lambda: "Threshold" not in _labels_shown(user))
        assert wrap.enabled
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        assert wrap.enabled
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: session.tree(A)["children"][0].get("combinator"))
        node = session.tree(A)["children"][0]
        assert node["combinator"] == "weighted"
        assert (node["weights"], node["threshold"]) == ([1.0], 0.25)
        assert errors == {}


async def test_edit_to_weighted_waits_for_a_threshold_too(session):
    """The same rule when Edit switches a node that has no threshold to
    weighted; its weights start as one 1 per child."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _f):
        user.find(marker=f"edit:{k_of_n}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        (threshold,) = _labelled(user, ui.number, "Threshold")
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        assert (threshold.value, weights.value) == (None, "1, 1")
        apply = _one(user, f"apply-edit:{k_of_n}")
        assert not apply.enabled
        assert _shown_tips(apply) == ["enter a threshold"]
        threshold.set_value(0.5)
        await _settled(lambda: apply.enabled)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(
            lambda: session.tree(D)["children"][1]["combinator"] == "weighted"
        )
        node = session.tree(D)["children"][1]
        assert (node["weights"], node["threshold"]) == ([1.0, 1.0], 0.5)
        assert "k" not in node and "direction" not in node  # `less` is the default
        assert errors == {}


def test_a_weighted_act_with_no_threshold_is_refused_by_name(session):
    """The act does not read a missing threshold as 0 — that always fails."""
    with pytest.raises(ValueError, match="enter a threshold"):
        EditTree().act(
            SessionView(session, _node(A, ROOT)),
            action="wrap",
            model_id=A,
            path="/metrics/children/0",
            kind="weighted",
            k=1,
            weights="1",
            threshold=None,
            direction="less",
        )


async def test_an_unchanged_edit_stages_nothing_and_closes_the_row(session):
    """Apply with nothing changed staged four ops (the kind, and every field
    again). Now it stages what moved, and here nothing did."""
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 2.0],
        "threshold": 0.5,
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    session.stage([PatchOp(D, "add", "/metrics/children/-", copy.deepcopy(weighted))])
    for node in ("/metrics/children/1", "/metrics/children/3"):
        async with host(session, EditTree(), _node(D, node)) as (user, errors, _f):
            staged = len(session.spec.pending)
            user.find(marker=f"edit:{node}").click()
            await user.should_see(marker=f"apply-edit:{node}")
            user.find(marker=f"apply-edit:{node}").click()
            await _settled(lambda: _none(user, "host-row:title"))
            await asyncio.sleep(0.1)
            assert len(session.spec.pending) == staged, session.spec.pending[staged:]
            assert errors == {}


def test_an_edit_stages_only_the_fields_that_moved(session):
    """One field changed is one op; ``direction`` is written only when it
    differs from the node's own, or from `less` when the node has none."""
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 2.0],
        "threshold": 0.5,
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    node = "/metrics/children/0"
    view = SessionView(session, _node(D, node))
    common = {"action": "edit", "model_id": D, "path": node, "kind": "weighted"}
    common |= {"k": 1, "weights": "1, 2", "threshold": 0.5}
    outcome = EditTree().act(view, **common, direction="greater")
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("add", f"{node}/direction", "greater")
    ]
    outcome = EditTree().act(view, **{**common, "threshold": 0.75}, direction="less")
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("replace", f"{node}/threshold", 0.75)
    ]


def test_unwrap_at_a_later_index_lands_its_children_there(session):
    """`and[l0, k-of-n[a, b], l2]` unwrap the k-of-n → `and[l0, a, b, l2]`:
    the adds count from the node's own index, not from 0."""
    l0 = {"metric": "nrmse", "variable": "x"}
    a = {"metric": "range", "variable": "x", "max_value": 9.0}
    b = {"metric": "range", "variable": "x", "max_value": 8.0}
    l2 = {"metric": "range", "variable": "x", "max_value": 5.0}
    tree = {
        "combinator": "and",
        "children": [l0, {"combinator": "k-of-n", "k": 1, "children": [a, b]}, l2],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    node = "/metrics/children/1"
    outcome = EditTree().act(
        SessionView(session, _node(D, node)), action="unwrap", model_id=D, path=node
    )
    assert [(op.op, op.path) for op in outcome.ops] == [
        ("remove", node),
        ("add", "/metrics/children/1"),
        ("add", "/metrics/children/2"),
    ]
    session.apply(outcome)
    assert session.tree(D) == {"combinator": "and", "children": [l0, a, b, l2]}


async def test_add_beside_a_nested_leaf_reveals_its_parents_next_child(session):
    """Beside a leaf two levels down: the new criterion is its OWN parent's
    next child, not the root's."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    leaf = "/metrics/children/1/children/0"
    reveals: list[str] = []
    async with host(session, EditTree(), _node(D, leaf), reveals) as (user, e, _f):
        user.find(marker=f"add:{leaf}").click()
        await user.should_see(marker=f"apply-add:{leaf}")
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(D)["children"][1]["children"]) == 3)
        assert reveals == ["/metrics/children/1/children/2"] and e == {}


async def test_the_add_rows_fields_read_variable_and_metric(session):
    """The board's table names the Add row's fields `Variable` and
    `Metric`; they read `leaf variable` / `leaf metric`."""
    async with host(session, EditTree(), _node(A, ROOT)) as (user, _errors, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert {"Variable", "Metric"} <= _labels_shown(user)
        assert not {"leaf variable", "leaf metric"} & _labels_shown(user)


# -- Accept (§4.6 table, §4.7) --------------------------------------------


def test_accept_is_greyed_without_a_result(session):
    """C has no result and is `pending` in the batch manifest, so the shared
    accept function refuses it — and the capability says so."""
    view = SessionView(session, Selection(tests=frozenset({C})))
    assert Accept().available(view) == "re-run before accepting"


async def test_accept_stores_the_rest(session):
    """A and B are stored, C is refused with its row's reason, and the
    reload flips A's and B's verdicts."""
    import json

    from dstf.reporting.status import PASS, classify

    selection = Selection(tests=frozenset({A, B, C}))
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see("Accept 2")  # names what it will store: A and B
        await user.should_see("re-run before accepting")  # C's row, from the manifest
        user.find(kind=ui.button, content="Accept 2 results as baselines").click()
        notice = session.notices[-1]
        assert notice.startswith("Accept: stored 2")
        assert C in notice and "re-run before accepting" in notice
        assert classify(session.comparisons[B]) == PASS  # NO_REF -> PASS
        assert classify(session.comparisons[A]) == PASS  # re-baselined
        rid = session.project.store.index.get_id(B)
        assert rid == "0004"  # A, D and G hold 0001-0003
        raw = (session.project.config.reference_dir / f"ref_{rid}.json").read_bytes()
        assert b"\r" not in raw and json.loads(raw)["model_id"] == B


def test_accept_act_returns_a_store_write_over_the_selection(session):
    """`act` is pure: the StoreWrite names the selection and its `run`
    reports one line — what it stored and what it refused."""
    from dstf.authoring.capabilities import StoreWrite

    view = SessionView(session, Selection(tests=frozenset({A, B, C})))
    out = Accept().act(view)
    assert isinstance(out, StoreWrite) and out.models == {A, B, C}
    line = out.run(session.project.store)
    assert line.startswith("Accept: stored 2") and C in line
    # The line is a report of an effect that happened: B has a baseline now.
    assert session.project.store.index.get_id(B) == "0004"


def test_accept_is_greyed_while_a_selected_test_is_locked(session):
    from dstf.authoring.capabilities import Job

    view = SessionView(session, Selection(tests=frozenset({A})))
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert Accept().available(view) == f"rerun is running on {A}"
    gate.release.set()
    assert job.done.wait(5)
    # The lock is the Session's, not the page's: it is gone with the job.
    assert session.running is None
    assert Accept().available(view) is True


async def test_accept_is_greyed_when_the_selection_is_a_pending_test(tmp_path):
    """A's result file parses, but the run never finished, so the shared
    accept function would refuse it and the capability says so instead of
    offering `Accept 0`."""
    import json

    from dstf.authoring.builtin.accept import REFUSED

    cfg, work = _project(tmp_path)
    manifest_path = work / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_0001"]["last_run_status"] = "pending"  # A, in flight
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == REFUSED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(REFUSED)  # the host's reason, in the panel's place
        await user.should_not_see("Accept 0")


async def test_accept_excludes_a_simulate_only_test_from_the_count(session):
    """A simulate-only test is neither stored nor skipped by the shared
    accept function — the forecast says so and the count leaves it out."""
    from dstf.authoring.builtin.accept import SIMULATE_ONLY
    from dstf.reporting.status import NO_REF

    session.project.test(B).simulate_only = True

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {SIMULATE_ONLY}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == SIMULATE_ONLY


async def test_accept_excludes_a_result_with_no_tracked_variables(session):
    """`store_reference` returns False for a variable-less result and
    `accept_results` skips it with this reason; the panel has the result in
    hand, so it never promises the baseline."""
    from dstf.authoring.builtin.accept import NO_VARIABLES
    from dstf.backends.types import TestResult
    from dstf.reporting.status import NO_REF

    session.project.results[B] = TestResult(model_id=B, success=True, variables=[])

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {NO_VARIABLES}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == NO_VARIABLES


async def test_accept_reports_the_row_reason_the_empty_selection_and_a_selection_naming_no_test(
    session,
):
    """With rows to draw, the greyed capability says exactly what the first
    row says; the generic line is left for a selection with no rows at all."""
    from dstf.authoring.builtin.accept import SIM_FAILED
    from dstf.backends.types import TestResult

    session.project.results[A] = TestResult(
        model_id=A, success=False, variables=[], error_message="boom"
    )

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == SIM_FAILED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(SIM_FAILED)  # the host's reason
        await user.should_not_see("Accept 0")
    empty = SessionView(session, Selection(tests=frozenset()))
    assert Accept().available(empty) == "Accept needs a selection"
    assert (
        Accept().available(SessionView(session, Selection(tests=frozenset({"Nope.X"}))))
        == "no successful result in the selection"
    )


# -- Rerun (§4.6 table, §4.7) ---------------------------------------------


def test_the_builtins_register_in_order():
    assert [c.label for c in caps.registered_capabilities()] == [
        "Edit criteria",
        "Edit tree",
        "Accept",
        "Rerun",
        "Edit on plot",
    ]


def test_rerun_act_returns_a_job_over_the_selection(session, monkeypatch):
    """`act` is pure: the Job names the selection, and its `run` builds a
    fresh quiet persistent-preferring backend and hands the selected tests,
    in project order, to the run callable."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.capabilities import Job, JobContext

    view = SessionView(session, Selection(tests=frozenset({A, D})))
    job = Rerun().act(view)
    assert isinstance(job, Job)
    assert job.label == "rerun" and job.models == {A, D} and job.reloads_results

    class _Backend:
        quiet = False

    built = _Backend()
    calls: dict = {}

    def _select_backend(config, *, persistent):
        calls["select"] = (config, persistent)
        return built

    def _run_pipeline(config, tests, *, backend, store, **kwargs):
        calls["pipeline"] = (config, list(tests), backend, store, kwargs)

    monkeypatch.setattr(rerun_mod, "select_backend", _select_backend)
    monkeypatch.setattr(rerun_mod, "run_pipeline", _run_pipeline)

    project = session.project
    job.run(JobContext(project=project, models=frozenset({A, D})))

    assert calls["select"] == (project.config, True)
    assert built.quiet is True  # the tool's process never narrates
    config, tests, backend, store, kwargs = calls["pipeline"]
    assert config is project.config
    assert backend is built and store is project.store
    assert [t.model_id for t in tests] == [A, D]  # project order
    assert kwargs == {}  # no merge, no candidates: the scope is the run


def test_rerun_job_run_propagates_an_unbuildable_backend(session, monkeypatch):
    """`run` has no `try`: a backend that cannot be built on this machine
    raises out of the job. That is what the Session's thread body catches
    into `job.error` (tests/test_session.py) rather than the tool reporting
    a rerun that did nothing."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.capabilities import JobContext
    from dstf.backends import BackendUnavailableError

    def _unavailable(config, *, persistent):
        raise BackendUnavailableError("no omc")

    monkeypatch.setattr(rerun_mod, "select_backend", _unavailable)

    view = SessionView(session, Selection(tests=frozenset({A})))
    job = Rerun().act(view)
    ctx = JobContext(project=session.project, models=frozenset({A}))
    with pytest.raises(BackendUnavailableError, match="no omc"):
        job.run(ctx)


async def test_rerun_names_the_backend_and_is_greyed_while_a_job_runs(session):
    from dstf.authoring.capabilities import Job

    selection = Selection(tests=frozenset({A}))
    view = SessionView(session, selection)
    async with host(session, Rerun(), selection) as (user, errors, _):
        await user.should_see("Rerun 1")
        # Via the MARKER: a header elsewhere reads `backend: Python`, so a
        # `should_see("Python")` would pass without the panel's label.
        (label,) = user.find(marker="rerun-backend").elements
        assert label.text == "runs through Python"
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert Rerun().available(view) == "rerun is running"
    gate.release.set()
    assert job.done.wait(5)
    assert Rerun().available(view) is True


def test_the_builtins_declare_their_placements():
    """Brief §3.2: Accept and Rerun at suite and test; Edit tree as a tree
    node's controls and a variable's "Add a criterion for x"; Edit on plot
    and Edit criteria at the open criterion — the two that read as ONE
    editor. Nothing built-in is left at TEST but Accept and Rerun."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    S, T, V, C_ = (
        Placement.SUITE,
        Placement.TEST,
        Placement.VARIABLE,
        Placement.CRITERION,
    )
    assert Accept.placements == {S, T} and Rerun.placements == {S, T}
    assert EditTree.placements == {Placement.NODE, V}
    assert EditOnPlot.placements == {C_}
    assert EditCriteria.placements == {C_}
    at_test = {c.name for c in caps.registered_capabilities() if T in c.placements}
    assert at_test == {"accept", "rerun"}


def test_accept_caption_counts_what_it_will_store_and_needs_a_selection(session):
    """DESIGN.md "Buttons": object, effect, count — and at zero the label
    still says what it would do while `available` carries the sentence."""
    three = SessionView(session, Selection(tests=frozenset({A, B, C})))
    assert Accept().caption(three) == "Accept 2 results as baselines"  # C is refused
    one = SessionView(session, Selection(tests=frozenset({D})))
    assert Accept().caption(one) == "Accept 1 result as baselines"
    none = SessionView(session, Selection())
    assert Accept().caption(none) == "Accept 0 results as baselines"
    assert Accept().available(none) == "Accept needs a selection"


def test_rerun_caption_names_count_backend_and_the_selections_unsaved_edits(session):
    one = SessionView(session, Selection(tests=frozenset({A})))
    assert Rerun().caption(one) == "Rerun 1 selected test (Python)"
    assert (
        Rerun().available(SessionView(session, Selection()))
        == "Rerun needs a selection"
    )
    session.stage([LOOSEN])  # two ops on A: the materialization and the edit
    assert (
        Rerun().caption(one) == "Rerun 1 selected test (Python) (with 2 unsaved edits)"
    )
    # Another model's edits are not this selection's.
    other = SessionView(session, Selection(tests=frozenset({D})))
    assert Rerun().caption(other) == "Rerun 1 selected test (Python)"
