"""The Test page (brief §3.2; roadmap §3): the sticky test header, the
criteria tree closed to one line, the plots drawn on scroll with their
criteria rows, the one open editor below its row, the page following the
Session plot by plot. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import copy
import json
import logging

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from capability_host import open_menu  # noqa: E402
from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Artifact, Job, Placement  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell import plot_card  # noqa: E402
from dstf.authoring.shell.actions import ActionBar, InlineHost  # noqa: E402
from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
from dstf.authoring.shell.tree_card import leaves_of  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import LOOSEN, A, B, C, D, _Gate, _two_var_project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    capabilities._reset_for_tests()
    register_builtins()
    yield
    capabilities._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _two_var_project(tmp_path)  # A tracks x and y, FAIL; D tracks x, PASS
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


def _none(user, marker: str) -> bool:
    try:
        user.find(marker=marker)
    except AssertionError:
        return True
    return False


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _marks(element) -> list[str]:
    """Every marker under ``element``, in document order."""
    out: list[str] = []
    for slot in element.slots.values():
        for child in slot.children:
            out.extend(child._markers)
            out.extend(_marks(child))
    return out


def _ancestors(element):
    while element.parent_slot is not None:
        element = element.parent_slot.parent
        yield element


def _inside(user, marker: str, within: str) -> list:
    """Every element marked ``marker`` inside the host marked ``within``,
    empty when there is none: the same node's controls are drawn in a tree
    row and in a criteria row, so a bare find cannot say which is gone."""
    if _none(user, marker):
        return []
    host = _one(user, within)
    return [e for e in user.find(marker=marker).elements if host in list(_ancestors(e))]


def _click_in(user, marker: str, within: str) -> None:
    """Click the element marked ``marker`` INSIDE the host marked
    ``within``: a tree row and a criteria row carry the same node controls,
    and `User.find(...).click()` clicks the lowest id of them."""
    from nicegui.testing.user_interaction import UserInteraction

    host = _one(user, within)
    (element,) = [
        e for e in user.find(marker=marker).elements if host in list(_ancestors(e))
    ]
    UserInteraction(user, {element}, None).click()


class _Probe:
    """A capability the shell has never heard of: a label, a submit button,
    and a figure subscription when the placement has a figure."""

    label = "Probe"

    def __init__(
        self,
        name,
        placements,
        *,
        answer=True,
        raises=None,
        subscribe=False,
        boom=None,
        outcome=None,
    ):
        self.name, self.placements = name, frozenset(placements)
        self.answer, self.raises, self.subscribe = answer, raises, subscribe
        self.boom, self.outcome = boom, outcome
        self.seen: list[tuple[str, dict]] = []

    def available(self, s):
        return self.answer

    def caption(self, s):
        return f"Probe {self.name}"

    def panel(self, s, ui_panel):
        if self.boom:
            raise RuntimeError(self.boom)
        ui.label(
            f"probe {self.name} {s.selection.leaf_path or s.selection.node_path}"
        ).mark(f"probe:{self.name}")
        if self.subscribe:
            ui_panel.figure.on("plotly_relayout", lambda h, p: self.seen.append((h, p)))
        ui.button("go", on_click=lambda: ui_panel.submit()).mark(f"go:{self.name}")

    def act(self, s, **inputs):
        if self.raises:
            raise ValueError(self.raises)
        return self.outcome


def _only(*caps):
    capabilities._reset_for_tests()
    for cap in caps:
        capabilities.register_capability(cap)


LEAF = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0")
#: Something the Session applies without touching the spec or the store —
#: what a probe's act returns when the point is that it DID apply something.
OUTCOME = Artifact(paths=[], label="probe")
RELAYOUT = {"shapes[0].y0": 1}


def _hosted(shell, placement=Placement.CRITERION, figure=None):
    """A bare page holding one InlineHost; the host is `box[0]` once built."""
    box: list[InlineHost] = []

    def root() -> None:
        box.append(
            InlineHost(shell, placement, lambda: LEAF, figure=figure, marker="editor")
        )
        box[0].build()

    return box, root


def _applying(shell, on_applied):
    """`_hosted` with an `on_applied` to record."""
    box: list[InlineHost] = []

    def root() -> None:
        box.append(
            InlineHost(
                shell,
                Placement.CRITERION,
                lambda: LEAF,
                figure=None,
                marker="editor",
                on_applied=on_applied,
            )
        )
        box[0].build()

    return box, root


def _barred(shell, figure):
    """A bare page holding one ActionBar over a variable; `bars[0]` once
    built."""
    bars: list[ActionBar] = []

    def root() -> None:
        bars.append(
            ActionBar(
                shell,
                Placement.VARIABLE,
                lambda: Selection(tests=frozenset({A}), variable="x"),
                marker="variable-actions",
                figure=figure,
            )
        )

    return bars, root


async def test_an_inline_host_builds_every_placed_panel_without_a_button(shell):
    _only(
        _Probe("one", {Placement.CRITERION}),
        _Probe("suite", {Placement.SUITE}),
        _Probe("two", {Placement.CRITERION}),
    )
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see(marker="probe:one")
        panels = [m for m in _marks(_one(user, "editor")) if m.startswith("panel:")]
        assert panels == ["panel:one", "panel:two"]  # registration order
        assert _one(user, "probe:one").text == "probe one /metrics/children/0"
        assert _none(user, "probe:suite")
        assert _none(user, "action:one") and _none(user, "action:two")


async def test_an_unavailable_inline_capability_says_why_in_place(shell):
    _only(_Probe("one", {Placement.CRITERION}, answer="needs a baseline"))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        assert _one(user, "why:one").text == "needs a baseline"
        assert _none(user, "probe:one")


async def test_an_act_error_shows_above_its_panel_and_a_success_clears_it(shell):
    probe = _Probe("one", {Placement.CRITERION}, raises="no")
    _only(probe, _Probe("two", {Placement.CRITERION}))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        go = _one(user, "go:one")
        user.find(marker="go:one").click()
        await _settled(lambda: box[0].errors["one"].text == "ValueError: no")
        assert box[0].errors["one"].visible
        marks = _marks(_one(user, "editor"))
        assert marks.index("error:one") < marks.index("panel:one")
        probe.raises = None
        user.find(marker="go:one").click()
        # read through the label: `User.find` excludes hidden elements
        await _settled(lambda: not box[0].errors["one"].visible)
        assert _one(user, "go:one") is go  # the panel was not rebuilt
        two = _one(user, "go:two")
        box[0].build(skip="one")
        assert _one(user, "go:one") is go  # the skipped cell is left untouched
        assert _one(user, "go:two") is not two  # every other cell is rebuilt


async def test_a_panel_that_raises_is_shown_in_its_slot(shell):
    _only(
        _Probe("bad", {Placement.CRITERION}, boom="boom"),
        _Probe("good", {Placement.CRITERION}),
    )
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see("RuntimeError: boom")
        (label,) = user.find("RuntimeError: boom").elements
        assert "text-negative" in label.classes
        assert label.parent_slot.parent is _one(user, "panel:bad")
        await user.should_see(marker="probe:good")


async def test_clear_removes_the_hosts_handlers_from_the_hook(shell):
    probe = _Probe("one", {Placement.CRITERION}, subscribe=True)
    _only(probe)
    hook = FigureHook()
    box, root = _hosted(shell, figure=hook)
    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see(marker="probe:one")
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 1
        box[0].clear()
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 1
        assert _none(user, "probe:one")
        box[0].build()
        box[0].build()
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 2  # once per dispatch: no accumulation


async def test_a_skipping_rebuild_drops_the_rebuilt_panels_handlers(shell):
    """The path a submit takes: `build(skip=<the submitter>)` rebuilds every
    OTHER panel, and a subscriber among them must not accumulate."""
    two = _Probe("two", {Placement.CRITERION}, subscribe=True)
    _only(_Probe("one", {Placement.CRITERION}), two)
    hook = FigureHook()
    box, root = _hosted(shell, figure=hook)
    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see(marker="probe:two")
        box[0].build(skip="one")
        box[0].build(skip="one")
        hook._dispatch("plotly_relayout", RELAYOUT)
        assert len(two.seen) == 1


async def test_a_build_after_clear_places_the_panels_in_the_column_again(shell):
    """`clear()` empties the column AND forgets its cells: the next build
    puts live ones back, not writes into the removed ones."""
    _only(_Probe("one", {Placement.CRITERION}))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        box[0].clear()
        box[0].build()
        panel = _one(user, "probe:one")
        assert not panel.is_deleted and box[0].column in list(_ancestors(panel))


async def test_the_first_build_draws_the_skipped_capability_too(shell):
    """There is no cell to leave untouched yet, so `skip` names nothing."""
    _only(_Probe("one", {Placement.CRITERION}), _Probe("two", {Placement.CRITERION}))
    box: list[InlineHost] = []

    def root() -> None:
        box.append(
            InlineHost(
                shell, Placement.CRITERION, lambda: LEAF, figure=None, marker="editor"
            )
        )
        box[0].build(skip="one")

    async with user_simulation(root=root) as user:
        await user.open("/")
        assert not _none(user, "probe:one")


async def test_an_error_label_starts_hidden(shell):
    _only(_Probe("one", {Placement.CRITERION}))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see(marker="probe:one")
        assert not box[0].errors["one"].visible


async def test_a_capability_that_answers_false_says_not_available(shell):
    """`available` may answer with a sentence or with a bare False; the bare
    one gets the page's own sentence, and no panel either way."""
    _only(_Probe("off", {Placement.CRITERION}, answer=False))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        assert _one(user, "why:off").text == "not available"
        assert _none(user, "probe:off")


async def test_on_applied_names_the_capability_whose_act_applied_something(shell):
    one = _Probe("one", {Placement.CRITERION}, raises="no", outcome=OUTCOME)
    _only(one, _Probe("two", {Placement.CRITERION}, outcome=OUTCOME))
    applied: list[str] = []
    box, root = _applying(shell, applied.append)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="go:one").click()
        await _settled(lambda: box[0].errors["one"].visible)
        assert applied == []  # a raising act applied nothing
        user.find(marker="go:two").click()
        await _settled(lambda: len(applied) == 1)
        assert applied == ["two"]
        one.raises = None
        user.find(marker="go:one").click()
        await _settled(lambda: len(applied) == 2)
        assert applied == ["two", "one"]


async def test_an_act_that_returned_nothing_leaves_on_applied_alone(shell):
    """An act returning None changed nothing, so nothing needs redrawing —
    and a rebuild would take the row being typed in with it."""
    _only(
        _Probe("one", {Placement.CRITERION}),  # its act returns None
        _Probe("two", {Placement.CRITERION}, outcome=OUTCOME),
    )
    applied: list[str] = []
    box, root = _applying(shell, applied.append)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="go:one").click()
        user.find(marker="go:two").click()  # the fence: this one DID apply
        await _settled(lambda: "two" in applied)
        assert applied == ["two"]


async def test_follow_rebuilds_a_host_only_when_an_available_answer_moved(shell):
    """A host that is never rebuilt — a tree row's, a criteria row's —
    would keep the answer of the moment it was drawn. `follow` re-reads
    them and rebuilds when one moved, and leaves the host alone when none
    did: a rebuild on every Session event is what the row exists to avoid."""
    probe = _Probe("one", {Placement.CRITERION})
    _only(probe)
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        panel = _one(user, "probe:one")
        box[0].follow()
        assert _one(user, "probe:one") is panel  # nothing moved, nothing rebuilt
        probe.answer = "rerun is running on FakeLib.A"
        box[0].follow()
        assert _none(user, "probe:one")
        assert _one(user, "why:one").text == "rerun is running on FakeLib.A"
        probe.answer = True
        box[0].follow()
        assert _none(user, "why:one") and _one(user, "probe:one") is not panel


async def test_an_action_bars_dialog_closes_only_for_an_act_that_applied(shell):
    """Like `InlineHost.on_applied`: every plot sends `{"autosize": true}`
    at mount, and a panel that answers such a relayout with None changed
    nothing — the dialog it sits in must stay open."""
    probe = _Probe("one", {Placement.VARIABLE})  # its act returns None
    _only(probe)
    bars, root = _barred(shell, None)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:one").click()
        await user.should_see(marker="probe:one")
        user.find(marker="go:one").click()
        await asyncio.sleep(0.1)
        assert bars[0].dialog.value is True  # nothing applied, nothing to close over
        probe.outcome = OUTCOME
        user.find(marker="go:one").click()
        await _settled(lambda: bars[0].dialog.value is False)


async def test_an_action_bar_hands_its_figure_to_the_dialog_panel(shell):
    probe = _Probe("one", {Placement.VARIABLE}, subscribe=True)
    _only(probe)
    hook = FigureHook()

    def root() -> None:
        ActionBar(
            shell,
            Placement.VARIABLE,
            lambda: Selection(tests=frozenset({A}), variable="x"),
            marker="variable-actions",
            figure=hook,
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:one").click()
        await user.should_see(marker="probe:one")
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 1
        user.find(marker="close:one").click()
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 1  # Close dropped the subscription
        user.find(marker="action:one").click()
        user.find(marker="action:one").click()
        await asyncio.sleep(0.1)
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})
        assert len(probe.seen) == 2  # opened twice, delivered once


async def test_a_dismissed_dialog_drops_its_handlers(shell):
    """Esc and a click on the backdrop close the dialog without going
    through Close: a panel nobody can see must not stage an edit from a
    drag."""
    probe = _Probe("one", {Placement.VARIABLE}, subscribe=True)
    _only(probe)
    hook = FigureHook()
    bars, root = _barred(shell, hook)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:one").click()
        await user.should_see(marker="probe:one")
        hook._dispatch("plotly_relayout", RELAYOUT)
        assert len(probe.seen) == 1
        bars[0].dialog.close()  # what a dismissal amounts to: no Close click
        await _settled(lambda: not bars[0].dialog.value)
        hook._dispatch("plotly_relayout", RELAYOUT)
        assert len(probe.seen) == 1


async def test_opening_another_capability_takes_the_figure_from_the_first(shell):
    one = _Probe("one", {Placement.VARIABLE}, subscribe=True)
    two = _Probe("two", {Placement.VARIABLE}, subscribe=True)
    _only(one, two)
    hook = FigureHook()
    bars, root = _barred(shell, hook)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:one").click()
        await user.should_see(marker="probe:one")
        user.find(marker="action:two").click()
        await user.should_see(marker="probe:two")
        hook._dispatch("plotly_relayout", RELAYOUT)
        assert one.seen == [] and len(two.seen) == 1
        owners = {o for entries in hook._handlers.values() for o, _ in entries}
        assert owners == {"variable-actions:two"}


# -- the page's frame: the sticky test header and the criteria tree ---------


def _page(shell, user) -> TestPage:
    (page,) = [
        p for p in shell.pages if isinstance(p, TestPage) and p.client is user.client
    ]
    return page


async def _test_page(shell, user, model=A) -> TestPage:
    """Open the Test page for ``model`` in the simulation already running:
    `Shell.mount()` is once per Shell, so a test over two models reopens
    inside the same simulation."""
    shell.mount()
    await _open(user, f"/test/{model}")
    return _page(shell, user)


def _stage_tree(shell, model=D, tree=None):
    shell.session.stage(
        [PatchOp(model, "add", "/metrics", copy.deepcopy(tree if tree else TREE))]
    )


def _range(lo):
    return {"metric": "range", "variable": "x", "min_value": lo}


def _plot(*names):
    return {"series": [{"var": n} for n in names]}


async def _menu(user, marker: str, within: str) -> None:
    """Open the cluster's menu marked ``marker`` and wait for its content:
    an Edit tree menu is built when the menu opens, which under the user
    simulation is `menu.open()` — a click moves no menu's value."""
    open_menu(user, marker, within=within)
    kind, path = marker.split(":", 1)
    await _settled(lambda: not _none(user, f"apply-{kind}:{path}"))


async def test_the_test_header_is_a_second_row_of_the_shared_header(shell):
    """Decision 8: a second row INSIDE `ui.header`, so it is sticky with no
    offset arithmetic and no dependence on the first row's height."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        sub = _one(user, "subheader")
        for marker in ("back", "model", "status", "test-actions"):
            assert sub in list(_ancestors(_one(user, marker))), marker
        header = sub.parent_slot.parent
        assert isinstance(header, ui.header)
        assert header in list(_ancestors(_one(user, "wordmark")))
        assert _one(user, "status").text == "FAIL"
        assert _one(user, "meta").text == "Python · ref_0001"
        await _test_page(shell, user, B)
        assert _one(user, "meta").text == "Python · no baseline"


async def test_the_tree_loads_closed_to_one_line_naming_what_failed(shell):
    """Board 1A: closed to one line whose tally stops at the counts — each
    failure's name is drawn ONCE, as the button that jumps to it."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        # The implicit tree is ALWAYS an `and`; A tracks x and y, both failing.
        assert _one(user, "tree:root").text == "and · 2 criteria"
        assert _one(user, "tree:summary").text == "0 pass · 2 fail"
        assert sorted(j.text for j in user.find(marker="tree:jump").elements) == [
            "nrmse · x",
            "nrmse · y",
        ]
        assert _none(user, "tree:more")
        assert page.tree.rows.visible is False
        assert "dstf-pill-fail" in page.tree.root_pill.label.classes
        page = await _test_page(shell, user, D)
        assert _one(user, "tree:root").text == "and · 1 criterion"
        assert _one(user, "tree:summary").text == "1 pass"
        assert _none(user, "tree:jump") and _none(user, "tree:more")


async def test_the_root_line_says_what_the_root_is_and_counts_the_leaves(shell):
    """A staged root leaf IS the test's one criterion (its name is already
    the line's jump); a combinator counts the leaves under it, however deep
    — `and[k-of-n[l, l], l]` is three criteria, not two children."""
    nested = {
        "combinator": "and",
        "children": [
            {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(-2)]},
            _range(-3),
        ],
    }
    async with user_simulation(root=shell.index) as user:
        _stage_tree(shell, D, nested)
        await _test_page(shell, user, D)
        assert _one(user, "tree:root").text == "and · 3 criteria"
        shell.session.discard()
        shell.session.stage(
            [PatchOp(A, "add", "/metrics", {"metric": "nrmse", "variable": "x"})]
        )
        await _test_page(shell, user, A)
        assert _one(user, "tree:root").text == "1 criterion"
        assert _one(user, "tree:summary").text == "0 pass · 1 fail"
        assert [j.text for j in user.find(marker="tree:jump").elements] == ["nrmse · x"]


async def test_the_summary_names_the_first_failures_and_counts_the_rest(shell):
    four = {"combinator": "and", "children": [_range(-1)] + [_range(5)] * 4}
    advisory = {
        "combinator": "and",
        "children": [{"combinator": "warn", "children": [_range(5)]}, _range(-1)],
    }
    # Hard failures are named before advisory ones, whatever the tree order.
    mixed = {
        "combinator": "and",
        "children": [
            {"combinator": "warn", "children": [_range(5)]},
            {"combinator": "warn", "children": [_range(6)]},
            {"metric": "range", "variable": "x", "max_value": -9},
        ],
    }
    async with user_simulation(root=shell.index) as user:
        _stage_tree(shell, D, four)
        page = await _test_page(shell, user, D)
        assert _one(user, "tree:summary").text == "1 pass · 4 fail"
        assert len(user.find(marker="tree:jump").elements) == 2
        assert _one(user, "tree:more").text == "+2 more"
        shell.session.discard()
        _stage_tree(shell, D, advisory)
        page = await _test_page(shell, user, D)
        assert _one(user, "tree:summary").text == "1 pass · 1 advisory"
        assert [j.text for j in user.find(marker="tree:jump").elements] == ["range · x"]
        assert _none(user, "tree:more")
        assert "dstf-pill-pass" in page.tree.root_pill.label.classes
        shell.session.discard()
        _stage_tree(shell, D, mixed)
        page = await _test_page(shell, user, D)
        assert _one(user, "tree:summary").text == "0 pass · 1 fail · 2 advisory"
        assert _one(user, "tree:more").text == "+1 more"
        assert page.tree.named[0]["path"] == "/metrics/children/2"
        assert "dstf-pill-fail" in page.tree.root_pill.label.classes


async def test_show_tree_opens_the_rows_with_controls_on_every_node(shell):
    """Board 1B: open, a row per node with that node's controls — which are
    whatever attaches at NODE, never Edit tree by name."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        assert _one(user, "tree:toggle").text == "Show tree"
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        # The line is rebuilt on toggle: re-find the button before reading it.
        assert _one(user, "tree:toggle").text == "Hide tree"
        for path in ["/metrics"] + [f"/metrics/children/{i}" for i in range(6)]:
            row = _one(user, f"tree-row:{path}")
            node = _one(user, f"node:{path}")
            assert row in list(_ancestors(node)), path
            assert not _none(user, f"tree-pill:{path}"), path
            (wrap,) = [
                e
                for e in user.find(marker=f"wrap:{path}").elements
                if node in list(_ancestors(e))
            ]
            del wrap
        assert _none(user, "remove:/metrics")  # the root is removed from nothing
        user.find(marker="tree:toggle").click()
        await _settled(lambda: not page.tree.rows.visible)


async def test_a_tree_rows_controls_follow_the_job_that_locked_the_test(shell):
    """A tree opened DURING a rerun drew its rows on "rerun is running on
    …"; the rows are not rebuilt when the job ends, so without `follow`
    that sentence stood for the rest of the page's life."""
    _stage_tree(shell)
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
        await _settled(lambda: shell.session.running is not None)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        host = "node:/metrics"
        assert _inside(user, "wrap:/metrics", host) == []
        (why,) = _inside(user, "why:edit-tree", host)
        assert why.text == f"rerun is running on {D}"
        gate.release.set()
        await _settled(lambda: bool(_inside(user, "wrap:/metrics", host)))
        assert _inside(user, "why:edit-tree", host) == []


async def test_a_criteria_rows_controls_follow_the_lock_the_other_way(shell):
    """The reverse: a row drawn before the job kept clickable controls
    under the lock, and the error a click earned then stayed on the row."""
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        host = f"node:0:{T0}"
        assert _inside(user, f"wrap:{T0}", host)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await _settled(lambda: _inside(user, f"wrap:{T0}", host) == [])
        (why,) = _inside(user, "why:edit-tree", host)
        assert why.text == f"rerun is running on {A}"
        gate.release.set()
        await _settled(lambda: bool(_inside(user, f"wrap:{T0}", host)))


async def test_a_wrap_from_the_tree_restages_the_rows(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        host = "node:/metrics/children/1"
        await _menu(user, "wrap:/metrics/children/1", within=host)
        _click_in(user, "apply-wrap:/metrics/children/1", within=host)
        await _settled(
            lambda: not _none(user, "tree-row:/metrics/children/1/children/0")
        )
        assert page.tree.rows.visible  # the tree stays OPEN


async def test_the_rows_are_rebuilt_only_when_the_tree_s_shape_moved(shell):
    """A verdict that flips repaints the row that is already there; a wrap
    moves the shape (paths, kinds, variables) and rebuilds the rows."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        row = _one(user, "tree-row:/metrics/children/0")
        pill = _one(user, "tree-pill:/metrics/children/0")
        assert "dstf-pill-fail" in pill.classes
        shell.session.stage([LOOSEN])  # x passes now; the shape did not move
        await _settled(lambda: "dstf-pill-pass" in pill.classes)
        # The status it had is GONE: `.dstf-pill-fail` is declared after
        # `-pass`, so a pill carrying both reads PASS in red.
        assert "dstf-pill-fail" not in pill.classes
        assert _one(user, "tree-row:/metrics/children/0") is row
        assert _one(user, "tree-pill:/metrics/children/0") is pill
        host = "node:/metrics/children/1"
        await _menu(user, "wrap:/metrics/children/1", within=host)
        _click_in(user, "apply-wrap:/metrics/children/1", within=host)
        await _settled(
            lambda: not _none(user, "tree-row:/metrics/children/1/children/0")
        )
        assert _one(user, "tree-row:/metrics/children/0") is not row


async def test_a_repainted_row_says_the_new_readout_the_new_text_and_the_new_k(shell):
    """A row that is not rebuilt must have everything it says written into
    it: the CLI's readout, the node's text, and a k-of-n's own k among it."""
    nested = {
        "combinator": "and",
        "children": [
            {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(5)]},
            {"metric": "nrmse", "variable": "x"},
        ],
    }
    _stage_tree(shell, A, nested)
    leaf, combinator = "/metrics/children/1", "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        row = _one(user, f"tree-row:{leaf}")
        readout = page.tree.painters[leaf].readout
        assert "tolerance 1.000e-02" in readout.text
        shell.session.stage([PatchOp(A, "add", f"{leaf}/tolerance", 0.5)])
        await _settled(lambda: "tolerance 5.000e-01" in readout.text)
        assert _one(user, f"tree-row:{leaf}") is row  # painted, not rebuilt
        text = page.tree.painters[combinator].text
        assert text.text == "k-of-n · 2 criteria · k=1"
        krow = _one(user, f"tree-row:{combinator}")
        shell.session.stage([PatchOp(A, "replace", f"{combinator}/k", 2)])
        await _settled(lambda: text.text == "k-of-n · 2 criteria · k=2")
        assert _one(user, f"tree-row:{combinator}") is krow


async def test_a_rows_indent_is_its_depth_in_the_tree(shell):
    """The rows are one flat column: the indent is the only thing that says
    what is under what."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        root = _one(user, "tree-row:/metrics")
        child = _one(user, "tree-row:/metrics/children/0")
        assert dict(root.style).get("padding-left") == "0px"
        assert dict(child.style).get("padding-left") == "16px"


async def test_the_closed_line_follows_the_session_with_the_tree_hidden(shell):
    """The line is five elements and is rebuilt on every sync, open or
    closed: a tree hidden again must not freeze on the verdict it had."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: not page.tree.rows.visible)
        assert _one(user, "tree:summary").text == "0 pass · 2 fail"
        shell.session.stage([LOOSEN])  # x passes now
        await _settled(lambda: _one(user, "tree:summary").text == "1 pass · 1 fail")
        assert [j.text for j in user.find(marker="tree:jump").elements] == ["nrmse · y"]


async def test_the_jump_names_the_failing_criterion(shell):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        jumps = user.find(marker="tree:jump").elements
        assert sorted(j.text for j in jumps) == ["nrmse · x", "nrmse · y"]
        assert all(isinstance(j, ui.button) for j in jumps)
        # Two criteria on one variable read identically: the tooltip is the
        # tree path, which is what tells them apart.
        tips = sorted(
            t.text for j in jumps for t in j.descendants() if isinstance(t, ui.tooltip)
        )
        assert tips == ["/metrics/children/0", "/metrics/children/1"]


def test_the_closed_lines_tally_counts_each_kind_of_leaf():
    """The tally alone (board 1A): pass always, then the kinds that are
    non-zero — an unscored leaf is one whose `passed` is None, and the
    failures over the two the line names are left to `+k more`."""
    from dstf.authoring.shell.tree_card import summary_text

    def leaf(passed, advisory=False, path="/p"):
        return {
            "kind": "leaf",
            "path": path,
            "metric": "range",
            "variable": "x",
            "passed": passed,
            "advisory": advisory,
        }

    def tree(*children):
        return {"kind": "combinator", "path": "/metrics", "children": list(children)}

    assert summary_text(tree(leaf(True), leaf(True))) == ("2 pass", [], 0)
    text, named, more = summary_text(
        tree(leaf(True), leaf(False), leaf(False, True), leaf(None))
    )
    assert text == "1 pass · 1 fail · 1 advisory · 1 unscored"
    assert [n["passed"] for n in named] == [False, False]  # hard, then advisory
    assert more == 0
    text, named, more = summary_text(
        tree(*[leaf(False, path=f"/{i}") for i in range(5)])
    )
    assert (text, [n["path"] for n in named], more) == (
        "0 pass · 5 fail",
        ["/0", "/1"],
        3,
    )
    assert summary_text(tree(leaf(None))) == ("0 pass · 1 unscored", [], 0)


async def test_a_test_with_nothing_scored_says_so(shell):
    async with user_simulation(root=shell.index) as user:
        for model, text in (
            (B, "nothing scored: this test has no baseline"),
            (C, "nothing scored: this test has no result"),
        ):
            await _test_page(shell, user, model)
            assert _one(user, "tree:summary").text == text
            assert _none(user, "tree:toggle")


async def test_a_test_whose_scoring_raised_does_not_say_no_baseline(shell, monkeypatch):
    """A model whose scoring raised keeps its result and has no comparison
    at all (the Suite row says `scoring failed` for the same state); D has
    a baseline, so "no baseline" would name the wrong cause."""
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)
    with pytest.raises(RuntimeError):
        shell.session._reload_results()
    assert D not in shell.session.comparisons
    assert shell.session.project.store.index.get_id(D) is not None
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert _one(user, "tree:summary").text == "nothing scored: scoring failed"
        # The baseline is right there — the id comes from the index alone.
        assert _one(user, "meta").text.startswith("Python · ref_")


async def test_an_unknown_model_keeps_the_stub_message(shell):
    async with user_simulation(root=shell.index) as user:
        shell.mount()
        await _open(user, "/test/FakeLib.Nope")
        await user.should_see("FakeLib.Nope is not a test of this project")
        # The frame is built — that is how a page recovers when the model
        # turns up — and it says nothing about a test this project has not
        # got: no tree, no cards, and a blank sub-header.
        page = _page(shell, user)
        assert page.gone and page.cards == []
        assert _none(user, "tree:summary") and _none(user, "plot-slot:0")
        assert page.badge.text == "" and page.meta.text == ""
        assert page.actions.actions == {}


def test_the_readout_says_the_criterion_once():
    """The first screenshot's defect: `criterion` already carries the score,
    so printing the display beside it said the number twice. The verdict is
    not stripped off the end — `tube` and `range` do not end with it."""
    from dstf.authoring.shell.tree_card import leaf_readout

    leaf = {
        "score_display": "NRMSE 1.531e-01",
        "criterion": "NRMSE 1.531e-01 vs tolerance 1.000e-02 → FAIL",
    }
    assert leaf_readout(leaf) == leaf["criterion"]
    assert leaf_readout({"score_display": "not scored"}) == "not scored"
    assert leaf_readout({}) == ""


async def test_a_sentinel_leafs_readout_is_the_criterion_that_says_the_reason(shell):
    """A leaf the scorer could not score carries its reason IN the
    criterion ("scalar leaf not scored — …"), so the error is not a branch
    of its own: reading it there said the reason without the mode."""
    sentinel = {
        "combinator": "and",
        "children": [
            _range(-1),
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": "no.such.path",
                "min_value": 0,
            },
        ],
    }
    _stage_tree(shell, D, sentinel)
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        from dstf.authoring.shell.tree_card import leaf_readout, leaves_of

        scored, unscorable = leaves_of(shell.session.tree_view(D))
        assert leaf_readout(unscorable).startswith("scalar leaf not scored — ")
        assert unscorable["error"] not in ("", None)
        assert leaf_readout(unscorable) == unscorable["criterion"]
        assert _one(user, f"criterion-readout:0:{T1}").text == leaf_readout(unscorable)
        assert leaf_readout(scored) == scored["criterion"]


# -- the plot cards ----------------------------------------------------------

from test_session import LAYOUT, _plots_project  # noqa: E402

T0, T1 = "/metrics/children/0", "/metrics/children/1"


@pytest.fixture
def plots_shell(tmp_path):
    """`_plots_project`: A's result carries an UNTRACKED `z`, so a layout can
    draw a plot-only series beside a scored one."""
    cfg, work = _plots_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


def _plotlys(page) -> list:
    return [e for e in page.client.elements.values() if isinstance(e, ui.plotly)]


def _sans_rev(figure: dict) -> dict:
    """A card's figure without the `editrevision` the card stamps on it: the
    Session's figure is what the rest of it has to equal."""
    return {
        **figure,
        "layout": {k: v for k, v in figure["layout"].items() if k != "editrevision"},
    }


async def test_a_test_shows_one_card_per_tracked_variable(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "x"
        assert _one(user, "plot-title:1").text == "y"
        assert _one(user, "plots:count").text == "2 plots · one per tracked variable"
        # Nothing is drawn until the browser says a slot is near the viewport.
        assert [c.plot for c in page.cards] == [None, None]
        assert _plotlys(page) == []
        slot = _one(user, "plot-slot:0")
        assert "dstf-plot-slot" in slot.classes and slot.props["data-plot"] == "0"


@pytest.fixture
def awkward_expression_shell(tmp_path):
    """A's first variable is a UnitTests one — named `unitTests.x[1]` by its
    result file, with `cat(1, a, b)[1]` as the expression the model wrote
    for it. TRANSFORM has ~70 tests of that shape. The variable is scored
    and drawn under its name; the expression is a label."""
    from test_session import (
        AWKWARD_EXPRESSION,
        UT1,
        _awkward_expression_project,
        with_expressions,
    )

    cfg, work = _awkward_expression_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    with_expressions(s.session, A, {UT1: AWKWARD_EXPRESSION})
    # The comparisons `open` built were made before the expression was
    # stamped, and a leaf's label comes from the comparison it scored.
    # Scoring again is what a run over a Modelica project produces.
    s.session._score(A)
    s.load.phase = "done"
    return s


async def test_an_awkward_expression_gets_one_card_not_two(awkward_expression_shell):
    """The tracked list read the scored names and the result's names under
    two spellings: the variable appeared under both, so the page drew a
    third card with no criteria row and its own "Add a criterion for …"
    footer, over a trajectory already on the card above it (the 3b review).
    One name, one card."""
    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.series for c in page.cards] == [("unitTests.x[1]",), ("y",)]
        assert _one(user, "plots:count").text == "2 plots · one per tracked variable"
        assert _none(user, "variable-actions:2:unitTests.x[1]")
        with page.client:
            page.cards[0].show()
        kinds = [tr["meta"]["kind"] for tr in page.cards[0].plot.figure["data"]]
        assert "act" in kinds and "ref" in kinds  # the name resolves its data


async def test_a_card_reads_the_label_and_is_keyed_by_the_name(
    awkward_expression_shell,
):
    """The title is the expression the model wrote; the result-file name —
    what the criterion, the override and the marker are written against —
    rides beside it, muted. A variable with no expression has no second
    text at all."""
    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "cat(1, a, b)[1]"
        assert "dstf-varname" in _one(user, "plot-name:0").classes
        assert _one(user, "plot-name:0").text == "unitTests.x[1]"
        assert _one(user, "plot-title:1").text == "y"
        assert _none(user, "plot-name:1")
        # Every key is the name: the card's markers, and the footer's.
        assert _one(user, "plot:0") and _one(user, "plot-slot:0")
        footer = _one(user, "variable-actions:0:unitTests.x[1]")
        (button,) = [
            e
            for e in user.find(marker="action:edit-tree").elements
            if footer in list(_ancestors(e))
        ]
        assert button.text == "Add a criterion for cat(1, a, b)[1]"


async def test_a_criteria_row_reads_the_label(awkward_expression_shell):
    """`nrmse · cat(1, a, b)[1]` — the same text the closed tree line's jump
    button carries, from the same function."""
    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        (row,) = page.cards[0].rows.values()
        assert row.name.text == "nrmse · cat(1, a, b)[1]"
        assert row.variable == "unitTests.x[1]"  # the key the op targets


async def test_show_builds_the_plot_once_with_no_rings(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
            plot = page.cards[0].plot
            assert _sans_rev(plot.figure) == shell.session.plot_figure(A, 0, editing=())
            page.cards[0].show()
        assert page.cards[0].plot is plot and page.cards[1].plot is None
        assert plot.parent_slot.parent is _one(user, "plot-slot:0")


async def test_a_shown_plot_has_no_rings_until_a_criterion_opens(shell):
    """Over A (nrmse only) `editing=()` and `editing=None` are the same
    figure; TREE on D has ring-bearing leaves, so the two differ there."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.cards[0].show()
        figure = page.cards[0].plot.figure
        assert _sans_rev(figure) == shell.session.plot_figure(D, 0, editing=())
        assert _sans_rev(figure) != shell.session.plot_figure(D, 0)
        assert not [s for s in figure["layout"]["shapes"] if s.get("type") == "circle"]


async def test_the_browser_event_shows_the_named_card(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.on_visible("1")
            assert page.cards[1].plot is not None and page.cards[0].plot is None
            for junk in ("7", "x", "-1", None, 1.5, {"a": 1}):
                page.on_visible(junk)
        assert page.cards[0].plot is None


async def test_a_layout_draws_its_plots(plots_shell):
    shell = plots_shell
    shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 1 and _one(user, "plot-title:0").text == "x, z"
        assert _one(user, "plots:count").text == "1 plot · from this test's plots key"
        assert _one(user, "plot-only:0").text == "z is plot only: it has no criteria."
        assert list(page.cards[0].rows) == [T0]  # x's leaf only
        assert _none(user, "plot-missing:0")
        shell.session.discard()
        shell.session.stage(
            [
                PatchOp(
                    A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "no.such"}]}]
                )
            ]
        )
        await _test_page(shell, user)
        assert _one(user, "plot-missing:0").text == "Not in this result: no.such"


async def test_criteria_rows_follow_the_tree(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        criteria = _one(user, "criteria:0")
        rows = [
            m
            for child in criteria.default_slot.children
            for m in child._markers
            if m.startswith("criterion:")
        ]
        assert rows == [f"criterion:0:/metrics/children/{i}" for i in range(6)]
        assert page.cards[0].rows[T1].name.text == "range · x"
        assert "dstf-pill-pass" in _one(user, f"criterion-pill:0:{T1}").classes
        from dstf.authoring.shell.tree_card import leaf_readout

        node = shell.session.tree_view(D)["children"][1]
        assert leaf_readout(node)
        assert _one(user, f"criterion-readout:0:{T1}").text == leaf_readout(node)
        host = _one(user, f"node:0:{T1}")
        (wrap,) = user.find(marker=f"wrap:{T1}").elements  # the tree is closed
        assert host in list(_ancestors(wrap))


async def test_a_rows_node_selection_carries_its_variable(shell):
    """Through Edit tree this is unobservable (its add form falls back to the
    leaf's own variable); a probe at NODE sees the selection itself."""
    seen: list[Selection] = []

    class _Sees(_Probe):
        def panel(self, s, ui_panel):
            seen.append(s.selection)

    _only(_Sees("sees", {Placement.NODE}))
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert {(s.variable, s.node_path) for s in seen} == {("x", T0), ("y", T1)}


async def test_the_card_pill_is_the_worst_of_its_variables_criteria(shell):
    tree = {
        "combinator": "and",
        "children": [_range(-1), {"metric": "nrmse", "variable": "x"}],
    }
    _stage_tree(shell, A, tree)  # the first leaf passes, the second fails
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[0]
        assert "dstf-pill-fail" in _one(user, "plot-pill:0:x").classes
        shell.session.stage([PatchOp(A, "add", f"{T1}/tolerance", 0.5)])
        await _settled(lambda: "dstf-pill-pass" in _one(user, "plot-pill:0:x").classes)
        assert page.cards[0] is card  # painted in place, not rebuilt


async def test_the_footer_adds_a_criterion_for_the_variable(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        footer = _one(user, "variable-actions:0:x")
        (button,) = [
            e
            for e in user.find(marker="action:edit-tree").elements
            if footer in list(_ancestors(e))
        ]
        assert button.text == "Add a criterion for x"
        _click_in(user, "action:edit-tree", within="variable-actions:0:x")
        # A dialog hangs off the client's layout, not off the bar that opened
        # it: the marker carries the variable, so a plain find is unique.
        await user.should_see(marker="apply-add:x")
        user.find(marker="apply-add:x").click()
        await _settled(lambda: len(_page(shell, user).cards[0].rows) == 2)


async def test_a_test_with_no_result_has_no_cards(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, C)
        assert _one(user, "plots:count").text == "no plots: this test has no result"
        assert page.cards == []


async def test_an_empty_plots_key_does_not_blame_the_baseline(shell):
    """The plots bar says why there is no PLOT, which is not the tree's
    reason: a scored, baselined test with `"plots": []` asked for none, and
    reading the tree's reason there said "no baseline" about a test whose
    baseline is right beside it in the header."""
    shell.session.stage([PatchOp(A, "add", "/plots", [])])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards == []
        assert (
            _one(user, "plots:count").text == "no plots: this test's plots key is empty"
        )
        assert _one(user, "tree:summary").text == "0 pass · 2 fail"


async def test_a_failed_simulations_partial_result_draws_no_cards(shell):
    """A reader that keeps what a stopped run wrote hands the page a result
    with variables and `success=False`. They are not the test's plots — a
    card each for a run that produced nothing the test asked for (the final
    review) — and the reason is the run, not the baseline."""
    from dstf.backends.types import TestResult

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert len(page.cards) == 2
        result = shell.session.project.results[B]
        shell.session.project.results[B] = TestResult(
            model_id=B,
            success=False,
            variables=list(result.variables),
        )
        with page.client:
            page.sync()
        assert page.cards == []
        assert _one(user, "plots:count").text == "no plots: this test has no result"


async def test_a_result_with_no_variables_says_that_and_not_no_baseline(tmp_path):
    """The third reason: a successful result the test tracks nothing of.
    The tree's line is still the baseline's — the two sentences answer two
    questions."""
    import json

    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0002" / "result.json"  # B's
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["variables"] = {}
    path.write_text(json.dumps(payload), encoding="utf-8")
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert page.cards == []
        assert _one(user, "plots:count").text == "no plots: the result has no variables"
        assert (
            _one(user, "tree:summary").text
            == "nothing scored: this test has no baseline"
        )


async def test_a_no_baseline_test_draws_its_result(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert len(page.cards) == 2 and all(not c.rows for c in page.cards)
        footer = _one(user, "variable-actions:0:x")
        (why,) = [
            e
            for e in user.find(marker="action:edit-tree:why").elements
            if footer in list(_ancestors(e))
        ]
        assert why.text == "no tree to edit (nothing scored)"
        with page.client:
            page.cards[0].show()
        assert page.cards[0].plot is not None


async def test_a_plot_that_could_not_draw_says_so_once_and_retries(shell, monkeypatch):
    """`show()` is called again on every opening row: a slot that kept the
    label of each failed try stacked them, and the recovered plot was drawn
    UNDER them. The slot holds one try's answer."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        real = shell.session.plot_figure

        def boom(*a, **k):
            raise RuntimeError("cannot draw")

        monkeypatch.setattr(shell.session, "plot_figure", boom)
        with page.client:
            page.cards[0].show()
            page.cards[0].show()
            page.on_visible("0")
        slot = page.cards[0].slot.default_slot.children
        assert [c.text for c in slot] == ["RuntimeError: cannot draw"]
        monkeypatch.setattr(shell.session, "plot_figure", real)
        with page.client:
            page.cards[0].show()
        assert [type(c) for c in page.cards[0].slot.default_slot.children] == [
            ui.plotly
        ]


async def test_a_variable_keeps_its_card_when_its_last_criterion_goes(shell):
    """A one-way door (the review): the cards, the footers and the add forms
    all came from what was SCORED, so removing y's only criterion took y's
    card, its footer and every way to add a criterion back — until Discard.
    A tracked variable is the test's, staged edit or not."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click_in(user, f"remove:{T1}", within=f"node:1:{T1}")
        await _settled(lambda: len(page.cards) == 2 and not page.cards[1].rows)
        card = page.cards[1]
        assert card.resolved.series == ("y",) and card.rows == {}
        assert card.pills == {} and _none(user, "plot-pill:1:y")
        (button,) = [
            e
            for e in user.find(marker="action:edit-tree").elements
            if _one(user, "variable-actions:1:y") in list(_ancestors(e))
        ]
        assert button.text == "Add a criterion for y"
        await _menu(user, f"add:{T0}", within=f"node:0:{T0}")
        assert _one(user, f"apply-add:{T0}:variable").options == ["x", "y"]


async def test_a_multi_series_card_names_the_variable_of_each_pill(shell):
    """Board: two pills reading "FAIL FAIL" look like the plot's verdict said
    twice. A card drawing one series keeps the bare status — its title
    already names the variable."""
    shell.session.stage(
        [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "y"}]}])]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "x, y"
        assert [_one(user, f"plot-pill:0:{v}").text for v in ("x", "y")] == [
            "x FAIL",
            "y FAIL",
        ]
        shell.session.stage([PatchOp(A, "add", f"{T0}/tolerance", 0.5)])
        await _settled(lambda: _one(user, "plot-pill:0:x").text == "x PASS")
        assert page.cards[0].pills["x"].label.classes.count("dstf-pill-pass") == 1
        shell.session.discard()
        await _settled(lambda: len(_page(shell, user).cards) == 2)
        assert _one(user, "plot-pill:0:x").text == "fail"  # one series: the status


async def test_the_variable_footers_share_one_wrapping_row(shell):
    """Board: one footer per line pushed the next plot off the screen. They
    sit in ONE row that wraps."""
    shell.session.stage(
        [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "y"}]}])]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        bars = page.cards[0].bars
        assert [b.marker for b in bars] == [
            "variable-actions:0:x",
            "variable-actions:0:y",
        ]
        parents = {b.container.parent_slot.parent for b in bars}
        (row,) = parents
        assert isinstance(row, ui.row) and "wrap" in row.classes


def test_worst_is_fail_then_warn_then_pass_then_queued():
    """The card's pill is the WORST of its variable's criteria, and every
    pair matters: a warn beside a pass is a warn, and an unscored leaf
    beside a passing one must not read `queued`."""
    from dstf.authoring.shell.plot_card import worst

    assert worst(["pass", "warn"]) == "warn"
    assert worst(["warn", "fail"]) == "fail"
    assert worst(["queued", "pass"]) == "pass"
    assert worst(["queued"]) == "queued" and worst([]) == "queued"


async def test_every_slot_names_its_own_index(shell):
    """`data-plot` is what the browser's observer sends back: a slot naming
    another card's index draws the wrong plot on scroll."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert _one(user, "plot-slot:1").props["data-plot"] == "1"


async def test_two_plot_only_series_say_so_in_the_plural_and_get_no_footer(
    plots_shell,
):
    """A plot-only series has no criteria and no "Add a criterion for it"
    footer — the footers are the SCORED series'."""
    shell = plots_shell
    layout = [{"series": [{"var": "x"}, {"var": "z"}, {"var": "a0"}]}]
    shell.session.stage([PatchOp(A, "add", "/plots", layout)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert (
            _one(user, "plot-only:0").text
            == "z, a0 are plot only: they have no criteria."
        )
        assert [b.marker for b in page.cards[0].bars] == ["variable-actions:0:x"]


async def test_a_variable_with_no_criteria_has_no_header_pill(shell):
    """B is scored by nothing: its cards draw its result and carry no
    verdict at all — an empty pill would read `queued` forever."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert page.cards[0].pills == {} and _none(user, "plot-pill:0:x")


async def test_a_mode_switch_repaints_the_row_in_place(shell):
    """A leaf's metric is NOT part of the page's shape: switching it
    repaints the row's name and its readout where they are, and the card —
    its plot, its zoom, its editor — stands."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card, row = page.cards[0], page.cards[0].rows[T1]
        assert row.name.text == "range · x"
        shell.session.stage(
            [PatchOp(D, "replace", T1, {"metric": "nrmse", "variable": "x"})]
        )
        await _settled(lambda: row.name.text == "nrmse · x")
        assert page.cards[0] is card and page.cards[0].rows[T1] is row
        from dstf.authoring.shell.tree_card import leaf_readout

        node = shell.session.tree_view(D)["children"][1]
        assert row.readout.text == leaf_readout(node) and "NRMSE" in row.readout.text


async def test_a_layout_staged_under_the_open_page_rebuilds_the_cards(plots_shell):
    """The plots ARE part of the shape: a `/plots` op under the open page
    rebuilds the cards, which repainting could never do."""
    shell = plots_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 2
        shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        await _settled(lambda: len(page.cards) == 1)
        assert _one(user, "plot-title:0").text == "x, z"


async def test_a_leaf_that_changes_variable_moves_to_the_other_card(shell):
    """A leaf's variable is part of the shape: its row belongs to another
    card now, and no repaint moves a row between cards."""
    shell.session.stage(
        [
            PatchOp(
                A,
                "add",
                "/plots",
                [{"series": [{"var": "x"}]}, {"series": [{"var": "y"}]}],
            )
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [list(c.rows) for c in page.cards] == [[T0], [T1]]
        shell.session.stage([PatchOp(A, "replace", f"{T0}/variable", "y")])
        await _settled(lambda: [list(c.rows) for c in page.cards] == [[], [T0, T1]])


async def test_the_variable_footer_follows_the_job_that_locked_the_test(shell):
    """The footers are painted, never rebuilt, so what they MAY do has to be
    re-read on every Session event: a rerun greys them and says so, and the
    end of the job brings them back."""
    gate = _Gate()
    footer = "variable-actions:0:x"

    def why(user) -> str:
        found = _inside(user, "action:edit-tree:why", footer)
        return found[0].text if found else ""

    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert why(user) == ""
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await _settled(lambda: why(user) == f"rerun is running on {A}")
        gate.release.set()
        await _settled(lambda: why(user) == "")


async def test_showing_a_plot_attaches_the_hook_to_it(shell):
    """The hook exists before the plot does — a panel hosted under the card
    subscribes to it first — so `show()` has to attach it, or no drag is
    ever delivered."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[0]
        with page.client:
            card.show()
        kinds = {li.type for li in card.plot._event_listeners.values()}
        assert {"plotly_click", "plotly_relayout", "plotly_selected"} <= kinds
        assert card.hook._plot is card.plot


async def test_painting_the_header_never_loads_the_baseline(shell, monkeypatch):
    """The header prints the baseline's FILE NAME, which the index knows:
    `get_reference` reads the whole thing off disk, and this runs on every
    Session event."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)

        def boom(*a, **k):
            raise AssertionError("the baseline was read to paint the header")

        monkeypatch.setattr(shell.session.project.store, "get_reference", boom)
        with page.client:
            page.sync()
        assert _one(user, "meta").text == "Python · ref_0001"


async def test_a_closed_menus_form_is_gone_from_the_page(shell):
    """A node's forms are built when their menu opens (Task 6) and dropped
    when it closes: a page holding a form per node of every row is what that
    replaced."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _menu(user, f"wrap:{T0}", within=f"node:0:{T0}")
        (apply,) = user.find(marker=f"apply-wrap:{T0}").elements
        menu = next(a for a in apply.ancestors() if isinstance(a, ui.menu))
        menu.close()
        await _settled(lambda: _none(user, f"apply-wrap:{T0}"))


async def test_a_rebuilt_card_gives_up_its_hook(shell):
    """A rebuild drops the cards, and a dropped card's hook must not go on
    delivering drags: the editor's panels give theirs back when it closes,
    but a footer's OPEN dialog holds the same hook and the rebuild does not
    reach it. `dispose` is what takes it back."""
    _only(_Probe("sub", {Placement.VARIABLE}, subscribe=True))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        old = page.cards[0]
        _click_in(user, "action:sub", within="variable-actions:0:x")
        await user.should_see(marker="probe:sub")
        assert [o for entries in old.hook._handlers.values() for o, _ in entries]
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}]}] * 3)]
        )
        await _settled(lambda: len(page.cards) == 3)
        assert page.cards[0] is not old
        assert not [e for entries in old.hook._handlers.values() for e in entries]


async def test_a_rebuild_reads_the_tree_and_the_tracked_names_once(shell):
    """The cost the review measured: every card read `tree_view` and
    `tracked` for itself, so a rebuild cost one read per plot. The page
    reads and hands both in, so neither grows with the plots."""
    shell.session.stage([PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}]}] * 5)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 5
        session = shell.session
        calls = {"tree_view": 0, "tracked": 0}
        for name in calls:
            real = getattr(session, name)

            def counting(*a, _real=real, _name=name, **k):
                calls[_name] += 1
                return _real(*a, **k)

            setattr(session, name, counting)
        view = session.tree_view(A)
        calls["tree_view"] = 0
        with page.column:
            page.rebuild(view)
        # The page reads no view of its own, and the few `tracked` reads are
        # `Session.plots`'s own: the count does not follow the card count.
        assert calls["tree_view"] == 0 and calls["tracked"] < len(page.cards)
        calls["tracked"] = 0
        with page.client:
            page.sync()
        assert calls["tree_view"] == 1


# -- the editor: below its row, one at a time, the plot as its surface ------


def _pending(shell, path: str):
    ops = [op for op in shell.session.spec.pending if op.path == path]
    return ops[-1].value if ops else None


def _counted(card) -> list[int]:
    """Count what a card RE-SENDS: `update_figure` is the one call that
    reaches the browser, as `Plotly.react` on the node already there."""
    calls = [0]
    real = card.plot.update_figure

    def counting(figure):
        calls[0] += 1
        real(figure)

    card.plot.update_figure = counting
    return calls


def _shape_index(card, name: str) -> int:
    """The index the hook's payload names a shape by — the figure the plot
    currently holds, which is what `FigureHook._shape_name` resolves against."""
    return next(
        i
        for i, shape in enumerate(card.plot.figure["layout"]["shapes"])
        if shape.get("name") == name
    )


def _numbers(page, label: str) -> list:
    return [
        e
        for e in page.client.elements.values()
        if isinstance(e, ui.number) and e.props.get("label") == label
    ]


def _click(user, element) -> None:
    from nicegui.testing.user_interaction import UserInteraction

    UserInteraction(user, {element}, None).click()


#: A criterion on each of A's two tracked variables: one row per card.
TWO_RANGES = {
    "combinator": "and",
    "children": [
        {"metric": "range", "variable": "x", "min_value": -1},
        {"metric": "range", "variable": "y", "min_value": -1},
    ],
}


async def test_a_click_on_a_row_opens_its_editor_below_it(shell):
    """Brief §3.2: the editor is whatever attaches at CRITERION, stacked
    without headings — Edit criteria and Edit on plot read as ONE editor,
    so there are no tabs and no panel of the shell's own."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        row = page.cards[0].rows[T0]
        assert len(row.slot.default_slot.children) == 0
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: not _none(user, f"editor:0:{T0}"))
        editor = _one(user, f"editor:0:{T0}")
        ancestors = list(_ancestors(editor))
        assert row.slot in ancestors
        siblings = row.row.parent_slot.children
        assert siblings.index(row.slot) == siblings.index(row.row) + 1
        assert any("dstf-editor" in e.classes for e in ancestors)
        panels = [m for m in _marks(editor) if m.startswith("panel:")]
        assert panels == ["panel:edit-criteria", "panel:edit-on-plot"]
        assert not [e for e in page.client.elements.values() if isinstance(e, ui.tabs)]
        assert "dstf-crit-open" in row.row.classes


async def test_one_editor_at_a_time_page_wide(shell):
    """One open criterion, whatever card its row is on — and only the plot
    whose rings it moved is re-sent."""
    _stage_tree(shell, A, TWO_RANGES)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
            page.cards[1].show()
        on_x, on_y = _counted(page.cards[0]), _counted(page.cards[1])
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        # `editing_for` names ONE card: y's plot draws nothing new.
        assert (on_x[0], on_y[0]) == (1, 0)
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        row = page.cards[0].rows[T0]
        assert len(row.slot.default_slot.children) == 0
        assert "dstf-crit-open" not in row.row.classes
        assert _none(user, f"editor:0:{T0}") and not _none(user, f"editor:1:{T1}")
        user.find(marker=f"criterion:1:{T1}").click()  # the open row closes it
        await _settled(lambda: page.open is None)
        assert _none(user, f"editor:1:{T1}")
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        user.find(marker=f"editor-done:1:{T1}").click()
        await _settled(lambda: page.open is None)


async def test_the_open_criterion_is_the_only_one_with_rings(shell):
    """Decision 7: the figure is always `editable=True` and carries the
    rings of the open leaf alone, so opening one is a `Plotly.react` on the
    node already there and the zoom survives."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        plot = card.plot
        config = copy.deepcopy(card.plot.figure["config"])
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert _sans_rev(card.plot.figure) == shell.session.plot_figure(
            D, 0, editing=(T0,)
        )
        rings = {
            s["name"]
            for s in card.plot.figure["layout"]["shapes"]
            if s.get("type") == "circle"
        }
        assert rings and all(name.startswith(T0 + "/") for name in rings)
        user.find(marker=f"editor-done:0:{T0}").click()
        await _settled(lambda: page.open is None)
        assert _sans_rev(card.plot.figure) == shell.session.plot_figure(
            D, 0, editing=()
        )
        assert card.plot is plot  # the same node throughout
        assert card.plot.figure["config"] == config


async def test_opening_a_criterion_shows_its_plot(shell):
    """The plot above the editor is its editing surface: a row on a card
    nobody has scrolled to draws it."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        assert page.cards[0].plot is None
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        assert page.cards[0].plot is not None


async def test_a_drag_stages_one_replace_and_the_plot_follows(shell):
    """The drag's whole order: the hook dispatches, the panel submits, the
    Session stages, the editor's OTHER panels follow the ring, and the
    `finally` redraws the plot."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        n = _shape_index(card, f"{T0}/tube/upper/0")
        with page.client:
            card.hook._dispatch(
                "plotly_relayout",
                {f"shapes[{n}].xanchor": 0.5, f"shapes[{n}].yanchor": 0.8},
            )
        ops = [op for op in shell.session.spec.pending if op.path == T0]
        assert [op.op for op in ops] == ["replace"]
        assert ops[0].value["tube_points"][0]["upper"] == pytest.approx(0.3)
        assert _sans_rev(card.plot.figure) == shell.session.plot_figure(
            D, 0, editing=(T0,)
        )
        # The table followed the ring: the editor was rebuilt whole.
        await _settled(
            lambda: _one(user, f"{T0}:tube_points:0:upper").value == pytest.approx(0.3)
        )


async def test_a_refused_drag_is_said_in_the_editor_and_the_plot_snaps_back(shell):
    """Only the open criterion gets rings, but every leaf's bound LINES are
    on the plot and Plotly lets them be dragged: one of those is refused by
    name, stages nothing, and the plot is re-sent so the line goes back."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        other = "/metrics/children/3/scalar/min"  # the scalar leaf's lower bound
        n = _shape_index(card, other)
        sent = _counted(card)
        pending = list(shell.session.spec.pending)
        with page.client:
            card.hook._dispatch(
                "plotly_relayout", {f"shapes[{n}].y0": 1.0, f"shapes[{n}].y1": 1.0}
            )
        assert shell.session.spec.pending == pending
        error = card.editor.errors["edit-on-plot"]
        assert error.visible
        assert error.text == f"HandleEditError: {other} is not a handle of {T1}"
        assert sent[0] == 1  # the snap-back, once


async def test_typing_in_a_form_keeps_the_field(shell):
    """A panel's own submit rebuilds every panel BUT its own: the control
    being typed in survives, and the other panel follows the edit."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        (cell,) = _numbers(page, "Lower bound (optional)")
        on_plot = _one(user, "panel:edit-on-plot")
        with page.client:
            cell.set_value(-1.0)
        await _settled(lambda: (_pending(shell, T1) or {}).get("min_value") == -1.0)
        assert _numbers(page, "Lower bound (optional)") == [cell]
        assert _one(user, "panel:edit-on-plot") is not on_plot


async def test_closing_the_editor_drops_its_handlers(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        owners = {o for entries in card.hook._handlers.values() for o, _ in entries}
        assert any(o.startswith("editor:") for o in owners)
        user.find(marker=f"editor-done:0:{T0}").click()
        await _settled(lambda: page.open is None)
        owners = {o for entries in card.hook._handlers.values() for o, _ in entries}
        assert not [o for o in owners if o.startswith("editor:")]
        pending = list(shell.session.spec.pending)
        with page.client:
            card.hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
        assert shell.session.spec.pending == pending


async def test_the_jump_opens_the_failing_criterion(shell):
    """Board 1A: the closed line names the failure, and the name is what
    jumps to it — on whatever card its row sits."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        (jump,) = [
            j for j in user.find(marker="tree:jump").elements if j.text == "nrmse · y"
        ]
        _click(user, jump)
        await _settled(lambda: page.open == (1, T1))
        assert page.cards[1].plot is not None
        assert "dstf-crit-open" in page.cards[1].rows[T1].row.classes


async def test_a_shape_change_closes_the_editor(shell):
    """A removed criterion rebuilds the rows the editor hangs under: it
    closes rather than hang off an element that is gone."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        n = len(page.cards[0].rows)
        _click_in(user, f"remove:{T0}", within=f"node:0:{T0}")
        await _settled(lambda: len(page.cards[0].rows) == n - 1)
        assert page.open is None
        assert _none(user, f"editor:0:{T1}")


async def test_an_open_editors_panels_follow_the_job_that_locked_the_test(shell):
    """The editor is not rebuilt while it is open, so what its capabilities
    MAY do has to be re-read: a rerun greys them and says so, and the end of
    the job brings them back."""
    _stage_tree(shell)
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        editor = f"editor:0:{T0}"
        assert _inside(user, "panel:edit-criteria", editor)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
        await _settled(lambda: _inside(user, "panel:edit-criteria", editor) == [])
        (why,) = _inside(user, "why:edit-criteria", editor)
        assert why.text == f"rerun is running on {D}"
        gate.release.set()
        await _settled(lambda: bool(_inside(user, "panel:edit-criteria", editor)))


# -- the snap-back, the rebuild, and an editor whose edit came from elsewhere


@pytest.fixture
def scored_shell(tmp_path):
    """A shell whose D entry carries its tree IN THE SPEC, so a staged edit
    can be discarded or saved without taking the tree with it."""
    return _spec_tree_shell(tmp_path, {"combinator": "and", "children": [_range(-1)]})


@pytest.fixture
def tubed_shell(tmp_path):
    """`scored_shell` with a TUBE on D's x — the criterion whose editor owns
    a table, so an edit staged elsewhere has rows to follow."""
    return _spec_tree_shell(
        tmp_path,
        {"combinator": "and", "children": [copy.deepcopy(TREE["children"][0])]},
    )


def _spec_tree_shell(tmp_path, tree):
    cfg, work = _two_var_project(tmp_path)
    path = cfg.parent / "test_spec.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == D:
            entry["metrics"] = tree
    path.write_text(json.dumps(spec), encoding="utf-8")
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def test_every_figure_a_card_sends_carries_a_new_editrevision(shell):
    """The snap-back's own condition, in Chromium: the figure carries
    `uirevision: "keep"`, Plotly's `editrevision` DEFAULTS to it, and a
    shape whose server value equals its pre-drag value keeps the dragged
    position through `Plotly.react`. A refused drag then never went back.
    Every figure a card sends says its shapes are new."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[0]
        with page.client:
            card.show()
            first = card.plot.figure["layout"]["editrevision"]
            card.redraw()
            second = card.plot.figure["layout"]["editrevision"]
            card.redraw()
            third = card.plot.figure["layout"]["editrevision"]
        assert len({first, second, third}) == 3
        # The zoom is the other revision, and it must NOT move with it.
        assert card.plot.figure["layout"]["uirevision"] == "keep"


async def test_the_cards_revision_stays_out_of_the_sessions_figure(shell):
    """The revision is the card's, not the figure layer's: a Session figure
    is deterministic, and the card COPIES the layout rather than writing
    into the dict it was handed — which is the Session's to cache."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        handed: list[dict] = []
        real = shell.session.plot_figure

        def spy(*a, **k):
            figure = real(*a, **k)
            handed.append(figure)
            return figure

        shell.session.plot_figure = spy
        with page.client:
            page.cards[0].show()
            page.cards[0].redraw()
        assert len(handed) == 2
        assert all("editrevision" not in figure["layout"] for figure in handed)
        assert "editrevision" in page.cards[0].plot.figure["layout"]


async def test_swapping_two_rows_on_one_card_sends_its_figure_once(shell):
    """A close that redraws before the open that follows it re-sent the same
    card twice for one swap. The editor closes WITHOUT redrawing; the
    signature decides the one redraw."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        sent = _counted(card)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert sent[0] == 1
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        assert sent[0] == 2


async def test_a_layout_that_drops_the_open_editors_card_rebuilds_the_page(
    plots_shell, caplog
):
    """The rebuild closed the editor, which redrew the card it was about to
    drop: with the editor open on the last card and a `/plots` that leaves
    ONE plot, that redraw asked the Session for a plot that no longer
    exists, the rebuild died half-done with the shape already recorded, and
    the page never rebuilt again."""
    import logging

    shell = plots_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
            await _settled(lambda: len(page.cards) == 1)
        assert page.open is None
        assert _one(user, "plot-title:0").text == "x, z"
        assert [
            r.getMessage() for r in caplog.records if r.name.startswith("dstf")
        ] == []


async def test_a_discard_elsewhere_leaves_the_open_editor_showing_it(scored_shell):
    """The editor is built from the tree as it was when it opened: an edit
    undone elsewhere left its fields showing the undone value, and the next
    keystroke staged that value back."""
    shell = scored_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        (upper,) = _numbers(page, "Upper bound (optional)")
        with page.client:
            upper.set_value(5.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("max_value") == 5.0)
        shell.session.discard()
        await _settled(
            lambda: _numbers(page, "Upper bound (optional)")[0].value is None
        )
        # ONCE: the rebuild re-records what the editor now shows, or every
        # later sync rebuilds it again and no field can be typed in.
        rebuilt = _numbers(page, "Upper bound (optional)")[0]
        with page.client:
            page.sync()
        assert _numbers(page, "Upper bound (optional)") == [rebuilt]
        (lower,) = _numbers(page, "Lower bound (optional)")
        with page.client:
            lower.set_value(-3.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("min_value") == -3.0)
        # The discarded edit does not come back with the next keystroke.
        assert _pending(shell, T0).get("max_value") is None


async def test_a_window_staged_elsewhere_reaches_the_open_editor(scored_shell):
    """The window is one of the leaf's edited fields, so an edit that moves
    only it must rebuild the editor: the fields went on showing no window
    while the plot beside them was scored over one."""
    shell = scored_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert _numbers(page, "window start")[0].value is None
        shell.session.stage(
            [PatchOp(D, "add", f"{T0}/window", {"start": 0.25, "end": 0.75})]
        )
        await _settled(lambda: _numbers(page, "window start")[0].value == 0.25)
        assert _numbers(page, "window end")[0].value == 0.75


async def test_a_save_leaves_the_editor_alone(scored_shell):
    """A Save empties the pending list, but the LEAF under the editor is
    where the edit left it — the value is on disk now. What the editor
    shows is still true, so it is not rebuilt: the field is the same
    element and the next keystroke keeps it. (Comparing the whole
    document's pending ops rebuilt it here for nothing.)"""
    shell = scored_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        (upper,) = _numbers(page, "Upper bound (optional)")
        with page.client:
            upper.set_value(5.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("max_value") == 5.0)
        shell.session.save()
        await _settled(lambda: shell.session.spec.pending == [])
        await asyncio.sleep(0.4)  # the spec / results events reach the page
        assert _numbers(page, "Upper bound (optional)") == [upper]
        assert upper.value == 5.0
        assert page.open == (0, T0)
        (lower,) = _numbers(page, "Lower bound (optional)")
        with page.client:
            lower.set_value(-3.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("min_value") == -3.0)
        # The saved bound is not staged back on top of the file's own.
        assert _pending(shell, T0).get("max_value") == 5.0


async def test_the_first_edit_of_a_flat_test_keeps_the_field(shell):
    """A's tree is implicit: the first form edit stages the materialization
    (`add /metrics`) AND the replace. An editor that recorded what it was
    built from before both ops landed read the materialization as an edit
    staged elsewhere and rebuilt the control being typed in — the whole
    point of skipping the submitting panel."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        assert shell.session.spec.pending == []
        with page.client:
            page.open_criterion(T0)
        (field,) = _numbers(page, "Tolerance")
        with page.client:
            field.set_value(0.5)
        await _settled(lambda: len(shell.session.spec.pending) == 2)
        await asyncio.sleep(0.4)  # the spec / results events reach the page
        assert _numbers(page, "Tolerance") == [field]
        assert page.open == (0, T0)


async def test_another_models_edit_leaves_a_half_typed_row_alone(shell):
    """`pending` is the WHOLE document's: an edit to another model moved it
    and the next event that was D's — any `job` — rebuilt D's editor, which
    threw away a cleared table cell nobody had retyped yet. The editor
    follows its OWN leaf."""
    _stage_tree(shell)  # D: TREE (tube on T0, range on T1)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.open_criterion(T0)
        cell = _one(user, f"{T0}:tube_points:0:time")
        assert cell.value is not None
        with page.client:
            cell.set_value(None)  # cleared to retype: kept locally, not staged
        await asyncio.sleep(0.3)
        shell.session.stage([LOOSEN])  # ANOTHER model (A), e.g. from another tab
        await asyncio.sleep(0.3)
        with page.client:
            page.handle({"event": "job", "state": "finished"})
        await asyncio.sleep(0.3)
        assert _one(user, f"{T0}:tube_points:0:time") is cell
        assert cell.value is None


async def test_a_combinator_edit_that_moves_no_leaf_leaves_the_editor_alone(shell):
    """`and` -> `or` on the root: the shape stands, no leaf moved, and the
    open editor's fields say exactly what they said."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.open_criterion(T1)
        (lower,) = _numbers(page, "Lower bound (optional)")
        shell.session.stage([PatchOp(D, "replace", "/metrics/combinator", "or")])
        await _settled(lambda: "or" in _one(user, "tree:root").text)
        await asyncio.sleep(0.3)
        assert _numbers(page, "Lower bound (optional)") == [lower]
        assert page.open == (0, T1)


async def test_a_job_event_leaves_the_editors_own_control_alone(shell):
    """A job that staged nothing and locked nothing: the editor was opened
    on the leaf it still shows, so nothing is rebuilt."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.open_criterion(T1)
        (lower,) = _numbers(page, "Lower bound (optional)")
        with page.client:
            page.handle({"event": "job", "state": "finished"})
        assert _numbers(page, "Lower bound (optional)") == [lower]


async def test_an_open_tube_editor_follows_a_point_staged_elsewhere(tubed_shell):
    """The rebuild an edit from elsewhere DOES need, on the panel that owns
    the tables: Edit on plot's rows must show the staged point, and go back
    to the loaded one on a Discard."""
    shell = tubed_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.open_criterion(T0)
        cell = _one(user, f"{T0}:tube_points:0:time")
        loaded = cell.value
        moved = copy.deepcopy(TREE["children"][0])
        moved["tube_points"][0]["time"] = 0.6
        shell.session.stage([PatchOp(D, "replace", T0, moved)])
        await _settled(lambda: _one(user, f"{T0}:tube_points:0:time").value == 0.6)
        shell.session.discard()
        await _settled(lambda: _one(user, f"{T0}:tube_points:0:time").value == loaded)
        assert loaded != 0.6


async def test_a_card_redraws_for_a_window_or_a_baseline_that_moved(shell):
    """Both ride in the figure — a window shades the plot, a baseline is the
    reference the leaf is drawn against — and neither is in `params`."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        loaded = shell.session.tree_view(D)
        for key, value in (("window", {"start": 0.2, "end": 0.8}), ("against", "peer")):
            moved = copy.deepcopy(loaded)
            leaf = next(n for n in leaves_of(moved) if str(n["path"]) == T0)
            leaf[key] = value
            assert card.signature(moved) != card.signature(loaded), key


async def test_focus_closes_an_editor_on_a_card_it_hides(shell):
    """An editor nobody can see is no editor — and it would go on staging
    edits from drags on a plot that is not on screen."""
    _stage_tree(shell, A, TWO_RANGES)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            page.open_criterion(T1)  # y's, on card 1
        assert page.open == (1, T1)
        with page.client:
            page.focus(0)
        assert page.open is None
        assert page.cards[1].editor is None
        assert page.cards[1].card.visible is False


async def test_focus_draws_a_card_that_was_never_shown(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[1].plot is None
        with page.client:
            page.focus(1)
        assert page.cards[1].plot is not None


async def test_a_plot_that_could_not_draw_is_retried_by_the_next_sync(
    shell, monkeypatch
):
    """Only a row opening or Focus asked a failed card again: a result file
    being rewritten under `show()` left the error sitting there through
    every later event. The card remembers it was asked to draw."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        real = shell.session.plot_figure
        boom = {"on": True}

        def flaky(*a, **k):
            if boom["on"]:
                raise RuntimeError("result file is being rewritten")
            return real(*a, **k)

        monkeypatch.setattr(shell.session, "plot_figure", flaky)
        with page.client:
            page.cards[0].show()
        assert page.cards[0].plot is None
        with page.client:  # still failing: ONE label, never a stack of them
            page.handle({"event": "job", "state": "started"})
        slot = page.cards[0].slot.default_slot.children
        assert [c.text for c in slot] == [
            "RuntimeError: result file is being rewritten"
        ]
        boom["on"] = False
        with page.client:
            page.handle({"event": "job", "state": "finished"})
        assert page.cards[0].plot is not None
        assert page.cards[1].plot is None  # a card nobody asked for stays a slot


async def test_a_plot_that_keeps_failing_is_logged_once_per_error(shell, monkeypatch):
    """The retry is free; the traceback is not. A card that cannot draw is
    tried again by EVERY sync, a rescan's progress ticks included — 50 of
    them over two failing cards logged 100 tracebacks for one broken result
    (the final review). The card keeps the last error's text and logs only
    when it changes."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        real = shell.session.plot_figure
        raising = {"text": "result file is being rewritten"}

        def boom(*a, **k):
            raise RuntimeError(raising["text"])

        monkeypatch.setattr(shell.session, "plot_figure", boom)
        logged = []
        monkeypatch.setattr(
            plot_card.logger, "exception", lambda *a, **k: logged.append(a)
        )
        with page.client:
            page.cards[0].show()
            page.cards[1].show()
        assert len(logged) == 2  # the first failure of each card
        with page.client:
            for i in range(50):
                page.handle(
                    {"event": "loading", "phase": "scan", "done": i, "total": 50}
                )
        assert len(logged) == 2
        raising["text"] = "corrupt result"  # a DIFFERENT error is worth a line
        with page.client:
            page.handle({"event": "loading", "phase": "scan", "done": 50, "total": 50})
        assert len(logged) == 4
        # Still retried: the next sync draws as soon as a figure can be built.
        monkeypatch.setattr(shell.session, "plot_figure", real)
        with page.client:
            page.handle({"event": "loading", "phase": "scan", "done": 50, "total": 50})
        assert page.cards[0].plot is not None and page.cards[1].plot is not None


async def test_only_a_job_that_moved_the_data_re_sends_the_plots(shell):
    """Decision 10 is a FINISHED job; the rest of a Rerun's states move no
    trajectory. Forcing on every one of them re-sent every drawn plot five
    times per Rerun (the 3b review)."""
    from dstf.authoring.session import RunningJob

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        sent = _counted(page.cards[0])
        job = RunningJob(label="rerun", models=frozenset({A}))
        for state in ("started", "artifact"):
            with page.client:
                page.handle({"event": "job", "state": state, "job": job})
        assert sent[0] == 0
        for state in ("written", "finished", "failed"):
            with page.client:
                page.handle({"event": "job", "state": state, "job": job})
        assert sent[0] == 3


async def test_a_row_click_reads_the_tree_once_however_many_plots_are_drawn(shell):
    """`_redraw_moved`, `show` and the signature each read `tree_view` for
    themselves: one row click on a twelve-plot page cost fourteen reads."""
    shell.session.stage([PatchOp(A, "add", "/plots", [_plot("x")] * 12)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            for card in page.cards:
                card.show()
        calls = [0]
        real = shell.session.tree_view

        def counting(model_id):
            calls[0] += 1
            return real(model_id)

        shell.session.tree_view = counting
        with page.client:
            page.open_criterion(T0, 0)
        opened = calls[0]
        with page.client:
            page.close_editor()
        assert opened <= 2, opened
        assert calls[0] - opened <= 2, calls[0] - opened


def _project_without(tmp_path, model):
    """A second `_two_var_project` whose spec has no entry for ``model``:
    opening it under a page swaps the library and takes that test away."""
    from test_session import _two_var_project

    other = tmp_path / "other"
    other.mkdir()
    cfg, work = _two_var_project(other)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"] = [t for t in spec["tests"] if t["model"] != model]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


async def test_a_page_whose_test_is_gone_says_so_instead_of_raising(
    shell, tmp_path, caplog
):
    """An `OpenProject` outcome swaps the library under an open page: every
    read below `sync` raised, the page kept showing the old test's cards,
    and every event logged a traceback."""
    cfg2, work2 = _project_without(tmp_path, A)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            page.cards[0].show()
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.open(cfg2, work_dir=work2)
            await _settled(lambda: page.gone)
            shell.session.apply(Artifact(paths=[], label="probe"))
            await asyncio.sleep(0.3)
        assert [r for r in caplog.records if "a page failed" in r.getMessage()] == []
        assert page.cards == [] and _none(user, "plot-slot:0")
        assert _one(user, "gone").text == f"{A} is not a test of this project"
        assert _none(user, "tree:summary")
        # The sub-header went with them. It stayed LIVE and STALE: the last
        # project's verdict, its baseline's name, and buttons whose Rerun
        # started a job for a model this project does not have (the final
        # review).
        assert page.badge.text == "" and page.meta.text == ""
        assert page.actions.dialog.value is not True
        assert page.actions.actions == {} and _none(user, "action:rerun")
        assert page.actions.container.visible is False
        # And no later event brings them back while the test is gone.
        shell.session.apply(Artifact(paths=[], label="probe again"))
        await asyncio.sleep(0.3)
        assert page.actions.actions == {} and page.badge.text == ""


async def test_a_closed_session_leaves_the_page_saying_so(shell, caplog):
    """`Session.close()` emits a `spec` naming NO model: a page that read an
    empty `models` as "not mine" never heard, and the next job event raised
    "no project is open"."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.close()
            await _settled(lambda: page.gone)
            with page.client:
                page.handle({"event": "job", "state": "finished"})
        assert [r for r in caplog.records if "a page failed" in r.getMessage()] == []
        assert _one(user, "gone").text == f"{A} is not a test of this project"


async def test_a_test_that_comes_back_is_drawn_again(shell, tmp_path):
    """The page is not dead: reopening a project that HAS the test rebuilds
    it from the sentence — its sub-header with it."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        shell.session.close()
        await _settled(lambda: page.gone)
        cfg, work = _two_var_project(tmp_path / "again")
        shell.session.open(cfg, work_dir=work)
        await _settled(lambda: not page.gone)
        assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]
        assert _none(user, "gone")
        assert page.badge.text == "FAIL" and page.meta.text.startswith("Python · ")
        assert page.actions.container.visible is True
        assert "rerun" in page.actions.actions


async def test_a_page_built_while_its_test_is_missing_comes_back(shell, tmp_path):
    """A page opened or reloaded while the project is still scanning, or
    with another library open, was BUILT in the gone state: it had no frame
    to fill, so `sync` returned early for ever and the test never came back
    however many events arrived (the final review)."""
    cfg2, work2 = _project_without(tmp_path, A)
    async with user_simulation(root=shell.index) as user:
        shell.session.open(cfg2, work_dir=work2)  # a project WITHOUT A
        page = await _test_page(shell, user, A)
        assert page.gone
        assert _one(user, "gone").text == f"{A} is not a test of this project"
        cfg3, work3 = _two_var_project(tmp_path / "again")
        shell.session.open(cfg3, work_dir=work3)  # A is a test again
        await _settled(lambda: not page.gone)
        assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]
        assert _none(user, "gone") and page.badge.text == "FAIL"


async def test_a_raising_rebuild_is_tried_again_by_the_next_sync(
    shell, monkeypatch, caplog
):
    """The new shape was recorded BEFORE the rebuild ran: a raise inside it
    left the page showing the old cards with the NEW shape on record, and
    no later sync ever rebuilt it."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        real = page.rebuild
        boom = {"on": True}

        def flaky(view):
            if boom["on"]:
                raise RuntimeError("the layout moved under the rebuild")
            real(view)

        monkeypatch.setattr(page, "rebuild", flaky)
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.stage([PatchOp(A, "add", "/plots", [_plot("x")])])
            await asyncio.sleep(0.4)
        assert len(page.cards) == 2  # every try raised; the old cards stand
        boom["on"] = False
        with page.client:
            page.sync()
        assert [c.resolved.series for c in page.cards] == [("x",)]


async def test_a_jump_to_a_criterion_on_no_plot_says_so(shell, monkeypatch):
    """A criterion whose variable is on no plot of this test has no row to
    open: the jump said nothing and — with another editor open — scrolled to
    THAT row instead."""
    from dstf.authoring.shell import test_page as page_mod

    shell.session.stage([PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}]}])])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 1
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        scrolled: list[str] = []
        monkeypatch.setattr(page_mod.ui, "run_javascript", scrolled.append)
        (jump,) = [
            j for j in user.find(marker="tree:jump").elements if j.text == "nrmse · y"
        ]
        _click(user, jump)
        await _settled(lambda: user.notify.messages != [])
        assert user.notify.messages == ["y is on no plot of this test"]
        assert scrolled == []  # never the OTHER editor's row
        assert page.open == (0, T0)


async def test_the_same_criterion_on_two_cards_swaps_the_editor(shell):
    """The open editor is (card, path), never the path alone: one variable
    drawn on two plots has the same criterion row on both."""
    shell.session.stage([PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}]}] * 2)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [list(c.rows) for c in page.cards] == [[T0], [T0]]
        user.find(marker=f"criterion:1:{T0}").click()
        await _settled(lambda: page.open == (1, T0))
        _click(user, page.cards[0].rows[T0].row)
        await _settled(lambda: page.open == (0, T0))
        assert _none(user, f"editor:1:{T0}")


async def test_a_jump_opens_the_first_card_holding_the_row(shell):
    """With no card named, the jump takes the first plot that draws it."""
    shell.session.stage([PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}]}] * 2)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        (jump,) = [
            j for j in user.find(marker="tree:jump").elements if j.text == "nrmse · x"
        ]
        _click(user, jump)
        await _settled(lambda: page.open is not None)
        assert page.open == (0, T0)


# -- following the Session plot by plot, and Focus --------------------------

#: A criterion on each of A's variables: a tube on x whose band the trace
#: leaves (33.3% inside), and a passing range on y.
TUBE_AND_RANGE = {
    "combinator": "and",
    "children": [
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.5, "upper": 0.1, "lower": 0.2},
                {"time": 0.9, "upper": 0.1, "lower": 0.1},
            ],
        },
        {"metric": "range", "variable": "y", "min_value": -1},
    ],
}
#: The same band, wide enough to hold the trace: the verdict and the readout
#: move with it ("100.0% inside tube → PASS").
WIDE = [{"time": 0.5, "upper": 5, "lower": 5}, {"time": 0.9, "upper": 5, "lower": 5}]
#: And the same again with one point moved along: the PARAMETERS move and
#: the plot's band with them, while the verdict and the readout do not — a
#: signature without `params` draws nothing here.
MOVED = [{"time": 0.55, "upper": 5, "lower": 5}, {"time": 0.9, "upper": 5, "lower": 5}]


def _points(shell, path, points):
    shell.session.stage(
        [PatchOp(A, "replace", f"{path}/tube_points", copy.deepcopy(points))]
    )


async def test_an_edit_redraws_only_the_plots_it_changed(shell):
    """Follow by signature: the plot whose criterion moved is re-sent, and
    it alone — the cost is elements on the wire, not a figure assembled."""
    _stage_tree(shell, A, TUBE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        cards = list(page.cards)
        with page.client:
            for card in cards:
                card.show()
        on_x, on_y = _counted(cards[0]), _counted(cards[1])
        readout = cards[0].rows[T0].readout
        assert "33.3%" in readout.text
        _points(shell, T0, WIDE)
        await _settled(lambda: "100.0%" in readout.text)
        assert (on_x[0], on_y[0]) == (1, 0)
        assert page.cards == cards  # painted in place, not rebuilt
        # The band moves under the trace: the parameters are part of what the
        # plot DRAWS, and this edit moves nothing else.
        said = readout.text
        _points(shell, T0, MOVED)
        await _settled(lambda: on_x[0] == 2)
        assert readout.text == said and on_y[0] == 0


async def test_an_unshown_plot_is_not_built_by_an_edit(shell):
    """A plot nobody has scrolled to stays a slot: an edit does not draw it."""
    _stage_tree(shell, A, TUBE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        readout = page.cards[0].rows[T0].readout
        _points(shell, T0, WIDE)
        await _settled(lambda: "100.0%" in readout.text)
        assert page.cards[1].plot is None


async def test_a_shape_change_rebuilds_the_cards_and_redraws_what_was_shown(shell):
    """A new criterion is a new row, which no repaint can add. What was
    drawn is drawn again: the browser's observer has fired for those slots
    and will not fire twice, so a rebuild would leave them blank."""
    _stage_tree(shell, A, TUBE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        with page.client:
            page.cards[1].show()
        old = page.cards[1]
        shell.session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/metrics/children/-",
                    {"metric": "range", "variable": "y", "max_value": 99},
                )
            ]
        )
        await _settled(lambda: len(page.cards[1].rows) == 2)
        assert page.cards[1] is not old
        assert page.cards[1].plot is not None
        assert page.cards[0].plot is None  # and nothing else is drawn
        assert page.tree.rows.visible


async def test_a_finished_job_redraws_every_shown_plot(shell):
    """Decision 10: a rerun changes the trajectories under every plot, and
    no leaf's signature sees that."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        sent = _counted(page.cards[0])
        with page.client:
            page.handle({"event": "job", "state": "finished"})
        assert sent[0] == 1
        assert page.cards[1].plot is None  # still nothing draws an unshown plot


async def test_a_discard_follows(shell):
    """Discard restores the loaded verdicts: the pill goes back and the plot
    with it."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        shell.session.stage([LOOSEN])  # x passes now
        await _settled(lambda: "dstf-pill-pass" in _one(user, "plot-pill:0:x").classes)
        sent = _counted(page.cards[0])
        shell.session.discard()
        await _settled(lambda: "dstf-pill-fail" in _one(user, "plot-pill:0:x").classes)
        assert sent[0] == 1


async def test_an_event_for_another_model_costs_this_page_nothing(shell):
    """The page follows ITS test: an event naming other models must not cost
    it a tree read, let alone a repaint of every row."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        session = shell.session
        calls = [0]
        real = session.tree_view

        def counting(model_id):
            calls[0] += 1
            return real(model_id)

        session.tree_view = counting
        with page.client:
            page.handle({"event": "results", "models": {D}})
            assert calls[0] == 0
            page.handle({"event": "loading", "phase": "results", "model": D})
            assert calls[0] == 0
            page.handle({"event": "results", "models": {A, D}})
            assert calls[0] == 1
            # A step that names no model at all is the whole project's.
            page.handle({"event": "loading", "phase": "done"})
        assert calls[0] == 2


async def test_one_cards_failure_leaves_the_others_painted(shell, caplog):
    """One plot's exception is logged against that plot and stops nothing."""
    import logging

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        painted: list = []
        real = page.cards[1].paint

        def boom(view):
            raise RuntimeError("no")

        page.cards[0].paint = boom
        page.cards[1].paint = lambda view: painted.append(view) or real(view)
        with caplog.at_level(logging.ERROR, logger="dstf"), page.client:
            page.sync()
        assert painted
        assert [r for r in caplog.records if "plot 0 of FakeLib.A" in r.getMessage()]


async def test_a_form_edit_redraws_its_card_once_and_keeps_the_rings(shell):
    """With an editor open, a keystroke in its form is one edit: the card it
    is on is re-sent once, and the criterion is still the open one — its
    rings stay on the plot being edited."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        sent = _counted(card)
        (cell,) = _numbers(page, "Lower bound (optional)")
        with page.client:
            cell.set_value(-3.0)
        await _settled(lambda: (_pending(shell, T1) or {}).get("min_value") == -3.0)
        await _settled(lambda: sent[0] == 1)
        await asyncio.sleep(0.3)
        assert sent[0] == 1
        assert page.open == (0, T1)
        assert _sans_rev(card.plot.figure) == shell.session.plot_figure(
            D, 0, editing=(T1,)
        )


async def test_focus_hides_the_other_cards_and_the_bar_brings_them_back(shell):
    """Board 5A: "Showing 1 of N plots: <name>", the focused plot's button
    reading "Focused", and the bar's own way back to all of them."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.focus_bar.visible is False
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert [c.card.visible for c in page.cards] == [True, False]
        assert _one(user, "focus-bar:text").text == "Showing 1 of 2 plots: x"
        assert _one(user, "focus-bar:all").text == "Show all 2 plots"
        assert _one(user, "focus:0").text == "Focused"
        user.find(marker="focus-bar:all").click()
        await _settled(lambda: page.focused is None)
        assert all(c.card.visible for c in page.cards)
        assert page.focus_bar.visible is False
        assert _one(user, "focus:0").text == "Focus"
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        user.find(marker="focus:0").click()  # the focused plot's own button
        await _settled(lambda: page.focused is None)
        assert all(c.card.visible for c in page.cards)


async def test_focus_survives_an_edit_and_ends_on_a_rebuild(shell):
    """Focus names an INDEX: after a rebuild the indices may name other
    plots, so it ends there — and an edit that only repaints leaves it."""
    _stage_tree(shell, A, TUBE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        readout = page.cards[0].rows[T0].readout
        _points(shell, T0, WIDE)
        await _settled(lambda: "100.0%" in readout.text)
        assert page.focused == 0 and page.cards[1].card.visible is False
        shell.session.stage([PatchOp(A, "add", "/plots", [{"series": [{"var": "y"}]}])])
        await _settled(lambda: len(page.cards) == 1)
        assert page.focused is None
        assert page.cards[0].card.visible is True
        assert page.focus_bar.visible is False
        assert _one(user, "focus:0").text == "Focus"


async def test_opening_a_criterion_on_a_hidden_card_ends_the_focus(shell):
    """The editor has to be on screen: a jump to a criterion on another
    plot ends Focus rather than open an editor nobody can see."""
    _stage_tree(shell, A, TWO_RANGES)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        with page.client:
            page.open_criterion(T1)
        assert page.focused is None
        assert page.open == (1, T1)
        assert page.cards[1].card.visible is True


async def test_a_mode_switch_keeps_the_editor_open(shell):
    """A leaf's metric is not part of the page's SHAPE: switching modes in
    the editor's own form repaints the row's name and leaves the editor
    open, with the other panel following the new mode."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        editor = _one(user, f"editor:0:{T1}")
        (mode,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, ui.select)
            and e.props.get("label") == "mode"
            and editor in list(_ancestors(e))
        ]
        with page.client:
            mode.set_value("tube")
        await _settled(lambda: page.cards[0].rows[T1].name.text == "tube · x")
        assert page.open == (0, T1)
        await _settled(lambda: not _none(user, f"{T1}:tube_points:add"))
