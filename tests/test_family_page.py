"""A family's folded criteria row on the Test page (D152; the owner's R2),
and the add form's Indices (R1), in NiceGUI's user simulation: what is
drawn, what a click and an edit do to the page's one editor, and that the
rows are repainted — never rebuilt — when the fold moves."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.family_rows import fold_key  # noqa: E402
from dstf.authoring.shell.family_fold import HINT, FoldRow  # noqa: E402
from dstf.authoring.shell.plot_card import FOLD_ADDS, STAGED  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from family_project import element, family_project  # noqa: E402
from test_session import A  # noqa: E402
from test_test_page import (  # noqa: E402
    _click,
    _none,
    _numbers,
    _one,
    _settled,
    _test_page,
)

ROOT = "/metrics"


@pytest.fixture(autouse=True)
def _registry():
    capabilities._reset_for_tests()
    register_builtins()
    yield
    capabilities._reset_for_tests()
    register_builtins()


def _shell(cfg, work) -> Shell:
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


@pytest.fixture
def six(tmp_path):
    """A: `and[nrmse x, nrmse pipe.T[1..6]]`, implicit; pipe.T[3] fails.
    Plot 0 is x's, plot 1 the family's."""
    return _shell(*family_project(tmp_path, 6))


def leaf(i: int) -> str:
    """pipe.T[i]'s leaf in `six`: child i of the root (x is child 0)."""
    return f"{ROOT}/children/{i}"


FOLD = fold_key(leaf(1))


def _hidden(card) -> list[str]:
    return [p for p, row in card.rows.items() if row.row.props.get("hidden")]


def _members(card) -> list[str]:
    return [p for p, row in card.rows.items() if row.row.props.get("data-member")]


def _fold_rows(card) -> dict[str, FoldRow]:
    return {k: r for k, r in card.rows.items() if isinstance(r, FoldRow)}


def _row_marks(children) -> list[str]:
    out = []
    for child in children:
        out.extend(m for m in child._markers if m.startswith(("family:", "criterion:")))
    return out


def _dom_order(card) -> list[str]:
    """The rows' markers in the order the criteria column HOLDS them."""
    return _row_marks(card.folding.column.default_slot.children)


def _drawn_order(card) -> list[str]:
    """The rows' markers in the order the column SHOWS them: by each
    element's CSS `order`, the column's own order among equals."""
    children = card.folding.column.default_slot.children
    return _row_marks(sorted(children, key=lambda c: int(c.style.get("order", 0))))


def _tolerances(shell) -> list:
    return [c.get("tolerance") for c in shell.session.tree(A)["children"]]


def _enter(user, marker: str) -> None:
    from nicegui.testing.user_interaction import UserInteraction

    UserInteraction(user, {_one(user, marker)}, None).trigger("keydown.enter")


# -- the row ------------------------------------------------------------------------


async def test_a_familys_identical_criteria_are_one_folded_row(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        card = page.cards[1]
        assert list(_fold_rows(card)) == [FOLD]
        row = card.rows[FOLD]
        assert row.name.text == "nrmse · pipe.T[1:6]"
        # the count by verdict, and the WORST of them as the badge
        assert _one(user, f"family-count:1:{FOLD}").text == "5 pass · 1 fail"
        assert _one(user, f"family-pill:1:{FOLD}").text == "fail"
        # before its first member's row; the members stand, hidden
        assert _drawn_order(card)[:2] == [f"family:1:{FOLD}", f"criterion:1:{leaf(1)}"]
        assert _hidden(card) == [leaf(i) for i in range(1, 7)]
        assert _members(card) == [leaf(i) for i in range(1, 7)]
        assert row.row.props.get("data-folded") == "1"
        chevron = _one(user, f"family-chev:1:{FOLD}")
        assert chevron.props["aria-expanded"] == "false"
        assert chevron.props["aria-label"] == "Unfold nrmse · pipe.T[1:6]"
        # x's card has a criterion's row and no fold
        assert _fold_rows(page.cards[0]) == {}
        assert _hidden(page.cards[0]) == []


async def test_the_tree_card_keeps_one_line_per_leaf(six):
    """D7: the fold is the plot's alone."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, "tree:toggle"))
        await _settled(lambda: page.tree.rows.visible)
        for i in range(1, 7):
            assert _one(user, f"tree-text:{leaf(i)}").text == f"nrmse · {element(i)}"
        assert sorted(page.tree.leaves) == sorted(leaf(i) for i in range(0, 7))


async def test_the_chevron_unfolds_to_the_members_and_the_tab_remembers(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        card = page.cards[1]
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        assert _hidden(card) == []
        assert _members(card) == [leaf(i) for i in range(1, 7)]  # still indented
        assert "data-folded" not in card.rows[FOLD].row.props
        chevron = _one(user, f"family-chev:1:{FOLD}")
        assert chevron.props["aria-expanded"] == "true"
        assert chevron.props["aria-label"] == "Fold nrmse · pipe.T[1:6]"
        assert page.open is None  # the chevron's click is not the row's
        # the tab's: the members' paths, which a rebuilt page reads back
        assert page.state.family_unfolded[A] == [leaf(i) for i in range(1, 7)]
        with page.client:
            page._shape = None
            page.sync()
        assert _hidden(page.cards[1]) == []
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        assert _hidden(page.cards[1]) == [leaf(i) for i in range(1, 7)]
        assert A not in page.state.family_unfolded


async def test_folding_shut_closes_what_is_open_under_a_member(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        _click(user, _one(user, f"criterion:1:{leaf(2)}"))
        assert page.open == (1, leaf(2))
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        assert page.open is None
        assert _hidden(page.cards[1]) == [leaf(i) for i in range(1, 7)]


# -- its editor ---------------------------------------------------------------------


async def test_the_folded_rows_click_opens_a_fields_only_editor(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        assert page.open == (1, FOLD)
        # D3: nothing of a fold is dragged — no leaf's rings are drawn ...
        assert page.editing_for(1) == ()
        # ... no Edit on plot panel is offered, and the hint says so
        editor = _one(user, f"editor:1:{FOLD}")
        panels = [m for m in _marks_under(editor) if m.startswith("panel:")]
        assert panels == ["panel:edit-family", "panel:edit-criteria"]
        assert _one(user, f"editor-hint:1:{FOLD}").text == HINT.format(n=6) + STAGED
        assert "all 6 criteria" in HINT.format(n=6)
        assert _one(user, "edit-family:indices").value == "1:6"
        # the fold stays shut: its members are not what is being edited
        assert _hidden(page.cards[1]) == [leaf(i) for i in range(1, 7)]
        _click(user, _one(user, f"family:1:{FOLD}"))  # a second click closes it
        assert page.open is None
        assert _none(user, f"editor:1:{FOLD}")


async def test_a_folded_editors_hint_is_its_own_through_every_repaint(six):
    """The card says its open criterion's hint again whenever its panels
    follow (D151: a run that starts takes the handles). A fold's editor is
    the card's too and is no criterion: said again, its line read "Edit
    the fields." — that its fields change all six, and to unfold to drag
    one, gone at the first sync."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        page.sync()
        assert _one(user, f"editor-hint:1:{FOLD}").text == HINT.format(n=6) + STAGED
        # ... and no ⌖: no click on the plot sets a field of twenty criteria.
        assert page.cards[1].placing._buttons == {}


#: `six`'s family scored by a tube of control points: a metric a
#: Ctrl+click adds a row to (D149), the same on every element — one fold.
_TUBE = {
    "metric": "tube",
    "tube_width_mode": "band",
    "tube_points": [
        {"time": 0.25, "upper": 0.5, "lower": 0.5},
        {"time": 0.75, "upper": 0.5, "lower": 0.5},
    ],
}
TUBES = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        *({**_TUBE, "variable": element(i)} for i in range(1, 5)),
    ],
}


def _ctrl_click(card, x: float, y: float) -> dict:
    """What the page sends for a Ctrl+click on ``card``'s time plot."""
    return {
        "plot": card.plot.id,
        "xaxis": "x",
        "yaxis": "y",
        "x": x,
        "y": y,
        "xrange": [0.0, 1.0],
        "yrange": [0.0, 4.0],
        "ctrl": True,
    }


@pytest.mark.parametrize("modifier", ["ctrl", "meta"])
async def test_a_ctrl_click_with_a_fold_open_adds_nothing_and_says_why(
    tmp_path, modifier
):
    """On-plot editing over a fold (D149 × D152): a click adds a row to ONE
    criterion's table, and a fold's editor is fields only — its Ctrl+click
    (Cmd+click, alone, on a Mac) went nowhere, with nothing said. The
    plot's own line says where the gesture is; with a member open it adds
    to that member, which is then a row of its own."""
    shell = _shell(*family_project(tmp_path, 4, fail=(), tree=TUBES))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        _click(user, _one(user, f"family:1:{FOLD}"))
        assert page.open == (1, FOLD) and card.placing._buttons == {}
        # A plain click is no gesture of a fold's: nothing is said of it.
        plain = {**_ctrl_click(card, 0.6, 1.0), "ctrl": False}
        with page.client:
            page.on_plot("clicked", plain)
        assert not card.refusal.visible and shell.session.spec.pending == []
        with page.client:
            page.on_plot("clicked", {**plain, modifier: True})
        assert shell.session.spec.pending == []
        assert card.refusal.visible and card.refusal.text == FOLD_ADDS.format(
            "a control point"
        )
        # The line is about the FOLDED row: it goes when the row unfolds,
        # the fold's editor still open; a member's then takes the click.
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        assert page.open == (1, FOLD) and not card.refusal.visible
        _click(user, _one(user, f"criterion:1:{leaf(2)}"))
        assert page.open == (1, leaf(2)) and not card.refusal.visible
        assert ("edit-on-plot", ("tube_points", 0)) in card.placing._buttons
        with page.client:
            page.on_plot("clicked", _ctrl_click(card, 0.5, 1.0))
        (op,) = shell.session.spec.pending
        assert op.path == leaf(2) and len(op.value["tube_points"]) == 3
        await _settled(
            lambda: _one(user, f"family-count:1:{fold_key(leaf(1))}").text == "3 pass"
        )
        assert not card.rows[leaf(2)].row.props.get("data-member")


async def test_a_ctrl_click_on_a_fold_of_a_metric_without_rows_says_nothing(six):
    """nrmse has no rows: a Ctrl+click adds none to a member either (D149),
    so there is nothing to unfold for."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        card = page.cards[1]
        _click(user, _one(user, f"family:1:{FOLD}"))
        with page.client:
            page.on_plot("clicked", _ctrl_click(card, 0.5, 1.0))
        assert not card.refusal.visible and six.session.spec.pending == []


def _marks_under(element) -> list[str]:
    from test_test_page import _marks

    return _marks(element)


async def test_a_folded_field_changes_every_member_and_the_count_follows(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        assert _tolerances(six) == [None, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        # D4: one gesture, one op — after the flat test's own tree, written
        # out first as for any criterion's edit
        assert [
            (op.op, op.path, op.materializes) for op in six.session.spec.pending
        ] == [("add", ROOT, True), ("add", ROOT, False)]
        await _settled(lambda: _one(user, f"family-count:1:{FOLD}").text == "6 pass")
        assert _one(user, f"family-pill:1:{FOLD}").text == "pass"
        assert _one(user, "pending").text == "2 pending edits"
        # the editor being typed in is not rebuilt, nor closed
        assert _numbers(page, "Tolerance") == [field]
        assert page.open == (1, FOLD)
        # typed over: still one edit
        with page.client:
            field.set_value(0.25)
        assert len(six.session.spec.pending) == 2
        assert _tolerances(six)[1:] == [0.25] * 6


async def test_a_refused_folded_edit_stages_nothing_and_says_why_once(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(-1)
        assert six.session.spec.pending == []
        errors = [
            e for e in user.find(marker="error:edit-criteria").elements if e.visible
        ]
        assert len(errors) == 1 and "tolerance" in errors[0].text


async def test_a_member_edited_leaves_the_fold_and_stands_as_its_own_row(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        card = page.cards[1]
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        _click(user, _one(user, f"criterion:1:{leaf(3)}"))
        assert page.open == (1, leaf(3))
        (field,) = _numbers(page, "Tolerance")
        before = _dom_order(card)
        ids = {p: row.row.id for p, row in card.rows.items()}
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: card.rows[FOLD].name.text == "nrmse · pipe.T[1,2,4:6]")
        # its own row now: not indented, never hidden; the rest still fold
        assert _members(card) == [leaf(i) for i in (1, 2, 4, 5, 6)]
        assert _hidden(card) == []
        assert _one(user, f"family-count:1:{FOLD}").text == "5 pass"
        # REPAINTED: every row is the element it was, where the column
        # holds it, and the field being typed in is still the field ...
        assert _dom_order(card) == before
        assert {p: row.row.id for p, row in card.rows.items()} == ids
        # ... and it is SHOWN after the fold's block, a sibling of the fold
        assert _drawn_order(card) == [
            f"family:1:{FOLD}",
            *(f"criterion:1:{leaf(i)}" for i in (1, 2, 4, 5, 6)),
            f"criterion:1:{leaf(3)}",
        ]
        assert _numbers(page, "Tolerance") == [field]
        assert page.open == (1, leaf(3))
        # ... and typed back, it folds again
        with page.client:
            field.set_value(None)
        await _settled(lambda: card.rows[FOLD].name.text == "nrmse · pipe.T[1:6]")
        assert _members(card) == [leaf(i) for i in range(1, 7)]
        assert _numbers(page, "Tolerance") == [field]


async def test_the_first_member_leaving_moves_the_folds_row_and_keeps_it_open(six):
    """The fold is keyed by its first member; the tab's record by every
    member — so the fold that was opened is still open under its new key."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        card = page.cards[1]
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        _click(user, _one(user, f"criterion:1:{leaf(1)}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        new = fold_key(leaf(2))
        await _settled(lambda: list(_fold_rows(card)) == [new])
        assert card.rows[new].name.text == "nrmse · pipe.T[2:6]"
        assert _drawn_order(card)[:3] == [
            f"criterion:1:{leaf(1)}",
            f"family:1:{new}",
            f"criterion:1:{leaf(2)}",
        ]
        assert _hidden(card) == []  # still unfolded
        assert _numbers(page, "Tolerance") == [field]


async def test_a_fold_of_two_with_one_edited_is_two_rows(tmp_path):
    shell = _shell(*family_project(tmp_path, 2, fail=()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        assert list(_fold_rows(card)) == [FOLD]
        shell.session.stage(
            [
                PatchOp(
                    A,
                    "replace",
                    leaf(2),
                    {"metric": "nrmse", "variable": element(2), "tolerance": 0.5},
                )
            ]
        )
        await _settled(lambda: _fold_rows(card) == {})
        assert _hidden(card) == [] and _members(card) == []
        assert _none(user, f"family:1:{FOLD}")
        assert _drawn_order(card) == [
            f"criterion:1:{leaf(1)}",
            f"criterion:1:{leaf(2)}",
        ]


async def test_a_value_on_its_way_through_another_groups_does_not_merge_them(tmp_path):
    """Two folds of one family, tolerances 0.1 and 0.5; the first's editor
    types 0.5 on its way to 0.55. Merged at 0.5, the next key would carry
    the second group to 0.55 with it."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *(
                {
                    "metric": "nrmse",
                    "variable": element(i),
                    "tolerance": 0.1 if i < 3 else 0.5,
                }
                for i in range(1, 5)
            ),
        ],
    }
    shell = _shell(*family_project(tmp_path, 4, fail=(), tree=tree))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        second = fold_key(leaf(3))
        assert list(_fold_rows(card)) == [FOLD, second]
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: _tolerances(shell)[1:] == [0.5] * 4)
        with page.client:
            page.sync()
        assert list(_fold_rows(card)) == [FOLD, second]
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1:2]"
        with page.client:
            field.set_value(0.55)
        assert _tolerances(shell)[1:] == [0.55, 0.55, 0.5, 0.5]
        # ... and closed at one value they ARE one criterion, and one fold:
        # Done releases the hold and repaints the rows from the tree — no
        # other event comes to do it.
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: _tolerances(shell)[1:] == [0.5] * 4)
        await asyncio.sleep(0.5)  # every event of the edit heard
        _click(user, _one(user, f"editor-done:1:{FOLD}"))
        assert page.open is None
        assert list(_fold_rows(card)) == [FOLD]
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1:4]"


def _t3_apart() -> dict:
    """`and[x, nrmse pipe.T[1..6]]` with pipe.T[3] at its own tolerance."""
    return {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *(
                {
                    "metric": "nrmse",
                    "variable": element(i),
                    **({"tolerance": 0.5} if i == 3 else {}),
                }
                for i in range(1, 7)
            ),
        ],
    }


async def test_done_releases_the_hold_and_the_rows_are_the_trees_again(tmp_path):
    """The executing review's H1: the fold pipe.T[1,2,4:6] typed to T3's own
    value and closed with Done still read pipe.T[1,2,4:6] beside T3's row —
    nothing repainted once the hold was gone — and its Indices, grown over
    the "gap", staged a second identical criterion on pipe.T[3]."""
    shell = _shell(*family_project(tmp_path, 6, tree=_t3_apart()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1,2,4:6]"
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: _tolerances(shell)[1:] == [0.5] * 6)
        await asyncio.sleep(0.5)  # every event of the edit heard
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1,2,4:6]"  # held
        _click(user, _one(user, f"editor-done:1:{FOLD}"))
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1:6]"
        assert _members(card) == [leaf(i) for i in range(1, 7)]
        # reopened, it is the fold of all six, and its Indices as they are
        # stage nothing
        _click(user, _one(user, f"family:1:{FOLD}"))
        assert _one(user, f"editor-hint:1:{FOLD}").text == HINT.format(n=6) + STAGED
        assert _one(user, "edit-family:indices").value == "1:6"
        pending = len(shell.session.spec.pending)
        _enter(user, "edit-family:indices")
        assert len(shell.session.spec.pending) == pending
        names = [c["variable"] for c in shell.session.tree(A)["children"]]
        assert names == ["x", *(element(i) for i in range(1, 7))]


async def test_switching_to_another_criterion_releases_the_hold_too(tmp_path):
    shell = _shell(*family_project(tmp_path, 6, tree=_t3_apart()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: _tolerances(shell)[1:] == [0.5] * 6)
        await asyncio.sleep(0.5)
        _click(user, _one(user, f"criterion:0:{leaf(0)}"))  # x's row, on plot 0
        assert page.open == (0, leaf(0))
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1:6]"


async def test_an_edit_from_elsewhere_rebuilds_the_fields_or_closes_the_editor(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        # A Discard puts every member back: the fields must not go on
        # showing 0.5, or the next key stages it straight back.
        with page.client:
            six.session.discard()
        await _settled(lambda: _numbers(page, "Tolerance") != [field])
        (rebuilt,) = _numbers(page, "Tolerance")
        assert rebuilt.value is None
        assert page.open == (1, FOLD)
        # Another tab takes one member apart: they share no criterion, and
        # an editor of "all" has nothing to show.
        six.session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    ROOT,
                    {
                        **six.session.tree(A),
                        "children": [
                            *six.session.tree(A)["children"][:6],
                            {
                                "metric": "nrmse",
                                "variable": element(6),
                                "tolerance": 0.9,
                            },
                        ],
                    },
                )
            ]
        )
        await _settled(lambda: page.open is None)
        assert page.cards[1].rows[FOLD].name.text == "nrmse · pipe.T[1:5]"


# -- its Indices --------------------------------------------------------------------


@pytest.fixture
def some(tmp_path):
    """A: `and[nrmse x, nrmse pipe.T[1..4]]` of a 10-element family."""
    return _shell(*family_project(tmp_path, 10, tracked=(1, 2, 3, 4), fail=()))


async def test_indices_in_the_folded_editor_add_and_remove_and_reopen_it(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        field = _one(user, "edit-family:indices")
        assert field.value == "1:4"
        assert "pipe.T[1:10]" in field.props["hint"]
        with page.client:
            field.set_value("2:4,8")
        _enter(user, "edit-family:indices")  # Enter applies
        names = [c["variable"] for c in some.session.tree(A)["children"]]
        assert names == ["x", element(2), element(3), element(4), element(8)]
        assert len(some.session.spec.pending) == 2
        # pipe.T[2] is child 1 now: the same key, on rows drawn anew
        new = fold_key(leaf(1))
        await _settled(
            lambda: (
                page.open == (1, new)
                and page.cards[1].rows[new].name.text == "nrmse · pipe.T[2:4,8]"
            )
        )
        assert _one(user, "edit-family:indices").value == "2:4,8"
        assert _one(user, f"editor-hint:1:{new}").text == HINT.format(n=4) + STAGED
        # the Apply button does what Enter does
        with page.client:
            _one(user, "edit-family:indices").set_value("2:3")
        _click(user, _one(user, "edit-family:apply"))
        await _settled(
            lambda: (
                page.open == (1, new)
                and page.cards[1].rows[new].name.text == "nrmse · pipe.T[2:3]"
            )
        )


async def test_indices_down_to_one_element_opens_that_criterions_own_editor(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        with page.client:
            _one(user, "edit-family:indices").set_value("3")
        assert _one(user, "edit-family:count").text == "→ 1 element (removes 3)"
        _enter(user, "edit-family:indices")
        # three of four would go: asked once, inline, and nothing is staged
        assert _one(user, "edit-family:count").text == (
            "Removes 3 of 4 criteria — Apply again to confirm"
        )
        assert some.session.spec.pending == []
        _enter(user, "edit-family:indices")
        await _settled(lambda: page.open == (1, leaf(1)))
        assert _fold_rows(page.cards[1]) == {}
        assert [c["variable"] for c in some.session.tree(A)["children"]] == [
            "x",
            element(3),
        ]


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("1:11", "pipe.T[1:10] has 10 elements; 11 is not one of them."),
        ("", "Indices is empty. To remove the criterion"),
        ("4:1", "4:1 ends before it starts."),
    ],
)
async def test_a_refused_indices_edit_says_why_and_stages_nothing(some, text, says):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        with page.client:
            _one(user, "edit-family:indices").set_value(text)
        _enter(user, "edit-family:indices")
        (error,) = [
            e for e in user.find(marker="error:edit-family").elements if e.visible
        ]
        assert says in error.text
        assert some.session.spec.pending == []
        assert page.open == (1, FOLD)
        assert _one(user, "edit-family:indices").value == text  # what was typed stays


async def test_indices_as_they_are_stage_nothing(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        with page.client:
            _one(user, "edit-family:indices").set_value("4,1:3")
        _enter(user, "edit-family:indices")
        assert some.session.spec.pending == []
        assert _one(user, "edit-family:indices").value == "1:4"  # as the fold reads
        assert page.open == (1, FOLD)


# -- the add form's Indices (R1) ---------------------------------------------------


async def _open_add(user, page, marker: str) -> None:
    _click(user, _one(user, marker))
    await user.should_see("Variable")


async def test_the_add_form_offers_indices_for_an_element_and_not_for_a_scalar(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        # the family plot's `+`: its first member is the variable picked
        await _open_add(user, page, f"add-for:{element(1)}")
        mark = f"apply-add:{element(1)}"
        indices = _one(user, f"{mark}:indices")
        assert indices.visible and indices.value == "1:10"
        assert "pipe.T[1:10]" in indices.props["hint"]
        picker = _one(user, f"{mark}:variable")
        with page.client:
            picker.set_value("x")
        assert not indices.visible
        with page.client:
            picker.set_value(element(7))
        assert indices.visible and indices.value == "1:10"


async def test_add_over_a_slice_draws_one_folded_row_and_opens_its_editor(tmp_path):
    shell = _shell(*family_project(tmp_path, 20, tracked=(), fail=(6,)))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 1  # x's
        await _open_add(user, page, "add-for:x")
        mark = "apply-add:x"
        picker = _one(user, f"{mark}:variable")
        with page.client:
            picker.set_value(element(1))
        indices = _one(user, f"{mark}:indices")
        assert indices.value == "1:20"
        with page.client:
            indices.set_value("1:5:20")
        _enter(user, f"{mark}:indices")  # Enter in Indices is Add
        names = [c["variable"] for c in shell.session.tree(A)["children"]]
        assert names == ["x", element(1), element(6), element(11), element(16)]
        key = fold_key(leaf(1))
        await _settled(lambda: page.open == (1, key))
        card = page.cards[1]
        assert card.rows[key].name.text == "nrmse · pipe.T[1:5:16]"
        assert _one(user, f"family-count:1:{key}").text == "3 pass · 1 fail"
        assert _one(user, "pending").text == "2 pending edits"
        assert _hidden(card) == [leaf(i) for i in range(1, 5)]


async def test_a_refused_indices_text_keeps_the_add_form_open(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        await _open_add(user, page, f"add-for:{element(1)}")
        mark = f"apply-add:{element(1)}"
        with page.client:
            _one(user, f"{mark}:indices").set_value("9:12")
        _click(user, _one(user, mark))
        (error,) = [
            e for e in user.find(marker="error:edit-tree").elements if e.visible
        ]
        assert "pipe.T[1:10] has 10 elements; 11:12 are not among them." in error.text
        assert some.session.spec.pending == []
        assert _one(user, f"{mark}:indices").value == "9:12"


# -- being asked for by name ---------------------------------------------------------


async def test_a_jump_to_a_folded_criterion_unfolds_to_its_own_row(six):
    """The tree line's jump names ONE criterion (pipe.T[3], the failure):
    its own row is shown and its own editor opens — handles and all."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        with page.client:
            page.jump_to(leaf(3))
        assert page.open == (1, leaf(3))
        assert _hidden(page.cards[1]) == []
        assert page.editing_for(1) == (leaf(3),)
        assert not _none(user, f"editor:1:{leaf(3)}")


async def test_the_trees_show_unfolds_to_the_criterions_row(six):
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        with page.client:
            page.show_criterion(leaf(4))
        assert _hidden(page.cards[1]) == []
        assert page.open is None  # ⤓ opens no editor


async def test_an_edit_that_moves_the_members_moves_what_the_tab_unfolded(six):
    """`TestPage.edited`: the record is by member path, and follows them."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        with page.client:
            page.edited({leaf(i): leaf(i + 1) for i in range(1, 7)})
        assert page.state.family_unfolded[A] == [leaf(i) for i in range(2, 8)]
        with page.client:
            page.edited({})
        assert A not in page.state.family_unfolded


def test_an_edit_that_is_not_the_tabs_folds_the_rows_again():
    """As the tree card's folds go back to their default (`heard_spec`,
    `caught_up`) — here the default is folded."""
    from dstf.authoring.shell.tabstate import TabState, caught_up, heard_spec

    state = TabState(family_unfolded={A: [leaf(1)], "FakeLib.D": [leaf(1)]})
    state.own_revisions = {3}
    heard_spec(state, {"event": "spec", "revision": 3, "models": [A]})
    assert A in state.family_unfolded  # this tab's own edit
    heard_spec(state, {"event": "spec", "revision": 4, "models": [A]})
    assert state.family_unfolded == {"FakeLib.D": [leaf(1)]}
    heard_spec(state, {"event": "spec", "revision": 5, "models": []})
    assert state.family_unfolded == {}
    state.family_unfolded = {A: [leaf(1)]}
    caught_up(state, 9)
    assert state.family_unfolded == {}


def test_a_folded_rows_placement_is_edit_criterias_and_edit_familys_alone():
    """No capability that edits ONE leaf — Edit on plot — is offered where
    twenty are selected."""
    from dstf.authoring.capabilities import Placement, registered_capabilities

    placed = [
        c.name
        for c in registered_capabilities()
        if Placement.FAMILY_CRITERION in c.placements
    ]
    assert placed == ["edit-family", "edit-criteria"]


async def test_an_add_from_a_tree_row_reveals_the_fold_on_its_plot(tmp_path):
    """A tree row's `+` has no plot of its own: the folded row it made is
    opened on the first plot that holds it."""
    from test_test_page import _open_form

    shell = _shell(*family_project(tmp_path, 20, tracked=(), fail=()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click(user, _one(user, "tree:toggle"))
        await _settled(lambda: page.tree.rows.visible)
        await _open_form(user, f"add:{ROOT}", f"tree-row:{ROOT}")
        mark = f"apply-add:{ROOT}"
        with page.client:
            _one(user, f"{mark}:variable").set_value(element(4))
            _one(user, f"{mark}:indices").set_value("4:6")
        _click(user, _one(user, mark))
        key = fold_key(leaf(1))
        await _settled(lambda: page.open == (1, key))
        assert page.cards[1].rows[key].name.text == "nrmse · pipe.T[4:6]"


async def test_unfolded_members_are_one_block_and_a_differing_row_follows_it(tmp_path):
    """A fold's members stand together under it; an element with its own
    criterion comes AFTER the block, a sibling of the fold's row — between
    two members, with a 14 px outdent, it read as one of them. Two folds
    whose members alternate in the tree are two blocks."""
    shell = _shell(*family_project(tmp_path, 6, tree=_t3_apart()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        expected = [
            f"family:1:{FOLD}",
            *(f"criterion:1:{leaf(i)}" for i in (1, 2, 4, 5, 6)),
            f"criterion:1:{leaf(3)}",
        ]
        assert _drawn_order(card) == expected  # folded shut
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        assert _drawn_order(card) == expected  # and unfolded
        assert _members(card) == [leaf(i) for i in (1, 2, 4, 5, 6)]
        # every row's slot — its editor's place — follows its row
        children = sorted(
            card.folding.column.default_slot.children,
            key=lambda c: int(c.style.get("order", 0)),
        )
        for path, row in card.rows.items():
            assert children[children.index(row.row) + 1] is row.slot, path
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *(
                {"metric": "nrmse", "variable": element(i), "tolerance": 0.1 + i % 2}
                for i in range(1, 7)
            ),
        ],
    }
    shell = _shell(*family_project(tmp_path / "two", 6, fail=(), tree=tree))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        odd, even = fold_key(leaf(1)), fold_key(leaf(2))
        assert _drawn_order(page.cards[1]) == [
            f"family:1:{odd}",
            *(f"criterion:1:{leaf(i)}" for i in (1, 3, 5)),
            f"family:1:{even}",
            *(f"criterion:1:{leaf(i)}" for i in (2, 4, 6)),
        ]


async def test_a_criterion_typed_to_a_shut_folds_value_unfolds_it(tmp_path):
    """pipe.T[3] has its own row beside the SHUT fold of the rest; typed to
    their value it joins them — and the fold opens, or its editor would go
    on staging edits from a row nobody can see."""
    shell = _shell(*family_project(tmp_path, 6, tree=_t3_apart()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        assert _hidden(card) == [leaf(i) for i in (1, 2, 4, 5, 6)]
        _click(user, _one(user, f"criterion:1:{leaf(3)}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(None)
        await _settled(lambda: card.rows[FOLD].name.text == "nrmse · pipe.T[1:6]")
        assert _hidden(card) == []
        assert page.open == (1, leaf(3))
        assert _numbers(page, "Tolerance") == [field]
        # ... and it stays open once the editor is closed: the tab's record
        _click(user, _one(user, f"editor-done:1:{leaf(3)}"))
        assert _hidden(card) == []


async def test_a_form_under_a_row_that_joins_a_shut_fold_keeps_it_open(tmp_path):
    from test_test_page import _open_form

    shell = _shell(*family_project(tmp_path, 6, tree=_t3_apart()))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        await _open_form(user, f"wrap:{leaf(3)}", f"criterion:1:{leaf(3)}")
        assert page.form is not None
        # another tab puts pipe.T[3] back with the rest
        shell.session.stage(
            [
                PatchOp(
                    A, "replace", leaf(3), {"metric": "nrmse", "variable": element(3)}
                )
            ]
        )
        await _settled(lambda: card.rows[FOLD].name.text == "nrmse · pipe.T[1:6]")
        assert page.form is not None
        assert _hidden(card) == []


async def test_unit_tests_elements_are_never_one_folded_row(tmp_path):
    """D140 on the page: each UnitTests element is a plot of its own, so no
    card holds two of them and nothing folds — whatever their names' shape
    — and the add form offers no Indices for one."""
    from test_session import UT1, UT2, _unit_tests_project

    shell = _shell(*_unit_tests_project(tmp_path))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [tuple(card.subject) for card in page.cards] == [(UT1,), (UT2,)]
        for card in page.cards:
            assert _fold_rows(card) == {}
            assert _hidden(card) == [] and _members(card) == []
        await _open_add(user, page, f"add-for:{UT1}")
        assert not _none(user, f"apply-add:{UT1}:variable")
        assert _none(user, f"apply-add:{UT1}:indices")  # there, and not shown


# -- the fix batch: the add form ------------------------------------------------------


async def test_a_rows_plus_starts_at_its_own_element_and_the_plots_at_all(tmp_path):
    """Decided 1: the `+` on the row of ONE element is asked for a second
    criterion on it — Indices starts at that element's index. The plot's
    `+` and a group's start at every element."""
    from test_test_page import _click_in, _open_form

    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *({"metric": "nrmse", "variable": element(i)} for i in (1, 2, 3)),
        ],
    }
    shell = _shell(*family_project(tmp_path, 10, tracked=(1, 2, 3), fail=(), tree=tree))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click(user, _one(user, f"family-chev:1:{FOLD}"))
        # a member's own row, under the family's plot
        await _open_form(user, f"add:{leaf(3)}", f"criterion:1:{leaf(3)}")
        mark = f"apply-add:{leaf(3)}"
        assert _one(user, f"{mark}:indices").value == "3"
        assert _one(user, f"{mark}:count").text == "1 element"
        assert _one(user, mark).text == "Add 1 criterion"
        # ... another element picked in that form: every element again
        with page.client:
            _one(user, f"{mark}:variable").set_value(element(7))
        assert _one(user, f"{mark}:indices").value == "1:10"
        _click(user, _one(user, f"cancel:{leaf(3)}"))
        # the same row in the tree
        _click(user, _one(user, "tree:toggle"))
        await _settled(lambda: page.tree.rows.visible)
        await _open_form(user, f"add:{leaf(2)}", f"tree-row:{leaf(2)}")
        assert _one(user, f"apply-add:{leaf(2)}:indices").value == "2"
        _click_in(user, f"cancel:{leaf(2)}", "tree")
        # the plot's `+`, and a group's: all
        await _open_add(user, page, f"add-for:{element(1)}")
        assert _one(user, f"apply-add:{element(1)}:indices").value == "1:10"


async def test_the_add_form_counts_what_indices_name_as_they_are_typed(some):
    """Decided 2 and 3: beside Indices a count, on the button the same,
    and in the row's title what is being added to — `1:10:5` is
    start:step:end, ONE element, and reads as five."""
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        await _open_add(user, page, f"add-for:{element(1)}")
        mark = f"apply-add:{element(1)}"
        indices, count, add = (
            _one(user, f"{mark}:indices"),
            _one(user, f"{mark}:count"),
            _one(user, mark),
        )
        title = _one(user, "form-title:plot:1")
        assert (indices.value, count.text, add.text) == (
            "1:10",
            "10 elements",
            "Add 10 criteria",
        )
        assert title.text == "Add a criterion for pipe.T[1:10]"
        with page.client:
            indices.set_value("1:10:5")
        assert (count.text, add.text) == ("1 element", "Add 1 criterion")
        assert title.text == "Add a criterion for pipe.T[1]"
        with page.client:
            indices.set_value("1:5:10")
        assert (count.text, add.text) == ("2 elements", "Add 2 criteria")
        assert title.text == "Add a criterion for pipe.T[1:5:6]"
        # a text that names nothing yet says why, and the title waits
        with page.client:
            indices.set_value("1:")
        assert "is not an index or a range" in count.text
        assert add.text == "Add"
        assert title.text == "Add a criterion for pipe.T[1:5:6]"
        with page.client:
            indices.set_value("12")
        assert count.text == "pipe.T[1:10] has 10 elements; 12 is not one of them."
        # a variable that is no element: no count, the plain button, its name
        with page.client:
            _one(user, f"{mark}:variable").set_value("x")
        assert _none(user, f"{mark}:count")
        assert add.text == "Add"
        assert title.text == "Add a criterion for x"


async def test_the_folded_editor_counts_what_enter_would_do(some):
    """Decided 2 and 7: the count beside the fold's Indices, and Esc."""
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        field, count = (
            _one(user, "edit-family:indices"),
            _one(user, "edit-family:count"),
        )
        assert count.text == "→ 4 elements (no change)"
        for text, says in (
            ("1:6", "→ 6 elements (adds 2)"),
            ("2:4", "→ 3 elements (removes 1)"),
            ("3:6", "→ 4 elements (removes 2, adds 2)"),
            ("1:4:1", "→ 1 element (removes 3)"),
            ("1:", "'1:' is not an index or a range: write 3, 1:5 or 1:5:20."),
        ):
            with page.client:
                field.set_value(text)
            assert count.text == says, text
        # Esc puts the applied text back, and nothing was staged by typing
        from nicegui.testing.user_interaction import UserInteraction

        UserInteraction(user, {field}, None).trigger("keydown.escape")
        assert field.value == "1:4"
        assert count.text == "→ 4 elements (no change)"
        assert some.session.spec.pending == []


async def test_a_large_removal_is_asked_once_and_a_changed_text_asks_again(some):
    async with user_simulation(root=some.index) as user:
        page = await _test_page(some, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        field, count = (
            _one(user, "edit-family:indices"),
            _one(user, "edit-family:count"),
        )
        with page.client:
            field.set_value("1:4:1")  # meant 1:1:4, every element
        _enter(user, "edit-family:indices")
        assert count.text == "Removes 3 of 4 criteria — Apply again to confirm"
        assert some.session.spec.pending == []
        # typed on: the question is gone, and another large removal asks anew
        with page.client:
            field.set_value("2")
        assert count.text == "→ 1 element (removes 3)"
        _click(user, _one(user, "edit-family:apply"))
        assert count.text == "Removes 3 of 4 criteria — Apply again to confirm"
        assert some.session.spec.pending == []
        # half is not "more than half": no question
        with page.client:
            field.set_value("1:2")
        _enter(user, "edit-family:indices")
        assert [c["variable"] for c in some.session.tree(A)["children"]] == [
            "x",
            element(1),
            element(2),
        ]


# -- the fix batch: the preview --------------------------------------------------------


async def test_the_preview_says_what_a_fold_gesture_did_and_that_save_writes_the_tree(
    six,
):
    """M5: on a flat test the note a single criterion's edit gets, and one
    line per gesture above the diff."""
    async with user_simulation(root=six.index) as user:
        page = await _test_page(six, user)
        _click(user, _one(user, f"family:1:{FOLD}"))
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.05)
            _one(user, "edit-family:indices").set_value("1:5")
        _enter(user, "edit-family:indices")
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        _click(user, _one(user, "pending"))
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        assert _one(user, "drawer-note").text == (
            "Save also writes this test's 7 criteria out as a tree; they score "
            "the same."
        )
        # (`elements` is a set: in the order they were drawn, by id)
        said = sorted(user.find(marker="drawer-gesture").elements, key=lambda e: e.id)
        assert [e.text for e in said] == [
            "tolerance → 0.05 on 6 criteria of pipe.T[1:6]",
            "Indices: removed pipe.T[6]",
        ]
        removed = [
            "".join(c.text for c in e.default_slot.children)
            for e in user.find(marker="diff-del").elements
        ]
        assert any("pipe.T[6]" in line for line in removed)


# -- the fix batch: what an Indices change tells the page ------------------------------


async def test_an_indices_change_moves_what_the_page_keys_by_path(tmp_path):
    """M44: the nodes after the fold shift when its leaves come and go, and
    the page is told where each went (`Panel.edited`): a group folded in
    the tree card stays folded where it now is, and the record of unfolded
    family rows does not land on another fold."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *({"metric": "nrmse", "variable": element(i)} for i in (1, 2)),
            *(
                {"metric": "nrmse", "variable": element(i), "tolerance": 0.5}
                for i in (3, 4)
            ),
            {"combinator": "or", "children": [{"metric": "nrmse", "variable": "x"}]},
        ],
    }
    shell = _shell(
        *family_project(tmp_path, 6, tracked=(1, 2, 3, 4), fail=(), tree=tree)
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[1]
        second = fold_key(leaf(3))
        assert list(_fold_rows(card)) == [FOLD, second]
        page.state.tree_folded[A] = [leaf(5)]  # the `or`, folded in the tree
        _click(user, _one(user, f"family-chev:1:{FOLD}"))  # the first, unfolded
        assert page.state.family_unfolded[A] == [leaf(1), leaf(2)]
        _click(user, _one(user, f"family:1:{FOLD}"))
        with page.client:
            _one(user, "edit-family:indices").set_value("1")
        _enter(user, "edit-family:indices")
        await _settled(lambda: page.open == (1, leaf(1)))
        # pipe.T[2] went: the second fold is at children 2 and 3 now, the
        # `or` at 4
        assert page.state.tree_folded[A] == [leaf(4)]
        assert page.state.family_unfolded.get(A) == [leaf(1)]
        card = page.cards[1]
        assert list(_fold_rows(card)) == [fold_key(leaf(2))]
        assert _hidden(card) == [leaf(2), leaf(3)]  # still shut
