"""A criterion over an array family in Chromium, served by `dstf author`
(D152): the add form's Indices field and the folded criteria row. The user
simulation renders nothing and has no keyboard, so this is where the things
a person does are said: Enter in an Indices field, typing in a folded
field while the count beside it follows, a member typed out of its fold
with the focus still in its field, a chevron's click that is not its
row's.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes and by role and name."""

from __future__ import annotations

import os
import re
import shutil
from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from playwright.sync_api import expect  # noqa: E402

from family_project import family_project  # noqa: E402
from test_author_suite_playwright import EXPECT_TIMEOUT  # noqa: E402
from test_author_test_page_playwright import _plots_mounted, _served  # noqa: E402

#: The one fixed sentence of a folded editor's hint (`family_fold.HINT`).
HINT = "These fields change all {n} criteria. Unfold to drag one on the plot."


def _shot(page, tmp_path: Path, name: str) -> None:
    """Viewport-sized, never full page (the global image rules). With
    `DSTF_KEEP_SHOTS` set it is also copied where the gate can read it."""
    path = tmp_path / f"family-{name}.png"
    page.screenshot(path=str(path))
    keep = os.environ.get("DSTF_KEEP_SHOTS")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(keep) / path.name)


def _card(page, index: int = 1):
    return page.locator(".dstf-plot").nth(index)


def _fold(page, index: int = 1):
    """The folded rows of plot ``index``."""
    return _card(page, index).locator(".dstf-fold")


def _members(page, index: int = 1):
    """The criteria rows of plot ``index`` that are members of a fold."""
    return _card(page, index).locator(".dstf-crit[data-member]")


def _own(page, index: int = 1):
    """The criteria rows of plot ``index`` that are a criterion's own."""
    return _card(page, index).locator(".dstf-crit:not(.dstf-fold):not([data-member])")


def _pending(page):
    return page.locator(".dstf-header-row").get_by_role(
        "button", name=re.compile(r"pending edit")
    )


def _open_fold(page, index: int = 1) -> None:
    """Click the folded row open — on its name, clear of its chevron."""
    _fold(page, index).first.locator(".dstf-crit-name").click()
    expect(_card(page, index).locator(".dstf-editor")).to_be_visible(
        timeout=EXPECT_TIMEOUT
    )


def test_the_add_form_takes_a_slice_and_add_draws_one_folded_row(
    tmp_path, playwright_browser
):
    """R1: a family's element picked, Indices shows every element (`1:20`);
    a slice typed and Enter pressed, ONE folded row stands under the
    family's new plot with the count of its four verdicts, its editor open
    — and the preview holds four literal leaves and says what was added."""
    project = partial(family_project, n=20, tracked=(), fail=(6,))
    with _served(tmp_path, playwright_browser, project=project) as page:
        expect(page.locator(".dstf-plot")).to_have_count(1)
        head = _card(page, 0).locator(".dstf-criteria-head")
        head.get_by_role("button", name="Add a criterion for x").click()
        box = page.get_by_role("combobox", name="Variable")
        expect(box).to_be_visible(timeout=EXPECT_TIMEOUT)
        indices = page.get_by_label("Indices")
        expect(indices).to_be_hidden()  # x is no element of an array
        box.fill("pipe.T[1?]")
        rows = page.locator(".dstf-pick-row")
        expect(rows).to_have_count(10, timeout=EXPECT_TIMEOUT)
        rows.filter(has_text="pipe.T[12]").click()
        expect(page.locator(".dstf-pick-chosen")).to_have_text("Chosen: pipe.T[12]")
        expect(indices).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(indices).to_have_value("1:20")
        form = page.locator(".dstf-form-row")
        expect(form).to_contain_text("Elements of pipe.T[1:20]")
        # what Add would add, said three times over: beside the field, on
        # the button, and in the row's title — which no longer says "for x"
        expect(form).to_contain_text("20 elements")
        expect(form.get_by_role("button", name="Add 20 criteria")).to_be_visible()
        expect(form).to_contain_text("Add a criterion for pipe.T[1:20]")
        expect(form).not_to_contain_text("Add a criterion for x")
        _shot(page, tmp_path, "add-form-indices")
        # start:step:end — the other way round it is ONE element, and says so
        indices.fill("1:20:5")
        expect(form.get_by_role("button", name="Add 1 criterion")).to_be_visible()
        expect(form).to_contain_text("Add a criterion for pipe.T[1]")
        indices.fill("1:5:20")
        expect(form).to_contain_text("4 elements")
        expect(form.get_by_role("button", name="Add 4 criteria")).to_be_visible()
        expect(form).to_contain_text("Add a criterion for pipe.T[1:5:16]")
        indices.press("Enter")  # Enter in Indices is Add
        # the flat test's own tree written out and the op of the add: ONE
        # Add, one edit
        expect(_pending(page)).to_have_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-plot")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        fold = _fold(page)
        expect(fold).to_have_count(1)
        expect(fold).to_contain_text("nrmse · pipe.T[1:5:16]")
        expect(fold).to_contain_text("3 pass · 1 fail")
        expect(fold.locator(".dstf-pill")).to_have_text(re.compile("fail", re.I))
        # its members stand under it, folded away
        expect(_members(page)).to_have_count(4)
        for i in range(4):
            expect(_members(page).nth(i)).to_be_hidden()
        # the new criterion's editor is the fold's
        expect(_card(page).locator(".dstf-editor")).to_contain_text(
            HINT.format(n=4), timeout=EXPECT_TIMEOUT
        )
        _pending(page).click()
        drawer = page.locator(".dstf-pending-drawer")
        expect(drawer).to_contain_text("1 pending edit · 1 test")
        # what the gesture did, in words, and that Save writes the tree too
        expect(drawer).to_contain_text("added 4 criteria: pipe.T[1:5:16]")
        expect(drawer).to_contain_text(
            "Save also writes this test's 1 criterion out as a tree"
        )
        text = drawer.inner_text()
        for i in (1, 6, 11, 16):
            assert f'"variable": "pipe.T[{i}]"' in text, text
        # four literal leaves and nothing else of the family: no slice, no glob
        assert text.count('"variable": "pipe.T[') == 4, text


def test_a_folded_field_changes_every_member_and_the_count_follows(
    tmp_path, playwright_browser
):
    """R2: the folded row's editor is one member's form; a tolerance typed
    there reaches all six, and the row's count and badge follow each value
    while the field keeps the focus. Unfolded, every member says the same."""
    with _served(tmp_path, playwright_browser, project=family_project) as page:
        fold = _fold(page)
        expect(fold).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(fold).to_contain_text("nrmse · pipe.T[1:6]")
        expect(fold).to_contain_text("5 pass · 1 fail")
        _card(page).scroll_into_view_if_needed()
        _shot(page, tmp_path, "fold-closed")
        _open_fold(page)
        editor = _card(page).locator(".dstf-editor")
        expect(editor).to_contain_text(HINT.format(n=6))
        expect(editor.get_by_label("Indices")).to_have_value("1:6")
        # fields only: no ring, no arrow, no bound line is drawn for a fold
        _plots_mounted(page)
        assert (
            page.evaluate(
                "document.querySelectorAll('.js-plotly-plot')[1]"
                "._fullLayout.shapes?.length ?? 0"
            )
            == 0
        )
        tolerance = editor.get_by_label("Tolerance")
        tolerance.fill("0.5")
        expect(fold).to_contain_text("6 pass", timeout=EXPECT_TIMEOUT)
        expect(fold.locator(".dstf-pill")).to_have_text(re.compile("pass", re.I))
        # this test is flat: its tree written out under the one edit
        expect(_pending(page)).to_have_text("1 pending edit")
        assert tolerance.evaluate("e => document.activeElement === e")
        _card(page).scroll_into_view_if_needed()
        _shot(page, tmp_path, "fold-open")
        # typed over, on the same field: every member fails at 1e-4
        tolerance.fill("0.0001")
        expect(fold).to_contain_text("0 pass · 6 fail", timeout=EXPECT_TIMEOUT)
        expect(_pending(page)).to_have_text("1 pending edit")  # typed over: the same
        assert tolerance.evaluate("e => document.activeElement === e")
        # unfolded, each member's own row says what the fold's count said
        fold.get_by_role("button", name="Unfold nrmse · pipe.T[1:6]").click()
        expect(_members(page)).to_have_count(6)
        for i in range(6):
            row = _members(page).nth(i)
            expect(row).to_be_visible()
            expect(row).to_contain_text(f"nrmse · pipe.T[{i + 1}]")
            expect(row.locator(".dstf-pill")).to_have_text(re.compile("fail", re.I))
        # the chevron's click was not the row's: the fold's editor is still open
        expect(editor).to_contain_text(HINT.format(n=6))


def test_a_member_typed_out_of_its_fold_is_its_own_row_and_keeps_the_focus(
    tmp_path, playwright_browser
):
    """Unfold, open one member, change it: it stands as its own row, the
    fold's label shows the gap — and the field being typed in is the same
    field, focused, with what was typed. Typed back, it folds again."""
    with _served(tmp_path, playwright_browser, project=family_project) as page:
        fold = _fold(page)
        expect(fold).to_have_count(1, timeout=EXPECT_TIMEOUT)
        chevron = fold.locator("button.dstf-chev")
        expect(chevron).to_have_attribute("aria-expanded", "false")
        chevron.click()
        expect(chevron).to_have_attribute("aria-expanded", "true")
        expect(_members(page).first).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(_card(page).locator(".dstf-editor")).to_have_count(0)
        member = _members(page).filter(has_text="nrmse · pipe.T[3]")
        member.locator(".dstf-crit-name").click()
        editor = _card(page).locator(".dstf-editor")
        expect(editor).to_be_visible(timeout=EXPECT_TIMEOUT)
        # a member's editor is a criterion's: Indices is the fold's alone
        expect(editor.get_by_label("Indices")).to_have_count(0)
        tolerance = editor.get_by_label("Tolerance")
        tolerance.click()
        page.keyboard.type("0.9")
        expect(fold).to_contain_text("nrmse · pipe.T[1,2,4:6]", timeout=EXPECT_TIMEOUT)
        expect(fold).to_contain_text("5 pass")
        expect(_members(page)).to_have_count(5)
        own = _own(page)
        expect(own).to_have_count(1)
        expect(own).to_contain_text("nrmse · pipe.T[3]")
        expect(own).to_be_visible()
        assert tolerance.evaluate("e => document.activeElement === e")
        assert tolerance.input_value() == "0.9"
        _card(page).scroll_into_view_if_needed()
        _shot(page, tmp_path, "fold-one-differs")
        # where it stands: SHOWN after the fold's block, a sibling of the
        # fold's row and flush with it — while the document holds the rows
        # where they were (nothing moved under the typing hand)
        names = _card(page).locator(".dstf-crit:not(.dstf-fold) .dstf-crit-name")
        assert names.all_inner_texts() == [f"nrmse · pipe.T[{i}]" for i in range(1, 7)]
        last = _members(page).last.bounding_box()
        mine, head = own.bounding_box(), fold.bounding_box()
        assert last is not None and mine is not None and head is not None
        assert mine["y"] >= last["y"] + last["height"] - 1, (last, mine)
        assert abs(mine["x"] - head["x"]) < 1, (head, mine)
        assert _members(page).first.bounding_box()["x"] > head["x"] + 8
        for _ in range(3):
            page.keyboard.press("Backspace")
        expect(fold).to_contain_text("nrmse · pipe.T[1:6]", timeout=EXPECT_TIMEOUT)
        expect(_members(page)).to_have_count(6)
        assert tolerance.evaluate("e => document.activeElement === e")
        expect(_pending(page)).to_have_text("0 pending edits")
        # folded shut, the editor under a member goes with it
        chevron.click()
        expect(_card(page).locator(".dstf-editor")).to_have_count(0)
        for i in range(6):
            expect(_members(page).nth(i)).to_be_hidden()


def test_indices_in_the_folded_editor_add_and_remove_by_the_keyboard(
    tmp_path, playwright_browser
):
    """D5: Enter in Indices applies — leaves come and go, the row reads the
    new elements and its editor is open on them again; an index the family
    lacks is refused in a sentence naming its size, and nothing is staged;
    an empty text says how the criterion is removed."""
    project = partial(family_project, n=10, tracked=(1, 2, 3, 4), fail=())
    with _served(tmp_path, playwright_browser, project=project) as page:
        fold = _fold(page)
        expect(fold).to_contain_text("nrmse · pipe.T[1:4]", timeout=EXPECT_TIMEOUT)
        _open_fold(page)
        editor = _card(page).locator(".dstf-editor")
        indices = editor.get_by_label("Indices")
        expect(indices).to_have_value("1:4")
        expect(editor).to_contain_text("Elements of pipe.T[1:10]")
        indices.fill("1:11")
        indices.press("Enter")
        expect(editor).to_contain_text(
            "pipe.T[1:10] has 10 elements; 11 is not one of them.",
            timeout=EXPECT_TIMEOUT,
        )
        expect(_pending(page)).to_have_text("0 pending edits")
        expect(indices).to_have_value("1:11")  # what was typed stays
        indices.fill("")
        indices.press("Enter")
        expect(editor).to_contain_text("Indices is empty. To remove the criterion")
        expect(_pending(page)).to_have_text("0 pending edits")
        # the count beside the field follows the typing; Esc puts back
        indices.fill("1:6")
        expect(editor).to_contain_text("→ 6 elements (adds 2)")
        indices.press("Escape")
        expect(indices).to_have_value("1:4")
        expect(editor).to_contain_text("→ 4 elements (no change)")
        expect(_card(page).locator(".dstf-editor")).to_have_count(1)  # still open
        indices.fill("2:4, 8")
        expect(editor).to_contain_text("→ 4 elements (removes 1, adds 1)")
        indices.press("Enter")
        expect(fold).to_contain_text("nrmse · pipe.T[2:4,8]", timeout=EXPECT_TIMEOUT)
        expect(fold).to_contain_text("4 pass")
        expect(_pending(page)).to_have_text("1 pending edit")
        # redrawn (leaves came and went), and open again on what it holds
        editor = _card(page).locator(".dstf-editor")
        expect(editor).to_contain_text(HINT.format(n=4), timeout=EXPECT_TIMEOUT)
        expect(editor.get_by_label("Indices")).to_have_value("2:4,8")
        # down to one element: no fold is left, and that criterion's own
        # editor is the one open
        editor.get_by_label("Indices").fill("8")
        editor.get_by_label("Indices").press("Enter")
        # three of four would go: asked once, and nothing has happened yet
        expect(editor).to_contain_text(
            "Removes 3 of 4 criteria — Apply again to confirm", timeout=EXPECT_TIMEOUT
        )
        expect(_fold(page)).to_have_count(1)
        editor.get_by_label("Indices").press("Enter")
        expect(_fold(page)).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(_own(page)).to_have_count(1)
        expect(_own(page)).to_contain_text("nrmse · pipe.T[8]")
        expect(
            _card(page).locator(".dstf-editor").get_by_label("Indices")
        ).to_have_count(0)
        # The tree given whole again, over the one just given: the same edit.
        expect(_pending(page)).to_have_text("1 pending edit")


def test_the_chevron_answers_the_keyboard(tmp_path, playwright_browser):
    """It is a button: Tab reaches it, Enter and Space toggle it, and its
    name says what a press does."""
    with _served(tmp_path, playwright_browser, project=family_project) as page:
        fold = _fold(page)
        expect(fold).to_have_count(1, timeout=EXPECT_TIMEOUT)
        chevron = fold.get_by_role("button", name="Unfold nrmse · pipe.T[1:6]")
        chevron.focus()
        page.keyboard.press("Enter")
        opened = fold.get_by_role("button", name="Fold nrmse · pipe.T[1:6]")
        expect(opened).to_have_attribute(
            "aria-expanded", "true", timeout=EXPECT_TIMEOUT
        )
        expect(_members(page).first).to_be_visible()
        # a member is indented under its fold, its name right of the fold's
        fold_x = fold.locator(".dstf-crit-name").bounding_box()["x"]
        member_x = _members(page).first.locator(".dstf-crit-name").bounding_box()["x"]
        assert member_x > fold_x + 8, (fold_x, member_x)
        page.keyboard.press("Space")
        expect(chevron).to_have_attribute(
            "aria-expanded", "false", timeout=EXPECT_TIMEOUT
        )
        expect(_members(page).first).to_be_hidden()
        # no editor opened: the chevron's key is not the row's click
        expect(_card(page).locator(".dstf-editor")).to_have_count(0)


def _t3_apart() -> dict:
    """`and[x, nrmse pipe.T[1..6]]`, saved, pipe.T[3] at its own tolerance."""
    return {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            *(
                {
                    "metric": "nrmse",
                    "variable": f"pipe.T[{i}]",
                    **({"tolerance": 0.5} if i == 3 else {}),
                }
                for i in range(1, 7)
            ),
        ],
    }


def test_done_releases_the_fold_and_its_indices_never_double_a_criterion(
    tmp_path, playwright_browser
):
    """The executing review's H1, with a real keyboard: the fold of five
    typed to pipe.T[3]'s own value and closed with Done is the fold of six
    — at once, with no other event to repaint it — and its Indices as they
    are leave ONE criterion on pipe.T[3]."""
    project = partial(family_project, n=6, tree=_t3_apart())
    with _served(tmp_path, playwright_browser, project=project) as page:
        fold = _fold(page)
        expect(fold).to_contain_text("nrmse · pipe.T[1,2,4:6]", timeout=EXPECT_TIMEOUT)
        expect(_own(page)).to_have_count(1)
        _open_fold(page)
        editor = _card(page).locator(".dstf-editor")
        tolerance = editor.get_by_label("Tolerance")
        tolerance.click()
        page.keyboard.type("0.5", delay=80)
        expect(_pending(page)).to_have_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        # held while its editor is open: still the fold of five
        expect(fold).to_contain_text("nrmse · pipe.T[1,2,4:6]")
        page.wait_for_timeout(800)  # every event of the edit heard
        editor.get_by_role("button", name="Done").click()
        expect(fold).to_contain_text("nrmse · pipe.T[1:6]", timeout=EXPECT_TIMEOUT)
        expect(fold).to_contain_text("6 pass")
        expect(_own(page)).to_have_count(0)
        _open_fold(page)
        editor = _card(page).locator(".dstf-editor")
        expect(editor).to_contain_text(HINT.format(n=6))
        indices = editor.get_by_label("Indices")
        expect(indices).to_have_value("1:6")
        expect(editor).to_contain_text("→ 6 elements (no change)")
        indices.press("Enter")
        page.wait_for_timeout(800)
        expect(_pending(page)).to_have_text("1 pending edit")
        fold.locator("button.dstf-chev").click()
        expect(_members(page)).to_have_count(6)
        names = _card(page).locator(".dstf-crit:not(.dstf-fold) .dstf-crit-name")
        assert names.all_inner_texts() == [f"nrmse · pipe.T[{i}]" for i in range(1, 7)]


def test_a_differing_row_follows_the_block_and_a_rows_plus_is_its_own_elements(
    tmp_path, playwright_browser
):
    """Unfolded, the fold's five members are one block under it and
    pipe.T[3], with its own criterion, comes after them — flush with the
    fold's row, not between two members. A member's `+` starts Indices at
    that member's index; Indices over pipe.T[3] is refused, naming it."""
    project = partial(family_project, n=6, tree=_t3_apart())
    with _served(tmp_path, playwright_browser, project=project) as page:
        fold = _fold(page)
        expect(fold).to_contain_text("nrmse · pipe.T[1,2,4:6]", timeout=EXPECT_TIMEOUT)
        fold.locator("button.dstf-chev").click()
        expect(_members(page).first).to_be_visible(timeout=EXPECT_TIMEOUT)
        boxes = [_members(page).nth(i).bounding_box() for i in range(5)]
        own, head = _own(page).bounding_box(), fold.bounding_box()
        assert (
            all(b is not None for b in boxes) and own is not None and head is not None
        )
        ys = [b["y"] for b in boxes]
        assert ys == sorted(ys) and head["y"] < ys[0]
        # one block: each member right under the one before, no row between
        for above, below in zip(boxes, boxes[1:], strict=False):
            assert abs(below["y"] - (above["y"] + above["height"])) < 2, boxes
        assert own["y"] >= boxes[-1]["y"] + boxes[-1]["height"] - 1, (boxes[-1], own)
        assert abs(own["x"] - head["x"]) < 1 and boxes[0]["x"] > head["x"] + 8
        _card(page).scroll_into_view_if_needed()
        _shot(page, tmp_path, "fold-block")
        # a member's `+`: its own element
        member = _members(page).filter(has_text="nrmse · pipe.T[4]")
        member.get_by_role("button", name="Add a criterion after this one").click()
        form = page.locator(".dstf-form-row")
        expect(form.get_by_label("Indices")).to_have_value("4", timeout=EXPECT_TIMEOUT)
        expect(form.get_by_role("button", name="Add 1 criterion")).to_be_visible()
        form.get_by_role("button", name="Cancel").click()
        # the fold's Indices over the element with its own criterion
        _open_fold(page)
        editor = _card(page).locator(".dstf-editor")
        indices = editor.get_by_label("Indices")
        indices.fill("1:6")
        refusal = (
            "pipe.T[3] already has its own nrmse criterion under this group — "
            "edit or remove that one, or leave 3 out"
        )
        expect(editor).to_contain_text(refusal)  # said as it is typed
        indices.press("Enter")
        page.wait_for_timeout(600)
        expect(_pending(page)).to_have_text("0 pending edits")
        expect(_own(page)).to_have_count(1)


#: The family scored by a tube of control points — a metric a Ctrl+click
#: adds a row to (D149) — the same on every element: one fold of four.
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
        *({**_TUBE, "variable": f"pipe.T[{i}]"} for i in range(1, 5)),
    ],
}


def _ctrl_click(
    page, x: float, y: float, card: int = 1, modifier: str | None = "Control"
) -> None:
    """A real Ctrl+click at a data position of plot ``card``'s time plot —
    or the click with another ``modifier`` held, or (None) with none."""
    at = page.evaluate(
        "([card, x, y]) => { const gd = document.querySelectorAll('.dstf-plot')[card]"
        ".querySelector('.js-plotly-plot'); const p = gd._fullLayout._plots.xy,"
        " r = gd.getBoundingClientRect(); return [r.left + p.xaxis._offset +"
        " p.xaxis.d2p(x), r.top + p.yaxis._offset + p.yaxis.d2p(y)]; }",
        [card, x, y],
    )
    page.mouse.move(at[0] - 12, at[1] - 12)
    page.mouse.move(at[0], at[1], steps=3)
    if modifier:
        page.keyboard.down(modifier)
    page.mouse.down()
    page.mouse.up()
    if modifier:
        page.keyboard.up(modifier)


def test_a_fold_takes_no_click_on_the_plot_and_an_unfolded_member_does(
    tmp_path, playwright_browser
):
    """On-plot editing over a fold (D149 × D152). A fold's editor is fields
    only: no ⌖, a hint that offers no click, and a Ctrl+click on the
    family's plot adds nothing and says where that gesture is, on the line
    under the plot. An unfolded member's editor has the ⌖ and the offer;
    its Ctrl+click adds a control point to THAT member, which leaves the
    fold — three stay folded."""
    project = partial(family_project, n=4, fail=(), tree=TUBES)
    with _served(tmp_path, playwright_browser, project=project) as page:
        card = _card(page)
        card.scroll_into_view_if_needed()
        expect(_fold(page)).to_contain_text("tube · pipe.T[1:4]")
        _open_fold(page)
        editor = card.locator(".dstf-editor")
        expect(editor.locator(".dstf-why").first).to_contain_text(HINT.format(n=4))
        expect(editor.locator(".dstf-why").first).not_to_contain_text("Ctrl+click")
        expect(editor.locator(".dstf-arm")).to_have_count(0)
        _plots_mounted(page)  # each click is aimed through the drawn axes
        card.locator(".js-plotly-plot").evaluate(
            "e => window.scrollBy(0, e.getBoundingClientRect().top - 190)"
        )
        page.wait_for_timeout(300)
        refusal = card.locator(".dstf-plot-refusal")
        said = (
            "Ctrl+click adds a control point to one criterion: unfold this row "
            "and open the one to add to."
        )
        chevron = _fold(page).first.locator(".dstf-fold-chev")
        # A plain click is no gesture of a fold's: nothing is said of it.
        _ctrl_click(page, 0.4, 1.0, modifier=None)
        page.wait_for_timeout(700)  # a round trip, had it said anything
        expect(refusal).to_be_hidden()
        # Cmd, alone, is the Mac's Ctrl: the line says where the gesture is,
        # and goes when the row it is about unfolds — its editor still open.
        _ctrl_click(page, 0.5, 1.0, modifier="Meta")
        expect(refusal).to_have_text(said, timeout=EXPECT_TIMEOUT)
        chevron.click()
        expect(_members(page).first).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(refusal).to_be_hidden(timeout=EXPECT_TIMEOUT)
        expect(editor.locator(".dstf-why").first).to_contain_text(HINT.format(n=4))
        chevron.click()
        expect(_members(page).first).to_be_hidden(timeout=EXPECT_TIMEOUT)
        card.locator(".js-plotly-plot").evaluate(
            "e => window.scrollBy(0, e.getBoundingClientRect().top - 190)"
        )
        page.wait_for_timeout(300)
        _ctrl_click(page, 0.5, 1.0)
        expect(refusal).to_have_text(said, timeout=EXPECT_TIMEOUT)
        expect(_pending(page)).to_have_text("0 pending edits")
        _shot(page, tmp_path, "fold-ctrl-click")

        # Unfolded, pipe.T[2]'s own editor: the ⌖, the offer, the click.
        chevron.click()
        expect(refusal).to_be_hidden(timeout=EXPECT_TIMEOUT)
        _members(page).filter(has_text="pipe.T[2]").click()
        expect(editor.locator(".dstf-why").first).to_contain_text(
            "Ctrl+click adds a control point."
        )
        expect(editor.locator(".dstf-table-row .dstf-arm")).to_have_count(2)
        card.locator(".js-plotly-plot").evaluate(
            "e => window.scrollBy(0, e.getBoundingClientRect().top - 190)"
        )
        page.wait_for_timeout(300)
        _ctrl_click(page, 0.5, 1.0)
        expect(_pending(page)).to_have_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        expect(editor.locator(".dstf-table-row")).to_have_count(3)
        expect(_fold(page)).to_contain_text("tube · pipe.T[1,3,4]")
        expect(_own(page).filter(has_text="pipe.T[2]")).to_have_count(1)
