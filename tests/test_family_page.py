"""A family's folded criteria row on the Test page (D152; the owner's R2),
and the add form's Indices (R1), in NiceGUI's user simulation: what is
drawn, what a click and an edit do to the page's one editor, and that the
rows are repainted — never rebuilt — when the fold moves."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.family_rows import fold_key  # noqa: E402
from dstf.authoring.shell.family_fold import HINT, FoldRow  # noqa: E402
from dstf.authoring.shell.plot_card import STAGED  # noqa: E402
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


def _drawn_order(card) -> list[str]:
    """The rows' markers in the order the criteria column holds them."""
    out = []
    for child in card.folding.column.default_slot.children:
        out.extend(m for m in child._markers if m.startswith(("family:", "criterion:")))
    return out


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
        # D4: one gesture, one pending edit
        assert [(op.op, op.path) for op in six.session.spec.pending] == [("add", ROOT)]
        await _settled(lambda: _one(user, f"family-count:1:{FOLD}").text == "6 pass")
        assert _one(user, f"family-pill:1:{FOLD}").text == "pass"
        assert _one(user, "pending").text == "1 pending edit"
        # the editor being typed in is not rebuilt, nor closed
        assert _numbers(page, "Tolerance") == [field]
        assert page.open == (1, FOLD)
        # typed over: still one edit
        with page.client:
            field.set_value(0.25)
        assert len(six.session.spec.pending) == 1
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
        before = _drawn_order(card)
        ids = {p: row.row.id for p, row in card.rows.items()}
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: card.rows[FOLD].name.text == "nrmse · pipe.T[1,2,4:6]")
        # its own row now: not indented, never hidden; the rest still fold
        assert _members(card) == [leaf(i) for i in (1, 2, 4, 5, 6)]
        assert _hidden(card) == []
        assert _one(user, f"family-count:1:{FOLD}").text == "5 pass"
        # REPAINTED: every row is the element it was, where it was, and the
        # field being typed in is still the field
        assert _drawn_order(card) == before
        assert {p: row.row.id for p, row in card.rows.items()} == ids
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
        # ... and closed at one value they ARE one criterion, and one fold
        with page.client:
            field.set_value(0.5)
            page.close_editor()
            page.sync()
        assert list(_fold_rows(card)) == [FOLD]
        assert card.rows[FOLD].name.text == "nrmse · pipe.T[1:4]"


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
        assert len(some.session.spec.pending) == 1
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
        assert _one(user, "pending").text == "1 pending edit"
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
