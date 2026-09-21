"""The Test page (brief §3.2; roadmap §3): the sticky test header, the
criteria tree closed to one line, the plots drawn on scroll with their
criteria rows, the one open editor below its row, the page following the
Session plot by plot. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import copy

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from capability_host import open_menu  # noqa: E402
from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Artifact, Job, Placement  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell.actions import ActionBar, InlineHost  # noqa: E402
from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
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
        assert _none(user, "subheader") and _none(user, "tree")


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


async def test_show_builds_the_plot_once_with_no_rings(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
            plot = page.cards[0].plot
            assert plot.figure == shell.session.plot_figure(A, 0, editing=())
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
        assert figure == shell.session.plot_figure(D, 0, editing=())
        assert figure != shell.session.plot_figure(D, 0)
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
        assert card.plot.figure == shell.session.plot_figure(D, 0, editing=(T0,))
        rings = {
            s["name"]
            for s in card.plot.figure["layout"]["shapes"]
            if s.get("type") == "circle"
        }
        assert rings and all(name.startswith(T0 + "/") for name in rings)
        user.find(marker=f"editor-done:0:{T0}").click()
        await _settled(lambda: page.open is None)
        assert card.plot.figure == shell.session.plot_figure(D, 0, editing=())
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
        assert card.plot.figure == shell.session.plot_figure(D, 0, editing=(T0,))
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
