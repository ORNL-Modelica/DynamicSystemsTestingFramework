"""The gestures on a plot (D149) as the Test page hears them, in NiceGUI's
user simulation: the page's three events — a click, a held handle, Esc —
reach the card whose plot they name; a Ctrl+click stages one row; an armed
field takes the next plain click; a held handle's fields follow it and
stage nothing; and a move that arrives after its release paints nothing."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import UNSCORED  # noqa: E402
from dstf.authoring.live_rows import NOT_SCORED, WAITING  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
from dstf.authoring.situation import Situation  # noqa: E402
from dstf.authoring.typed_number import TypedNumber  # noqa: E402
from live_fake import make_live, steer, write_scripts  # noqa: E402
from on_plot_project import (  # noqa: E402
    EVENT_TIMING,
    NRMSE,
    POINTS,
    RANGE,
    TUBE,
    wave_project,
)
from test_session import A  # noqa: E402

STAGED = " Edits are staged as you make them; Save writes them."


@pytest.fixture(autouse=True)
def _registry():
    capabilities._reset_for_tests()
    register_builtins()
    yield
    capabilities._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = wave_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


@pytest.fixture
def live(tmp_path, monkeypatch):
    """`shell`'s project on the Python backend made live (`live_fake`),
    every script one a test holds at a row of its choosing: the shell, and
    the library's Examples directory to steer a run from."""
    make_live(monkeypatch.setattr)
    cfg, work = wave_project(tmp_path)
    examples = tmp_path / "FakeLib" / "Examples"
    write_scripts(examples)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.session.rescan_done.wait(30)
    s.load.phase = "done"
    yield s, examples
    for hold in examples.glob("*.hold"):
        hold.unlink()
    s.session.stop_queue()
    s.session.wait_background(30)


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


async def _opened(shell, user, path: str) -> tuple[TestPage, object]:
    """The Test page of A with the criterion at ``path`` open on x's plot."""
    shell.mount()
    await user.open(f"/test/{A}")
    # The page awaits its client's handshake before it draws.
    await user.should_see(marker="wordmark", retries=20)
    (page,) = [
        p for p in shell.pages if isinstance(p, TestPage) and p.client is user.client
    ]
    card = page.cards[0]
    with page.client:
        card.show()
    user.find(marker=f"criterion:0:{path}").click()
    await _settled(lambda: page.open == (0, path))
    return page, card


def _click(card, x, y=0.0, axis="x", **keys) -> dict:
    spans = {"x": [0.0, 1.0], "x2": [0.0, 12.0]}
    return {
        "plot": card.plot.id,
        "xaxis": axis,
        "yaxis": "y2" if axis == "x2" else "y",
        "x": x,
        "y": y,
        "xrange": spans[axis],
        "yrange": [-2.0, 2.0],
        **keys,
    }


def _leaf(shell, path: str) -> dict:
    return shell.session.tree(A)["children"][int(path.rsplit("/", 1)[1])]


def _press(user, card, owner: str, pointer: tuple) -> ui.button:
    button = card.placing._buttons[owner, pointer]
    UserInteraction(user, {button}, None).click()
    return button


def _hint(user, path: str) -> str:
    (hint,) = user.find(marker=f"editor-hint:0:{path}").elements
    return hint.text


def _cell(user, path: str, table: str, row: int, key: str) -> TypedNumber:
    (cell,) = user.find(marker=f"{path}:{table}:{row}:{key}").elements
    return cell


async def test_a_ctrl_click_stages_exactly_one_row_and_one_edit(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        assert "Ctrl+click adds a point." in _hint(user, POINTS)
        with page.client:
            page.on_plot("clicked", _click(card, 0.78123, 1.0, ctrl=True))
        (op,) = shell.session.spec.pending
        assert (op.op, op.path) == ("replace", POINTS)
        rows = op.value["points"]
        assert len(rows) == 3 and rows[2] == {"time": 0.781}
        # The table followed: the editor was rebuilt whole.
        await _settled(lambda: _cell(user, POINTS, "points", 2, "time").value == 0.781)
        # ⌘+click is Ctrl+click where Ctrl+click is the context menu.
        with page.client:
            page.on_plot("clicked", _click(card, 0.9, meta=True))
        assert len(_leaf(shell, POINTS)["points"]) == 4
        assert len(shell.session.spec.pending) == 1  # one leaf, one replace


@pytest.mark.parametrize("keys", [{}, {"shift": True}, {"alt": True}])
async def test_a_click_with_nothing_armed_and_no_ctrl_changes_nothing(shell, keys):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, 0.5, **keys))
        assert shell.session.spec.pending == []
        assert card.placing.armed is None


async def test_a_click_reaches_only_the_card_whose_plot_it_names(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        with page.client:
            page.on_plot("clicked", {**_click(card, 0.5, ctrl=True), "plot": -1})
            page.on_plot("clicked", None)
            # The other plot has no editor open: its clicks are nothing.
            page.cards[1].show()
            page.on_plot(
                "clicked",
                {**_click(card, 0.5, ctrl=True), "plot": page.cards[1].plot.id},
            )
        assert shell.session.spec.pending == []


async def test_a_ctrl_click_on_a_metric_without_rows_changes_nothing(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        assert "Ctrl+click" not in _hint(user, RANGE)
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, 0.5, ctrl=True))
        assert shell.session.spec.pending == []
        assert not card.refusal.visible


async def test_an_armed_field_takes_the_next_plain_click_and_disarms(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        rest = _hint(user, POINTS)
        pointer = ("points", 0)
        button = _press(user, card, "edit-on-plot", pointer)
        assert button.props["aria-pressed"] == "true"
        assert card.placing.armed.pointer == pointer
        said = "Click the plot to set row 1 — Esc cancels."
        assert _hint(user, POINTS) == said
        # The plot's own card says so too: an attribute the stylesheet
        # outlines the plot by, and a line under the plot.
        assert "data-armed" in card.card.props
        assert card.armed_line.visible and card.armed_line.text == said
        assert "dstf-plot-armed" in card.armed_line.classes
        # Every other ⌖ says it is not the one.
        others = [b for k, b in card.placing._buttons.items() if k[1] != pointer]
        assert others and {b.props["aria-pressed"] for b in others} == {"false"}
        with page.client:
            page.on_plot("clicked", _click(card, 0.33333, -0.4567))
        (op,) = shell.session.spec.pending
        # The row holds a value of its own: the click is its place.
        assert op.value["points"][0]["time"] == 0.333
        assert op.value["points"][0]["value"] == -0.46
        assert card.placing.armed is None
        assert _hint(user, POINTS) == rest
        assert "data-armed" not in card.card.props
        assert not card.armed_line.visible
        await _settled(lambda: _cell(user, POINTS, "points", 0, "time").value == 0.333)
        # Disarmed: the next plain click is nothing again.
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, 0.0))
        assert len(shell.session.spec.pending) == 1
        assert _leaf(shell, POINTS)["points"][0]["time"] == 0.333


async def test_a_modified_click_does_not_set_an_armed_field(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        _press(user, card, "edit-criteria", ("max_value",))
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, 1.0, shift=True))
        assert shell.session.spec.pending == []
        assert card.placing.armed is not None


async def test_esc_the_same_button_and_closing_the_editor_disarm(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        pointer = ("min_value",)
        button = _press(user, card, "edit-criteria", pointer)
        assert card.placing.armed is not None
        with page.client:
            page.disarm()  # Esc, as the page says it
        assert card.placing.armed is None
        assert button.props["aria-pressed"] == "false"
        _press(user, card, "edit-criteria", pointer)
        _press(user, card, "edit-criteria", pointer)  # pressed again
        assert card.placing.armed is None
        # Arming another field moves the one armed field.
        _press(user, card, "edit-criteria", pointer)
        other = _press(user, card, "edit-criteria", ("max_value",))
        assert card.placing.armed.pointer == ("max_value",)
        assert (button.props["aria-pressed"], other.props["aria-pressed"]) == (
            "false",
            "true",
        )
        # Switching criterion closes this editor: nothing stays armed.
        user.find(marker=f"criterion:0:{POINTS}").click()
        await _settled(lambda: page.open == (0, POINTS))
        assert card.placing.armed is None
        assert "Click the plot" not in _hint(user, POINTS)
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, -1.5))
        assert shell.session.spec.pending == []


async def test_a_refused_click_says_why_and_leaves_the_field_armed(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        _press(user, card, "edit-criteria", ("min_value",))
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, 2.5))  # above max_value 2.0
        assert shell.session.spec.pending == []
        assert card.placing.armed.pointer == ("min_value",)
        assert card.refusal.visible
        assert "min_value 2.5 would exceed max_value 2" in card.refusal.text
        assert "Click the plot to set" in _hint(user, RANGE)
        # The next click, inside the bound, goes through and disarms.
        with page.client:
            page.on_plot("clicked", _click(card, 0.5, -1.2345))
        (op,) = shell.session.spec.pending
        assert op.value["min_value"] == -1.23
        assert card.placing.armed is None and not card.refusal.visible


def _buttons(card) -> dict[str, set]:
    """The ⌖ drawn in the open editor, by the capability that drew them."""
    out: dict[str, set] = {"edit-criteria": set(), "edit-on-plot": set()}
    for (owner, pointer), button in card.placing._buttons.items():
        if not button.is_deleted:
            out[owner].add(pointer)
    return out


#: Per criterion of the fixture: the ⌖ of its form (Edit criteria's) and of
#: its table (Edit on plot's) — ONE per row, and a form field's only where
#: a click can set it as the criterion is.
WINDOW = {("window", "start"), ("window", "end")}
BUTTONS = {
    # Row 2 defers to the criterion's tolerance: its sides are on the plot.
    POINTS: (
        WINDOW | {("tolerance", "lower"), ("tolerance", "upper")},
        {("points", 0), ("points", 1)},
    ),
    # Its row holds its own tolerance: nothing shows the criterion's.
    EVENT_TIMING: (WINDOW, {("events", 0)}),
    # Control points: neither constant width, nor the minimum width.
    TUBE: (WINDOW, {("tube_points", 0), ("tube_points", 1)}),
    RANGE: (WINDOW | {("min_value",), ("max_value",)}, set()),
    NRMSE: (WINDOW, set()),
}


@pytest.mark.parametrize("path", BUTTONS)
async def test_a_button_is_drawn_only_where_a_click_can_set_its_field(shell, path):
    """One rule for the ⌖ and for the click (`edit_on_plot.placeable`):
    four ⌖ of a tube's editor stood on fields no click could set, and a
    points row carried six."""
    form, table = BUTTONS[path]
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, path)
        assert _buttons(card) == {"edit-criteria": form, "edit-on-plot": table}
        # ... and every one of them is a click `placed` takes.
        from dstf.authoring.builtin.edit_on_plot import placeable

        view = shell.view_for(card.editor.selection_of())
        can = placeable(view, model_id=A, variable="x", path=path)
        assert all(can(pointer) for pointer in form | table)


async def test_nrmse_arms_its_window_and_nothing_else(shell):
    """nrmse draws nothing to drag and has no Edit on plot place: its
    window is Edit criteria's field, armed and set by Edit criteria."""
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, NRMSE)
        assert _buttons(card)["edit-criteria"] == WINDOW
        _press(user, card, "edit-criteria", ("window", "end"))
        assert _hint(user, NRMSE) == "Click the plot to set window end — Esc cancels."
        with page.client:
            page.on_plot("clicked", _click(card, 0.81234, 0.3))
        (op,) = shell.session.spec.pending
        assert op.value["window"] == {"end": 0.812}
        assert card.placing.armed is None


def _move(card, name: str, seq: int, drag: int = 100, **position) -> dict:
    index = next(
        i
        for i, shape in enumerate(card.plot.figure["layout"]["shapes"])
        if shape.get("name") == name
    )
    update = {f"shapes[{index}].{key}": value for key, value in position.items()}
    update[f"shapes[{index}].visible"] = {"x": 1.0, "y": 4.0}
    update[f"shapes[{index}].drag"] = drag
    return {"plot": card.plot.id, "seq": seq, "update": update}


async def test_a_held_handles_fields_follow_it_and_nothing_is_staged(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        upper = _cell(user, POINTS, "points", 0, "tolerance:upper")
        lower = _cell(user, POINTS, "points", 0, "tolerance:lower")
        time = _cell(user, POINTS, "points", 0, "time")
        name = f"{POINTS}/points/upper/0"
        for seq, y in enumerate((-0.61234, -0.5234, -0.4321), start=1):
            with page.client:
                page.on_plot("moved", _move(card, name, seq, xanchor=0.25, yanchor=y))
            want = round(y + 1.0, 2)  # the row's value is -1.0; snapped to 0.01
            # A number: both sides follow, as both will be written.
            assert (upper.previewed, lower.previewed) == (want, want)
            assert upper.preview_revision == 100
            # What the drag does not change shows what it holds.
            assert time.previewed is None
            assert upper.value == 0.3 and shell.session.spec.pending == []
        # The release stages what the last move showed.
        release = _move(card, name, 9, xanchor=0.25, yanchor=-0.4321)["update"]
        index = next(iter(release)).split(".")[0]
        release[f"{index}.seq"] = 9
        with page.client:
            card.hook._dispatch("plotly_relayout", release)
        (op,) = shell.session.spec.pending
        assert op.value["points"][0]["tolerance"] == 0.57
        await _settled(
            lambda: _cell(user, POINTS, "points", 0, "tolerance:upper").value == 0.57
        )
        rebuilt = _cell(user, POINTS, "points", 0, "tolerance:upper")
        assert rebuilt.previewed is None
        # A move of that drag, delivered after its release: dropped.
        with page.client:
            page.on_plot("moved", _move(card, name, 8, xanchor=0.25, yanchor=0.9))
        assert rebuilt.previewed is None
        assert len(shell.session.spec.pending) == 1


async def test_a_drag_that_ends_unmoved_or_refused_puts_the_fields_back(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        (field,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, TypedNumber)
            and e.props.get("label") == "Upper bound (optional)"
        ] or [None]
        assert field is not None
        name = f"{RANGE}/range/max"
        with page.client:
            page.on_plot("moved", _move(card, name, 1, y0=1.2345, y1=1.2345))
        assert field.previewed == 1.23
        with page.client:
            page.on_plot("moved", {"plot": card.plot.id, "seq": 2, "end": True})
        assert field.previewed is None
        # Held again, then released where it is refused (under the minimum).
        with page.client:
            page.on_plot("moved", _move(card, name, 3, drag=101, y0=0.5, y1=0.5))
        assert field.previewed == 0.5
        refused = _move(card, name, 4, drag=101, y0=-3.0, y1=-3.0)["update"]
        with page.client:
            card.hook._dispatch("plotly_relayout", refused)
        assert shell.session.spec.pending == []
        assert card.refusal.visible and field.previewed is None


async def test_a_held_handle_previews_both_panels_fields(shell):
    """A tube's ring moves a table cell (Edit on plot's); an event's window
    arrow of a row that defers moves the criterion's field (Edit
    criteria's): one preview reaches both panels."""
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, TUBE)
        name = f"{TUBE}/tube/upper/1"
        with page.client:
            page.on_plot("moved", _move(card, name, 1, xanchor=0.81234, yanchor=None))
        assert _cell(user, TUBE, "tube_points", 1, "time").previewed == 0.812
        assert _cell(user, TUBE, "tube_points", 0, "time").previewed is None
        assert shell.session.spec.pending == []
        user.find(marker=f"criterion:0:{EVENT_TIMING}").click()
        await _settled(lambda: page.open == (0, EVENT_TIMING))
        sides = [
            e
            for e in page.client.elements.values()
            if isinstance(e, TypedNumber)
            and str(e.props.get("label", "")).endswith("Time tolerance (s)")
            and not e.is_deleted
        ]
        assert len(sides) == 2
        # Event 1 of the run (0.8) is not declared: no arrow. Event 0's row
        # holds its own tolerance, so its arrow moves the ROW's.
        name = f"{EVENT_TIMING}/events/late/0"
        with page.client:
            page.on_plot("moved", _move(card, name, 2, drag=5, xanchor=0.47654))
        cell = _cell(user, EVENT_TIMING, "events", 0, "tolerance:upper")
        assert cell.previewed == 0.077
        assert {side.previewed for side in sides} == {None}


async def test_a_typed_value_is_staged_as_typed_to_its_last_digit(shell):
    """Snapping is the pointer's: what is typed — in a table cell, in the
    form — is written as typed."""
    async with user_simulation(root=shell.index) as user:
        page, _card = await _opened(shell, user, POINTS)
        with page.client:
            _cell(user, POINTS, "points", 0, "time").set_value(0.123456789)
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        assert _leaf(shell, POINTS)["points"][0]["time"] == 0.123456789
        (start,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, TypedNumber)
            and e.props.get("label") == "window start"
            and not e.is_deleted
        ]
        with page.client:
            start.set_value(0.0123456789)
        await _settled(lambda: "window" in _leaf(shell, POINTS))
        assert _leaf(shell, POINTS)["window"] == {"start": 0.0123456789}


async def _armed_row(shell, user, row: int = 1):
    page, card = await _opened(shell, user, POINTS)
    _press(user, card, "edit-on-plot", ("points", row))
    assert card.placing.armed.pointer == ("points", row)
    return page, card


def _disarmed(user, card) -> bool:
    return (
        card.placing.armed is None
        and "Click the plot" not in _hint(user, POINTS)
        and "data-armed" not in card.card.props
    )


@pytest.mark.parametrize("change", ["Remove row", "Add row"])
async def test_a_row_added_or_removed_disarms(shell, change):
    """A ⌖ names its row by index: with "row 2" armed and row 1 removed,
    the click set the row that had been third."""
    async with user_simulation(root=shell.index) as user:
        page, card = await _armed_row(shell, user)
        marker = (
            f"{POINTS}:points:0:remove"
            if change == "Remove row"
            else f"{POINTS}:points:add"
        )
        user.find(marker=marker).click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        assert _disarmed(user, card)
        staged = _leaf(shell, POINTS)
        with page.client:
            page.on_plot("clicked", _click(card, 0.9, 0.5))
        assert _leaf(shell, POINTS) == staged


@pytest.mark.parametrize("change", ["add", "0:remove"])
async def test_a_row_change_the_table_holds_back_disarms_too(shell, change):
    """No edit says so: a required cell is blank, the list is held back
    and nothing is staged — the rows still moved under the ⌖. With "row 2"
    armed and row 1 removed, the click set the row that had been third."""
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, TUBE)
        _press(user, card, "edit-on-plot", ("tube_points", 1))
        with page.client:
            _cell(user, TUBE, "tube_points", 1, "time").set_value(None)
        assert card.placing.armed is not None  # held back: no edit, still armed
        user.find(marker=f"{TUBE}:tube_points:{change}").click()
        await _settled(lambda: card.placing.armed is None)
        assert shell.session.spec.pending == []
        assert "data-armed" not in card.card.props


async def test_a_mode_change_a_ctrl_click_and_an_edit_elsewhere_disarm(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _armed_row(shell, user)
        # A row added by a click moves no index — and is an edit of the test.
        with page.client:
            page.on_plot("clicked", _click(card, 0.9, 0.0, ctrl=True))
        assert len(_leaf(shell, POINTS)["points"]) == 3
        # The page hears of an edit on its next tick.
        await _settled(lambda: _disarmed(user, card))
        _press(user, card, "edit-on-plot", ("points", 1))
        (mode,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, ui.select)
            and e.props.get("label") == "mode"
            and not e.is_deleted
        ]
        mode.set_value("range")
        await _settled(lambda: _leaf(shell, POINTS)["metric"] == "range")
        assert card.placing.armed is None


async def test_discard_and_save_disarm(shell):
    async with user_simulation(root=shell.index) as user:
        page, card = await _armed_row(shell, user)
        with page.client:
            _cell(user, POINTS, "points", 0, "time").set_value(0.3)
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: _disarmed(user, card))  # an edit of the test
        _press(user, card, "edit-on-plot", ("points", 1))
        shell.session.save()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: card.placing.armed is None)
        with page.client:
            _cell(user, POINTS, "points", 0, "time").set_value(0.35)
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        _press(user, card, "edit-on-plot", ("points", 1))
        shell.session.discard()
        await _settled(lambda: card.placing.armed is None)
        assert _leaf(shell, POINTS)["points"][0]["time"] == 0.3


async def test_done_disarms_with_no_other_editor_opening(shell):
    """Closing is its own disarm: opening another criterion rebuilds an
    editor, which would have hidden a close that did not."""
    async with user_simulation(root=shell.index) as user:
        page, card = await _armed_row(shell, user)
        user.find(marker=f"editor-done:0:{POINTS}").click()
        await _settled(lambda: page.open is None)
        assert card.placing.armed is None
        assert "data-armed" not in card.card.props and not card.armed_line.visible


async def _running(shell, page) -> None:
    """Tick the shell — the pool's posts, its events — until the page's
    test runs AND the Session took its start in: the pool marks an item
    running a tick before its result is dropped."""
    results = shell.session.project.results

    def started() -> bool:
        shell.tick()
        return page.strip.situation is Situation.RUNNING and A not in results

    await _settled(started, tries=600)


def _ticked(shell) -> bool:
    shell.tick()
    return True


async def test_a_run_that_starts_leaves_no_field_armed_that_is_not_drawn(live):
    """A rebuild no edit caused: the queue starts the test (D150), nothing
    of it is scored until its result arrives (D151), and the editor's
    table — a row's ⌖ with it — becomes a sentence. Nothing stays armed,
    and the ⌖ that are still drawn are the ones a click can still set: a
    window's ends, which are read off the click alone."""
    shell, examples = live
    async with user_simulation(root=shell.index) as user:
        page, card = await _armed_row(shell, user)
        steer(examples, A, hold=3)
        shell.session.enqueue([A])
        await _running(shell, page)
        (card,) = [c for c in page.cards if c.subject == ["x"]]
        assert card.placing.armed is None
        assert "data-armed" not in card.card.props and not card.armed_line.visible
        assert page.open == (card.index, POINTS)  # its fields stay editable
        assert _buttons(card) == {"edit-criteria": WINDOW, "edit-on-plot": set()}
        assert "Click the plot" not in _hint(user, POINTS)
        # ... and a ⌖ that is drawn takes its click, mid-run.
        _press(user, card, "edit-criteria", ("window", "start"))
        with page.client:
            page.on_plot("clicked", _click(card, 0.31234, 0.3))
        (op,) = shell.session.spec.pending
        assert op.value["window"] == {"start": 0.312}
        assert card.placing.armed is None


@pytest.mark.parametrize("ended", [False, True], ids=["running", "cancelled"])
async def test_an_unscored_tests_editor_offers_no_click_that_nothing_takes(live, ended):
    """While a test runs, and after a run that did not finish, nothing of
    it is scored (D151): no handle is drawn and the table's place is a
    sentence. The hint offered "Ctrl+click adds a point." all the same,
    and the sentence was "/metrics/children/0 is not a criterion on x"."""
    shell, examples = live
    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, POINTS)
        assert "Ctrl+click adds a point." in _hint(user, POINTS)
        steer(examples, A, hold=3)
        shell.session.enqueue([A])
        await _running(shell, page)
        word = WAITING
        if ended:
            # Once its file holds a row: a partial result is what it wrote.
            # (A second's poll of a file a subprocess has to start writing:
            # three seconds were not enough on a loaded machine.)
            await _settled(
                lambda: _ticked(shell) and "t = " in page.strip.text.text, tries=600
            )
            user.find(marker="strip-cancel").click()
            await _settled(
                lambda: _ticked(shell) and shell.session.partial(A), tries=600
            )
            word = NOT_SCORED
        (card,) = [c for c in page.cards if c.subject == ["x"]]
        await _settled(
            lambda: _ticked(shell) and page.open == (card.index, POINTS), tries=600
        )
        assert _hint(user, POINTS) == "Edit the fields." + STAGED
        (why,) = user.find(marker="why:edit-on-plot").elements
        assert why.text == UNSCORED.format(word)
        with page.client:
            page.on_plot("clicked", _click(card, 0.9, 0.0, ctrl=True))
        assert shell.session.spec.pending == []


async def test_a_new_drags_preview_shows_though_its_value_is_the_last_ones(shell):
    """The field compares the DRAG's number with the one it was typed in
    during: the same value previewed again on a new drag is a new preview."""
    async with user_simulation(root=shell.index) as user:
        _page, _card = await _opened(shell, user, POINTS)
        cell = _cell(user, POINTS, "points", 0, "time")
        cell.preview(0.5, 7)
        assert (cell.previewed, cell.preview_revision) == (0.5, 7)
        cell.preview(0.5, 8)
        assert (cell.previewed, cell.preview_revision) == (0.5, 8)
        cell.preview(None, 8)
        assert cell.previewed is None


async def test_a_pairs_preview_shows_each_side_its_own(shell):
    async with user_simulation(root=shell.index) as user:
        _page, _card = await _opened(shell, user, POINTS)
        lower = _cell(user, POINTS, "points", 0, "tolerance:lower")
        upper = _cell(user, POINTS, "points", 0, "tolerance:upper")
        group = lower.parent_slot.parent
        group.preview({"lower": 0.11, "upper": 0.44}, 3)
        assert (lower.previewed, upper.previewed) == (0.11, 0.44)
        # A side that would not change shows what it holds.
        group.preview({"lower": 0.3, "upper": 0.44}, 3)
        assert (lower.previewed, upper.previewed) == (None, 0.44)


async def test_a_refused_value_logs_one_line_and_no_traceback(shell, caplog):
    """A refusal is no error: a negative tolerance typed, a click out of
    bounds. Each logged "edit-criteria failed" with a traceback."""
    import logging

    async with user_simulation(root=shell.index) as user:
        page, card = await _opened(shell, user, RANGE)
        (low,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, TypedNumber)
            and e.props.get("label") == "Lower bound (optional)"
        ]
        with caplog.at_level(logging.DEBUG), page.client:
            low.set_value(9.0)  # above the upper bound: refused
            await _settled(lambda: card.editor.errors["edit-criteria"].visible)
            _press(user, card, "edit-criteria", ("max_value",))
            page.on_plot("clicked", _click(card, 0.5, -3.0))  # under the lower
        assert shell.session.spec.pending == []
        ours = [r for r in caplog.records if r.name.startswith("dstf")]
        assert [r for r in ours if r.exc_info] == []
        assert len([r for r in ours if "edit-criteria" in r.getMessage()]) <= 1
