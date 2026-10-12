"""The Test page (brief §3.2; roadmap §3): the sticky test header, the
criteria tree closed to one line, the plots drawn on scroll with their
criteria rows, the one open editor below its row, the page following the
Session plot by plot. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import time

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Artifact, Patch, Placement  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell import plot_card  # noqa: E402
from dstf.authoring.shell.actions import ActionBar, InlineHost  # noqa: E402
from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.test_page import STATS, TestPage  # noqa: E402
from dstf.authoring.shell.variables_drawer import marker as var_marker  # noqa: E402
from dstf.authoring.shell.widgets import MiddleCut, middle_split  # noqa: E402
from dstf.authoring.situation import CANCELLED_SENTENCE, Situation  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from dstf.figures.plotly import RING_PATH  # noqa: E402
from dstf.reporting.tree_rows import leaves_of  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import (  # noqa: E402
    CASES_X,
    LOOSEN,
    A,
    B,
    C,
    D,
    G,
    _two_var_project,
)
from test_session_queue import fake_item  # noqa: E402


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


def _barred(shell):
    """A bare page holding one ActionBar at TEST (the placements a bar
    hosts: SUITE and TEST); `bars[0]` once built. Its selection carries a
    variable so a probe's panel can name what it was built for."""
    bars: list[ActionBar] = []

    def root() -> None:
        bars.append(
            ActionBar(
                shell,
                Placement.TEST,
                lambda: Selection(tests=frozenset({A}), variable="x"),
                marker="test-actions",
            )
        )

    return bars, root


def _logged(caplog, text: str) -> bool:
    """Whether the shell logged a failed act whose exception says ``text``."""
    return any(
        r.name == "dstf.authoring.shell.actions"
        and r.exc_info is not None
        and text in str(r.exc_info[1])
        for r in caplog.records
    )


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


async def test_an_act_error_shows_above_its_panel_and_a_success_clears_it(
    shell, caplog
):
    """A ValueError reads as its text alone, but it may be a bug rather than
    a person's edit (a JSONDecodeError is one), so it is also logged."""
    probe = _Probe("one", {Placement.CRITERION}, raises="no")
    _only(probe, _Probe("two", {Placement.CRITERION}))
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        go = _one(user, "go:one")
        user.find(marker="go:one").click()
        await _settled(lambda: box[0].errors["one"].text == "no")
        assert box[0].errors["one"].visible
        assert _logged(caplog, "no")
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


async def test_a_host_says_each_distinct_sentence_once(shell):
    """Two capabilities unavailable for the same reason drew it twice, one
    line each: a criterion's editor on a test with no tree read the
    Session's reason under Edit criteria and again under Edit on plot. The
    first capability to say a sentence says it; a different sentence is a
    line of its own."""
    _only(
        _Probe("one", {Placement.CRITERION}, answer="needs a baseline"),
        _Probe("two", {Placement.CRITERION}, answer="needs a baseline"),
        _Probe("three", {Placement.CRITERION}, answer="three has no handles"),
    )
    box, root = _hosted(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        assert _one(user, "why:one").text == "needs a baseline"
        assert _none(user, "why:two")
        assert _one(user, "why:three").text == "three has no handles"


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
        probe.answer = "needs a baseline"
        box[0].follow()
        assert _none(user, "probe:one")
        assert _one(user, "why:one").text == "needs a baseline"
        probe.answer = True
        box[0].follow()
        assert _none(user, "why:one") and _one(user, "probe:one") is not panel


async def test_an_action_bars_dialog_closes_only_for_an_act_that_applied(shell):
    """Like `InlineHost.on_applied`: every plot sends `{"autosize": true}`
    at mount, and a panel that answers such a relayout with None changed
    nothing — the dialog it sits in must stay open."""
    probe = _Probe("one", {Placement.TEST})  # its act returns None
    _only(probe)
    bars, root = _barred(shell)
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


async def test_an_action_bars_dialog_stays_open_on_a_refused_act(shell, caplog):
    """A raising act changed nothing: the dialog stays open with the error
    above its panel and what was chosen in it, so the person can correct
    it there. Once the act goes through, the dialog closes."""
    probe = _Probe("one", {Placement.TEST}, raises="no", outcome=OUTCOME)
    _only(probe)
    bars, root = _barred(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:one").click()
        await user.should_see(marker="probe:one")
        user.find(marker="go:one").click()
        await _settled(lambda: _one(user, "error:one").visible)
        assert _one(user, "error:one").text == "no"
        assert _logged(caplog, "no")
        await asyncio.sleep(0.1)
        assert bars[0].dialog.value is True
        probe.raises = None
        user.find(marker="go:one").click()
        await _settled(lambda: bars[0].dialog.value is False)


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


def _hold(shell, model, state: ItemState = ItemState.WAITING):
    """``model`` in the simulation queue in ``state``, with no pool behind
    it, said to the pages as the Session says it."""
    item = fake_item(shell.session, model, state)
    shell.session._emit(
        "queue",
        state="started" if state is ItemState.RUNNING else "queued",
        models={model},
    )
    shell.tick()
    return item


def _stage_tree(shell, model=D, tree=None):
    shell.session.stage(
        [PatchOp(model, "add", "/metrics", copy.deepcopy(tree if tree else TREE))]
    )


def _range(lo):
    return {"metric": "range", "variable": "x", "min_value": lo}


async def _open_form(user, marker: str, within: str) -> None:
    """Click the cluster's icon marked ``marker`` and wait for its form: an
    Edit tree form is built on the icon's click, into a row under its host's
    row. The row sits OUTSIDE the host, so its buttons are found without
    ``within`` — there is one open form on the page."""
    _click_in(user, marker, within=within)
    kind, path = marker.split(":", 1)
    await _settled(lambda: not _none(user, f"apply-{kind}:{path}"))


async def test_the_test_header_is_a_second_row_of_the_shared_header(shell):
    """Decision 8: a second row INSIDE `ui.header`, so it is sticky with no
    offset arithmetic and no dependence on the first row's height."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        sub = _one(user, "subheader")
        for marker in ("back", "model", "status"):
            assert sub in list(_ancestors(_one(user, marker))), marker
        # Its TEST-placement buttons are the strip's, the header's third row.
        assert sub not in list(_ancestors(_one(user, "test-actions")))
        header = sub.parent_slot.parent
        assert header in list(_ancestors(_one(user, "strip")))
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


async def test_a_tree_rows_controls_follow_a_refusal_that_went(no_tree_shell):
    """A tree opened while the Session will not materialize the test's tree
    draws its rows on that refusal; the rows are not rebuilt when a staged
    override lifts it, so without `follow` that sentence would stand for
    the rest of the page's life."""
    from dstf.authoring.session import NO_TREE

    shell = no_tree_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        host = "node:/metrics"
        row = page.tree.rows_by_path["/metrics"]
        assert _inside(user, "wrap:/metrics", host) == []
        (why,) = _inside(user, "why:edit-tree", host)
        assert why.text == NO_TREE
        shell.session.stage([SHADOW_X])
        await _settled(lambda: bool(_inside(user, "wrap:/metrics", host)))
        assert _inside(user, "why:edit-tree", host) == []
        assert page.tree.rows_by_path["/metrics"] is row  # followed, not rebuilt


async def test_a_criteria_rows_controls_follow_a_refusal_that_came(no_tree_shell):
    """The reverse: without `follow` a row drawn while its tree could be
    edited would keep clickable controls once the Session refuses it, and
    the error a click earns then would stay on the row."""
    from dstf.authoring.session import NO_TREE

    shell = no_tree_shell
    shell.session.stage([SHADOW_X])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        host = f"node:0:{T0}"
        row = page.cards[0].rows[T0]
        assert _inside(user, f"wrap:{T0}", host)
        shell.session.discard()
        await _settled(lambda: _inside(user, f"wrap:{T0}", host) == [])
        (why,) = _inside(user, "why:edit-tree", host)
        assert why.text == NO_TREE
        assert page.cards[0].rows[T0] is row  # followed, not rebuilt
        shell.session.stage([SHADOW_X])
        await _settled(lambda: bool(_inside(user, f"wrap:{T0}", host)))


async def test_a_wrap_from_the_tree_restages_the_rows(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        host = "node:/metrics/children/1"
        await _open_form(user, "wrap:/metrics/children/1", within=host)
        user.find(marker="apply-wrap:/metrics/children/1").click()
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
        await _open_form(user, "wrap:/metrics/children/1", within=host)
        user.find(marker="apply-wrap:/metrics/children/1").click()
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
        # The kind is the dropdown beside it; the text is the count.
        assert text.text == "k=1 · 2 criteria"
        krow = _one(user, f"tree-row:{combinator}")
        shell.session.stage([PatchOp(A, "replace", f"{combinator}/k", 2)])
        await _settled(lambda: text.text == "k=2 · 2 criteria")
        assert _one(user, f"tree-row:{combinator}") is krow


#: A tree five levels deep: and[and[r, and[r, and[r, and[r]]]], r] — the
#: fifth level's bar takes the first level's colour again.
DEEP = {
    "combinator": "and",
    "children": [
        {
            "combinator": "and",
            "children": [
                _range(-1),
                {
                    "combinator": "and",
                    "children": [
                        _range(5),
                        {
                            "combinator": "and",
                            "children": [
                                _range(-3),
                                {"combinator": "and", "children": [_range(-4)]},
                            ],
                        },
                    ],
                },
            ],
        },
        _range(-5),
    ],
}


def _bars(row) -> list[str]:
    """The level classes of ``row``'s bars, in order."""
    (bars,) = [c for c in row.default_slot.children if "dstf-bars" in c.classes]
    return [
        next(k for k in bar.classes if k.startswith("dstf-lvl-"))
        for bar in bars.default_slot.children
    ]


async def test_a_rows_bars_say_its_depth_and_the_root_says_root(shell):
    """The rows are one flat column: a bar per level is what says what is
    under what (Q1) — level i's bar in level i's colour, the cycle
    repeating at five — and the root draws none and says `root` (Q2). No
    indent: the bars are it (Y3)."""
    from dstf.authoring.tree_text import nodes_of

    _stage_tree(shell, A, DEEP)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        view = shell.session.tree_view(A)
        depths = []
        for node in nodes_of(view):
            path = str(node["path"])
            depth = path.count("/children/")
            depths.append(depth)
            row = _one(user, f"tree-row:{path}")
            assert row.props["data-tree-path"] == path
            assert row.props["data-depth"] == str(depth), path
            group = node.get("kind") != "leaf"
            assert (row.props.get("data-group") == "1") is group, path
            assert _bars(row) == [
                f"dstf-lvl-{(i - 1) % 4 + 1}" for i in range(1, depth + 1)
            ], path
            assert "padding-left" not in dict(row.style), path
            # The bars first, then the chevron's place: a criterion's empty.
            first, second = list(row.default_slot.children)[:2]
            assert "dstf-bars" in first.classes and "dstf-chev-slot" in second.classes
        assert max(depths) == 5
        assert _bars(_one(user, "tree-row:/metrics")) == []
        tag = _one(user, "tree:root-tag")
        assert tag.text == "root"
        assert _one(user, "tree-row:/metrics") in list(_ancestors(tag))


async def test_a_moves_line_starts_under_the_moved_rows_pill(shell):
    """E4: the "Moved." line under a row is indented to that row's pill —
    its bars, the chevron's place and the handle's, each with its gap."""
    from dstf.authoring.shell.tree_card import pill_left

    _stage_tree(shell, A, DEEP)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        path = "/metrics/children/0/children/1"
        page.tree.say(path, "Moved.")
        line = _one(user, "moved-note")
        assert dict(line.style)["padding-left"] == f"{pill_left(2)}px"
        assert pill_left(2) == 8 * 2 + 12 + 28 + 12 + 20 + 12


# -- folding (U5 Task 2) -----------------------------------------------------

G0, G1, G2, G3 = (
    "/metrics/children/0",
    "/metrics/children/0/children/1",
    "/metrics/children/0/children/1/children/1",
    "/metrics/children/0/children/1/children/1/children/1",
)


def _hidden(page) -> set[str]:
    """The tree rows the server renders hidden."""
    return {
        path for path, row in page.tree.rows_by_path.items() if row.props.get("hidden")
    }


def _under(page, group: str) -> set[str]:
    return {p for p in page.tree.rows_by_path if p.startswith(group + "/")}


async def _deep_open(shell, user, tree=None) -> TestPage:
    _stage_tree(shell, A, tree or DEEP)
    page = await _test_page(shell, user)
    user.find(marker="tree:toggle").click()
    await _settled(lambda: page.tree.rows.visible)
    return page


def _fold(user, path: str) -> None:
    user.find(marker=f"fold:{path}").click()


def test_a_folded_groups_text_counts_its_criteria_by_verdict():
    """Q4 / E6: every criterion under the group, however deep — each once —
    by verdict; fail, advisory and unscored only when there are any."""
    from dstf.authoring.shell.tree_card import fold_text

    def leaf(passed, advisory=False):
        return {"kind": "leaf", "passed": passed, "advisory": advisory}

    node = {
        "kind": "combinator",
        "children": [
            leaf(True),
            {"kind": "combinator", "children": [leaf(False), leaf(True)]},
            {"kind": "combinator", "children": [leaf(False, advisory=True)]},
            leaf(None),
        ],
    }
    assert fold_text(node) == ("· 5 hidden · 2 pass · 1 fail · 1 advisory · 1 unscored")
    assert fold_text({"kind": "combinator", "children": [leaf(True)]}) == (
        "· 1 hidden · 1 pass"
    )


async def test_a_folded_group_hides_its_rows_and_says_what_it_hides(shell):
    """Q3 / Q4 / Y6 / E2: a group's chevron folds it — its descendants'
    rows AND form slots hidden (attributes, set by a props update), the
    path in the tab's state, its row saying what it hides counted by
    verdict, a nested folded group's criteria counted once — and unfolds
    it, rebuilding nothing."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        rev = page.tree.revision
        assert _none(user, "fold:/metrics")  # the root folds with Show/Hide
        assert _none(user, f"fold:{G0}/children/0")  # a criterion has none
        chev = _one(user, f"fold:{G1}")
        assert chev.props["aria-expanded"] == "true"
        assert chev.props["aria-label"] == "Fold and · 3 criteria"
        _fold(user, G1)
        await _settled(lambda: _hidden(page))
        assert page.state.tree_folded == {A: [G1]}
        assert _hidden(page) == _under(page, G1)
        slots = {
            path
            for path, target in page.tree.targets.items()
            if target.slot is not None and target.slot.props.get("hidden")
        }
        assert slots == _under(page, G1)
        assert page.tree.rows_by_path[G1].props.get("data-folded") == "1"
        assert chev.props["aria-expanded"] == "false"
        assert chev.props["aria-label"] == "Unfold and · 3 criteria"
        assert _one(user, f"tree-summary:{G1}").text == "· 3 hidden · 2 pass · 1 fail"
        _fold(user, G3)  # inside it: hidden already, folded too
        _fold(user, G0)
        await _settled(lambda: len(page.state.tree_folded[A]) == 3)
        assert _one(user, f"tree-summary:{G0}").text == "· 4 hidden · 3 pass · 1 fail"
        assert page.tree.revision == rev  # nothing rebuilt
        _fold(user, G0)
        await _settled(lambda: G0 not in page.state.tree_folded[A])
        # G1 is still folded: what is under it stays hidden.
        assert _hidden(page) == _under(page, G1)
        assert "data-folded" not in page.tree.rows_by_path[G0].props
        _fold(user, G1)
        _fold(user, G3)
        await _settled(lambda: not page.state.tree_folded)
        assert _hidden(page) == set()
        assert page.tree.revision == rev


async def test_collapse_all_and_expand_all(shell):
    """Y7: Collapse all folds every group but the root; Expand all none."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        user.find(marker="tree:collapse-all").click()
        await _settled(lambda: _hidden(page))
        assert sorted(page.state.tree_folded[A]) == [G0, G1, G2, G3]
        assert _hidden(page) == _under(page, G0)
        user.find(marker="tree:expand-all").click()
        await _settled(lambda: not _hidden(page))
        assert not page.state.tree_folded


async def test_a_tree_of_criteria_alone_has_no_collapse_all(shell):
    _stage_tree(shell, A, {"combinator": "and", "children": [_range(-1), _range(5)]})
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        assert _none(user, "tree:collapse-all") and _none(user, "tree:expand-all")


async def test_the_folded_set_is_the_tabs_per_test(shell):
    """Q8: switching to another test and back keeps it, and so does opening
    the test again in the tab (the tab's state)."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        _fold(user, G1)
        await _settled(lambda: _hidden(page))
        for model in (D, A, A):
            page = await _test_page(shell, user, model)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        assert page.state.tree_folded == {A: [G1]}
        assert _hidden(page) == _under(page, G1)
        assert page.tree.rows_by_path[G1].props.get("data-folded") == "1"


async def test_a_move_rekeys_the_folded_groups_it_shifts(shell):
    """Q8 / R1 / E1: a folded group that moved is folded at its new path —
    and a folded group whose path a move SHIFTED (a sibling left before it)
    is folded at ITS new path, not lost, and no other group takes its
    place."""
    tree = {
        "combinator": "and",
        "children": [
            {"combinator": "and", "children": [_range(-1)]},
            {"combinator": "and", "children": [_range(-2)]},
            {"combinator": "or", "children": [_range(-3), _range(-4)]},
        ],
    }
    a, b, c = (f"/metrics/children/{i}" for i in range(3))
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, a)
        _fold(user, c)
        await _settled(lambda: page.state.tree_folded.get(A) == [a, c])
        # a onto the root: appended last. b and c each move up one.
        page.tree.dropped(
            {"src": a, "target": "/metrics", "zone": "onto", "rev": page.tree.revision}
        )
        await _settled(lambda: page.tree.said is not None)
        # The `or`, now second; a, moved, folded where it went.
        assert page.state.tree_folded == {A: [b, c]}
        assert _hidden(page) == {
            f"{b}/children/0",
            f"{b}/children/1",
            f"{c}/children/0",
        }


async def test_a_swap_of_twin_groups_moves_the_fold_onto_the_row_standing(shell):
    """R1 (the U5 review, H1): two groups whose rows have the same shape
    trade places — the rows are painted, not rebuilt — and the fold, re-keyed
    to the moved group's new path, is drawn on the row standing there, not
    left on the row it was folded on."""
    twin = {
        "combinator": "and",
        "children": [
            {"combinator": "and", "children": [_range(-1)]},
            {"combinator": "and", "children": [_range(-2)]},
        ],
    }
    a, b = "/metrics/children/0", "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, twin)
        _fold(user, a)
        await _settled(lambda: page.state.tree_folded.get(A) == [a])
        rev = page.tree.revision
        page.tree.dropped({"src": a, "target": "/metrics", "zone": "onto", "rev": rev})
        await _settled(lambda: page.tree.said is not None)
        assert page.tree.revision == rev  # painted, not rebuilt
        assert page.state.tree_folded == {A: [b]}
        assert page.tree.rows_by_path[b].props.get("data-folded") == "1"
        assert page.tree.rows_by_path[a].props.get("data-folded") is None
        assert _hidden(page) == _under(page, b)


#: and[r, and[r], and[r, r]]: a folded group after a criterion and a group.
SHIFTED = {
    "combinator": "and",
    "children": [
        _range(-1),
        {"combinator": "and", "children": [_range(-2)]},
        {"combinator": "and", "children": [_range(-3), _range(-4)]},
    ],
}


@pytest.mark.parametrize(
    ("marker", "at", "folded"),
    [
        # The criterion before it removed: it moves up one.
        ("remove", "/metrics/children/0", ["/metrics/children/1"]),
        # A criterion added after the first one: it moves down one.
        ("add", "/metrics/children/0", ["/metrics/children/3"]),
        # It wrapped: one level down, folded still.
        ("wrap", "/metrics/children/2", ["/metrics/children/2/children/0"]),
    ],
)
async def test_a_fold_follows_its_group_through_an_edit(shell, marker, at, folded):
    """R1: every tree edit says where each node went, and the folded set
    follows it — a group shifted by an add or a remove before it, or pushed
    down by a wrap, is folded where it went."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, SHIFTED)
        _fold(user, "/metrics/children/2")
        await _settled(lambda: page.state.tree_folded.get(A) == ["/metrics/children/2"])
        rev = page.tree.revision
        _click_in(user, f"{marker}:{at}", within=f"tree-row:{at}")
        if marker != "remove":
            await _settled(lambda: not _none(user, f"apply-{marker}:{at}"))
            user.find(marker=f"apply-{marker}:{at}").click()
        await _settled(lambda: page.tree.revision != rev)
        assert page.state.tree_folded == {A: folded}
        assert _hidden(page) == _under(page, folded[0])
        assert page.tree.rows_by_path[folded[0]].props.get("data-folded") == "1"


async def test_an_unwrap_lifts_a_folded_group_and_keeps_it_folded(shell):
    """R1: the group's parent unwrapped (the kind dropdown's last entry):
    the folded group takes its parent's place, folded still; the parent's
    own fold goes with it."""
    tree = {
        "combinator": "and",
        "children": [
            _range(-1),
            {
                "combinator": "or",
                "children": [
                    {"combinator": "and", "children": [_range(-2), _range(-3)]},
                ],
            },
        ],
    }
    inner, outer = "/metrics/children/1/children/0", "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, inner)
        await _settled(lambda: page.state.tree_folded.get(A) == [inner])
        rev = page.tree.revision
        (kind,) = _inside(user, f"kind:{outer}", f"tree-row:{outer}")
        kind.set_value("unwrap…")
        await _settled(lambda: page.tree.revision != rev)
        assert page.state.tree_folded == {A: [outer]}
        assert _hidden(page) == _under(page, outer)


async def test_a_removed_groups_fold_is_dropped(shell):
    """Q8: removed through its own control (its path in no edit's map) or
    staged from anywhere else (its path naming no group at the sync)."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        _fold(user, G3)
        _fold(user, G2)
        await _settled(lambda: len(page.state.tree_folded.get(A, [])) == 2)
        _click_in(user, f"remove:{G3}", within=f"tree-row:{G3}")
        await _settled(lambda: G3 not in page.tree.rows_by_path)
        assert page.state.tree_folded == {A: [G2]}
        shell.session.stage([PatchOp(A, "remove", G2)])
        await _settled(lambda: G2 not in page.tree.rows_by_path)
        assert not page.state.tree_folded.get(A)


async def test_a_move_into_a_folded_group_opens_just_the_path_to_it(shell):
    """R2: a move landing inside a folded group unfolds that group and its
    folded ancestors — and no other folded group."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        leaf = "/metrics/children/1"
        _fold(user, G2)
        _fold(user, G1)
        _fold(user, G3)
        await _settled(lambda: len(page.state.tree_folded.get(A, [])) == 3)
        # The leaf onto G2: appended inside G1 > G2; G3 (G2's child) stays.
        page.tree.dropped(
            {"src": leaf, "target": G2, "zone": "onto", "rev": page.tree.revision}
        )
        await _settled(lambda: page.tree.said is not None)
        assert sorted(page.state.tree_folded[A]) == [G3]
        assert _hidden(page) == {f"{G3}/children/0"}


async def test_a_refusal_under_a_folded_row_opens_just_the_path_to_it(shell):
    """R2: a refused act's sentence under a row inside a folded group — a
    drop that started on rows rebuilt since — unfolds the path to it."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        for group in (G3, G2, G1, G0):
            _fold(user, group)
        await _settled(lambda: len(page.state.tree_folded.get(A, [])) == 4)
        target = f"{G2}/children/0"
        page.tree.dropped(
            {
                "src": "/metrics/children/1",
                "target": target,
                "zone": "before",
                "rev": -1,
            }
        )
        await _settled(lambda: len(page.state.tree_folded[A]) == 1)
        assert page.state.tree_folded[A] == [G3]  # its sibling's fold stays
        assert _hidden(page) == _under(page, G3)
        await user.should_see("The tree changed while you dragged: try again.")


async def test_a_form_under_a_folded_row_opens_just_the_path_to_it(page_shell):
    """R2: a form row opened under a row inside a folded group unfolds the
    path to it; another folded group stays folded."""
    shell = page_shell
    _only(_RowProbe())
    tree = {
        "combinator": "and",
        "children": [
            {"combinator": "and", "children": [_range(-1), _range(-2)]},
            {"combinator": "and", "children": [_range(-3)]},
        ],
    }
    inner = "/metrics/children/0/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, "/metrics/children/0")
        _fold(user, "/metrics/children/1")
        await _settled(lambda: len(page.state.tree_folded.get(A, [])) == 2)
        # Under the same criterion's row on its PLOT: outside the tree.
        _click_in(user, f"probe-open:{inner}", within=f"criterion:0:{inner}")
        await user.should_see(marker=f"form-row:crit:0:{inner}")
        assert len(page.state.tree_folded[A]) == 2
        _click_in(user, f"probe-open:{inner}", within=f"tree-row:{inner}")
        await user.should_see(marker=f"form-row:tree:{inner}")
        assert page.state.tree_folded == {A: ["/metrics/children/1"]}
        assert _hidden(page) == {"/metrics/children/1/children/0"}


async def test_an_edit_outside_the_tree_leaves_the_folds_alone(page_shell):
    """R2: what this page does outside the tree unfolds nothing. A plot's
    `+` adds at the root's end; the header's named jump opens a criterion
    inside a folded group on its plot. (An edit staged from anywhere else
    unfolds every group: the owner's ruling, H2.)"""
    shell = page_shell
    tree = {
        "combinator": "and",
        "children": [
            {"combinator": "and", "children": [_range(-1), _range(-2), _range(5)]},
        ],
    }
    group = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, group)
        await _settled(lambda: page.state.tree_folded.get(A) == [group])
        assert (
            _one(user, f"tree-summary:{group}").text == "· 3 hidden · 2 pass · 1 fail"
        )
        _click_in(user, "add-for:x", within="criteria-header:0")
        await _settled(lambda: not _none(user, "apply-add:x"))
        user.find(marker="apply-add:x").click()
        await _settled(lambda: "/metrics/children/1" in page.tree.rows_by_path)
        assert page.state.tree_folded == {A: [group]}
        assert _hidden(page) == _under(page, group)
        with page.client:
            page.jump_to(f"{group}/children/2")
        await _settled(lambda: page.open is not None)
        assert page.state.tree_folded == {A: [group]}
        assert _hidden(page) == _under(page, group)


#: and[and[r], and[r, r]]: the group at /1 moves to /0 when /0 goes.
TWO_GROUPS = {
    "combinator": "and",
    "children": [
        {"combinator": "and", "children": [_range(-1)]},
        {"combinator": "and", "children": [_range(-2), _range(-3)]},
    ],
}


def _all_open(page) -> bool:
    return (
        not page.state.tree_folded.get(A)
        and not _hidden(page)
        and not any(
            row.props.get("data-folded") for row in page.tree.rows_by_path.values()
        )
    )


async def test_a_discard_unfolds_every_group(shell):
    """H2, the owner's ruling (2026-10-01): a tree change this page did not
    make itself unfolds every group of the test. Fold /1, remove /0 here
    (the fold follows its group to /0), then Discard: the tree is the
    saved one again and every group is open."""
    p0, p1 = "/metrics/children/0", "/metrics/children/1"
    _stage_tree(shell, A, TWO_GROUPS)
    shell.session.save()
    assert not shell.session.spec.pending
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        _fold(user, p1)
        await _settled(lambda: page.state.tree_folded.get(A) == [p1])
        _click_in(user, f"remove:{p0}", within=f"tree-row:{p0}")
        await _settled(lambda: p1 not in page.tree.rows_by_path)
        assert page.state.tree_folded == {A: [p0]}
        rev = page.tree.revision
        shell.session.discard(models={A})
        await _settled(lambda: page.tree.revision != rev)
        assert p1 in page.tree.rows_by_path
        assert _all_open(page)


async def test_an_edit_this_page_did_not_make_unfolds_every_group(shell):
    """H2: another tab's edit — a stage that is no act of this page — unfolds
    every group, though it moved no row (the rows are painted, not
    rebuilt)."""
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        rev = page.tree.revision
        shell.session.stage([PatchOp(A, "replace", f"{p0}/children/0/min_value", -5)])
        await _settled(lambda: not page.state.tree_folded.get(A))
        assert page.tree.revision == rev
        assert _all_open(page)


async def test_this_pages_own_edits_keep_the_folds(page_shell):
    """H2: an edit this page made is no reason to unfold — a criterion's
    editor on its plot (the folded group's summary follows the verdict it
    moved, painted in place) and a layout edit."""
    from dstf.authoring.layout import new_watch

    shell = page_shell
    tree = {
        "combinator": "and",
        "children": [
            {"combinator": "and", "children": [_range(-1), _range(5)]},
            _range(-2),
        ],
    }
    group, failing = "/metrics/children/0", "/metrics/children/0/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, group)
        await _settled(lambda: page.state.tree_folded.get(A) == [group])
        summary = _one(user, f"tree-summary:{group}")
        assert summary.text == "· 2 hidden · 1 pass · 1 fail"
        rev = page.tree.revision
        user.find(marker=f"criterion:0:{failing}").click()
        await _settled(lambda: page.open == (0, failing))
        (lower,) = _numbers(page, "Lower bound (optional)")
        with page.client:
            lower.set_value(-5.0)
        await _settled(lambda: summary.text == "· 2 hidden · 2 pass")
        assert page.tree.revision == rev  # painted, not rebuilt
        assert page.state.tree_folded == {A: [group]}
        cards = len(page.cards)
        with page.client:
            assert page.stage_layout(new_watch(shell.session.layout(A), "y"))
        await _settled(lambda: len(page.cards) == cards + 1)
        assert page.state.tree_folded == {A: [group]}
        assert _hidden(page) == _under(page, group)


#: and[and[r, r], r]: a group to fold and a criterion outside it.
GROUP_AND_LEAF = {
    "combinator": "and",
    "children": [
        {"combinator": "and", "children": [_range(-1), _range(5)]},
        _range(-2),
    ],
}


async def test_a_tree_editors_edit_keeps_the_folds(page_shell):
    """H2: the tree editor's edit is this page's own (`TreeCard`'s editor
    applies through `TestPage.apply`)."""
    shell = page_shell
    group, leaf = "/metrics/children/0", "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, GROUP_AND_LEAF)
        _fold(user, group)
        await _settled(lambda: page.state.tree_folded.get(A) == [group])
        _tree_click(user, leaf)
        await _settled(lambda: page.open is not None and page.open[1] == leaf)
        (lower,) = _numbers(page, "Lower bound (optional)")
        rev = shell.session.spec_revision
        with page.client:
            lower.set_value(-7.0)
        await _settled(lambda: shell.session.spec_revision > rev)
        shell.tick()  # every event of the edit heard
        assert page.state.tree_folded == {A: [group]}


async def test_reset_layout_keeps_the_folds(page_shell):
    """H2: Reset layout is this page's own edit."""
    from dstf.authoring.layout import new_watch

    shell = page_shell
    group = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, GROUP_AND_LEAF)
        _fold(user, group)
        await _settled(lambda: page.state.tree_folded.get(A) == [group])
        cards = len(page.cards)
        with page.client:
            assert page.stage_layout(new_watch(shell.session.layout(A), "y"))
        await _settled(lambda: len(page.cards) == cards + 1)
        rev = shell.session.spec_revision
        with page.client:
            page.reset_layout()
        await _settled(lambda: shell.session.spec_revision > rev)
        await _settled(lambda: len(page.cards) != cards + 1)
        shell.tick()  # every event of the edit heard
        assert page.state.tree_folded == {A: [group]}


async def test_a_plot_criteria_rows_remove_keeps_the_folds(page_shell):
    """H2: a plot's criteria row's − (and wrap) is this page's own edit."""
    shell = page_shell
    group, leaf = "/metrics/children/0", "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, GROUP_AND_LEAF)
        _fold(user, group)
        await _settled(lambda: page.state.tree_folded.get(A) == [group])
        rev = shell.session.spec_revision
        _click_in(user, f"remove:{leaf}", within=f"node:0:{leaf}")
        await _settled(lambda: shell.session.spec_revision > rev)
        shell.tick()  # every event of the edit heard
        assert page.state.tree_folded == {A: [group]}


async def test_a_page_left_in_the_tab_hears_the_new_pages_edit_as_the_tabs(
    page_shell,
):
    """J0: a page navigated away from stays in `Shell.pages` until NiceGUI's
    reconnect timeout (for ever in the simulation) and hears every event.
    The test opened again in the same tab, folded and edited there: the
    edit is the TAB's own, and the page left behind keeps the folds too —
    it heard the new page's edit as foreign and cleared them."""
    from dstf.authoring.layout import new_watch

    shell = page_shell
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        left = await _deep_open(shell, user, TWO_GROUPS)
        page = await _test_page(shell, user)  # the same test, the same tab
        assert left is not page and left in shell.pages
        assert left.state is page.state
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        cards = len(page.cards)
        with page.client:
            assert page.stage_layout(new_watch(shell.session.layout(A), "y"))
        await _settled(lambda: len(page.cards) == cards + 1)
        assert page.state.tree_folded == {A: [p0]}
        assert _hidden(page) == _under(page, p0)


async def test_another_tabs_edit_unfolds_this_tabs_groups(page_shell):
    """J0: the own revisions are the TAB's — an edit made on the same test
    in another tab is one this tab did not make, and unfolds its groups
    (the ruling, H2)."""
    from nicegui.testing import User

    from dstf.authoring.layout import new_watch

    shell = page_shell
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        other = User(user.http_client)
        await _open(other, f"/test/{A}")
        there = _page(shell, other)
        assert there.state is not page.state
        cards = len(there.cards)
        with there.client:
            assert there.stage_layout(new_watch(shell.session.layout(A), "y"))
        await _settled(lambda: len(there.cards) == cards + 1)
        await _settled(lambda: not page.state.tree_folded.get(A))
        assert _all_open(page)


async def _folded_then_removed(shell, user) -> TestPage:
    """A's /1 folded, /0 removed here (the fold follows its group to /0)
    over a saved TWO_GROUPS: a Discard brings /1 back."""
    p0, p1 = "/metrics/children/0", "/metrics/children/1"
    _stage_tree(shell, A, TWO_GROUPS)
    shell.session.save()
    page = await _test_page(shell, user)
    user.find(marker="tree:toggle").click()
    await _settled(lambda: page.tree.rows.visible)
    _fold(user, p1)
    await _settled(lambda: page.state.tree_folded.get(A) == [p1])
    _click_in(user, f"remove:{p0}", within=f"tree-row:{p0}")
    await _settled(lambda: p1 not in page.tree.rows_by_path)
    assert page.state.tree_folded == {A: [p0]}
    return page


def _heard_all(shell, state) -> bool:
    return state.seen_revision == shell.session.spec_revision


async def test_a_discard_heard_on_another_test_unfolds_this_ones_groups(shell):
    """K1: the clearing rule is the TAB's, heard by every page of it. The
    tab moved to D and A's page is gone (NiceGUI's reconnect timeout), then
    A's edits are discarded: back on A, the folds that followed the removed
    group are not left on the group the Discard put back there."""
    async with user_simulation(root=shell.index) as user:
        page = await _folded_then_removed(shell, user)
        here = await _test_page(shell, user, D)
        shell.pages.remove(page)
        shell.session.discard()  # the header's Discard
        await _settled(lambda: _heard_all(shell, here.state))
        assert not here.state.tree_folded.get(A)
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        assert "/metrics/children/1" in page.tree.rows_by_path
        assert _all_open(page)


async def test_a_discard_heard_on_the_suite_page_unfolds_the_tests_groups(shell):
    """K1: the Suite page hears the rule too."""
    async with user_simulation(root=shell.index) as user:
        page = await _folded_then_removed(shell, user)
        state = page.state
        await _open(user, "/")
        shell.pages.remove(page)
        shell.session.discard()
        await _settled(lambda: _heard_all(shell, state))
        assert not state.tree_folded.get(A)


async def test_another_tabs_edit_heard_on_another_test_unfolds_the_groups(shell):
    """K1: another tab's edit of A, heard while this tab shows D."""
    from nicegui.testing import User

    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        here = await _test_page(shell, user, D)
        shell.pages.remove(page)
        other = User(user.http_client)
        await _open(other, f"/test/{A}")
        there = _page(shell, other)
        assert there.state is not here.state
        with there.client:
            there.apply(Patch(ops=(PatchOp(A, "remove", p0),)))
        await _settled(lambda: _heard_all(shell, here.state))
        assert not here.state.tree_folded.get(A)


async def test_an_edit_of_another_test_leaves_this_tests_folds(shell):
    """K1: a `spec` event clears the folds of the tests it NAMES, and no
    other's: an edit of D, made from no page of this tab, keeps A's."""
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        _stage_tree(shell, D)
        await _settled(lambda: _heard_all(shell, page.state))
        assert page.state.tree_folded == {A: [p0]}
        assert _hidden(page) == _under(page, p0)


async def test_an_edit_made_while_the_tab_had_no_page_unfolds_on_open(shell):
    """K2: a tab with no live page hears nothing. Its pages gone (NiceGUI's
    reconnect timeout), A is edited elsewhere; the test opened again in
    the tab finds a revision it neither heard nor made, and every group is
    open."""
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        state = page.state
        for gone in [p for p in shell.pages if p.client is user.client]:
            shell.pages.remove(gone)
        shell.session.stage([PatchOp(A, "replace", f"{p0}/children/0/min_value", -5)])
        shell.tick()
        assert state.tree_folded == {A: [p0]}  # nobody heard it
        page = await _test_page(shell, user)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        assert _all_open(page)


async def test_rows_built_folded_hide_their_slots_and_name_the_chevron(shell):
    """Q3 / E2: rows BUILT under a folded group — the test opened again in
    the tab — hide their form slots as their rows, and the folded group's
    chevron says it is folded, as a fold made on the page does."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        _fold(user, G1)
        await _settled(lambda: _hidden(page))
        for model in (D, A):
            page = await _test_page(shell, user, model)
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.tree.rows.visible)
        slots = {
            path
            for path, target in page.tree.targets.items()
            if target.slot is not None and target.slot.props.get("hidden")
        }
        assert slots == _under(page, G1)
        chevron = page.tree.chevrons[G1]
        assert chevron.props["aria-expanded"] == "false"
        assert chevron.props["aria-label"].startswith("Unfold ")


async def test_removing_a_folded_group_does_not_fold_its_successor(shell):
    """R1: a folded group removed here is dropped from the folds — the
    group that takes its path is not folded in its place."""
    p0 = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, TWO_GROUPS)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        rev = page.tree.revision
        _click_in(user, f"remove:{p0}", within=f"tree-row:{p0}")
        await _settled(lambda: page.tree.revision != rev)
        assert "data-group" in page.tree.rows_by_path[p0].props
        assert _all_open(page)


async def test_a_k_change_renames_the_groups_chevron_in_place(shell):
    """The chevron is named by its group's text (`k-of-n` says its k): a k
    edited elsewhere renames it, painted, not rebuilt."""
    tree = {
        "combinator": "and",
        "children": [
            {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(-2)]}
        ],
    }
    group = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        chevron = page.tree.chevrons[group]
        before = chevron.props["aria-label"]
        rev = page.tree.revision
        shell.session.stage([PatchOp(A, "replace", f"{group}/k", 2)])
        await _settled(lambda: chevron.props["aria-label"] != before)
        assert page.tree.revision == rev
        assert chevron.props["aria-label"].startswith("Fold ")


async def test_a_hidden_tree_has_no_expand_or_collapse_all(shell):
    """Y7: Expand all / Collapse all are beside the tally while the rows
    show; Hide tree takes them away."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        assert not _none(user, "tree:collapse-all")
        assert not _none(user, "tree:expand-all")
        user.find(marker="tree:toggle").click()
        await _settled(lambda: not page.tree.rows.visible)
        assert _none(user, "tree:collapse-all") and _none(user, "tree:expand-all")


async def test_a_fold_whose_path_now_names_a_criterion_is_dropped(page_shell):
    """Y6: an edit of this page that does not say where its nodes went (a
    capability that never calls `Panel.edited`) leaves the folded set as it
    was; a folded path that names a criterion when the rows are rebuilt is
    dropped, not kept on the criterion now there."""
    shell = page_shell
    probe = _RowProbe()
    _only(probe)
    p0, p1 = "/metrics/children/0", "/metrics/children/1"
    tree = {
        "combinator": "and",
        "children": [{"combinator": "and", "children": [_range(-1)]}, _range(-2)],
    }
    probe.stage = [PatchOp(A, "remove", p0)]
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        _fold(user, p0)
        await _settled(lambda: page.state.tree_folded.get(A) == [p0])
        rev = page.tree.revision
        _click_in(user, f"probe-act:{p1}", within=f"tree-row:{p1}")
        await _settled(lambda: page.tree.revision != rev)
        assert "data-group" not in page.tree.rows_by_path[p0].props
        assert not page.state.tree_folded.get(A)


async def test_collapse_all_closes_a_form_under_a_row_it_hides(page_shell):
    """E6, as Hide tree: folding a group takes a form open under one of its
    descendants away; one under the folded group's own row stays."""
    shell = page_shell
    _only(_RowProbe())
    tree = {
        "combinator": "and",
        "children": [{"combinator": "and", "children": [_range(-1), _range(-2)]}],
    }
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user, tree)
        inner = "/metrics/children/0/children/0"
        _click_in(user, f"probe-open:{inner}", within=f"tree-row:{inner}")
        await user.should_see(marker=f"form-row:tree:{inner}")
        user.find(marker="tree:collapse-all").click()
        await _settled(lambda: page.form is None)
        _click_in(
            user,
            "probe-open:/metrics/children/0",
            within="tree-row:/metrics/children/0",
        )
        await user.should_see(marker="form-row:tree:/metrics/children/0")
        user.find(marker="tree:collapse-all").click()
        await asyncio.sleep(0.2)
        assert page.form is not None


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
    of its own: reading it there said the reason without the mode. A
    stored-scalar leaf names a statistic, not a series: it is read on the
    tree's row, and x's card has no row of it."""
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
        from dstf.authoring.shell.tree_card import leaf_readout

        scored, unscorable = leaves_of(shell.session.tree_view(D))
        assert leaf_readout(unscorable).startswith("scalar leaf not scored — ")
        assert unscorable["error"] not in ("", None)
        assert leaf_readout(unscorable) == unscorable["criterion"]
        assert leaf_readout(scored) == scored["criterion"]
        assert _none(user, f"criterion:0:{T1}")
        await _open_tree(user)
        assert leaf_readout(unscorable) in _labels_in(_one(user, f"tree-row:{T1}"))


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


async def test_a_test_shows_one_card_per_scored_variable(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "x"
        assert _one(user, "plot-title:1").text == "y"
        assert _one(user, "plots:count").text == "2 plots · derived from the criteria"
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
    button, over a trajectory already on the card above it (the 3b review).
    One name, one card: one criteria header, and one `+` for the name."""
    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.series for c in page.cards] == [("unitTests.x[1]",), ("y",)]
        assert _one(user, "plots:count").text == "2 plots · derived from the criteria"
        # Two cards, each with its header; one `+` for the name, not two.
        assert [c.variable_host.marker for c in page.cards] == [
            "variable:0",
            "variable:1",
        ]
        assert len(user.find(marker="add-for:unitTests.x[1]").elements) == 1
        assert _inside(user, "add-for:unitTests.x[1]", "criteria-header:0")
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
        # Every key is the name: the card's markers, and the `+`'s; the `+`
        # READS the label, and a screen reader hears what its tooltip says.
        assert _one(user, "plot:0") and _one(user, "plot-slot:0")
        (plus,) = _inside(user, "add-for:unitTests.x[1]", "criteria-header:0")
        assert _plus_tips(plus) == ["Add a criterion for cat(1, a, b)[1]"]
        assert plus.props["aria-label"] == "Add a criterion for cat(1, a, b)[1]"


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
            # The card's figure is the Session's with the card's Visibility.
            assert _sans_rev(plot.figure) == shell.session.plot_figure(
                A, 0, editing=(), visibility=page.cards[0].visibility()
            )
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
        assert _sans_rev(figure) == shell.session.plot_figure(
            D, 0, editing=(), visibility=page.cards[0].visibility()
        )
        assert _sans_rev(figure) != shell.session.plot_figure(D, 0)
        assert not [s for s in figure["layout"]["shapes"] if s.get("path") == RING_PATH]


async def test_the_browser_event_shows_the_named_card(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.on_visible("1")
            assert page.cards[1].plot is not None and page.cards[0].plot is None
            for junk in ("7", "x", "-1", None, 1.5, {"a": 1}):
                page.on_visible(junk)
        assert page.cards[0].plot is None


async def test_a_layout_draws_its_context(plots_shell):
    """The key's entry on x adds z to x's plot as context: listed above the
    plot, no criterion of its own and no `+`; the title is the subject's."""
    shell = plots_shell
    shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 2 and _one(user, "plot-title:0").text == "x"
        assert _one(user, "plots:count").text == "2 plots · derived from the criteria"
        assert _one(user, "context-name:0:z").text == "z"
        assert list(page.cards[0].rows) == [T0]  # x's leaf only
        assert _none(user, "add-for:z")
        assert _none(user, "plot-missing:0")
        shell.session.discard()
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["no.such"]}])]
        )
        await _test_page(shell, user)
        assert _one(user, "plot-missing:0").text == "Not in this result: no.such"


async def test_a_plot_whose_subject_the_result_lacks_draws_no_chart(shell):
    """A tree leaf on `ghost`, which the result does not carry: its card is
    the heading, its pill, the note and its criteria row — no empty chart,
    and nothing for the browser's observer to ask to draw."""
    _stage_tree(
        shell,
        A,
        {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "range", "variable": "ghost", "min_value": 0.0},
            ],
        },
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",), ("ghost",)]
        assert _one(user, "plot-missing:1").text == "Not in this result: ghost"
        assert list(page.cards[1].rows) == ["/metrics/children/1"]
        assert _none(user, "plot-slot:1") and not _none(user, "plot-slot:0")
        with page.client:
            page.on_visible("1")
            page.cards[1].show()
            page.cards[1].redraw()
        assert page.cards[1].plot is None


async def test_a_missing_subject_with_a_drawable_context_draws_no_chart(shell):
    """The report draws no chart when no SUBJECT is drawable, whatever the
    context; the tool agrees — `y`, drawable, is context on the plot of
    `ghost`, which the result lacks."""
    _stage_tree(
        shell,
        A,
        {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "range", "variable": "ghost", "min_value": 0.0},
            ],
        },
    )
    shell.session.stage(
        [PatchOp(A, "add", "/plots", [{"var": "ghost", "context": ["y"]}])]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        (card,) = [c for c in page.cards if c.resolved.subject == ("ghost",)]
        assert card.resolved.context == ("y",)
        assert card.resolved.missing == ("ghost",)
        assert _none(user, f"plot-slot:{card.index}")


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


async def test_the_plus_adds_a_criterion_for_the_variable(shell):
    """The plot's `+` opens its form as a row under the criteria header;
    Add stages the criterion, the row goes, and the new one's editor is
    open (the flat test's tree is materialized by the same stage)."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        (plus,) = _inside(user, "add-for:x", "criteria-header:0")
        assert _plus_tips(plus) == ["Add a criterion for x"]
        _click_in(user, "add-for:x", within="criteria-header:0")
        await user.should_see(marker="apply-add:x")
        user.find(marker="apply-add:x").click()
        await _settled(lambda: len(_page(shell, user).cards[0].rows) == 2)
        page = _page(shell, user)
        await _settled(lambda: page.open == (0, "/metrics/children/2"))
        assert page.form is None


async def test_a_test_with_no_result_has_no_cards(shell):
    """C's last run never finished it (its manifest entry is still
    pending): the reader hands in no result (D145), so the reason is the
    result, as the strip says — not the run."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, C)
        assert _one(user, "plots:count").text == "no plots: this test has no result"
        assert page.cards == []


def _c_run(tmp_path, status: str | None, live: str | None = None) -> Shell:
    """`shell` with C's manifest entry reading ``status`` — `pending` is a
    run that never finished C, `failed` one that finished it, failed — or,
    with None, gone: C was never run. C has no result file either way.
    ``live``: C's row in status.json, the record a pending entry falls
    through to."""
    cfg, work = _two_var_project(tmp_path)
    if live is not None:
        (work / "status.json").write_text(
            json.dumps({"tests": [{"model_id": C, "status": live}]}),
            encoding="utf-8",
        )
    path = work / "batch_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if status is None:
        del manifest["test_0003"]
    else:
        manifest["test_0003"]["last_run_status"] = status
    path.write_text(json.dumps(manifest), encoding="utf-8")
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


#: The lines of the Test page that say why a test has nothing to show.
_REFUSED = (
    "the base tolerance must be a number; a lower/upper pair belongs on a criterion"
    ' (the baseline\'s comparison.tolerance is {"lower": 0.1, "upper": 0.2})'
)


async def test_a_refused_test_says_why_on_the_tree_line_and_the_strip(tmp_path):
    """D's reference JSON holds a lower/upper pair as its base tolerance:
    the comparison refuses the test (FAIL, a reason, nothing scored). D has
    a baseline, so "no baseline" names the wrong cause, and accepting a
    refused test is refused, so the strip greys Accept with the reason."""
    cfg, work = _two_var_project(tmp_path)
    changed = 0
    for path in cfg.parent.rglob("ref_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("model_id") == D:
            data.setdefault("comparison", {})["tolerance"] = {
                "lower": 0.1,
                "upper": 0.2,
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            changed += 1
    assert changed == 1
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    assert shell.session.comparisons[D].error_message == _REFUSED
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        assert page.badge.text == "FAIL"
        assert _one(user, "tree:summary").text == f"nothing scored: {_REFUSED}"
        assert _one(user, "strip-text").text == f"Not scored: {_REFUSED}."
        assert "dstf-strip-red" in _one(user, "strip").classes
        # Queue, and an Accept no more prominent than a quiet button, greyed
        # with the reason: accepting a refused test is refused.
        assert _strip_buttons(user) == {"accept": "quiet", "enqueue": "quiet"}
        assert not _one(user, "action:accept").enabled
        assert _one(user, "action:accept:why").text == _REFUSED
        assert _one(user, "action:accept:why").visible
        assert _one(user, "action:enqueue").enabled


_RUN_LINES = ("strip-text", "plots:count", "tree:summary", "var-target")


async def test_an_unfinished_run_says_no_result_on_every_line(tmp_path):
    """ONE run-state rule (`TestPage.run_state`): the strip said "No result
    yet" for C's unfinished run while the count, the tree and the drawer
    said the run failed. Since D145 the reader hands in no result for it,
    so every line reads as for a test never run."""
    shell = _c_run(tmp_path, "pending")
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user, C)
        assert page.run_state() == "none"
        # D145: the header's badge agrees with the strip — NOT RUN, grey.
        assert page.badge.text == "NOT RUN"
        assert page.badge._style["background-color"] == "var(--dstf-not-run-fill)"
        said = {m: _one(user, m).text for m in _RUN_LINES}
        assert said == {
            "strip-text": "No result yet — queue it to simulate it.",
            "plots:count": "no plots: this test has no result",
            "tree:summary": "nothing scored: this test has no result",
            "var-target": "No plot yet: + new plot adds one",
        }


async def test_a_failed_run_says_the_run_failed_on_every_line(tmp_path):
    """C's run finished and failed: every line says the run failed — the
    tree's too, which said "this test has no result"."""
    shell = _c_run(tmp_path, "failed")
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user, C)
        assert page.run_state() == "failed"
        assert page.badge.text == "SIM_FAIL"  # it ran, and failed (D145)
        said = {m: _one(user, m).text for m in _RUN_LINES}
        # the strip: the stored reason, then the next action (D151) — read
        # back with no run result in hand, so the verdict the run recorded
        # leads what its file is
        reason = shell.session.comparisons[C].error_message
        assert reason.startswith("Run recorded failed — No Python result at ")
        assert said["strip-text"] == (
            f"{reason} — fix the model or its settings, then queue it again."
        )
        assert page.strip.situation is Situation.SIM_FAIL
        assert {m: t for m, t in said.items() if m != "strip-text"} == {
            "plots:count": "no plots: the run failed",
            "tree:summary": "nothing scored: the run failed",
            "var-target": "The run failed: no plots",
        }
        assert _one(user, "var-empty").text == "the run failed"


async def test_a_pending_run_status_json_records_failed_says_the_run_failed(
    tmp_path,
):
    """C's manifest entry is still pending but status.json records its run
    failed: the reader hands in a failed result and the badge says SIM_FAIL
    (D145). The strip said "No result yet" beside it — it reads the same
    result the badge was scored from."""
    shell = _c_run(tmp_path, "pending", live="failed")
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user, C)
        assert page.badge.text == "SIM_FAIL"
        assert page.run_state() == "failed"
        said = {m: _one(user, m).text for m in _RUN_LINES}
        # the strip: the stored reason, then the next action (D151) — read
        # back with no run result in hand, so the verdict the run recorded
        # leads what its file is
        reason = shell.session.comparisons[C].error_message
        assert reason.startswith("Run recorded failed — No Python result at ")
        assert said["strip-text"] == (
            f"{reason} — fix the model or its settings, then queue it again."
        )
        assert page.strip.situation is Situation.SIM_FAIL
        assert said["plots:count"] == "no plots: the run failed"


async def test_a_test_never_run_says_no_result_not_the_run_failed(tmp_path):
    """C is in no manifest and has no result at all: nothing on the page
    says the run failed — there was none."""
    shell = _c_run(tmp_path, None)
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user, C)
        assert shell.session.project.results.get(C) is None
        assert page.run_state() == "none"
        assert page.badge.text == "NOT RUN"  # D145
        assert (
            _one(user, "strip-text").text == "No result yet — queue it to simulate it."
        )
        assert _one(user, "plots:count").text == "no plots: this test has no result"
        assert _one(user, "tree:summary").text == (
            "nothing scored: this test has no result"
        )
        assert _one(user, "var-target").text == "No plot yet: + new plot adds one"


async def test_a_result_held_beside_a_pending_manifest_entry_reads_as_that_result(
    shell,
):
    """A run started outside the tool (a `dstf run` in a terminal, over the
    same work dir) stamps D's manifest entry pending while the Session still
    holds D's last result. The page reads the result it holds, never the
    manifest: the badge and the strip agree on D's verdict, and no line
    says it has no result."""
    session = shell.session
    path = session.project.config.work_dir / "batch_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["test_0004"]["model_id"] == D
    manifest["test_0004"]["last_run_status"] = "pending"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert session.project.results[D].success
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user, D)
        assert page.run_state() == "ok"
        assert page.badge.text == "PASS"
        said = {m: _one(user, m).text for m in _RUN_LINES}
        assert said["strip-text"] == "Passes against its baseline."
        assert not [t for t in said.values() if "no result" in t.lower()]


async def test_an_empty_plots_key_is_no_extras(shell):
    """`"plots": []` asks for no context and no watch plot: the derived
    plots are the tree's, and stay."""
    shell.session.stage([PatchOp(A, "add", "/plots", [])])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",), ("y",)]
        assert _one(user, "plots:count").text == "2 plots · derived from the criteria"


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
        assert _one(user, "plots:count").text == "no plots: the run failed"


async def test_a_failed_run_draws_no_cards_whatever_its_tree_and_extras(shell):
    """O2, literally: a failed run has no plots — not its tree's, not its
    extras' — as the report draws none. The reason is the run: a tree
    that names signals is not one "of statistics"."""
    from dstf.backends.types import TestResult

    shell.session.stage(
        [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["y"]}, {"var": "w"}])]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 3
        result = shell.session.project.results[A]
        shell.session.project.results[A] = TestResult(
            model_id=A, success=False, variables=list(result.variables)
        )
        with page.client:
            page.sync()
        assert page.cards == []
        assert shell.session.plots(A) == ()
        assert _one(user, "plots:count").text == "no plots: the run failed"


async def test_watch_plots_with_no_derived_plot_say_so(shell):
    """A tree whose every criterion scores a statistic derives no plot; a
    watch plot beside it is counted as what it is, never "0 plots ·
    derived from the criteria · 1 watch"."""
    tree = {
        "combinator": "and",
        "children": [
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": "CPUtime.final",
                "max_value": 1e9,
            }
        ],
    }
    shell.session.stage(
        [
            PatchOp(A, "add", "/metrics", tree),
            PatchOp(A, "add", "/plots", [{"var": "x"}]),
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [(c.resolved.subject, c.resolved.watch) for c in page.cards] == [
            (("x",), True)
        ]
        assert _one(user, "plots:count").text == (
            "1 watch plot · no criteria on a signal yet"
        )
        shell.session.stage([PatchOp(A, "add", "/plots", [{"var": "x"}, {"var": "y"}])])
        await _settled(lambda: len(page.cards) == 2)
        assert _one(user, "plots:count").text == (
            "2 watch plots · no criteria on a signal yet"
        )


async def test_a_result_with_no_variables_says_that_and_not_no_baseline(tmp_path):
    """The third reason: a successful result the test tracks nothing of.
    That is no longer "no baseline" on the tree's line either: a result
    that holds nothing the test tracks scored nothing, and no accept gives
    it a criterion — the comparison refuses the test and says so."""
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
        assert _one(user, "tree:summary").text == (
            "nothing scored: the result holds none of the variables this test "
            "tracks (x, y) — nothing was scored"
        )


async def test_a_no_baseline_test_draws_its_result(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert len(page.cards) == 2 and all(not c.rows for c in page.cards)
        (why,) = _inside(user, "why:edit-tree", "criteria-header:0")
        assert why.text == "nothing is scored"
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


async def test_a_variable_whose_last_criterion_goes_is_still_offered(shell):
    """Its card goes with its last criterion (Q16) — a derived plot is the
    tree's view — but the variable is still TRACKED, and the add forms are
    the way back to it: removing y's only criterion must not take every
    way to add one back (the one-way door the Task 5-6 review found)."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click_in(user, f"remove:{T1}", within=f"node:1:{T1}")
        await _settled(lambda: len(page.cards) == 1)
        assert page.cards[0].resolved.subject == ("x",)
        await _open_form(user, f"add:{T0}", within=f"node:0:{T0}")
        # Dict options: name → what to read it as (neither has an
        # expression here, so the two agree).
        assert _one(user, f"apply-add:{T0}:variable").options == {"x": "x", "y": "y"}


async def test_a_family_card_names_the_variable_of_each_pill(nested_shell):
    """Board: two pills reading "PASS PASS" look like the plot's verdict
    said twice. A card of one variable keeps the bare status — its title
    already names the variable."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "pipe.T[1:2]"
        assert [
            _one(user, f"plot-pill:0:{v}").text for v in ("pipe.T[1]", "pipe.T[2]")
        ] == ["pipe.T[1] PASS", "pipe.T[2] PASS"]
        shell.session.stage([PatchOp(A, "replace", f"{N_T2}/min_value", 100.0)])
        await _settled(
            lambda: _one(user, "plot-pill:0:pipe.T[2]").text == "pipe.T[2] FAIL"
        )
        pill = page.cards[0].pills["pipe.T[2]"]
        assert pill.label.classes.count("dstf-pill-fail") == 1
        assert _one(user, "plot-pill:1:x").text == "pass"  # one variable: the status


async def test_a_family_card_has_one_plus_whose_form_offers_the_others(
    nested_shell,
):
    """Q3 A: ONE `+` in the criteria header, for the subject's first member
    — one per member pushed the next plot off the screen (the board) — and
    its form's select offers the test's other variables."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[0].variable_host.marker == "variable:0"
        assert _inside(user, "add-for:pipe.T[2]", "criteria-header:0") == []
        _click_in(user, "add-for:pipe.T[1]", within="criteria-header:0")
        await _settled(lambda: not _none(user, "apply-add:pipe.T[1]:variable"))
        picker = _one(user, "apply-add:pipe.T[1]:variable")
        assert picker.value == "pipe.T[1]"
        # U2's picker: the plot's own series first, then every other name of
        # the result file — tracked or not.
        sections: dict[str, list[str]] = {}
        for row in picker.shown:
            sections.setdefault(row.section, []).append(row.name)
        assert sections == {
            "On this plot": ["pipe.T[1]", "pipe.T[2]"],
            "Other variables": ["x", "y", "w", "z"],
        }


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


# -- the cards are the tree's views ------------------------------------------

#: ``and[range·pipe.T[2], k-of-n[range·x, range·pipe.T[1]], range·x, nrmse·y]``:
#: a family whose members the tree names out of index order and at two
#: depths, a variable named twice, and a leaf under a nested combinator.
NESTED = {
    "combinator": "and",
    "children": [
        {"metric": "range", "variable": "pipe.T[2]", "min_value": -1.0},
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [
                {"metric": "range", "variable": "x", "min_value": -1.0},
                {"metric": "range", "variable": "pipe.T[1]", "min_value": -1.0},
            ],
        },
        {"metric": "range", "variable": "x", "max_value": 100.0},
        {"metric": "nrmse", "variable": "y"},
    ],
}
N_T2, N_KX, N_KT1, N_X2, N_Y = (
    "/metrics/children/0",
    "/metrics/children/1/children/0",
    "/metrics/children/1/children/1",
    "/metrics/children/2",
    "/metrics/children/3",
)


def _nested_project(tmp_path, plots=None):
    """`_two_var_project` with A's result carrying `pipe.T[1]`, `pipe.T[2]`
    (an array by name, so they fold), and the untracked `w` and `z`; A
    tracks x, y and the pipe and is scored by `NESTED`, with ``plots`` as
    its extras."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for k, name in enumerate(["pipe.T[1]", "pipe.T[2]", "w", "z"], start=3):
        payload["variables"][name] = [k * v for v in payload["time"]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = ["x", "y", "pipe.T[1]", "pipe.T[2]"]
            entry["metrics"] = copy.deepcopy(NESTED)
            if plots is not None:
                entry["plots"] = plots
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


def _opened(cfg, work) -> Shell:
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


@pytest.fixture
def nested_shell(tmp_path):
    return _opened(*_nested_project(tmp_path))


@pytest.fixture
def extras_shell(tmp_path):
    """`nested_shell` with z as context on x's plot and a watch plot of w."""
    return _opened(
        *_nested_project(tmp_path, [{"var": "x", "context": ["z"]}, {"var": "w"}])
    )


def _tips(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.tooltip)]


async def test_the_cards_follow_the_tree_and_their_rows_are_in_tree_order(
    nested_shell,
):
    """One card per scored variable or family, where the tree first names
    it; a card's rows are its subject's leaves in TREE order — pipe.T[2]'s
    before pipe.T[1]'s — each led by its ancestors' kinds."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [
            ("pipe.T[1]", "pipe.T[2]"),
            ("x",),
            ("y",),
        ]
        assert _one(user, "plot-title:0").text == "pipe.T[1:2]"
        assert [list(c.rows) for c in page.cards] == [
            [N_T2, N_KT1],
            [N_KX, N_X2],
            [N_Y],
        ]
        assert _one(user, f"crumb:0:{N_T2}").text == "and ›"
        assert _one(user, f"crumb:0:{N_KT1}").text == "and › k-of-n ›"
        assert "dstf-crumb" in _one(user, f"crumb:1:{N_KX}").classes
        # A card's crumbs are as wide as its longest, so its leaf names
        # start in one column whatever the rows' depths.
        for path in (N_T2, N_KT1):
            assert _one(user, f"crumb:0:{path}").style == {"min-width": "14ch"}
        for path in (N_KX, N_X2):
            assert _one(user, f"crumb:1:{path}").style == {"min-width": "14ch"}
        # y's one row is at depth 1: "and ›", five characters.
        assert _one(user, f"crumb:2:{N_Y}").style == {"min-width": "5ch"}
        assert _one(user, "plots:count").text == "3 plots · derived from the criteria"
        # The family's card names each pill's variable; a lone one does not.
        assert _one(user, "plot-pill:0:pipe.T[2]").text.startswith("pipe.T[2] ")
        assert _one(user, "plot-pill:1:x").text in ("pass", "fail")


async def test_a_root_leafs_row_has_no_breadcrumb(shell):
    """A leaf with no ancestor has nothing to lead its row with."""
    _stage_tree(shell, A, {"metric": "nrmse", "variable": "x"})
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert list(page.cards[0].rows) == ["/metrics"]
        assert _none(user, "crumb:0:/metrics")


async def test_both_tiers_lead_a_plots_rows_with_the_same_crumbs(tmp_path):
    """The report's plot sections and the tool's cards list a plot's
    criteria by ONE walk (`reporting.tree_rows.criteria_rows`): over the
    report's nested tree, every plot's rows carry the same leaves, in the
    same order, led by the same crumbs on both tiers."""
    import re as _re

    from dstf.backends.base import last_test_keys
    from dstf.reporting.report_suite import _build_per_test_args, _render_one_test
    from test_report_suite import TestPlotSections, _parse

    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    next(e for e in spec["tests"] if e["model"] == A)["metrics"] = copy.deepcopy(
        TestPlotSections.NESTED
    )
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    shell = _opened(cfg, work)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        tool = [
            [
                (
                    path,
                    _one(user, f"crumb:{card.index}:{path}").text
                    if not _none(user, f"crumb:{card.index}:{path}")
                    else "",
                )
                for path in card.rows
            ]
            for card in page.cards
        ]
    session = shell.session
    project = session.project
    args = _build_per_test_args(
        session.comparisons[A],
        project.results,
        {t.model_id: t for t in project.tests},
        project.store,
        project.config,
        {A: {"test_key": last_test_keys(work).get(A), "last_run_at": None}},
        backend_cls=type(project.backend),
    )
    out = tmp_path / "report"
    entry = _render_one_test(args, out)
    html = (out / entry["report_path"]).read_text(encoding="utf-8")
    doc = _parse(html)
    report = []
    for i in range(len(_re.findall(r'id="variable-section-\d+"', html))):
        rows = []
        for row in doc.by_id(f"variable-section-{i}").by_class("criteria-row"):
            crumbs = row.by_class("node-crumbs")
            leaf = next(el for el in row.walk() if "node-leaf" in el.classes)
            rows.append((leaf.attrs["data-path"], crumbs[0].text() if crumbs else ""))
        report.append(rows)
    assert tool == report
    assert tool == [
        [
            ("/metrics/children/0", "and ›"),
            ("/metrics/children/1/children/1", "and › k-of-n ›"),
        ],
        [("/metrics/children/1/children/0", "and › k-of-n ›")],
    ]


async def test_a_derived_card_has_no_remove_and_says_why(nested_shell):
    """Q16: a derived plot goes when its last criterion goes; in Remove's
    place an info icon says so (C6). Add series is gone."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 3
        for i in range(3):
            assert _none(user, f"remove-plot:{i}"), i
            assert _none(user, f"add-series:{i}"), i
            assert _tips(_one(user, f"plot-derived:{i}")) == [
                "This plot exists because of its criteria."
            ]


async def test_a_watch_card_says_no_criteria_and_its_remove_stages_the_rest(
    extras_shell,
):
    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.watch for c in page.cards] == [False, False, False, True]
        assert _one(user, "plot-empty:3").text == "No criteria yet."
        assert _one(user, "plot-title:3").text == "w"
        assert _none(user, "plot-derived:3") and _none(user, "plot-empty:1")
        # the derived plots counted apart from the watch plot
        assert (
            _one(user, "plots:count").text
            == "3 plots · derived from the criteria · 1 watch"
        )
        # w is not tracked, and its `+` is live (U2 lifted C2).
        (plus,) = _inside(user, "add-for:w", "criteria-header:3")
        assert plus.enabled
        assert _plus_tips(plus) == ["Add a criterion for w"]
        user.find(marker="remove-plot:3").click()
        assert _pending(shell, "/plots") == [{"var": "x", "context": ["z"]}]
        await _settled(lambda: len(page.cards) == 3)


async def test_a_watch_cards_plus_adds_a_seeded_range_on_untracked_w(extras_shell):
    """U2 lifts C2: w is not tracked, and its watch card's `+` adds a
    criterion on it — the picker preselects w, the leaf is seeded from the
    result FILE's series (`Session.view` falls back to it), and the watch
    plot becomes w's derived plot. The file holds w (the JSON driver), so
    the stage re-reads it and it scores at once: no new run (the owner's
    O2)."""
    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert "w" not in shell.session.tracked(A)
        (plus,) = _inside(user, "add-for:w", "criteria-header:3")
        assert plus.enabled
        user.find(marker="add-for:w").click()
        await _settled(lambda: not _none(user, "apply-add:w:variable"))
        picker = _one(user, "apply-add:w:variable")
        assert picker.value == "w"
        assert [r.name for r in picker.shown if r.section == "On this plot"] == ["w"]
        metric = next(
            e
            for e in user.find(kind=ui.select).elements
            if e.props.get("label") == "Metric"
        )
        metric.set_value("range")
        user.find(marker="apply-add:w").click()
        await _settled(lambda: bool(shell.session.spec.pending))
        leaf = shell.session.tree(A)["children"][-1]
        assert leaf["metric"] == "range" and leaf["variable"] == "w"
        assert "min_value" in leaf and "max_value" in leaf  # seeded from the file
        await _settled(lambda: not page.cards[-1].resolved.watch)
        assert "w" in shell.session.tracked(A)
        await _settled(lambda: page.strip.situation.value == "edits")


async def test_a_criterion_on_a_diagnostic_is_revealed_in_the_statistics_card(
    extras_shell,
):
    """B6, closed by U3: a diagnostic's criterion is scored through a
    quantity path and has no plot, so an Add from a plot's `+` has no row
    there to open: the Statistics card opens, with the new criterion's
    editor under the CPUtime row."""
    import numpy as np

    from dstf.backends.types import VariableResult

    shell = extras_shell
    result = shell.session.project.results[A]
    t = np.asarray(result.variables[0].time)
    result.diagnostics = [
        VariableResult(
            index=0, time=t, values=np.linspace(0, 0.4, len(t)), name="CPUtime"
        )
    ]
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.stats is not None and not page.stats.is_open
        before = len(shell.session.tree(A)["children"])
        _click_in(user, "add-for:x", within="criteria-header:1")
        await _settled(lambda: not _none(user, "apply-add:x:variable"))
        user.find(marker="apply-add:x:variable:pick:CPUtime").click()
        user.find(marker="apply-add:x").click()
        # CPU time is seeded inside a warn: the reveal is its LEAF's row
        added = f"/metrics/children/{before}/children/0"
        await _settled(lambda: len(shell.session.tree(A)["children"]) == before + 1)
        assert shell.session.tree(A)["children"][-1]["combinator"] == "warn"
        leaf = shell.session.tree(A)["children"][-1]["children"][0]
        assert leaf["quantity"] == "CPUtime.final"
        await _settled(lambda: page.open == (STATS, added))
        assert page.stats.is_open and not page.tree.is_open
        editor = _one(user, f"stats-editor:{added}")
        # FakeLib folds no diagnostic into its statistics: the criterion's
        # home is the "Other scored quantities" row (S4, B14).
        assert page.stats.slots["CPUtime.final"] in list(_ancestors(editor))


async def test_a_context_series_is_listed_with_an_x_that_takes_it_off(extras_shell):
    """C1: the context sits in a row above the plot, each name with a `×`;
    it is no criterion. x's entry holds nothing else, so it goes."""
    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[1].resolved.context == ("z",)
        assert _one(user, "context-name:1:z").text == "z"
        assert _inside(user, "context-name:1:z", "context:1")
        assert _none(user, "context:0") and _none(user, "context:3")
        remove = _one(user, "context-remove:1:z")
        assert remove.props["aria-label"] == "Remove z from this plot"
        assert list(page.cards[1].rows) == [N_KX, N_X2]
        user.find(marker="context-remove:1:z").click()
        assert _pending(shell, "/plots") == [{"var": "w"}]
        await _settled(lambda: page.cards[1].resolved.context == ())
        assert _none(user, "context:1")


async def test_removing_a_variables_last_criterion_takes_its_card(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        shell.session.stage([PatchOp(A, "remove", N_Y)])
        await _settled(lambda: len(page.cards) == 2)
        assert [c.resolved.subject for c in page.cards][-1] == ("x",)


async def test_with_an_extra_the_card_stays_as_a_watch_card(tmp_path):
    """A watch plot is what a derived plot with an extra becomes when its
    last criterion goes (Q14), its context kept. y is still tracked — the
    result carries it — so its `+` is live: the way back to a criterion."""
    shell = _opened(*_nested_project(tmp_path, [{"var": "y", "context": ["z"]}]))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[2].resolved.watch is False
        shell.session.stage([PatchOp(A, "remove", N_Y)])
        await _settled(lambda: page.cards[2].resolved.watch)
        card = page.cards[2]
        assert (card.resolved.subject, card.resolved.context) == (("y",), ("z",))
        assert _one(user, "plot-empty:2").text == "No criteria yet."
        assert _one(user, "remove-plot:2")
        (plus,) = _inside(user, "add-for:y", "criteria-header:2")
        assert plus.enabled and _plus_tips(plus) == ["Add a criterion for y"]


async def test_a_staged_context_rebuilds_the_card(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["w"]}])]
        )
        await _settled(lambda: page.cards[1].resolved.context == ("w",))
        assert _one(user, "context-name:1:w").text == "w"


async def test_a_tree_of_statistics_alone_has_no_plots_and_says_so(shell):
    """A stored-scalar leaf names a statistic, not a series: a tree of
    nothing else derives no plot, and the page says why — not "the result
    has no variables", which is false."""
    tree = {
        "combinator": "and",
        "children": [
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": "CPUtime.final",
                "max_value": 1e9,
            }
        ],
    }
    _stage_tree(shell, A, tree)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards == []
        assert _one(user, "plots:count").text == (
            "no plots: this test's criteria score statistics, not signals"
        )


async def test_a_test_with_no_tree_has_one_card_per_tracked_variable(shell):
    """C5: nothing scored derives nothing; the count line says what the
    cards are then — an array folds there too — and counts a watch plot
    apart."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert [c.resolved.subject for c in page.cards] == [("x",), ("y",)]
        assert _one(user, "plots:count").text == (
            "2 plots · one per tracked variable or array"
        )
        shell.session.stage([PatchOp(B, "add", "/plots", [{"var": "z"}])])
        await _settled(lambda: len(page.cards) == 3)
        assert _one(user, "plots:count").text == (
            "2 plots · one per tracked variable or array · 1 watch"
        )


async def test_a_tree_on_a_test_with_no_baseline_derives_its_plots(shell):
    """B has no baseline, so nothing is compared and there is no tree VIEW;
    an authored tree still names what its plots are (`plot_leaves`), and
    the count line says so — it branched on the view."""
    shell.session.stage(
        [
            PatchOp(
                B,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "y"}],
                },
            )
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, B)
        assert shell.session.tree_view(B) is None
        assert [c.resolved.subject for c in page.cards] == [("y",)]
        assert _one(user, "plots:count").text == "1 plot · derived from the criteria"


async def test_a_statistics_leaf_draws_no_row_on_its_variables_card(shell):
    """A stored-scalar leaf's ``variable`` is free text naming the leaf, not
    a series (`resolves_a_variable`): a watch plot of a variable that
    spelling matches has no criteria, and its card shows none — no row, no
    pill, nothing it draws."""
    stats = {
        "metric": "scalar",
        "variable": "x",
        "quantity": "CPUtime.final",
        "max_value": 1e9,
    }
    tree = {
        "combinator": "and",
        "children": [{"metric": "nrmse", "variable": "y"}, stats],
    }
    shell.session.stage(
        [
            PatchOp(A, "add", "/metrics", tree),
            PatchOp(A, "add", "/plots", [{"var": "x"}]),
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        card = page.cards[-1]
        assert (card.resolved.subject, card.resolved.watch) == (("x",), True)
        assert card.rows == {} and card.pills == {}
        assert card._leaves_all(shell.session.tree_view(A)) == []
        assert _one(user, "plot-empty:1").text == "No criteria yet."


async def test_a_watch_plot_turning_derived_keeps_the_selection(tmp_path):
    """A watch plot selected, then given a criterion: it becomes its
    variable's derived plot and MOVES — to where the tree names it — and
    the selection moves with it, by its subject; the index it had names
    another plot now."""
    shell = _opened(
        *_nested_project(tmp_path, [{"var": "w"}, {"var": "y", "context": ["z"]}])
    )
    shell.session.stage([PatchOp(A, "remove", N_Y)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [(c.resolved.subject, c.resolved.watch) for c in page.cards][-2:] == [
            (("w",), True),
            (("y",), True),
        ]
        with page.client:
            page.select(3)
        _click_in(user, "add-for:y", within="criteria-header:3")
        await user.should_see(marker="apply-add:y")
        user.find(marker="apply-add:y").click()
        await _settled(lambda: not _page(shell, user).cards[2].resolved.watch)
        page = _page(shell, user)
        card = page.cards[2]
        assert (card.resolved.subject, card.resolved.context) == (("y",), ("z",))
        assert page.selected == 2 and _ringed(page) == [2]
        await _settled(lambda: page.open is not None and page.open[0] == 2)


async def test_an_edit_to_a_context_variables_criterion_resends_only_its_card(
    tmp_path,
):
    """x's card draws y as CONTEXT; y's criterion, on y's own card, is
    edited: only y's card is re-sent. A context variable's leaves sign
    nothing onto a card (`PlotCard.signature` reads the SUBJECT)."""
    cfg, work = _two_var_project(tmp_path)
    shell = _opened(cfg, work)
    shell.session.stage(
        [
            PatchOp(A, "add", "/metrics", copy.deepcopy(TWO_RANGES)),
            PatchOp(A, "add", "/plots", [{"var": "x", "context": ["y"]}]),
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[0].resolved.context == ("y",)
        with page.client:
            page.cards[0].show()
            page.cards[1].show()
        on_x, on_y = _counted(page.cards[0]), _counted(page.cards[1])
        shell.session.stage(
            [PatchOp(A, "replace", "/metrics/children/1/min_value", -5)]
        )
        await _settled(lambda: on_y[0] >= 1)
        assert on_x[0] == 0


async def test_reset_layout_says_the_extras_go(extras_shell):
    """C7: what Reset layout takes away is the context and the watch plots;
    the derived plots stay."""
    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _tips(_one(user, "tree:reset-layout")) == [
            "Stage removing this test's plots key: its context series and "
            "watch plots go"
        ]
        user.find(marker="tree:reset-layout").click()
        await _settled(lambda: len(page.cards) == 3)
        assert all(not c.resolved.context for c in page.cards)


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
        old = page.cards[0]
        assert _none(user, "context:0")
        shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        await _settled(lambda: page.cards[0] is not old)
        assert page.cards[0].resolved.context == ("z",)
        assert _one(user, "context-name:0:z").text == "z"


async def test_a_leaf_that_changes_variable_moves_to_the_other_card(shell):
    """A leaf's variable is part of the shape: its row belongs to another
    card now, and no repaint moves a row between cards. x's card goes with
    its last criterion."""
    _stage_tree(shell, A, TWO_RANGES)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [list(c.rows) for c in page.cards] == [[T0], [T1]]
        shell.session.stage([PatchOp(A, "replace", f"{T0}/variable", "y")])
        await _settled(lambda: [list(c.rows) for c in page.cards] == [[T0, T1]])
        assert page.cards[0].resolved.subject == ("y",)


async def test_the_plots_plus_follows_a_refusal_that_comes_and_goes(no_tree_shell):
    """The criteria header is painted, not rebuilt, so what its `+` MAY do
    has to be re-read on every Session event: the Session's refusal to
    materialize the tree takes it and says why, and an override that lifts
    the refusal brings it back."""
    from dstf.authoring.session import NO_TREE

    shell = no_tree_shell
    shell.session.stage([SHADOW_X])
    header = "criteria-header:0"

    def why(user) -> str:
        found = _inside(user, "why:edit-tree", header)
        return found[0].text if found else ""

    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert why(user) == "" and _inside(user, "add-for:x", header)
        shell.session.discard()
        await _settled(lambda: why(user) == NO_TREE)
        assert _inside(user, "add-for:x", header) == []
        shell.session.stage([SHADOW_X])
        await _settled(lambda: why(user) == "")
        assert _inside(user, "add-for:x", header)


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


async def test_a_cancelled_forms_row_is_gone_from_the_page(shell):
    """A node's forms are built on the icon's click and dropped on Cancel
    (a menu's close, before the forms were rows): a page holding a form per
    node of every row is what that replaced."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_form(user, f"wrap:{T0}", within=f"node:0:{T0}")
        user.find(marker=f"cancel:{T0}").click()
        await _settled(lambda: _none(user, f"apply-wrap:{T0}"))
        assert _none(user, f"form-row:crit:0:{T0}")
        assert page.form is None
        assert shell.session.spec.pending == []


async def test_a_rebuilt_card_gives_up_its_hook(shell):
    """A rebuild drops the cards, and a dropped card's hook must not go on
    delivering drags: the editor's panels give theirs back when it closes,
    but the criteria header's inline VARIABLE panel holds the same hook for
    as long as it is drawn, and the rebuild does not reach it. `dispose` is
    what takes it back."""
    _only(_Probe("sub", {Placement.VARIABLE}, subscribe=True))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        old = page.cards[0]
        assert _inside(user, "probe:sub", "criteria-header:0")
        assert [o for entries in old.hook._handlers.values() for o, _ in entries]
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["y"]}])]
        )
        await _settled(lambda: page.cards[0] is not old)
        assert not [e for entries in old.hook._handlers.values() for e in entries]


def _watch_shell(tmp_path, n: int) -> Shell:
    """`_two_var_project` with ``n`` untracked variables `v0`, `v1`, ... in
    A's result, each a WATCH plot: a page of ``n + 2`` cards — x and y
    derived, the rest watched."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = [f"v{i}" for i in range(n)]
    for k, name in enumerate(names, start=3):
        payload["variables"][name] = [k * v for v in payload["time"]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["plots"] = [{"var": name} for name in names]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return _opened(cfg, work)


def _count_reads(session, names=("tree_view", "tracked", "variable_texts", "plots")):
    """Count the Session reads a page makes, by patching the reader on the
    instance. Returns the live dict."""
    calls = dict.fromkeys(names, 0)
    for name in calls:
        real = getattr(session, name)

        def counting(*a, _real=real, _name=name, **k):
            calls[_name] += 1
            return _real(*a, **k)

        setattr(session, name, counting)
    return calls


async def test_a_rebuild_reads_the_tree_and_the_tracked_names_once(tmp_path):
    """The cost the review measured: every card read `tree_view` and
    `tracked` for itself, so a rebuild cost one read per plot. The page
    reads and hands them in, so no CARD grows the count."""
    shell = _watch_shell(tmp_path, 3)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert len(page.cards) == 5
        session = shell.session
        calls = _count_reads(session)
        view = session.tree_view(A)
        calls["tree_view"] = 0
        calls["variable_texts"] = calls["plots"] = calls["tracked"] = 0
        with page.column:
            page.rebuild(view)
        # The page reads no view of its own, and the label scope ONCE — not
        # once per card. The cards' "Add a criterion for x" `+`s read the
        # scope the page hands down with their selection
        # (`Selection.texts`); deriving it in the caption instead was 2
        # scope builds per card, each re-resolving the test's plots and
        # tracked names (80 of them on a 40-card page).
        assert calls["tree_view"] == 0
        assert calls["variable_texts"] <= 1, calls
        # Bounded, and by the page — not by how many cards it has. Measured
        # with the plots derived from the tree: 2 plot resolutions (the
        # rebuild's and the label scope's) and 5 tracked-name reads (one of
        # them the page's, handed to every card's `+`).
        assert calls["plots"] <= 2 and calls["tracked"] <= 5, calls
        calls["tracked"] = 0
        with page.client:
            page.sync()
        assert calls["tree_view"] == 1


@pytest.mark.parametrize("n", [5, 40])
async def test_the_page_costs_the_same_whether_it_has_five_cards_or_forty(
    tmp_path, monkeypatch, n
):
    """The guard the review's cost probe is: a build, a rebuild and a sync
    read the Session a bounded number of times, and the bound does not move
    between five cards and forty. It was O(cards) with a factor of two —
    40 cards meant 82 label-scope builds, 84 plot resolutions and 169
    tracked-name reads per rebuild, 0.37 s against 0.03 s at five.

    Drawing is not in the bound: `show()` is per card by nature — a figure
    is assembled for the card being drawn — and the page draws only what is
    scrolled to."""
    shell = _watch_shell(tmp_path, n - 2)
    session = shell.session
    calls = _count_reads(session)
    # A card's `+` reads no name list when it is drawn: its add form reads
    # the result file's names when it OPENS (U2's picker).
    listed = [0]
    real_names = session.variable_names

    def counting_names(*a, **k):
        listed[0] += 1
        return real_names(*a, **k)

    monkeypatch.setattr(session, "variable_names", counting_names)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _settled(lambda: len(page.cards) == n)
        build = dict(calls)
        for key in calls:
            calls[key] = 0
        view = session.tree_view(A)
        for key in calls:
            calls[key] = 0
        listed[0] = 0
        with page.column:
            page.rebuild(view)
        # the page's own reads (the plots' `missing`), never one per card
        assert listed[0] <= 2, (listed, n)
        rebuild = dict(calls)
        for key in calls:
            calls[key] = 0
        with page.client:
            page.sync()
        sync = dict(calls)
    # Measured with the plots derived from the tree, the same at 5 and 40:
    # the build 2 scope builds, 4 plot resolutions and 9 tracked-name reads;
    # a rebuild or a sync about half that.
    for phase in (build, rebuild, sync):
        assert phase["variable_texts"] <= 2, (phase, n)
        assert phase["plots"] <= 4, (phase, n)
        assert phase["tracked"] <= 9, (phase, n)


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


def _slot_holds(row) -> list:
    """What a criteria row's slot holds besides its host's error labels,
    which live there — on a line of their own under the row, hidden until
    an act is refused (the Task 6 review)."""
    errors = list(row.host.errors.values())
    return [c for c in row.slot.default_slot.children if c not in errors]


async def test_a_click_on_a_row_opens_its_editor_below_it(shell):
    """Brief §3.2: the editor is whatever attaches at CRITERION, stacked
    without headings — Edit criteria and Edit on plot read as ONE editor,
    so there are no tabs and no panel of the shell's own."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        row = page.cards[0].rows[T0]
        assert _slot_holds(row) == []
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


NRMSE_AND_RANGE = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
        {"metric": "range", "variable": "x", "min_value": -1},
    ],
}
STAGED_AND_SAVE = " Edits are staged as you make them; Save writes them."


BOTH_HINT = (
    "Drag a ring or a tolerance arrow on the plot (hover a point to "
    "show its arrows), or edit the fields."
)
ARROWS_HINT = (
    "Drag a tolerance arrow on the plot (hover near it to show the arrows), "
    "or edit the fields."
)
RINGS_HINT = "Drag a ring on the plot, or edit the fields."
LINE_HINT = "Drag a line on the plot, or edit the fields."
FIELDS_HINT = "Edit the fields."


async def test_the_editors_hint_says_what_drags_on_the_plot(shell):
    """The hint says what THIS criterion draws to be dragged. A range has
    bound lines — no ring, no arrow, no point to hover — and was told to
    drag "a ring or a tolerance arrow"; nrmse draws nothing and was told to
    drag a ring. Its Edit on plot place says why it has none."""
    _stage_tree(shell, A, NRMSE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        assert _one(user, f"editor-hint:0:{T1}").text == LINE_HINT + STAGED_AND_SAVE
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert _one(user, f"editor-hint:0:{T0}").text == FIELDS_HINT + STAGED_AND_SAVE
        assert _one(user, "why:edit-on-plot").text == "nrmse has no on-plot handles"


#: `test_handles.TREE`'s leaves on D and four more — a scalar scored
#: against its baseline, a tube of constant width, declared events and
#: declared peaks: what each draws to be dragged, and whether it has a
#: table.
MORE_LEAVES = [
    {"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}},
    {"metric": "tube", "variable": "x", "tube_width_mode": "band", "tube_abs": 0.3},
    {
        "metric": "event-timing",
        "variable": "x",
        "events": [{"time": 0.5, "tolerance": 0.1}],
    },
    {
        "metric": "dominant-frequency",
        "variable": "x",
        "peaks": [{"freq": 1.0, "tolerance": 0.1}],
    },
]
#: What the hint adds where a Ctrl+click adds a row (D149), in the
#: metric's own word for one.
ADDS = " Ctrl+click adds {}."
HINTS = [
    # tube: a ring per control point, and no arrow
    (0, RINGS_HINT + ADDS.format("a control point"), True),
    (1, LINE_HINT, False),  # range: its two bound lines
    # points: a ring per checkpoint, an arrow per side
    (2, BOTH_HINT + ADDS.format("a point"), True),
    (3, LINE_HINT, False),  # scalar, by its bounds
    (6, LINE_HINT, False),  # scalar, against its baseline: its two sides' lines
    # a constant tube: an arrow per side, no ring — and no row to add
    (7, ARROWS_HINT, True),
    # events: a window's two arrows, no ring
    (8, ARROWS_HINT + ADDS.format("an event"), True),
    # peaks: a ring per declared peak, its window's arrows
    (9, BOTH_HINT + ADDS.format("a peak"), True),
]


@pytest.mark.parametrize(("child", "hint", "table"), HINTS)
async def test_the_hint_and_the_tables_line_follow_what_the_criterion_draws(
    shell, child, hint, table
):
    """Read off what the plot draws for the open criterion, not off its
    metric's name: rings and arrows, arrows alone (a constant tube and an
    event's window were told to "drag a ring" and to "hover a point" they
    do not have), rings alone, lines alone, or nothing. And "drag on the
    plot, or edit the table" is said where a table is drawn."""
    tree = copy.deepcopy(TREE)
    tree["children"] += copy.deepcopy(MORE_LEAVES)
    _stage_tree(shell, D, tree)
    path = f"/metrics/children/{child}"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.cards[0].show()
        user.find(marker=f"criterion:0:{path}").click()
        await _settled(lambda: page.open == (0, path))
        assert _one(user, f"editor-hint:0:{path}").text == hint + STAGED_AND_SAVE
        said = [
            e.text
            for e in _one(user, f"editor:0:{path}").descendants()
            if isinstance(e, ui.label)
        ]
        assert ("drag on the plot, or edit the table" in said) is table


async def test_the_editors_hint_follows_a_switch_of_mode(shell):
    """The hint is the open criterion's: switched from nrmse to a mode
    with lines to drag in the editor, it says the new mode's."""
    _stage_tree(shell, A, NRMSE_AND_RANGE)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        hint = _one(user, f"editor-hint:0:{T0}")
        assert hint.text == FIELDS_HINT + STAGED_AND_SAVE
        (mode,) = [
            e
            for e in page.client.elements.values()
            if isinstance(e, ui.select) and e.props.get("label") == "mode"
        ]
        mode.set_value("range")
        await _settled(lambda: hint.text == LINE_HINT + STAGED_AND_SAVE)
        mode.set_value("points")
        added = ADDS.format("a point")
        await _settled(lambda: hint.text == BOTH_HINT + added + STAGED_AND_SAVE)


async def test_the_editors_hint_is_the_leafs_again_after_a_discard_and_after_a_save(
    shell,
):
    """An edit staged elsewhere moves the leaf under the open editor, and
    the editor is rebuilt on it: Discard undoes a switch from nrmse to
    range, and the hint — "Drag a line" while the range was staged — is
    nrmse's again, over nrmse's fields. Save moves nothing under the
    editor: the range it writes is the range the editor shows, and the
    hint stands."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        hint = _one(user, f"editor-hint:0:{T0}")
        assert hint.text == FIELDS_HINT + STAGED_AND_SAVE

        def to_range() -> None:
            (mode,) = [
                e
                for e in page.client.elements.values()
                if isinstance(e, ui.select) and e.props.get("label") == "mode"
            ]
            mode.set_value("range")

        to_range()
        await _settled(lambda: hint.text == LINE_HINT + STAGED_AND_SAVE)
        await _settled(lambda: _one(user, "discard").enabled)
        user.find(marker="discard").click()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: hint.text == FIELDS_HINT + STAGED_AND_SAVE)
        # The same editor, on the same row: rebuilt, not reopened.
        assert page.open == (0, T0) and _one(user, f"editor-hint:0:{T0}") is hint

        to_range()
        await _settled(lambda: hint.text == LINE_HINT + STAGED_AND_SAVE)
        await _settled(lambda: _one(user, "save").enabled)
        user.find(marker="save").click()
        await _settled(lambda: shell.session.spec.pending == [])
        assert shell.session.spec.saved_entry(A)["metrics"]["children"][0][
            "metric"
        ] == ("range")
        await asyncio.sleep(0.25)
        assert page.open == (0, T0) and _one(user, f"editor-hint:0:{T0}") is hint
        assert hint.text == LINE_HINT + STAGED_AND_SAVE


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
        assert _slot_holds(row) == []
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
            D, 0, editing=(T0,), visibility=card.visibility()
        )
        rings = {
            s["name"]
            for s in card.plot.figure["layout"]["shapes"]
            if s.get("path") == RING_PATH
        }
        assert rings and all(name.startswith(T0 + "/") for name in rings)
        user.find(marker=f"editor-done:0:{T0}").click()
        await _settled(lambda: page.open is None)
        assert _sans_rev(card.plot.figure) == shell.session.plot_figure(
            D, 0, editing=(), visibility=card.visibility()
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
            D, 0, editing=(T0,), visibility=card.visibility()
        )
        # The table followed the ring: the editor was rebuilt whole.
        await _settled(
            lambda: _one(user, f"{T0}:tube_points:0:upper").value == pytest.approx(0.3)
        )


async def test_a_refused_drags_reason_is_said_under_the_plot_until_the_next_edit(
    shell,
):
    """A refused drag's reason was said in the editor alone — a screen
    below the plot the hand is on. It is also one line directly under the
    plot, the editor's own text; the next drag that goes through takes it
    away, and so does closing the criterion."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        # Hidden while it says nothing: the simulation finds no such line.
        assert _none(user, "plot-refusal:0")
        line = card.refusal
        assert not line.visible and "plot-refusal:0" in line._markers
        # Between the plot and the criteria rows.
        order = list(card.card.descendants())
        assert (
            order.index(card.slot)
            < order.index(line)
            < order.index(_one(user, "criteria-header:0"))
        )
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        n = _shape_index(card, f"{T0}/tube/upper/0")

        def drag(y: float) -> None:
            with page.client:
                card.hook._dispatch(
                    "plotly_relayout",
                    {f"shapes[{n}].xanchor": 0.5, f"shapes[{n}].yanchor": y},
                )

        # The reference is 0.5 at t = 0.5: an upper edge at 0.2 is under it.
        pending = list(shell.session.spec.pending)
        drag(0.2)
        assert shell.session.spec.pending == pending
        error = card.editor.errors["edit-on-plot"]
        assert error.visible and "would be negative" in error.text
        assert line.visible and line.text == error.text
        assert _one(user, "plot-refusal:0") is line
        assert "text-negative" in line.classes
        # A drag that goes through: the line goes.
        drag(0.8)
        assert shell.session.spec.pending != pending
        assert not line.visible
        # Refused again, then the criterion closed: gone with it.
        drag(0.2)
        assert line.visible and "would be negative" in line.text
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open is None)
        assert not line.visible


async def test_a_refused_drags_reason_goes_when_the_editor_is_rebuilt_from_elsewhere(
    shell,
):
    """The line under the plot is the editor's own error line said again.
    An edit staged ELSEWHERE — another node's controls, a Discard — moves
    the leaf under the open editor and rebuilds it, which takes the
    editor's line: the line under the plot goes with it, or it stood there
    saying a refusal of a drag the criterion no longer shows."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        line = card.refusal
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        n = _shape_index(card, f"{T0}/tube/upper/0")
        with page.client:
            card.hook._dispatch(
                "plotly_relayout",
                {f"shapes[{n}].xanchor": 0.5, f"shapes[{n}].yanchor": 0.2},
            )
        assert line.visible and "would be negative" in line.text
        cell = _one(user, f"{T0}:tube_points:0:time")
        moved = copy.deepcopy(TREE["children"][0])
        moved["tube_points"][0]["time"] = 0.45
        assert cell.value != 0.45
        shell.session.stage([PatchOp(D, "replace", T0, moved)])
        # The editor was rebuilt from the leaf as it now is ...
        await _settled(lambda: _one(user, f"{T0}:tube_points:0:time").value == 0.45)
        assert not card.editor.errors["edit-on-plot"].visible
        # ... and nothing is said under the plot.
        assert not line.visible


async def test_a_refused_field_edit_is_not_said_under_the_plot(shell):
    """Only a DRAG's refusal goes under the plot: one typed into the editor
    is said there, where the typing is."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        line = card.refusal
        cell = _one(user, f"{T0}:tube_points:0:time")
        with page.client:
            cell.set_value(float("inf"))
        error = card.editor.errors["edit-on-plot"]
        await _settled(lambda: error.visible)
        assert not line.visible


# -- a half cell outlives the panel it was typed in ---------------------------


def _half_tree() -> dict:
    """D's x scored by a points leaf of two rows, each holding the SAME
    pair, and a range beside it."""
    return {
        "combinator": "and",
        "children": [
            {
                "metric": "points",
                "variable": "x",
                "tolerance": 0.3,
                "points": [
                    {"time": 0.25, "value": 0.3, "tolerance": _pair(0.1, 0.3)},
                    {"time": 0.75, "value": 0.9, "tolerance": _pair(0.1, 0.3)},
                ],
            },
            {"metric": "range", "variable": "x", "min_value": -1},
        ],
    }


#: Row 1's tolerance cell in the open editor's table, and its row.
HALF_ROW = f"{T0}:points:1"
HALF = f"{HALF_ROW}:tolerance"


async def _editor_with_a_half_cell(shell, user) -> TestPage:
    """D's points criterion open, row 1's lower tolerance cleared: blank,
    "required", nothing pending."""
    page = await _test_page(shell, user, D)
    with page.client:
        page.cards[0].show()
    user.find(marker=f"criterion:0:{T0}").click()
    await _settled(lambda: page.open == (0, T0))
    with page.client:
        _one(user, f"{HALF}:lower").set_value(None)
    assert _one(user, f"{HALF}:lower").error == "required"
    assert shell.session.spec.pending == []
    return page


def _is_half(user) -> bool:
    """Row 1's cell as a half pair shows: its lower side blank and marked,
    its upper side as it was — and row 0's, the same pair, whole."""
    lower, upper = _one(user, f"{HALF}:lower"), _one(user, f"{HALF}:upper")
    other = _one(user, f"{T0}:points:0:tolerance:lower")
    return (
        (lower.value, lower.error, upper.value, upper.error)
        == (None, "required", 0.3, None)
        and (other.value, other.error) == (0.1, None)
        and _one(user, f"{HALF}:link").props["icon"] == "link_off"
    )


async def _fills_and_stages(shell, user) -> None:
    """The half cell still holds the list back — a time typed beside it is
    not staged — and the key that fills it stages the lot."""
    before = copy.deepcopy(shell.session.tree(D)["children"][0]["points"])
    with user.client:
        _one(user, f"{HALF_ROW}:time").set_value(0.8)
    await asyncio.sleep(0.25)
    assert shell.session.tree(D)["children"][0]["points"] == before
    with user.client:
        _one(user, f"{HALF}:lower").set_value(0.2)
    await _settled(
        lambda: (
            shell.session.tree(D)["children"][0]["points"][1]
            == {"time": 0.8, "value": 0.9, "tolerance": _pair(0.2, 0.3)}
        )
    )
    assert _one(user, f"{HALF}:lower").error is None


async def test_a_half_cell_stays_blank_through_a_drag_on_the_plot(tmp_path):
    """A drag rebuilds the WHOLE editor, so the table follows the ring.
    Built from the spec alone, the blank side of a half pair came back as
    its old value, "required" gone, and the next cell edit staged the list
    with that pair in it. The drag's own edit is staged — the leaf moved —
    and the half cell is still blank, marked, and holding the list back."""
    shell = _spec_tree_shell(tmp_path, _half_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_cell(shell, user)
        card = page.cards[0]
        was = _one(user, f"{HALF}:lower")
        n = _shape_index(card, f"{T0}/points/target/0")
        with page.client:
            card.hook._dispatch(
                "plotly_relayout",
                {f"shapes[{n}].xanchor": 0.4, f"shapes[{n}].yanchor": 0.3},
            )
        staged = _pending(shell, T0)["points"]
        assert staged[0]["time"] == pytest.approx(0.4)  # the drag's own edit
        assert staged[1] == {"time": 0.75, "value": 0.9, "tolerance": _pair(0.1, 0.3)}
        # The table followed the ring — rebuilt — and the cell stayed half.
        await _settled(
            lambda: _one(user, f"{T0}:points:0:time").value == pytest.approx(0.4)
        )
        assert _one(user, f"{HALF}:lower") is not was
        assert _is_half(user)
        await _fills_and_stages(shell, user)


async def test_a_half_cell_stays_blank_through_an_edit_in_the_criterions_form(
    tmp_path,
):
    """A field of the criterion's form typed in rebuilds every OTHER panel
    of the editor — the table's. The same cell, the same way."""
    shell = _spec_tree_shell(tmp_path, _half_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_cell(shell, user)
        was = _one(user, f"{HALF}:lower")
        with page.client:
            _one(user, "tolerance:lower").set_value(0.07)  # Default tolerance
        await _settled(lambda: (_pending(shell, T0) or {}).get("tolerance") == 0.07)
        assert _pending(shell, T0)["points"][1]["tolerance"] == _pair(0.1, 0.3)
        await _settled(lambda: _one(user, f"{HALF}:lower") is not was)
        assert _is_half(user)
        await _fills_and_stages(shell, user)


def _half_form_tree() -> dict:
    """D's x scored by a points leaf whose OWN tolerance is a pair, with
    two rows that follow it, and a range beside it."""
    return {
        "combinator": "and",
        "children": [
            {
                "metric": "points",
                "variable": "x",
                "tolerance": _pair(0.1, 0.3),
                "points": [
                    {"time": 0.25, "value": 0.3},
                    {"time": 0.75, "value": 0.9},
                ],
            },
            {"metric": "range", "variable": "x", "min_value": -1},
        ],
    }


async def _editor_with_a_half_form(shell, user) -> TestPage:
    """D's points criterion open, the `+` of its Default tolerance cleared:
    blank, "required", nothing pending."""
    page = await _test_page(shell, user, D)
    with page.client:
        page.cards[0].show()
    user.find(marker=f"criterion:0:{T0}").click()
    await _settled(lambda: page.open == (0, T0))
    with page.client:
        _one(user, "tolerance:upper").set_value(None)
    assert _one(user, "tolerance:upper").error == "required"
    assert shell.session.spec.pending == []
    return page


def _form_is_half(user) -> bool:
    """The form's tolerance as a half pair shows: `−` as it was, `+` blank
    and marked, unlinked."""
    lower, upper = _one(user, "tolerance:lower"), _one(user, "tolerance:upper")
    return (lower.value, lower.error, upper.value, upper.error) == (
        0.1,
        None,
        None,
        "required",
    ) and _one(user, "tolerance:link").props["icon"] == "link_off"


async def _fills_the_form(shell, user) -> None:
    """The key that fills the blank stages the pair, and the record goes."""
    with user.client:
        _one(user, "tolerance:upper").set_value(0.4)
    await _settled(
        lambda: (_pending(shell, T0) or {}).get("tolerance") == _pair(0.1, 0.4)
    )
    assert _one(user, "tolerance:upper").error is None


async def test_a_half_pair_on_the_form_stays_blank_through_a_drag_on_the_plot(
    tmp_path,
):
    """A drag rebuilds the WHOLE editor — the criterion's form too. Its
    fields never read `Panel.kept`: the blank `+` of a half-typed Default
    tolerance came back as its old value, unmarked. The drag's own edit is
    staged, and the form is still half."""
    shell = _spec_tree_shell(tmp_path, _half_form_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_form(shell, user)
        card = page.cards[0]
        was = _one(user, "tolerance:upper")
        n = _shape_index(card, f"{T0}/points/target/0")
        with page.client:
            card.hook._dispatch(
                "plotly_relayout",
                {f"shapes[{n}].xanchor": 0.4, f"shapes[{n}].yanchor": 0.3},
            )
        staged = _pending(shell, T0)
        assert staged["points"][0]["time"] == pytest.approx(0.4)  # the drag's own
        assert staged["tolerance"] == _pair(0.1, 0.3)
        await _settled(lambda: _one(user, "tolerance:upper") is not was)
        assert _form_is_half(user)
        assert page.state.kept[D][T0, "edit-criteria"]
        await _fills_the_form(shell, user)
        assert not page.state.kept[D][T0, "edit-criteria"]


async def test_a_half_pair_on_the_form_stays_blank_through_an_edit_in_the_table(
    tmp_path,
):
    """A cell of the table typed in rebuilds every OTHER panel of the
    editor — the criterion's form. The same fields, the same way."""
    shell = _spec_tree_shell(tmp_path, _half_form_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_form(shell, user)
        was = _one(user, "tolerance:upper")
        with page.client:
            _one(user, f"{T0}:points:1:time").set_value(0.8)
        await _settled(
            lambda: (
                (_pending(shell, T0) or {}).get("points", [{}, {}])[1].get("time")
                == 0.8
            )
        )
        assert _pending(shell, T0)["tolerance"] == _pair(0.1, 0.3)
        await _settled(lambda: _one(user, "tolerance:upper") is not was)
        assert _form_is_half(user)
        await _fills_the_form(shell, user)


@pytest.mark.parametrize("lands", ["discard", "save"])
async def test_a_discard_or_a_save_takes_a_half_pair_on_the_form_with_it(
    tmp_path, lands
):
    """The form's record has the tables' lifetime: a Discard or a Save
    drops it, and the fields show the spec's pair."""
    shell = _spec_tree_shell(tmp_path, _half_form_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_form(shell, user)
        with page.client:
            _one(user, f"{T0}:points:1:time").set_value(0.8)
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: _form_is_half(user))
        assert page.state.kept[D][T0, "edit-criteria"]
        getattr(shell.session, lands)()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: _one(user, "tolerance:upper").value == 0.3)
        assert _one(user, "tolerance:upper").error is None
        assert not page.state.kept.get(D, {}).get((T0, "edit-criteria"))
        assert page.open == (0, T0)


async def test_a_blank_required_cell_stays_blank_through_a_drag(shell):
    """The same record holds a required cell cleared to be retyped: a tube
    point's time, blank while the other point's ring is dragged."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        cell = f"{T0}:tube_points:0:time"
        with page.client:
            _one(user, cell).set_value(None)
        assert _one(user, cell).error == "time is required"
        n = _shape_index(card, f"{T0}/tube/upper/1")
        with page.client:
            card.hook._dispatch(
                "plotly_relayout",
                {f"shapes[{n}].xanchor": 0.9, f"shapes[{n}].yanchor": 1.2},
            )
        await _settled(
            lambda: _one(user, f"{T0}:tube_points:1:upper").value == pytest.approx(0.3)
        )
        time = _one(user, cell)
        assert (time.value, time.error) == (None, "time is required")
        assert _one(user, f"{T0}:tube_points:1:time").error is None
        assert _pending(shell, T0)["tube_points"][0]["time"] == 0.5
        with page.client:
            time.set_value(0.6)
        await _settled(lambda: _pending(shell, T0)["tube_points"][0]["time"] == 0.6)
        assert _pending(shell, T0)["tube_points"][1]["upper"] == pytest.approx(0.3)


@pytest.mark.parametrize("lands", ["discard", "save"])
async def test_a_discard_or_a_save_takes_a_half_cell_with_it(tmp_path, lands):
    """What a cell held beside the spec was typed against an entry that a
    Discard or a Save replaces: the record is dropped and the table shows
    the spec's rows — after a Discard, whose leaf moved, and after a Save,
    whose leaf did not (nothing else would rebuild the editor then, and
    the blank would outlive its record until the next drag filled it)."""
    shell = _spec_tree_shell(tmp_path, _half_tree())
    async with user_simulation(root=shell.index) as user:
        page = await _editor_with_a_half_cell(shell, user)
        with page.client:
            _one(user, "tolerance:lower").set_value(0.07)
        await _settled(lambda: (_pending(shell, T0) or {}).get("tolerance") == 0.07)
        assert _is_half(user)
        assert page.state.kept[D][T0, "edit-on-plot"]
        getattr(shell.session, lands)()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: _one(user, f"{HALF}:lower").value == 0.1)
        assert _one(user, f"{HALF}:lower").error is None
        assert not page.state.kept.get(D, {}).get((T0, "edit-on-plot"))
        assert page.open == (0, T0)


async def test_what_a_panel_kept_follows_its_node_and_goes_with_it(shell):
    """`Panel.kept` is keyed by the node's path: a tree edit that moves the
    node moves it (`TestPage.edited`), and one that drops the node drops
    it. The same dict comes back for the same node until then."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        first = page.kept(T0, "edit-on-plot")
        first["a"] = 1
        page.kept(T1, "edit-on-plot")["b"] = 2
        assert page.kept(T0, "edit-on-plot") is first
        assert page.kept(T0, "edit-criteria") is not first  # a panel's own
        page.edited({"/metrics": "/metrics", T1: T0})  # T0 removed
        assert page.state.kept[D] == {(T0, "edit-on-plot"): {"b": 2}}


def test_an_edit_that_is_not_the_tabs_drops_what_its_panels_kept():
    """As the folds (`heard_spec`, `caught_up`): the tab's own revision
    keeps the record, any other one drops the tests it names — all of them
    when it names none — and a tab that missed a revision keeps nothing."""
    from dstf.authoring.shell.tabstate import TabState, caught_up, heard_spec

    def state() -> TabState:
        kept = {m: {(T0, "edit-on-plot"): {"k": 1}} for m in (A, D)}
        return TabState(kept=kept, own_revisions={3}, seen_revision=2)

    own = state()
    heard_spec(own, {"event": "spec", "revision": 3, "models": {A}})
    assert set(own.kept) == {A, D}
    other = state()
    heard_spec(other, {"event": "spec", "revision": 4, "models": {A}})
    assert set(other.kept) == {D}
    closed = state()
    heard_spec(closed, {"event": "spec", "revision": 4, "models": set()})
    assert closed.kept == {}
    missed = state()
    caught_up(missed, 4)
    assert missed.kept == {}
    seen = state()
    caught_up(seen, 3)
    assert set(seen.kept) == {A, D}


async def test_a_refused_drag_is_said_in_the_editor_and_the_plot_snaps_back(
    shell, caplog
):
    """Only the open criterion gets rings, but every leaf's bound LINES are
    on the plot and Plotly lets them be dragged: one of those is refused
    with a sentence saying to open its criterion, stages nothing, and the
    plot is re-sent so the line goes back."""
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
        # Its reason alone: the error sits in that criterion's editor, and a
        # person's own drag is not a bug to log.
        assert error.text == "Open that criterion to drag its lines."
        assert not _logged(caplog, other)
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


async def test_an_open_editors_panels_follow_a_refusal_that_comes_and_goes(
    no_tree_shell,
):
    """The editor is not rebuilt while it is open, so what its capabilities
    MAY do has to be re-read: the Session's refusal to materialize the tree
    takes their panels and says so, and an override that lifts the refusal
    brings them back. The editor is y's: the override is on x, so the leaf
    under it does not move and nothing but `follow` reaches its panels."""
    from dstf.authoring.session import NO_TREE

    shell = no_tree_shell
    shell.session.stage([SHADOW_X])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        editor = f"editor:1:{T1}"
        assert _inside(user, "panel:edit-criteria", editor)
        shell.session.discard()
        await _settled(lambda: _inside(user, "panel:edit-criteria", editor) == [])
        (why,) = _inside(user, "why:edit-criteria", editor)
        assert why.text == NO_TREE
        # Edit on plot is refused too, and says nothing more: one line.
        texts = [getattr(e, "text", "") for e in _one(user, editor).descendants()]
        assert texts.count(NO_TREE) == 1
        assert page.open == (1, T1)
        shell.session.stage([SHADOW_X])
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


def _pair(lower: float, upper: float) -> dict:
    return {"lower": lower, "upper": upper}


#: A lower/upper pair in each of the nine fields that take one, every side a
#: number of its own: a leaf's `tolerance` on points and scalar; a points
#: row's `tolerance` and `time_tolerance`; event-timing's `time_tolerance`
#: and an events row's `tolerance`; a peaks row's `tolerance`; `tube_abs`;
#: `tube_rel`.
NINE_PAIRS = {
    "combinator": "and",
    "children": [
        {
            "metric": "points",
            "variable": "x",
            "tolerance": _pair(0.11, 0.12),
            "points": [
                {
                    "time": 0.5,
                    "value": 0.5,
                    "tolerance": _pair(0.21, 0.22),
                    "tolerance_mode": "abs",
                    "time_tolerance": _pair(0.31, 0.32),
                }
            ],
        },
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": "max"},
            "tolerance": _pair(0.41, 0.42),
        },
        {
            "metric": "event-timing",
            "variable": "x",
            "time_tolerance": _pair(0.51, 0.52),
            "events": [{"time": 0.5, "tolerance": _pair(0.61, 0.62)}],
        },
        {
            "metric": "dominant-frequency",
            "variable": "x",
            "peaks": [
                {"freq": 1.0, "tolerance": _pair(0.71, 0.72), "tolerance_mode": "rel"}
            ],
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_abs": _pair(0.81, 0.82),
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "rel",
            "tube_rel": _pair(0.91, 0.92),
        },
    ],
}
#: Per leaf of `NINE_PAIRS`, in tree order: the mark of each two-sided
#: control its editor draws (a form field's is the field's name, a table
#: cell's `{path}:{field}:{row}:{key}`) and the pair it must show.
NINE_SIDED = [
    [
        ("tolerance", (0.11, 0.12)),
        ("{path}:points:0:tolerance", (0.21, 0.22)),
        ("{path}:points:0:time_tolerance", (0.31, 0.32)),
    ],
    [("tolerance", (0.41, 0.42))],
    [
        ("time_tolerance", (0.51, 0.52)),
        ("{path}:events:0:tolerance", (0.61, 0.62)),
    ],
    [("{path}:peaks:0:tolerance", (0.71, 0.72))],
    [("tube_abs", (0.81, 0.82))],
    [("tube_rel", (0.91, 0.92))],
]


def _descendants(element):
    for slot in element.slots.values():
        for child in slot.children:
            yield child
            yield from _descendants(child)


def _error_lines(box) -> list[str]:
    """Every error SHOWN inside ``box``: `InlineHost` writes a panel that
    raised while it was built there (`TypeError: …`), and a refused act
    above its panel — both a `text-negative` label."""
    return [
        e.text
        for e in _descendants(box)
        if isinstance(e, ui.label)
        and "text-negative" in e.classes
        and e.visible
        and e.text
    ]


async def test_every_criterion_of_a_tree_holding_pairs_opens_without_an_error(
    tmp_path,
):
    """A leaf holding a lower/upper pair is a leaf like any other: its
    editor opens, both of its panels are drawn, and each of the nine fields
    that take a pair shows the two sides the file holds. Handed a dict where
    it expected a number, a number field raised while the panel was built
    and the editor read `TypeError: int() argument must be … not 'dict'`."""
    shell = _spec_tree_shell(tmp_path, NINE_PAIRS)
    seen = 0
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        for k, sided in enumerate(NINE_SIDED):
            path = f"/metrics/children/{k}"
            with page.client:
                page.open_criterion(path)
            assert page.open is not None and page.open[1] == path, path
            box = page._editor_on(page.open[0])
            assert box is not None
            assert _error_lines(box) == [], path
            drawn = {m for e in _descendants(box) for m in e._markers}
            assert {"panel:edit-criteria", "panel:edit-on-plot"} <= drawn, path
            for mark, pair in sided:
                mark = mark.format(path=path)
                shown = tuple(
                    e.value
                    for side in ("lower", "upper")
                    for e in _descendants(box)
                    if f"{mark}:{side}" in e._markers
                )
                assert shown == pair, (path, mark)
                seen += 1
        assert shell.session.spec.pending == []  # opening an editor stages nothing
    assert seen == 9


@pytest.mark.parametrize(
    "written", [_pair(0.1, 0.2), "0.1", True, [0.1, 0.2], {"lower": 0.1}]
)
async def test_a_criterion_holding_what_is_no_tolerance_opens_blank_and_unhinted(
    tmp_path, written
):
    """The same for a leaf whose tolerance is its config error — a pair on
    nrmse, which takes one number, and on nrmse and scalar a string, a
    bool, a list, half a pair. The criteria row says the error; the editor
    opens with its fields drawn, the one that cannot show the value blank,
    and NO "in effect" hint: that hint means nothing is set here. Handed
    the pair, nrmse's number field raised and the editor read `TypeError`."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x", "tolerance": written},
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "max"},
                "tolerance": written,
            },
            {"metric": "nrmse", "variable": "y"},
        ],
    }
    shell = _spec_tree_shell(tmp_path, tree)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        hints = {}
        for k in range(3):
            path = f"/metrics/children/{k}"
            with page.client:
                page.open_criterion(path)
            assert page.open is not None and page.open[1] == path, path
            box = page._editor_on(page.open[0])
            assert box is not None
            assert _error_lines(box) == [], path
            drawn = {m for e in _descendants(box) for m in e._markers}
            assert "panel:edit-criteria" in drawn, path
            numbers = {
                e.props["label"]: e
                for e in _descendants(box)
                if isinstance(e, ui.number) and "olerance" in e.props["label"]
            }
            hints[k] = {
                label: e.props.get("hint") for label, e in numbers.items() if e.visible
            }
            if k == 0:
                assert numbers["Tolerance"].value is None
            elif k == 1 and written != _pair(0.1, 0.2):  # a scalar takes a pair
                shown = (numbers["− Tolerance"].value, numbers["+ Tolerance"].value)
                assert shown == (
                    (0.1, None) if isinstance(written, dict) else (None,) * 2
                )
        said = "in effect: 0.01 (the project's tolerance)"
        assert hints == {
            0: {"Tolerance": None},
            1: {"− Tolerance": None, "+ Tolerance": None},
            2: {"Tolerance": said},  # the hint is drawn where nothing is set
        }
        assert shell.session.spec.pending == []


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


async def test_an_edit_that_drops_the_open_editors_card_rebuilds_the_page(
    shell, caplog
):
    """The rebuild closed the editor, which redrew the card it was about to
    drop: with the editor open on the last card and an edit that leaves
    ONE plot, that redraw asked the Session for a plot that no longer
    exists, the rebuild died half-done with the shape already recorded, and
    the page never rebuilt again. x's last criterion goes, and with it x's
    plot: y's card, the open one, moves up."""
    import logging

    _stage_tree(shell, A, TWO_RANGES)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.stage([PatchOp(A, "remove", T0)])
            await _settled(lambda: len(page.cards) == 1)
        assert page.open is None
        assert _one(user, "plot-title:0").text == "y"
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


async def test_a_value_typed_and_cleared_again_is_no_pending_edit(scored_shell):
    """An upper bound typed into a range that had none, then cleared: the
    entry is exactly the saved one. It counted — "1 pending edit", the
    strip's "1 unsaved edit", Save enabled, the Tests drawer's mark — and
    Save rewrote the file. Every one of them reads the pending list."""
    shell = scored_shell
    before = shell.session.spec.path.read_bytes()
    async with user_simulation(root=shell.index) as user:
        page = await _tests_drawer(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        (upper,) = _numbers(page, "Upper bound (optional)")
        with page.client:
            upper.set_value(5.0)
        await _settled(lambda: _one(user, "pending").text == "1 pending edit")
        assert _one(user, "strip-text").text == "1 unsaved edit · verdict stays PASS"
        assert _one(user, "save").enabled
        assert page.rail.tests.list.rows[D].pending == 1
        with page.client:
            upper.set_value(None)
        await _settled(lambda: _one(user, "pending").text == "0 pending edits")
        assert shell.session.spec.pending == []
        assert _one(user, "strip-text").text == "Passes against its baseline."
        assert page.strip.situation.value != "edits"
        assert not _one(user, "save").enabled
        assert _one(user, "save:why").text == "no pending edits"
        assert page.rail.tests.list.rows[D].pending == 0
        assert not page.rail.tests.list.rows[D].edits.visible
        # The preview has nothing to show, and the editor is still open on
        # the same field.
        user.find(marker="pending").click()
        await user.should_see("nothing staged", retries=20)
        assert _numbers(page, "Upper bound (optional)") == [upper]
    assert shell.session.spec.path.read_bytes() == before


async def test_a_value_typed_and_cleared_on_a_flat_test_is_no_pending_edit(shell):
    """D's entry holds no tree (every TRANSFORM test's shape): the first
    value typed writes its tree out under the edit — two ops — and the value
    cleared left that tree, "1 pending edit" and a Save that added a
    `metrics` block scoring as the entry already did. An entry as saved but
    for the tree it scores by is as saved: nothing pending, Save disabled."""
    before = shell.session.spec.path.read_bytes()
    assert "metrics" not in shell.session.spec.saved_entry(D)
    async with user_simulation(root=shell.index) as user:
        page = await _tests_drawer(shell, user, D)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        (tolerance,) = _numbers(page, "Tolerance")
        with page.client:
            tolerance.set_value(0.5)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        assert _one(user, "save").enabled
        assert page.rail.tests.list.rows[D].pending == 2
        with page.client:
            tolerance.set_value(None)
        await _settled(lambda: _one(user, "pending").text == "0 pending edits")
        assert shell.session.spec.pending == []
        assert _one(user, "strip-text").text == "Passes against its baseline."
        assert not _one(user, "save").enabled
        assert _one(user, "save:why").text == "no pending edits"
        assert page.rail.tests.list.rows[D].pending == 0
        assert not page.rail.tests.list.rows[D].edits.visible
        user.find(marker="pending").click()
        await user.should_see("nothing staged", retries=20)
        # The editor is still open on the same criterion, and takes the
        # next value: the tree is written out again under it.
        assert page.open == (0, T0)
        (again,) = _numbers(page, "Tolerance")
        with page.client:
            again.set_value(0.25)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        assert shell.session.tree(D)["children"][0]["tolerance"] == 0.25
    assert shell.session.spec.path.read_bytes() == before


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
    and the next event that was D's (here, its place in the queue moving)
    rebuilt D's editor, which threw away a cleared table cell nobody had
    retyped yet. The editor follows its OWN leaf."""
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
            page.handle({"event": "queue", "state": "queued", "models": {D}})
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


async def test_a_queue_event_leaves_the_editors_own_control_alone(shell):
    """The test's place in the queue moved and nothing was staged: the
    editor was opened on the leaf it still shows, so nothing is rebuilt —
    a queued test stays editable (D150), and what is being typed stays."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.open_criterion(T1)
        (lower,) = _numbers(page, "Lower bound (optional)")
        for state in ("queued", "started", "ended"):
            with page.client:
                page.handle({"event": "queue", "state": state, "models": {D}})
            assert _numbers(page, "Lower bound (optional)") == [lower], state
        assert page.open == (0, T1)


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
            page.handle({"event": "queue", "state": "queued", "models": {A}})
        slot = page.cards[0].slot.default_slot.children
        assert [c.text for c in slot] == [
            "RuntimeError: result file is being rewritten"
        ]
        boom["on"] = False
        with page.client:
            page.handle({"event": "queue", "state": "removed", "models": {A}})
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


async def test_only_an_event_that_moved_the_data_re_sends_the_plots(shell):
    """Decision 10: a run that STARTED took this test's trajectories with
    it, one that ENDED brought new ones, and a store write's notice reloaded
    every result — no leaf's signature sees any of the three, so each
    re-sends every drawn plot. The queue's other states move the strip and
    no trajectory, and re-send none: a force on every event of a run would
    re-send every drawn plot once per event."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        sent = _counted(page.cards[0])
        synced: list[bool] = []
        sync = page.sync
        page.sync = lambda force=False: (synced.append(force), sync(force))[1]

        def hear(event: str, **payload) -> None:
            with page.client:
                page.handle({"event": event, **payload})

        for state in ("queued", "removed", "cleared", "stopped"):
            hear("queue", state=state, models={A})
        assert synced == [False] * 4 and sent[0] == 0
        for state in ("started", "ended"):
            hear("queue", state=state, models={A})
        assert synced[4:] == [True, True] and sent[0] == 2
        hear("notice", message="Accepted 1 baseline")
        assert synced[6:] == [True] and sent[0] == 3
        # Another test's place in the queue, the worker count, and a notice
        # that carries an artifact are not this page's to sync on.
        for state in ("queued", "started", "ended", "removed"):
            hear("queue", state=state, models={D})
        hear("queue", state="workers", models=set())
        hear("notice", artifact=OUTCOME)
        assert len(synced) == 7 and sent[0] == 3


async def test_a_row_click_reads_the_tree_once_however_many_plots_are_drawn(
    tmp_path,
):
    """`_redraw_moved`, `show` and the signature each read `tree_view` for
    themselves: one row click on a twelve-plot page cost fourteen reads."""
    shell = _watch_shell(tmp_path, 10)
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
        # project's verdict, its baseline's name, and buttons that ran a
        # model this project does not have (the final review).
        assert page.badge.text == "" and page.meta.text == ""
        assert page.actions.dialog.value is not True
        assert page.actions.actions == {} and _none(user, "action:enqueue")
        assert page.actions.container.visible is False
        # And no later event brings them back while the test is gone.
        shell.session.apply(Artifact(paths=[], label="probe again"))
        await asyncio.sleep(0.3)
        assert page.actions.actions == {} and page.badge.text == ""


async def test_a_closed_session_leaves_the_page_saying_so(shell, caplog):
    """`Session.close()` emits a `spec` naming NO model: a page that read an
    empty `models` as "not mine" never heard, and the next event it synced
    on raised "no project is open"."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with caplog.at_level(logging.ERROR, logger="dstf"):
            shell.session.close()
            await _settled(lambda: page.gone)
            with page.client:
                page.handle({"event": "notice", "message": "Accepted 1 baseline"})
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
        assert "enqueue" in page.actions.actions


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
            shell.session.stage(
                [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["y"]}])]
            )
            await asyncio.sleep(0.4)
        # every try raised; the old cards stand
        assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]
        boom["on"] = False
        with page.client:
            page.sync()
        assert [c.resolved.series for c in page.cards] == [("x", "y"), ("y",)]


#: A stored-scalar leaf on y: it names a statistic, not a series, so it
#: derives no plot — y, scored by nothing else, is on no plot.
ON_NO_PLOT = {
    "combinator": "and",
    "children": [
        {"metric": "range", "variable": "x", "min_value": -1},
        {
            "metric": "scalar",
            "variable": "y",
            "quantity": "CPUtime.final",
            "max_value": 1e9,
        },
    ],
}


async def test_a_jump_to_a_statistics_criterion_opens_it_in_the_card(shell):
    """A criterion scored through a statistic is on no plot: its jump opens
    the Statistics card with its editor under its row (U3) — and the
    plot editor that was open goes, one editor page-wide."""
    _stage_tree(shell, A, ON_NO_PLOT)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",)]
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        with page.client:
            page.jump_to(T1)
        await _settled(lambda: page.open == (STATS, T1))
        assert page.stats.is_open
        assert _none(user, f"editor:0:{T0}")
        assert page.stats.slots["CPUtime.final"] in list(
            _ancestors(_one(user, f"stats-editor:{T1}"))
        )
        assert user.notify.messages == []


async def test_a_jump_to_a_criterion_with_no_row_opens_and_scrolls_nothing(
    shell, monkeypatch
):
    """The run failed while a jump was on screen, so no plot is drawn: the
    criterion has no row to open. With another editor open the jump
    scrolled to THAT row; now it opens and scrolls to nothing. (It said
    "… is on no plot of this test" until U3's fix batch 2: every leaf of a
    tree view is on a plot or in the Statistics card, and a page with no
    tree view shows no jump.)"""
    from dstf.authoring.shell import test_page as page_mod
    from dstf.backends.types import TestResult

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker=f"criterion:1:{T1}").click()
        await _settled(lambda: page.open == (1, T1))
        result = shell.session.project.results[A]
        shell.session.project.results[A] = TestResult(
            model_id=A, success=False, variables=list(result.variables)
        )
        _stage_tree(shell, A, TWO_RANGES)
        await _settled(lambda: _page(shell, user).cards == [])
        page = _page(shell, user)
        scrolled: list[str] = []
        monkeypatch.setattr(page_mod.ui, "run_javascript", scrolled.append)
        with page.client:
            page.jump_to(T1)
        await asyncio.sleep(0.2)
        assert scrolled == []
        assert page.open is None
        assert user.notify.messages == []


async def test_a_jump_opens_the_card_holding_the_row(shell):
    """With no card named, the jump takes the plot that draws it."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        (jump,) = [
            j for j in user.find(marker="tree:jump").elements if j.text == "nrmse · y"
        ]
        _click(user, jump)
        await _settled(lambda: page.open is not None)
        assert page.open == (1, T1)


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


async def test_a_run_that_ended_redraws_every_shown_plot(shell):
    """Decision 10: a new run changes the trajectories under every plot,
    and no leaf's signature sees that."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        sent = _counted(page.cards[0])
        with page.client:
            page.handle({"event": "queue", "state": "ended", "models": {A}})
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
            D, 0, editing=(T1,), visibility=card.visibility()
        )


async def test_focus_hides_the_other_cards_and_the_bar_brings_them_back(shell):
    """Board 5A: "Showing 1 of N plots" — the switcher beside it names the
    plot — the focused plot's button reading "Focused", and the bar's own
    way back to all of them."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.focus_bar.visible is False
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert [c.card.visible for c in page.cards] == [True, False]
        assert _one(user, "focus-bar:text").text == "Showing 1 of 2 plots"
        assert _one(user, "focus-switch").value == 0
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
        old = page.cards[0]
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "y", "context": ["x"]}])]
        )
        await _settled(lambda: page.cards[0] is not old)
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


async def test_the_focus_bar_reads_the_label(awkward_expression_shell):
    """A place that names a variable, on the page whose titles already read
    its expression: the focus switcher's option said `unitTests.x[1]`,
    which names nothing a person recognises."""
    from test_session import AWKWARD_EXPRESSION

    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == AWKWARD_EXPRESSION
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert _one(user, "focus-bar:text").text == "Showing 1 of 2 plots"
        assert _one(user, "focus-switch").options[0] == AWKWARD_EXPRESSION


async def test_the_scope_is_the_test_not_what_is_scored(shell):
    """Case (a) of the b147918 review: `x` and `y` share `a.Q` and only
    `x` still has a criterion. Disambiguated over the SCORED list the row
    read `nrmse · a.Q` under a card titled `a.Q` with `x` muted — the
    ambiguity the parentheses exist to remove. `y` is still the test's."""
    from test_session import with_expressions

    with_expressions(shell.session, A, {"x": "a.Q", "y": "a.Q"})
    shell.session._score(A)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click_in(user, f"remove:{T1}", within=f"node:1:{T1}")
        # y's card goes with its last criterion; y is still TRACKED.
        await _settled(lambda: len(page.cards) == 1)
        assert [r.name.text for r in page.cards[0].rows.values()] == ["nrmse · a.Q (x)"]
        assert _one(user, "plot-title:0").text == "a.Q"
        assert _one(user, "plot-name:0").text == "x"


async def test_a_legend_reads_the_tests_scope_not_the_plots(shell):
    """Case (b): the plot draws only ONE of the two variables sharing
    `a.Q`, so a legend disambiguated over the PLOT read `a.Q` while the
    same variable's criteria row read `a.Q (x)` two lines below it."""
    from test_session import with_expressions

    with_expressions(shell.session, A, {"x": "a.Q", "y": "a.Q"})
    shell.session._score(A)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.cards[0].resolved.series == ("x",)
        with page.client:
            page.cards[0].show()
        names = [
            tr["name"]
            for tr in page.cards[0].plot.figure["data"]
            if tr.get("meta", {}).get("kind") == "act"
        ]
        assert names == ["Actual"]  # one series: the picker's key
        hover = next(
            tr["meta"]["label"]
            for tr in page.cards[0].plot.figure["data"]
            if tr.get("meta", {}).get("kind") == "act"
        )
        assert hover == "a.Q (x)"
        assert [r.name.text for r in page.cards[0].rows.values()] == ["nrmse · a.Q (x)"]


async def test_a_context_variable_named_like_an_expression(plots_shell):
    """Case (c): A's result file holds a variable genuinely NAMED `z`, and
    `x` carries the EXPRESSION `z`. The context one keeps its plain `z` —
    its label IS its name — and the one carrying the expression reads its
    name beside it. Both read `z`, one line apart on the same card."""
    from test_session import with_expressions

    shell = plots_shell
    with_expressions(shell.session, A, {"x": "z"})
    shell.session._score(A)
    shell.session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "z"
        assert _one(user, "plot-name:0").text == "x"
        assert _one(user, "context-name:0:z").text == "z"
        assert [r.name.text for r in page.cards[0].rows.values()] == ["nrmse · z (x)"]
        with page.client:
            page.cards[0].show()
        # Each trace's hover reads its variable's text.
        labels = {
            tr["meta"]["kind"]: tr["meta"]["label"]
            for tr in page.cards[0].plot.figure["data"]
            if tr.get("meta", {}).get("kind") in ("act", "context")
        }
        assert labels == {"act": "z (x)", "context": "z"}


async def test_two_variables_sharing_one_expression_stay_tellable_apart(shell):
    """`x={a.Q, a.Q}` is legal Modelica: one expression, two result-file
    variables. Read as the label alone, both cards were titled "a.Q", both
    criteria rows said "nrmse · a.Q", and the `+`'s form offered two "a.Q".
    Two labels that collide fold into no family: one card each."""
    from test_session import with_expressions

    with_expressions(shell.session, A, {"x": "a.Q", "y": "a.Q"})
    shell.session._score(A)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",), ("y",)]
        # The TITLE reads the plain label: the name rides beside it, muted,
        # so repeating it in parentheses said each variable twice.
        assert [_one(user, f"plot-title:{i}").text for i in (0, 1)] == ["a.Q", "a.Q"]
        assert [_one(user, f"plot-name:{i}").text for i in (0, 1)] == ["x", "y"]
        with page.client:
            page.cards[0].show()
        (label,) = [
            tr["meta"]["label"]
            for tr in page.cards[0].plot.figure["data"]
            if tr.get("meta", {}).get("kind") == "act"
        ]
        assert label == "a.Q (x)"
        # The criteria rows and the `+` too — the disambiguation is scoped
        # to the TEST, so every surface of its page agrees; the `+`'s form
        # offers both, told apart the same way.
        assert [r.name.text for card in page.cards for r in card.rows.values()] == [
            "nrmse · a.Q (x)",
            "nrmse · a.Q (y)",
        ]
        (plus,) = _inside(user, "add-for:x", "criteria-header:0")
        assert _plus_tips(plus) == ["Add a criterion for a.Q (x)"]
        _click_in(user, "add-for:x", within="criteria-header:0")
        await _settled(lambda: not _none(user, "apply-add:x:variable"))
        assert _one(user, "apply-add:x:variable").options == {
            "x": "a.Q (x)",
            "y": "a.Q (y)",
        }


# -- legend b: a swatch per criterion, one error select -----------------------

TUBE = "/metrics/children/0"
RANGE_X = "/metrics/children/1"
RANGE_Y = "/metrics/children/2"


@pytest.fixture
def page_shell(tmp_path):
    """`_page_project`: A is `and[tube · x, range · x, range · y]` — the tube
    and range·y FAIL, range·x passes — drawn as seven plots (0: x, 1: y,
    2..6: WATCH plots of w1..w5, untracked variables of A's result)."""
    from test_session import _page_project

    cfg, work = _page_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


def _drawn_leaves(figure: dict) -> dict[str, bool]:
    """``{leaf path: drawn}`` from what a card last sent: a leaf is drawn
    when none of its traces and none of its shapes is ``visible: false``."""
    out: dict[str, bool] = {}
    for tr in figure["data"]:
        leaf = (tr.get("meta") or {}).get("leaf")
        if leaf is not None:
            out[leaf] = out.get(leaf, True) and tr.get("visible") is not False
    for shape in figure["layout"]["shapes"]:
        for path in (TUBE, RANGE_X, RANGE_Y):
            if shape["name"].startswith(path + "/"):
                out[path] = out.get(path, True) and shape.get("visible") is not False
    return out


def _range_lines_drawn(figure: dict, path: str) -> list[bool]:
    return [
        s.get("visible") is not False
        for s in figure["layout"]["shapes"]
        if s["name"].startswith(path + "/")
    ]


async def test_a_closed_range_starts_hidden_and_a_failing_one_drawn(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            page.cards[0].show()
            page.cards[1].show()
        # range·x passes and range draws nothing by default; the tube is on.
        assert _drawn_leaves(page.cards[0].plot.figure) == {
            TUBE: True,
            RANGE_X: False,
        }
        assert _range_lines_drawn(page.cards[0].plot.figure, RANGE_X) == [
            False,
            False,
        ]
        # range·y FAILS: a failure never starts hidden.
        assert _drawn_leaves(page.cards[1].plot.figure) == {RANGE_Y: True}


async def test_a_swatch_toggles_its_criterions_drawing(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            for card in page.cards[:3]:
                card.show()
        card = page.cards[0]
        sent = _counted(card)
        others = [_counted(c) for c in page.cards[1:3]]
        user.find(marker=f"swatch:0:{RANGE_X}").click()
        await _settled(lambda: _drawn_leaves(card.plot.figure)[RANGE_X])
        assert sent == [1] and page.open is None
        assert card.rows[RANGE_X].swatch_tip.text == "Hide this criterion's drawing"
        user.find(marker=f"swatch:0:{RANGE_X}").click()
        await _settled(lambda: not _drawn_leaves(card.plot.figure)[RANGE_X])
        assert sent == [2]
        assert card.rows[RANGE_X].swatch_tip.text == "Show this criterion's drawing"
        assert [c[0] for c in others] == [0, 0]


async def test_a_failing_criterions_swatch_hides_it(page_shell):
    """The owner's 2026-09-24 ruling: a FAILING criterion is drawn by
    default and its swatch hides it like any other — the failure stays on
    the row's pill and the card's."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[1]
        with page.client:
            card.show()
        assert _drawn_leaves(card.plot.figure) == {RANGE_Y: True}
        swatch = card.rows[RANGE_Y].swatch
        assert swatch.props.get("aria-disabled") != "true"
        assert "dstf-swatch-locked" not in swatch.classes
        assert card.rows[RANGE_Y].swatch_tip.text == "Hide this criterion's drawing"
        sent = _counted(card)
        user.find(marker=f"swatch:1:{RANGE_Y}").click()
        await _settled(lambda: not _drawn_leaves(card.plot.figure)[RANGE_Y])
        assert sent == [1]
        assert _range_lines_drawn(card.plot.figure, RANGE_Y) == [False, False]
        assert _one(user, f"criterion-pill:1:{RANGE_Y}").text == "fail"
        assert _one(user, "plot-pill:1:y").text == "fail"


async def test_the_open_criterions_swatch_cannot_hide_it(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        user.find(marker=f"criterion:0:{RANGE_X}").click()
        await _settled(lambda: page.open == (0, RANGE_X))
        assert _drawn_leaves(card.plot.figure)[RANGE_X] is True
        swatch = card.rows[RANGE_X].swatch
        assert swatch.props.get("aria-disabled") == "true"
        assert "dstf-swatch-locked" in swatch.classes
        assert card.rows[RANGE_X].swatch_tip.text == "the open criterion always draws"
        sent = _counted(card)
        user.find(marker=f"swatch:0:{RANGE_X}").click()  # does nothing
        await asyncio.sleep(0.1)
        assert sent == [0] and RANGE_X not in card.choices
        assert _drawn_leaves(card.plot.figure)[RANGE_X] is True
        user.find(marker=f"editor-done:0:{RANGE_X}").click()
        await _settled(lambda: page.open is None)
        # No choice was made: closed, it is the metric's default again.
        assert _drawn_leaves(card.plot.figure)[RANGE_X] is False
        assert RANGE_X not in card.choices
        assert swatch.props.get("aria-disabled") != "true"


async def test_the_error_select_shows_one_series(page_shell):
    from dstf.figures.plotly import _LABELS

    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            page.cards[0].show()
            page.cards[2].show()
        select = _one(user, "error-select:0")
        assert list(select.options.values()) == [
            "No error",
            _LABELS["error/signed"],
            _LABELS["error/abs"],
            _LABELS["error/nrmse"],
        ]
        assert list(select.options.values())[1:] == [
            "Signed error",
            "Abs error",
            "NRMSE (per-point)",
        ]
        card = page.cards[0]
        sent = _counted(card)
        with page.client:
            select.set_value("error/abs")
        await _settled(lambda: card.error == "error/abs")
        assert sent == [1]
        assert card.plot.figure["layout"]["yaxis3"]["visible"] is True
        assert card.plot.figure["layout"]["yaxis4"]["visible"] is False
        shown = [
            tr["meta"]["id"]
            for tr in card.plot.figure["data"]
            if tr["meta"].get("kind") == "error" and tr["visible"] is True
        ]
        assert shown == ["error/abs"]
        # A plot of several series has no error traces, so no select.
        assert page.cards[2].plot is not None
        assert _none(user, "error-select:2")


async def test_a_swatch_is_there_once_the_plot_is(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        # Nothing is known of what a leaf draws before its figure exists.
        assert card.plot is None
        # Invisible, never display:none: a hidden swatch keeps its box.
        assert all("invisible" in row.swatch.classes for row in card.rows.values())
        assert all(row.swatch.visible for row in card.rows.values())
        with page.client:
            card.show()
        assert {
            p for p, row in card.rows.items() if "invisible" not in row.swatch.classes
        } == {
            TUBE,
            RANGE_X,
        }


async def test_a_criterion_that_draws_only_a_shape_has_a_swatch(page_shell):
    """A windowed nrmse draws ONE shape — its window — and no trace: its
    swatch is found from the shape's name, not from `meta.leaf` alone."""
    shell = page_shell
    windowed = "/metrics/children/3"
    shell.session.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {
                    "metric": "nrmse",
                    "variable": "x",
                    "window": {"start": 0.2, "end": 0.6},
                },
            )
        ]
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        with page.client:
            card.show()
        figure = card.plot.figure
        assert not [
            tr
            for tr in figure["data"]
            if (tr.get("meta") or {}).get("leaf") == windowed
        ]
        assert any(
            s["name"].startswith(windowed + "/") for s in figure["layout"]["shapes"]
        )
        assert windowed in card.drawable
        assert "invisible" not in card.rows[windowed].swatch.classes


def _count_sends(card) -> list[int]:
    """Count every figure a card SENDS, the first included: the plot's
    creation carries one, and each `update_figure` after it another."""
    sends = [0]
    real_show = card.show

    def show(view=plot_card.UNREAD):
        had = card.plot is not None
        real_show(view)
        if not had and card.plot is not None:
            sends[0] += 1
            real_update = card.plot.update_figure

            def update(figure):
                sends[0] += 1
                real_update(figure)

            card.plot.update_figure = update

    card.show = show
    return sends


async def test_the_first_open_of_a_hidden_criterion_sends_one_figure(page_shell):
    """ "Is this criterion open" has ONE source, the page's `editing_for`,
    which is set before the card draws. Read from the card's `open_path` —
    set only after `show()` — the first figure drew range·x hidden and a
    second one, sent straight after, drew it."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        assert card.plot is None
        sends = _count_sends(card)
        user.find(marker=f"criterion:0:{RANGE_X}").click()
        await _settled(lambda: page.open == (0, RANGE_X))
        await asyncio.sleep(0.1)
        assert sends == [1]
        assert _drawn_leaves(card.plot.figure)[RANGE_X] is True
        assert "dstf-swatch-locked" in card.rows[RANGE_X].swatch.classes


async def test_a_paint_that_changes_nothing_repaints_no_swatch(page_shell):
    """A no-op sync re-sent every swatch: removing the state classes and
    adding them back is two changes to the element, each an update."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        with page.client:
            card.show()
        updates: dict[str, int] = {}
        for path, row in card.rows.items():
            real = row.swatch.update

            def counting(_path=path, _real=real):
                updates[_path] = updates.get(_path, 0) + 1
                _real()

            row.swatch.update = counting
        view = shell.session.tree_view(A)
        with page.client:
            card.paint(view)
        assert updates == {}
        # The spy sees a swatch that DOES change.
        user.find(marker=f"swatch:0:{RANGE_X}").click()
        await _settled(lambda: _drawn_leaves(card.plot.figure)[RANGE_X])
        assert updates.get(RANGE_X, 0) > 0


async def test_a_redraw_that_gains_error_traces_gains_the_select(page_shell):
    """The error select is built wherever a figure is sent, not only by
    the first draw: a figure that gains error traces on a redraw gains the
    select with them."""
    shell = page_shell
    session = shell.session
    real = session.plot_figure

    def without_errors(*a, **k):
        figure = real(*a, **k)
        data = [
            tr for tr in figure["data"] if (tr.get("meta") or {}).get("kind") != "error"
        ]
        return {**figure, "data": data}

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        session.plot_figure = without_errors
        with page.client:
            card.show()
        assert card.plot is not None
        assert _none(user, "error-select:0")
        session.plot_figure = real
        with page.client:
            card.redraw()
        assert list(_one(user, "error-select:0").options) == [
            "none",
            "error/signed",
            "error/abs",
            "error/nrmse",
        ]


# -- Panel.row / Panel.reveal: one thing open at a time ----------------------


class _RowProbe:
    """At NODE (and TEST, for the dialog): one button that opens a row
    under its host's row, whose content counts how many times it was built
    and carries a close button; and one that runs `act` — staging ``stage``
    — then reveals ``reveal``, recording anything the reveal raised."""

    name = "row-probe"
    label = "Row probe"
    placements = frozenset({Placement.NODE, Placement.TEST})

    def __init__(self):
        self.built: dict[str, int] = {}
        self.stage: list = []
        self.reveal: str | None = None
        self.reveal_errors: list[Exception] = []
        #: Open the row while the panel is being built, not on a click.
        self.open_at_build = False

    def available(self, s):
        return True

    def caption(self, s):
        return "Row probe"

    def panel(self, s, ui_panel):
        path = s.selection.node_path or f"var:{s.selection.variable}"

        def build(close):
            self.built[path] = self.built.get(path, 0) + 1
            ui.label(f"built {self.built[path]}").mark(f"probe-body:{path}")
            ui.button("close", on_click=close).mark(f"probe-close:{path}")

        def act_and_reveal():
            ui_panel.submit()
            if self.reveal is not None:
                try:
                    ui_panel.reveal(self.reveal)
                except Exception as e:  # recorded: a reveal must raise nothing
                    self.reveal_errors.append(e)

        ui.button("open", on_click=lambda: ui_panel.row(f"probe {path}", build)).mark(
            f"probe-open:{path}"
        )
        ui.button("act", on_click=act_and_reveal).mark(f"probe-act:{path}")
        if self.open_at_build:
            ui_panel.row(f"probe {path}", build)

    def act(self, s, **inputs):
        return Patch(ops=list(self.stage)) if self.stage else None


def _next_sibling(element):
    siblings = element.parent_slot.children
    return siblings[siblings.index(element) + 1]


def _has_rings(card) -> bool:
    return any(s.get("path") == RING_PATH for s in card.plot.figure["layout"]["shapes"])


async def test_a_panel_row_opens_in_a_dialogs_slot(shell):
    """A dialog has no row to open one under: the form is drawn in the
    panel's own slot, inside the dialog, and ``close`` takes it away. A
    dialog is no page, so ``reveal`` does nothing and raises nothing."""
    probe = _RowProbe()
    _only(probe)
    bars, root = _barred(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:row-probe").click()
        await user.should_see(marker="probe-open:var:x")
        user.find(marker="probe-open:var:x").click()
        await user.should_see("probe var:x")
        body = _one(user, "probe-body:var:x")
        assert bars[0].dialog in list(_ancestors(body))
        user.find(marker="probe-close:var:x").click()
        await _settled(lambda: _none(user, "probe-body:var:x"))
        probe.reveal = "/metrics/children/0"
        user.find(marker="probe-act:var:x").click()
        await asyncio.sleep(0.1)
        assert probe.reveal_errors == []
        assert bars[0].errors == {}


async def test_a_row_opens_under_the_tree_row_it_was_asked_from(page_shell):
    """The form opens BELOW the node's row (board 1A), in the slot between
    that row and the next node's, and the row stays lit."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        user.find(marker="tree:toggle").click()
        await user.should_see(marker=f"tree-row:{TUBE}")
        _click_in(user, f"probe-open:{TUBE}", within=f"tree-row:{TUBE}")
        await user.should_see(marker=f"form-row:tree:{TUBE}")
        row = _one(user, f"tree-row:{TUBE}")
        form = _one(user, f"form-row:tree:{TUBE}")
        slot = _next_sibling(row)
        assert form.parent_slot.parent is slot
        assert _next_sibling(slot) is _one(user, f"tree-row:{RANGE_X}")
        assert _one(user, f"form-title:tree:{TUBE}").text == f"probe {TUBE}"
        assert "dstf-row-open" in row.classes


async def test_one_thing_is_open_at_a_time(page_shell):
    """ONE thing open on the page: a row replaces another row, the editor
    replaces a row, and a row replaces the editor — whose rings go with it."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        with page.client:
            card.show()
        user.find(marker="tree:toggle").click()
        await user.should_see(marker=f"tree-row:{TUBE}")
        _click_in(user, f"probe-open:{TUBE}", within=f"tree-row:{TUBE}")
        await user.should_see(marker=f"form-row:tree:{TUBE}")
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        assert _none(user, f"form-row:tree:{TUBE}")
        assert "dstf-row-open" not in _one(user, f"tree-row:{TUBE}").classes
        user.find(marker=f"criterion:0:{TUBE}").click()
        await _settled(lambda: page.open == (0, TUBE))
        assert _none(user, f"form-row:crit:0:{RANGE_X}")
        assert page.form is None
        assert _has_rings(card)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        assert page.open is None
        assert _none(user, f"editor:0:{TUBE}")
        await _settled(lambda: not _has_rings(card))


async def test_a_click_on_the_criteria_row_closes_its_form_and_opens_nothing(
    page_shell,
):
    """Board 1A: on a criteria row, clicking the row's name closes a form
    open under it — and opens no editor in its place."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        user.find(marker=f"criterion:0:{RANGE_X}").click()
        await _settled(lambda: _none(user, f"form-row:crit:0:{RANGE_X}"))
        await asyncio.sleep(0.1)
        assert page.open is None and page.form is None
        # With no form under it, the same click opens the editor as ever.
        user.find(marker=f"criterion:0:{RANGE_X}").click()
        await _settled(lambda: page.open == (0, RANGE_X))


async def test_an_edit_staged_elsewhere_rebuilds_the_open_row(page_shell):
    """The criterion editor's rule, for a node: a form open while an edit is
    staged elsewhere is rebuilt from the tree as it is then — and ONLY
    then; an edit that moves the tree's shape takes the row away with the
    rows it hung under."""
    shell = page_shell
    probe = _RowProbe()
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        user.find(marker="tree:toggle").click()
        await user.should_see(marker="tree-row:/metrics")
        _click_in(user, "probe-open:/metrics", within="tree-row:/metrics")
        await user.should_see(marker="form-row:tree:/metrics")
        assert probe.built["/metrics"] == 1
        with page.client:
            page.sync()  # an unrelated sync rebuilds nothing
        assert probe.built["/metrics"] == 1
        shell.session.apply(
            Patch(
                ops=[
                    PatchOp(
                        A, "replace", "/metrics/children/0/tube_points/0/upper", 0.15
                    )
                ]
            )
        )
        await _settled(lambda: probe.built["/metrics"] == 2)
        assert _one(user, "probe-body:/metrics").text == "built 2"
        assert page.form is not None
        shell.session.apply(
            Patch(
                ops=[
                    PatchOp(
                        A,
                        "add",
                        "/metrics/children/-",
                        {"metric": "nrmse", "variable": "x"},
                    )
                ]
            )
        )
        await _settled(lambda: page.form is None)
        assert _none(user, "form-row:tree:/metrics")


async def test_reveal_opens_the_criterion_after_the_page_redraws(page_shell):
    """After an add, the new criterion's editor opens under its plot — at
    the end of the sync that drew its row: before it, there is no row."""
    shell = page_shell
    probe = _RowProbe()
    probe.stage = [
        PatchOp(A, "add", "/metrics/children/-", {"metric": "tube", "variable": "x"})
    ]
    probe.reveal = "/metrics/children/3"
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-act:{TUBE}", within=f"criterion:0:{TUBE}")
        await _settled(lambda: page.open == (0, "/metrics/children/3"))
        await user.should_see(marker="editor:0:/metrics/children/3")
        assert probe.reveal_errors == []


async def test_a_refused_add_reveals_nothing(page_shell):
    """Apply is synchronous: a refused add leaves no node, and its reveal
    is not kept for later — held, it opened the NEXT add's criterion by
    itself, one nobody asked to see."""
    shell = page_shell
    probe = _RowProbe()
    # A leaf holds no children: the validator refuses the add by its path.
    probe.stage = [
        PatchOp(
            A,
            "add",
            "/metrics/children/0/children/-",
            {"metric": "tube", "variable": "x"},
        )
    ]
    probe.reveal = "/metrics/children/3"
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-act:{TUBE}", within=f"criterion:0:{TUBE}")
        await _settled(
            lambda: page.cards[0].rows[TUBE].host.errors["row-probe"].visible
        )
        assert page.pending_reveal is None
        shell.session.apply(
            Patch(
                ops=[
                    PatchOp(
                        A,
                        "add",
                        "/metrics/children/-",
                        {"metric": "tube", "variable": "x"},
                    )
                ]
            )
        )
        await _settled(lambda: "/metrics/children/3" in page.cards[0].rows)
        await asyncio.sleep(0.2)
        assert page.open is None


# -- a form row lives only as long as its host (Task 5's review) ------------


class _HeldRowProbe(_RowProbe):
    """`_RowProbe` whose `available` follows the simulation queue, the way
    Accept's does: while the queue holds the test its host is rebuilt to
    the sentence."""

    def available(self, s):
        model = next(iter(s.selection.tests))
        if s.queue_state(model) in (ItemState.WAITING, ItemState.RUNNING):
            return f"{model} is in the queue"
        return True


async def test_a_form_row_goes_when_its_host_is_rebuilt_to_a_why_line(page_shell):
    """A host rebuilt to its why-line — its capability no longer available —
    takes the row its panel opened with it: left open with live buttons, an
    act from the row stages nothing and shows nothing, its error label
    having gone with the host's rebuild."""
    shell = page_shell
    _only(_HeldRowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        _hold(shell, A)
        (why,) = _inside(user, "why:row-probe", f"node:0:{RANGE_X}")
        assert why.text == f"{A} is in the queue"
        assert page.form is None
        assert _none(user, f"form-row:crit:0:{RANGE_X}")
        row = page.cards[0].rows[RANGE_X].row
        assert "dstf-row-open" not in row.classes


async def test_hide_tree_closes_a_tree_rows_form(page_shell):
    """A form nobody can see is no form: hiding the tree takes a form open
    under one of its rows away, as it would the criterion editor."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        user.find(marker="tree:toggle").click()
        await user.should_see(marker=f"tree-row:{TUBE}")
        _click_in(user, f"probe-open:{TUBE}", within=f"tree-row:{TUBE}")
        await user.should_see(marker=f"form-row:tree:{TUBE}")
        user.find(marker="tree:toggle").click()
        await _settled(lambda: not page.tree.rows.visible)
        assert page.form is None
        assert _none(user, f"form-row:tree:{TUBE}")


async def test_focus_on_another_plot_closes_a_form_on_the_card_it_hides(page_shell):
    """Focus shows ONE plot: a form under a criteria row of a card it hides
    goes, as the editor does; a form on the focused card stays."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert page.form is not None  # its card is the one shown
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused is None)
        user.find(marker="focus:1").click()
        await _settled(lambda: page.focused == 1)
        assert page.form is None
        assert _none(user, f"form-row:crit:0:{RANGE_X}")


async def test_a_form_whose_node_is_gone_closes_and_is_not_rebuilt(page_shell):
    """`_follow_row` over a node the tree no longer has: it closes the form
    rather than call ``build`` on a path that names nothing (the probe
    built twice for one). Driven directly: through a sync, the rows the
    form hangs under are rebuilt first and their host takes it away."""
    shell = page_shell
    probe = _RowProbe()
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_Y}", within=f"criterion:1:{RANGE_Y}")
        await user.should_see(marker=f"form-row:crit:1:{RANGE_Y}")
        assert probe.built[RANGE_Y] == 1
        with page.client:
            shell.session.stage([PatchOp(A, "remove", RANGE_Y)])
            page._follow_row()
        assert page.form is None
        assert probe.built[RANGE_Y] == 1
        assert _none(user, f"form-row:crit:1:{RANGE_Y}")


async def test_one_sync_after_a_rebuild_reveals_and_drops_the_form(page_shell):
    """The sync that rebuilds is the one that follows: its reveal opens the
    new criterion and the form the rebuild orphaned is gone, with no
    second sync to cover for it. Every call below runs before the app's
    timer can hand the Session's queued events to the page."""
    shell = page_shell
    _only(_RowProbe())
    new = "/metrics/children/3"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        with page.client:
            shell.session.apply(
                Patch(
                    ops=[
                        PatchOp(
                            A,
                            "add",
                            "/metrics/children/-",
                            {"metric": "tube", "variable": "x"},
                        )
                    ]
                )
            )
            page.reveal(new, index=None)
            page.sync()
            assert page.open == (0, new)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        with page.client:
            shell.session.apply(Patch(ops=[PatchOp(A, "remove", new)]))
            page.sync()
            assert page.form is None


async def test_a_dialogs_form_row_opens_while_its_panel_is_built(shell):
    """A panel may open its row while it is being built: the dialog's form
    column exists before the panel runs (it raised NameError). A second
    open shows ONE body, and a close takes the title with it."""
    probe = _RowProbe()
    probe.open_at_build = True
    _only(probe)
    bars, root = _barred(shell)
    async with user_simulation(root=root) as user:
        await user.open("/")
        user.find(marker="action:row-probe").click()
        await user.should_see(marker="probe-body:var:x")
        assert not [
            e
            for e in _one(user, "panel:row-probe").descendants()
            if isinstance(e, ui.label) and "Error" in e.text
        ]
        user.find(marker="probe-open:var:x").click()
        await _settled(lambda: probe.built["var:x"] == 2)
        assert len(user.find(marker="probe-body:var:x").elements) == 1
        assert _one(user, "probe-body:var:x").text == "built 2"
        user.find(marker="probe-close:var:x").click()
        await _settled(lambda: _none(user, "probe-body:var:x"))
        await asyncio.sleep(0.1)
        dialog = bars[0].dialog
        assert not [
            e
            for e in dialog.descendants()
            if isinstance(e, ui.label) and e.text == "probe var:x"
        ]


# -- Edit tree's forms as rows on the page (board 1A-1D) --------------------


async def test_a_wrap_form_opens_under_the_criteria_row_and_applies(page_shell):
    """Board 1D: Wrap on a criteria row opens its form in that row's slot,
    titled for the node; `warn` and Wrap stage the wrap, and the row goes."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"wrap:{TUBE}", within=f"node:0:{TUBE}")
        await user.should_see(marker=f"form-row:crit:0:{TUBE}")
        form = _one(user, f"form-row:crit:0:{TUBE}")
        assert form.parent_slot.parent is page.cards[0].rows[TUBE].slot
        assert _one(user, f"form-title:crit:0:{TUBE}").text == (
            "Wrap tube · x in a combinator"
        )
        (kind,) = [
            e
            for e in user.find(kind=ui.select).elements
            if e.props.get("label") == "Combinator"
        ]
        with page.client:
            kind.set_value("warn")
        await user.should_see(
            "A failure of this criterion becomes a warning; the test still passes."
        )
        user.find(marker=f"apply-wrap:{TUBE}").click()
        await _settled(
            lambda: any(
                op.path == TUBE and (op.value or {}).get("combinator") == "warn"
                for op in shell.session.spec.pending
            )
        )
        await _settled(lambda: _none(user, f"form-row:crit:0:{TUBE}"))
        assert page.form is None


async def test_a_discarded_edit_reopens_on_the_nodes_value(tmp_path):
    """The 3b stale-form probe, for the row: a k-of-n root edited to k=2 and
    then Discarded opens Edit on the k it has — and a form left OPEN over
    the Discard is rebuilt on it, so an Apply from it stages nothing stale."""
    from test_session import _page_project

    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = {**entry["metrics"], "combinator": "k-of-n", "k": 1}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    within = "tree-row:/metrics"

    def k_field():
        (k,) = [
            e
            for e in user.find(kind=ui.number).elements
            if e.props.get("label") == "k — how many must pass"
        ]
        return k

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        user.find(marker="tree:toggle").click()
        await user.should_see(marker=within)
        _tree_click(user, "/metrics")  # its row opens its fields
        await user.should_see(marker="form-row:tree:/metrics")
        assert k_field().value == 1
        with page.client:
            k_field().set_value(2)
        user.find(marker="apply-edit:/metrics").click()
        await _settled(lambda: shell.session.tree(A).get("k") == 2)
        # The header enables Discard when the Session's event reaches it.
        await _settled(lambda: _one(user, "discard").enabled)
        user.find(marker="discard").click()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: page.tree.targets["/metrics"].click is not None)
        _tree_click(user, "/metrics")  # its row opens its fields
        await user.should_see(marker="form-row:tree:/metrics")
        assert k_field().value == 1  # the node's, not the discarded 2
        # Now with the form OPEN across the edit and its Discard.
        with page.client:
            k_field().set_value(2)
        user.find(marker="apply-edit:/metrics").click()
        await _settled(lambda: shell.session.tree(A).get("k") == 2)
        _tree_click(user, "/metrics")  # its row opens its fields
        await _settled(lambda: not _none(user, "form-row:tree:/metrics"))
        assert k_field().value == 2
        # The header enables Discard when the Session's event reaches it.
        await _settled(lambda: _one(user, "discard").enabled)
        user.find(marker="discard").click()
        await _settled(lambda: k_field().value == 1)
        user.find(marker="apply-edit:/metrics").click()
        # An Apply stages only what moved (the Task 6 review): the rebuilt
        # form shows the node's own k, so nothing is staged — the stale 2
        # would have been.
        await _settled(lambda: page.form is None)
        await asyncio.sleep(0.1)
        assert shell.session.spec.pending == []
        assert shell.session.tree(A)["k"] == 1  # not the stale 2


# -- the Task 6 review's corrections ----------------------------------------

WEIGHTED_TIP = "a weighted node has one weight per child; a child cannot be added alone"


def _plus(user, path: str, within: str):
    (plus,) = _inside(user, f"add:{path}", within)
    return plus


def _plus_tips(plus) -> list[str]:
    return [e.text for e in plus.descendants() if isinstance(e, ui.tooltip)]


async def test_the_grey_plus_follows_the_parents_kind(shell):
    """`+` was decided when the cluster was built: a node edited from
    weighted to and kept a grey `+` with the weighted reason, and the
    reverse kept an enabled one whose Add was refused. The tree's rows
    rebuild when a node's kind moves, and a criteria row's controls
    rebuild when its PARENT's kind moves. Two ranges: a weighted node
    refuses to sum a tube's higher-is-better score with a range's."""
    _stage_tree(shell, A, {"combinator": "and", "children": [_range(-1), _range(5)]})
    to_weighted = [
        PatchOp(A, "replace", "/metrics/combinator", "weighted"),
        PatchOp(A, "add", "/metrics/weights", [1.0, 1.0]),
        PatchOp(A, "add", "/metrics/threshold", 0.5),
    ]
    to_and = [
        PatchOp(A, "replace", "/metrics/combinator", "and"),
        PatchOp(A, "remove", "/metrics/weights"),
        PatchOp(A, "remove", "/metrics/threshold"),
    ]
    places = [
        ("/metrics", "node:/metrics"),
        (TUBE, f"node:{TUBE}"),
        (TUBE, f"node:0:{TUBE}"),
    ]
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        user.find(marker="tree:toggle").click()
        await user.should_see(marker=f"tree-row:{TUBE}")
        for path, within in places:
            assert _plus(user, path, within).enabled, within
        shell.session.apply(Patch(ops=to_weighted))
        for path, within in places:
            await _settled(lambda p=path, w=within: not _plus(user, p, w).enabled)
            assert _plus_tips(_plus(user, path, within)) == [WEIGHTED_TIP], within
        shell.session.apply(Patch(ops=to_and))
        for path, within in places:
            await _settled(lambda p=path, w=within: _plus(user, p, w).enabled)


async def test_a_rows_error_sits_on_its_own_line_under_the_row(page_shell):
    """In the row's own slot, not beside the icons: drawn in the host, a
    long refusal widened the host and pushed its icons left of every other
    row's."""
    shell = page_shell
    probe = _RowProbe()
    probe.stage = [
        PatchOp(
            A,
            "add",
            "/metrics/children/0/children/-",
            {"metric": "tube", "variable": "x"},
        )
    ]
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        row = page.cards[0].rows[TUBE]
        _click_in(user, f"probe-act:{TUBE}", within=f"criterion:0:{TUBE}")
        await _settled(lambda: row.host.errors["row-probe"].visible)
        above = list(_ancestors(row.host.errors["row-probe"]))
        assert row.slot in above and row.row not in above
        # A rebuilt host takes its error with it.
        with page.client:
            row.host.build()
        assert not [
            e for e in row.slot.descendants() if isinstance(e, ui.label) and e.visible
        ]


# -- the review of item 17 and Task 7 ------------------------------------------


def _field(user, kind, label: str):
    (field,) = [
        e for e in user.find(kind=kind).elements if e.props.get("label") == label
    ]
    return field


async def _refused_weighted_wrap(user, page, path: str) -> None:
    """Open ``path``'s Wrap on plot 0's criteria row, choose weighted and
    type two weights for its one child: the validator refuses it by name."""
    _click_in(user, f"wrap:{path}", within=f"node:0:{path}")
    await user.should_see(marker=f"form-row:crit:0:{path}")
    with page.client:
        _field(user, ui.select, "Combinator").set_value("weighted")
    await _settled(lambda: not _none(user, f"apply-wrap:{path}"))
    with page.client:
        _field(user, ui.input, "Weights, one per child").set_value("1, 2")
        _field(user, ui.number, "Threshold").set_value(0.5)
    await _settled(lambda: _one(user, f"apply-wrap:{path}").enabled)
    user.find(marker=f"apply-wrap:{path}").click()


async def test_closing_an_editor_keeps_its_rows_errors(page_shell):
    """`close_editor` cleared the row's whole slot, and the row's host keeps
    its error labels there: after an editor opened and closed, a refused
    Wrap on that row staged nothing and SHOWED nothing (the review, in
    process and in Chromium). The editor keeps a column of its own, and
    closing it deletes only that."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        row = page.cards[0].rows[TUBE]
        user.find(marker=f"criterion:0:{TUBE}").click()
        await _settled(lambda: page.open == (0, TUBE))
        user.find(marker=f"editor-done:0:{TUBE}").click()
        await _settled(lambda: page.open is None)
        await _refused_weighted_wrap(user, page, TUBE)
        await _settled(lambda: bool(_errors_under(user, row)))
        assert _errors_under(user, row)[0] == (
            "2 weights for 1 criterion: a weighted node takes one weight per criterion."
        )
        assert shell.session.spec.pending == []


def _errors_under(user, row) -> list[str]:
    """The texts of the edit-tree errors SHOWN in ``row``'s slot, found on
    the page: a label deleted from it is not found, whatever it says."""
    if _none(user, "error:edit-tree"):
        return []
    return [
        e.text
        for e in user.find(marker="error:edit-tree").elements
        if e.visible and row.slot in list(_ancestors(e))
    ]


async def test_a_refused_wrap_keeps_its_row_and_what_was_typed(page_shell):
    """A form closes on SUCCESS: it closed before it submitted, so a refused
    Wrap took the typed weights with it and the refusal showed under a row
    with no form. Now the row stays open with what was typed, and the
    refusal shows under the node's row, above the form, as the criterion
    editor does; typed right, the Wrap stages and the row goes."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        row = page.cards[0].rows[TUBE]
        await _refused_weighted_wrap(user, page, TUBE)
        await _settled(lambda: bool(_errors_under(user, row)))
        assert page.form is not None and page.form[0].key == f"crit:0:{TUBE}"
        assert _field(user, ui.input, "Weights, one per child").value == "1, 2"
        assert shell.session.spec.pending == []
        with page.client:
            _field(user, ui.input, "Weights, one per child").set_value("1")
        user.find(marker=f"apply-wrap:{TUBE}").click()
        await _settled(lambda: page.form is None)
        assert any(
            op.path == TUBE and (op.value or {}).get("combinator") == "weighted"
            for op in shell.session.spec.pending
        )
        assert not _errors_under(user, page.cards[0].rows[TUBE])


async def test_an_error_belongs_to_its_row(page_shell):
    """A row's error is hidden when THAT row's form opens or closes, or its
    host rebuilds — not when another row's form opens and is cancelled. A
    page-wide hide took an error off the row it was about for a Cancel on
    another plot's row."""
    shell = page_shell
    probe = _RowProbe()
    # A leaf holds no children: the validator refuses the add by its path.
    probe.stage = [
        PatchOp(
            A,
            "add",
            "/metrics/children/0/children/-",
            {"metric": "tube", "variable": "x"},
        )
    ]
    _only(probe)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        error = page.cards[0].rows[TUBE].host.errors["row-probe"]

        async def refused() -> None:
            _click_in(user, f"probe-act:{TUBE}", within=f"criterion:0:{TUBE}")
            await _settled(lambda: error.visible)

        await refused()
        # Another row's form opens and is cancelled: the error stays.
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        assert error.visible
        user.find(marker=f"probe-close:{RANGE_X}").click()
        await _settled(lambda: page.form is None)
        assert error.visible
        # Its own row's form opening takes it.
        _click_in(user, f"probe-open:{TUBE}", within=f"criterion:0:{TUBE}")
        await user.should_see(marker=f"form-row:crit:0:{TUBE}")
        assert not error.visible
        # Refused while its own form is open, then that form closes: gone.
        await refused()
        assert page.form is not None
        user.find(marker=f"probe-close:{TUBE}").click()
        await _settled(lambda: page.form is None)
        assert not error.visible


# -- 3c: the rail and the Tests drawer -----------------------------------------


class TestRail:
    """The rail's two buttons open one drawer at a time; the Tests drawer is
    the Suite page's list without checkboxes (the owner's ruling,
    2026-09-25). `user.find` sees visible elements only, so a hidden panel
    is one `_none` finds nothing for."""

    async def test_the_rail_opens_one_drawer_at_a_time(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _test_page(shell, user)
            assert not _none(user, "rail:tests")
            assert not _none(user, "rail:variables")
            assert _none(user, "drawer:tests") and _none(user, "drawer:variables")
            user.find(marker="rail:tests").click()
            assert not _none(user, "drawer:tests")
            assert _none(user, "drawer:variables")
            user.find(marker="rail:variables").click()
            assert not _none(user, "drawer:variables")
            assert _none(user, "drawer:tests")
            user.find(marker="rail:variables").click()
            assert _none(user, "drawer:tests") and _none(user, "drawer:variables")

    async def test_the_tests_drawer_lists_the_suite_and_marks_this_test(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _test_page(shell, user)
            user.find(marker="rail:tests").click()
            await user.should_see(marker="tests-drawer:group:FakeLib")
            for model_id in shell.visible_tests():
                assert not _none(user, "tests-drawer:" + model_id), model_id
            assert _none(user, "tests-drawer:check:" + A)
            container = _one(user, "tests-drawer:" + A).parent_slot.parent
            assert "dstf-row-current" in container.classes
            other = _one(user, "tests-drawer:" + D).parent_slot.parent
            assert "dstf-row-current" not in other.classes
            user.find(marker="tests-drawer:needle").type("D")
            await _settled(lambda: _none(user, "tests-drawer:" + A))
            shown = [
                m for m in shell.visible_tests() if not _none(user, "tests-drawer:" + m)
            ]
            assert shown == [D]

    async def test_a_click_in_the_tests_drawer_opens_that_test_and_the_drawer_stays(
        self, shell
    ):
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            heard: list[str] = []
            real = page.handle
            page.handle = lambda e: (heard.append(e["event"]), real(e))
            shell.session.stage([LOOSEN])
            staged = list(shell.session.spec.pending)
            assert staged[-1] == LOOSEN
            # The stage's events delivered HERE, to A's page: one arriving
            # at D's page after it is built would apply the filter for it,
            # and the test would pass by the tick's timing.
            await _settled(lambda: "results" in heard)
            user.find(marker="rail:tests").click()
            user.find(marker="tests-drawer:needle").type("D")
            await _settled(lambda: _none(user, "tests-drawer:" + A))
            user.find(marker="tests-drawer:" + D).click()
            await user.should_see(D, marker="model", retries=20)
            await _settled(lambda: _page(shell, user).model_id == D)
            assert not _none(user, "drawer:tests")
            assert shell.session.spec.pending == staged
            # The remembered filter is APPLIED on the new page, not only
            # written into its box: the box read "D" over all five rows.
            assert _one(user, "tests-drawer:needle").value == "D"
            assert _none(user, "tests-drawer:" + A)
            assert not _none(user, "tests-drawer:" + D)

    async def test_a_rescore_repaints_the_drawers_row_in_place(self, shell):
        """A `results` event reaches the Tests drawer through the rail and
        repaints the model's pill and tint on the rows already there. A
        comparison tolerance staged on D (nrmse 6e-4) fails it: the stage
        emits `spec`, which repaints D's unsaved edits, then `results` for
        D."""
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            user.find(marker="rail:tests").click()
            drawer = page.rail.tests
            assert drawer is not None
            rows = dict(drawer.list.rows)
            assert rows[D].verdict == "PASS"
            rebuilds = []
            real = drawer.list.rebuild
            drawer.list.rebuild = lambda *a, **kw: (rebuilds.append(1), real(*a, **kw))
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            await _settled(lambda: drawer.list.rows[D].verdict == "FAIL")
            row = drawer.list.rows[D]
            assert row.badge.text == "FAIL"
            assert "dstf-tint-fail" in row.container.classes
            assert "dstf-tint-pass" not in row.container.classes
            assert all(drawer.list.rows[m] is r for m, r in rows.items())
            assert rebuilds == []

    async def test_a_row_not_scored_yet_wears_scoring_not_queued(self, shell):
        """A test with no verdict yet reads `…` under the SCORING class —
        its fill and its tint. `queued` is the simulation queue's (D150):
        a row wearing it says the test waits for a worker."""
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            user.find(marker="rail:tests").click()
            drawer = page.rail.tests
            assert drawer is not None
            assert drawer.list.rows[D].verdict == "PASS"
            del shell.session.comparisons[D]
            shell.session._emit("results", models={D})
            await _settled(lambda: drawer.list.rows[D].verdict == "")
            row = drawer.list.rows[D]
            assert row.badge.text == "…"
            assert row.badge._style["background-color"] == "var(--dstf-scoring-fill)"
            assert "dstf-tint-scoring" in row.container.classes
            assert "dstf-tint-queued" not in row.container.classes
            assert "dstf-tint-pass" not in row.container.classes


def _shown(shell, user) -> list[str]:
    """The Tests drawer's visible rows, in drawing order."""
    drawer = _page(shell, user).rail.tests
    assert drawer is not None
    return [m for m in drawer.list.rows if not _none(user, "tests-drawer:" + m)]


async def _tests_drawer(shell, user, model=A) -> TestPage:
    page = await _test_page(shell, user, model)
    user.find(marker="rail:tests").click()
    await user.should_see(marker="tests-drawer:sort")
    return page


def _bdi_text(element) -> str | None:
    """The text of the first `<bdi>` under ``element``, else None."""
    for slot in element.slots.values():
        for child in slot.children:
            if child.tag == "bdi":
                return child.text
            if (found := _bdi_text(child)) is not None:
                return found
    return None


async def _pick(user, menu: str, value: str) -> None:
    """Open ``menu``'s menu and choose ``value``."""
    user.find(marker=menu).click()
    await user.should_see(marker=f"{menu}:{value}")
    user.find(marker=f"{menu}:{value}").click()


class TestTestsDrawerControls:
    """The Tests drawer's control set (the owner's ruling Q7): the filter
    with `.*` / `Aa`, sort, group, names and status pills with EDITED, each
    the tab's. The fixture's verdicts: A FAIL, B NO_REF, C NOT RUN (its run
    never finished it, D145), D PASS, G PASS."""

    async def test_sort_by_name_and_by_status_failures_first(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            assert _shown(shell, user) == [A, B, C, D, G]
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, B, D, G, C])
            assert page.state.tests_sort == "status"
            item = _one(user, "tests-drawer:sort:status")
            assert item.props["role"] == "menuitemradio"
            assert item.props["aria-checked"] == "true"
            assert _one(user, "tests-drawer:sort:package").props["aria-checked"] == (
                "false"
            )
            await _pick(user, "tests-drawer:sort", "name")
            await _settled(lambda: page.state.tests_sort == "name")
            assert _shown(shell, user) == [A, B, C, D, G]

    async def test_scoring_failed_sorts_after_sim_fail_and_unscored_last(self, shell):
        """FAIL, SIM_FAIL, scoring failed, NO_REF, PASS, NOT RUN, then a test
        with no verdict yet — not `STATUS_ORDER`, which lists PASS first."""
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            shell.load.failed_models.append(G)
            del shell.session.comparisons[G]
            shell.session.comparisons.pop(D)
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, G, B, C, D])

    async def test_the_status_sort_follows_a_rescore(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, B, D, G, C])
            # D's nrmse 6e-4 fails at 1e-5: D moves up among the failures
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            drawer = page.rail.tests
            await _settled(lambda: drawer.list.rows[D].verdict == "FAIL")
            await _settled(lambda: _shown(shell, user) == [A, D, B, G, C])

    async def test_flat_and_full_names(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            group = _one(user, "tests-drawer:group")
            assert group.props["aria-pressed"] == "true"
            assert _plus_tips(group) == ["Show flat"]
            assert not _none(user, "tests-drawer:group:FakeLib")
            assert _one(user, "tests-drawer:" + A).text == "A"
            user.find(marker="tests-drawer:group").click()
            await _settled(lambda: _none(user, "tests-drawer:group:FakeLib"))
            assert page.state.tests_grouped is False
            assert group.props["aria-pressed"] == "false"
            assert _plus_tips(group) == ["Group by package"]
            await _pick(user, "tests-drawer:names", "full")
            # A Full name is a <bdi> in the button, not its label: it is cut
            # at its start (`test_list._full_name`).
            await _settled(lambda: _bdi_text(_one(user, "tests-drawer:" + A)) == A)
            assert _one(user, "tests-drawer:" + A).text == ""
            assert page.state.tests_short is False

    async def test_the_pills_count_every_test_whatever_the_filter(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            assert _one(user, "tests-drawer:pill:PASS").text == "PASS 2"
            user.find(marker="tests-drawer:needle").type(".A")
            await _settled(lambda: _shown(shell, user) == [A])
            assert _one(user, "tests-drawer:pill:PASS").text == "PASS 2"
            assert _one(user, "tests-drawer:pill:FAIL").text == "FAIL 1"
            assert _one(user, "tests-drawer:pill:SIM_FAIL").text == "SIM_FAIL 0"
            assert _one(user, "tests-drawer:pill:NO_REF").text == "NO_REF 1"
            not_run = _one(user, "tests-drawer:pill:NOT_RUN")  # D145
            assert not_run.text == "NOT RUN 1"
            assert "dstf-pill-not-run" in not_run.classes
            edited = _one(user, "tests-drawer:pill:EDITED")
            assert edited.text == "EDITED 0" and not edited.enabled
            assert "dstf-pill-pass" in _one(user, "tests-drawer:pill:PASS").classes
            # a second pass over rows the first one hid: still every test
            user.find(marker="tests-drawer:case").click()
            await _settled(lambda: _shown(shell, user) == [A])
            assert _one(user, "tests-drawer:pill:PASS").text == "PASS 2"

    async def test_edited_ors_with_the_statuses_and_rows_say_unsaved(self, shell):
        from dstf.reporting.rows import unsaved_text

        assert (unsaved_text(0), unsaved_text(2)) == ("", "2 unsaved")
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
            await _settled(
                lambda: _one(user, "tests-drawer:pill:EDITED").text == "EDITED 1"
            )
            assert _one(user, "tests-drawer:edits:" + D).text == "1 unsaved"
            assert _none(user, "tests-drawer:edits:" + A)
            user.find(marker="tests-drawer:pill:EDITED").click()
            await _settled(lambda: _shown(shell, user) == [D])
            assert page.state.tests_pills == {"EDITED"}
            user.find(marker="tests-drawer:pill:FAIL").click()
            await _settled(lambda: _shown(shell, user) == [A, D])
            assert _one(user, "tests-drawer:pill:FAIL").props["aria-pressed"] == "true"

    async def test_a_regex_and_a_bad_one(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:regex").click()
            user.find(marker="tests-drawer:needle").type(r"^FakeLib\.A$")
            await _settled(lambda: _shown(shell, user) == [A])
            user.find(marker="tests-drawer:needle").clear()
            user.find(marker="tests-drawer:needle").type("(")
            await _settled(lambda: not _none(user, "tests-drawer:filter:why"))
            assert _one(user, "tests-drawer:filter:why").text.startswith(
                "not a regular expression: "
            )
            assert _shown(shell, user) == [A, B, C, D, G]

    async def test_a_regex_that_takes_too_long_says_so_and_hides_nothing(
        self, shell, monkeypatch
    ):
        from dstf.authoring import needle as needle_module

        monkeypatch.setattr(needle_module, "REGEX_BUDGET", 0.0)
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:regex").click()
            user.find(marker="tests-drawer:needle").type("A")
            await _settled(lambda: not _none(user, "tests-drawer:filter:why"))
            assert (
                _one(user, "tests-drawer:filter:why").text
                == "the pattern took too long; simplify it"
            )
            assert _shown(shell, user) == [A, B, C, D, G]

    async def test_an_event_reapplies_the_filter_without_searching_again(
        self, shell, monkeypatch
    ):
        """The kept set is cached on the needle, its toggles and the ids: a
        pathological pattern cost 0.79 s of CPU per pass over 331 ids, and
        the queue's events repaint the rows as its tests end."""
        from dstf.authoring.shell import rail as rail_module

        asked: list[str] = []
        real = rail_module.matcher

        def counting(*args, **kwargs):
            keep = real(*args, **kwargs)
            return lambda text: (asked.append(text), keep(text))[1]

        monkeypatch.setattr(rail_module, "matcher", counting)
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:needle").type("D")
            await _settled(lambda: _shown(shell, user) == [D])
            asked.clear()
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            await _settled(
                lambda: _page(shell, user).rail.tests.list.rows[D].verdict == "FAIL"
            )
            assert _shown(shell, user) == [D]
            assert asked == []

    async def test_every_setting_survives_a_switch_of_test(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            await _pick(user, "tests-drawer:sort", "name")
            user.find(marker="tests-drawer:group").click()
            await _pick(user, "tests-drawer:names", "full")
            user.find(marker="tests-drawer:regex").click()
            user.find(marker="tests-drawer:case").click()
            user.find(marker="tests-drawer:pill:PASS").click()
            await _settled(lambda: page.state.tests_pills == {"PASS"})
            user.find(marker="tests-drawer:" + D).click()
            await _settled(lambda: _page(shell, user).model_id == D)
            other = _page(shell, user)
            assert other is not page and other.state is page.state
            assert _one(user, "tests-drawer:sort:name").props["aria-checked"] == "true"
            assert _one(user, "tests-drawer:names:full").props["aria-checked"] == "true"
            assert _one(user, "tests-drawer:group").props["aria-pressed"] == "false"
            assert _one(user, "tests-drawer:regex").props["aria-pressed"] == "true"
            assert _one(user, "tests-drawer:case").props["aria-pressed"] == "true"
            assert _one(user, "tests-drawer:pill:PASS").props["aria-pressed"] == "true"
            assert _shown(shell, user) == [D, G]
            assert _none(user, "tests-drawer:group:FakeLib")

    async def test_match_case_pressed_after_typing_filters_again(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:needle").type(".a")
            await _settled(lambda: _shown(shell, user) == [A])
            user.find(marker="tests-drawer:case").click()
            await _settled(lambda: _shown(shell, user) == [])

    async def test_regex_pressed_after_typing_filters_again(self, shell):
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:needle").type(r"^FakeLib\.A$")
            await _settled(lambda: _shown(shell, user) == [])
            user.find(marker="tests-drawer:regex").click()
            await _settled(lambda: _shown(shell, user) == [A])

    async def test_a_test_the_spec_adds_is_filtered_afresh(self, shell, monkeypatch):
        """The kept set is cached on the test set too: a test the spec adds
        is asked of the filter, never hidden as one it did not keep."""
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            real = shell.visible_tests
            monkeypatch.setattr(shell, "visible_tests", lambda: [*real(), "FakeLib.Z"])
            page.rail.tests.handle({"event": "spec", "models": set()})
            await _settled(lambda: "FakeLib.Z" in _shown(shell, user))

    async def test_a_pressed_pill_at_zero_stays_enabled_to_be_switched_off(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:pill:FAIL").click()
            await _settled(lambda: _shown(shell, user) == [A])
            shell.session.stage([PatchOp(A, "add", "/comparison", {"tolerance": 1.0})])
            await _settled(lambda: page.rail.tests.list.rows[A].verdict == "PASS")
            pill = _one(user, "tests-drawer:pill:FAIL")
            assert pill.text == "FAIL 0" and pill.enabled
            assert pill.props["aria-pressed"] == "true" and _shown(shell, user) == []
            user.find(marker="tests-drawer:pill:FAIL").click()
            await _settled(lambda: len(_shown(shell, user)) == 5)
            assert not _one(user, "tests-drawer:pill:FAIL").enabled

    async def test_the_name_sort_reorders_a_flat_list(self, two_package_shell):
        """Flat, package order puts Cases.X between C and D; by name, its
        full id's last part sorts it after every other test."""
        shell = two_package_shell
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            user.find(marker="tests-drawer:group").click()
            await _settled(lambda: _shown(shell, user)[3] == CASES_X)
            await _pick(user, "tests-drawer:sort", "name")
            await _settled(lambda: _shown(shell, user)[-1] == CASES_X)

    async def test_scoring_failed_counts_and_filters_as_sim_fail(self, shell):
        """The Suite's rule (`suite_rows`): a test the open could not score
        is SIM_FAIL to the pills — counted there and kept by that pill — and
        keeps its own place in the status sort, after SIM_FAIL: B, the one
        here, comes before C in package order."""
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            # C ran and failed here (the fixture's C never finished: NOT
            # RUN, D145), so there is a SIM_FAIL to sort after.
            comp = shell.session.comparisons[C]
            shell.session.comparisons[C] = dataclasses.replace(comp, ran=True)
            shell.load.failed_models.append(B)
            del shell.session.comparisons[B]
            page.rail.tests._rebuild()
            await _settled(
                lambda: page.rail.tests.list.rows[B].verdict == "scoring failed"
            )
            assert _one(user, "tests-drawer:pill:SIM_FAIL").text == "SIM_FAIL 2"
            assert _one(user, "tests-drawer:pill:NO_REF").text == "NO_REF 0"
            user.find(marker="tests-drawer:pill:SIM_FAIL").click()
            await _settled(lambda: _shown(shell, user) == [B, C])
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [C, B])

    async def test_a_status_sort_reorder_does_not_search_again(
        self, shell, monkeypatch
    ):
        """The kept set is keyed on the SET of tests, not the drawn order:
        a rescore that moves a test under the status sort rebuilds the rows
        and asks the filter nothing."""
        from dstf.authoring.shell import rail as rail_module

        asked: list[str] = []
        real = rail_module.matcher

        def counting(*args, **kwargs):
            keep = real(*args, **kwargs)
            return lambda text: (asked.append(text), keep(text))[1]

        monkeypatch.setattr(rail_module, "matcher", counting)
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            await _pick(user, "tests-drawer:sort", "status")
            user.find(marker="tests-drawer:needle").type("FakeLib")
            await _settled(lambda: asked != [])
            await _settled(lambda: _shown(shell, user) == [A, B, D, G, C])
            asked.clear()
            # D's nrmse 6e-4 fails at 1e-5: D moves up among the failures
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            await _settled(lambda: _shown(shell, user) == [A, D, B, G, C])
            assert asked == []


@pytest.fixture
def two_package_shell(tmp_path):
    """`_two_package_project`: `FakeLib.Cases.X` besides A, B, C, D, G."""
    from test_session import _two_package_project

    cfg, work = _two_package_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


def test_the_current_row_is_emitted_after_the_row_hover():
    """`.dstf-row.dstf-row-current` and `.dstf-row:hover` weigh the same, so
    the later one wins: the current test's row keeps its tint under the
    mouse only while it comes after."""
    from dstf.authoring.shell.style import STYLE

    assert STYLE.index(".dstf-row.dstf-row-current") > STYLE.index(".dstf-row:hover")


# -- 3c: the selected plot, Remove plot, Reset layout -------------------------


def _ringed(page) -> list[int]:
    return [c.index for c in page.cards if "dstf-plot-selected" in c.card.classes]


def _tooltip_text(element) -> str:
    """The text of the `ui.tooltip` directly inside ``element``."""
    (tip,) = [
        child
        for slot in element.slots.values()
        for child in slot.children
        if isinstance(child, ui.tooltip)
    ]
    return tip.text


async def test_a_selected_family_that_loses_a_member_keeps_the_selection(
    nested_shell,
):
    """The selection follows its plot by ANY member of the subject, not the
    exact subject: pipe.T[2]'s one criterion goes, the family's plot is
    pipe.T[1]'s now and moves to where the tree first names pipe.T[1] — the
    selection goes with it."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [
            ("pipe.T[1]", "pipe.T[2]"),
            ("x",),
            ("y",),
        ]
        with page.client:
            page.select(0)
        shell.session.stage([PatchOp(A, "remove", N_T2)])
        await _settled(
            lambda: (
                [c.resolved.subject for c in _page(shell, user).cards]
                == [("x",), ("pipe.T[1]",), ("y",)]
            )
        )
        page = _page(shell, user)
        assert page.selected == 1 and _ringed(page) == [1]


async def test_a_refused_placement_keeps_following_the_plot(nested_shell, monkeypatch):
    """A `stage_layout(select=...)` the Session refuses restores the
    selection AND what it follows: a later rebuild still moves the
    selection with its plot."""
    from dstf.authoring.layout import new_watch
    from dstf.discovery.patch_apply import PatchError

    shell = nested_shell
    session = shell.session
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.select(1)  # x
        real = session.stage

        def refuse(ops):
            raise PatchError("refused")

        monkeypatch.setattr(session, "stage", refuse)
        with page.client:
            assert not page.stage_layout(new_watch(session.layout(A), "w"), select=3)
        monkeypatch.setattr(session, "stage", real)
        assert page.selected == 1 and page.follow == ("x",)
        # x moves to the top when pipe.T[2]'s criterion goes; the selection
        # goes with it.
        session.stage([PatchOp(A, "remove", N_T2)])
        await _settled(
            lambda: (
                [c.resolved.subject for c in _page(shell, user).cards]
                == [("x",), ("pipe.T[1]",), ("y",)]
            )
        )
        assert _page(shell, user).selected == 0


class TestLayoutControls:
    """The plot the drawer's checkboxes act on (the owner's ruling, Q5 A),
    and the layout edits a card and the tree line make. `page_shell` is A
    drawn as seven plots (0: x, 1: y, 2..6: watch plots of w1..w5, from a
    `plots` key); `shell` is A drawn one plot per scored variable, no key."""

    async def test_the_first_plot_is_selected_and_select_moves_the_ring(
        self, page_shell
    ):
        async with user_simulation(root=page_shell.index) as user:
            page = await _test_page(page_shell, user)
            assert len(page.cards) == 7
            assert page.selected == 0 and _ringed(page) == [0]
            with page.client:
                page.select(3)
            assert page.selected == 3 and _ringed(page) == [3]
            with page.client:
                page.select(99)  # clamped to the cards
            assert page.selected == 6 and _ringed(page) == [6]

    async def test_remove_plot_stages_one_op_without_that_watch_plot(self, page_shell):
        shell = page_shell
        plots = copy.deepcopy(shell.session.spec.entry(A)["plots"])
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            user.find(marker="remove-plot:2").click()
            assert shell.session.spec.pending == [
                PatchOp(A, "replace", "/plots", plots[1:])
            ]
            await _settled(lambda: len(page.cards) == 6)

    async def test_removal_keeps_the_selection_on_the_same_plot(self, page_shell):
        """A removed plot above the selected one shifts it up by one; the
        selected plot removed selects the one now at its index, the last if
        it was the last."""
        shell = page_shell
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            with page.client:
                page.select(4)
            user.find(marker="remove-plot:2").click()
            await _settled(lambda: len(page.cards) == 6)
            assert page.selected == 3 and _ringed(page) == [3]
            user.find(marker="remove-plot:3").click()
            await _settled(lambda: len(page.cards) == 5)
            assert page.selected == 3 and _ringed(page) == [3]
            with page.client:
                page.select(4)
            user.find(marker="remove-plot:4").click()
            await _settled(lambda: len(page.cards) == 4)
            assert page.selected == 3 and _ringed(page) == [3]

    async def test_a_stale_remove_removes_nothing(self, page_shell):
        """A card drawn before the last change can name a watch plot that
        has gone: its Remove reads the extras NOW, and does nothing."""
        shell = page_shell
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            with page.client:
                page.remove_watch(2, "w1")
                page.remove_watch(2, "w1")
            assert [op.value for op in shell.session.spec.pending] == [
                [{"var": name} for name in ("w2", "w3", "w4", "w5")]
            ]

    async def test_reset_layout_shows_while_the_entry_has_plots_and_stages_its_removal(
        self, page_shell
    ):
        shell = page_shell
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            user.find(marker="tree:reset-layout").click()
            assert shell.session.spec.pending == [PatchOp(A, "remove", "/plots")]
            await _settled(lambda: _none(user, "tree:reset-layout"))
            await _settled(lambda: len(page.cards) == 2)
            assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]
            assert _one(user, "plots:count").text == (
                "2 plots · derived from the criteria"
            )

    async def test_reset_layout_is_absent_without_a_plots_key_and_on_an_unscored_test(
        self, shell
    ):
        """Both branches of the tree line: A is scored and has no key; B has
        no baseline, so no tree, and a staged key puts the button there."""
        async with user_simulation(root=shell.index) as user:
            await _test_page(shell, user)
            assert _none(user, "tree:reset-layout")
            shell.session.stage(
                [PatchOp(B, "add", "/plots", [{"var": "x", "context": ["y"]}])]
            )
            await _test_page(shell, user, B)
            assert shell.session.tree_view(B) is None
            assert not _none(user, "tree:reset-layout")

    async def test_a_refused_layout_edit_is_said_and_changes_nothing(
        self, page_shell, monkeypatch
    ):
        from dstf.discovery.patch_apply import PatchError

        shell = page_shell
        error = PatchError("the document refused it")

        def refuse(ops):
            raise error

        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            with page.client:
                page.select(4)
            monkeypatch.setattr(shell.session, "stage", refuse)
            user.find(marker="remove-plot:2").click()
            await _settled(lambda: user.notify.messages != [])
            assert user.notify.messages == [str(error)]
            assert shell.session.spec.pending == []
            assert page.selected == 4 and len(page.cards) == 7
            from dstf.authoring.layout import remove_watch

            with page.client:
                edit = remove_watch(shell.session.layout(A), "w5")
                assert page.stage_layout(edit, select=5) is False
            assert page.selected == 4
            assert shell.session.spec.pending == []

    async def test_an_edit_that_changes_nothing_stages_nothing(self, page_shell):
        """A gesture whose extras are the ones in effect stages no op."""
        async with user_simulation(root=page_shell.index) as user:
            page = await _test_page(page_shell, user)
            with page.client:
                assert page.stage_layout(page_shell.session.layout(A)) is False
            assert page_shell.session.spec.pending == []

    async def test_no_extras_on_an_entry_with_no_key_stage_nothing(self, shell):
        """Not a `remove` of a member that is not there."""
        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            with page.client:
                assert page.stage_layout(()) is False
            assert shell.session.spec.pending == []
            assert user.notify.messages == []

    async def test_reset_layout_follows_the_effective_entry(self, shell):
        """B has no ``plots`` key: a context series stages the key's add,
        and Reset its removal — which puts the entry back as saved, so
        nothing is pending — and the button goes again, because the
        EFFECTIVE entry has no key."""
        from dstf.authoring.layout import add_context

        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user, B)
            assert _none(user, "tree:reset-layout")
            with page.client:
                page.stage_layout(add_context((), page.cards[0].resolved, ["y"]))
            assert shell.session.spec.pending == [
                PatchOp(B, "add", "/plots", [{"var": "x", "context": ["y"]}])
            ]
            await _settled(lambda: not _none(user, "tree:reset-layout"))
            user.find(marker="tree:reset-layout").click()
            assert shell.session.spec.pending == []
            await _settled(lambda: _none(user, "tree:reset-layout"))
            await _settled(lambda: page.cards[0].resolved.context == ())

    async def test_a_refused_reset_is_said_and_changes_nothing(
        self, page_shell, monkeypatch
    ):
        from dstf.discovery.patch_apply import PatchError

        shell = page_shell
        error = PatchError("the document refused it")

        def refuse(ops):
            raise error

        async with user_simulation(root=shell.index) as user:
            page = await _test_page(shell, user)
            monkeypatch.setattr(shell.session, "stage", refuse)
            user.find(marker="tree:reset-layout").click()
            await _settled(lambda: user.notify.messages != [])
            assert user.notify.messages == [str(error)]
            assert shell.session.spec.pending == []
            assert len(page.cards) == 7 and not _none(user, "tree:reset-layout")

    async def test_a_patch_error_from_a_layout_edit_is_said_not_raised(
        self, page_shell, monkeypatch
    ):
        """Said as its reason, without the location its ``str()`` leads with."""
        from dstf.discovery.patch_apply import PatchError

        shell = page_shell
        error = PatchError("probe: the document refused it", reason="it refused")

        def refuse(ops):
            raise error

        async with user_simulation(root=shell.index) as user:
            from dstf.authoring.layout import remove_watch

            page = await _test_page(shell, user)
            monkeypatch.setattr(shell.session, "stage", refuse)
            with page.client:
                edit = remove_watch(shell.session.layout(A), "w5")
                assert page.stage_layout(edit) is False
            await _settled(lambda: user.notify.messages != [])
            assert user.notify.messages == ["it refused"]
            assert shell.session.spec.pending == [] and len(page.cards) == 7


# -- 3c: the Variables drawer ---------------------------------------------------

#: The rows of `_drawer_project`'s A, by `VarRow.id` (`<rank>/<group>/<key>`):
#: the markers are the ids, never the keys, which are not unique.
ROW_X, ROW_Y = "0/Tracked/x", "0/Tracked/y"
ROW_Z, ROW_A0 = "2/(top level)/z", "2/(top level)/a0"
ROW_PIPE = "1/pipe/pipe.T[]"
ROW_PORT = "1/port/port[2, 1].C[]"
PIPE = ["pipe.T[1]", "pipe.T[2]", "pipe.T[3]"]
PORT = ["port[2, 1].C[1]", "port[2, 1].C[2]", "port[2, 1].C[3]"]
#: A second port family whose id holds the first's words and more (the
#: marker test's own copy of the fixture adds it).
ROW_PORT3 = "1/port/port[2, 3, 1].C[]"
PORT3 = ["port[2, 3, 1].C[1]", "port[2, 3, 1].C[2]"]


def _ctx(var: str, *context: str) -> dict:
    """One entry of the ``plots`` key as a writer stores it."""
    return {"var": var, **({"context": list(context)} if context else {})}


@pytest.fixture
def drawer_shell(tmp_path):
    """`_drawer_project`: A tracks x and y (one nrmse each, one plot each)
    and its result carries a0, z, pipe.T[1:3], port[2, 1].C[1:3] and
    CPUtime besides."""
    from test_session import _drawer_project

    cfg, work = _drawer_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


def _labels_in(element) -> list[str]:
    """The text of every label under ``element``, in document order."""
    out: list[str] = []
    for slot in element.slots.values():
        for child in slot.children:
            if isinstance(child, MiddleCut):
                out.append(child.text)  # one label, cut in two spans
                continue
            if isinstance(child, ui.label):
                out.append(child.text)
            out.extend(_labels_in(child))
    return out


def _groups_shown(user) -> list[str]:
    return [
        m for m in _marks(_one(user, "drawer:variables")) if m.startswith("var-group:")
    ]


def _checked(user, row_id: str) -> str:
    return str(_one(user, var_marker("var-check", row_id)).props["aria-checked"])


def _op_value(shell) -> list:
    (op,) = shell.session.spec.pending
    assert (op.model_id, op.op, op.path) == (A, "add", "/plots")
    return op.value


def _new_why(user, row_id: str) -> str:
    """The tooltip on a row's grey "+ new plot"."""
    return _tooltip_text(_one(user, var_marker("var-new-why", row_id)))


def _raised(caplog) -> list[str]:
    """The exceptions a click's handler raised: NiceGUI logs them and the
    click returns as if nothing happened."""
    return [r.getMessage() for r in caplog.records if r.exc_info]


async def _drawer(shell, user, model=A) -> TestPage:
    page = await _test_page(shell, user, model)
    user.find(marker="rail:variables").click()
    await user.should_see(marker="var-target")
    return page


class TestVariablesDrawer:
    """The drawer edits the plot extras on the selected plot (the owner's
    rulings Q5-Q7, Q12-Q14): a check adds a row's members to its CONTEXT or
    takes them off — its subject's check is on and grey — and "+ new plot"
    makes a WATCH plot of one variable, after the others, and selects it."""

    async def test_the_filter_and_its_toggles_survive_a_switch_of_test(
        self, drawer_shell
    ):
        """The Variables drawer's needle, `.*` and `Aa` are the tab's
        (`TabState`), never the drawer instance's: a new page's drawer
        starts from them, drawn pressed and applied."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            user.find(marker="var-regex").click()
            user.find(marker="var-case").click()
            user.find(marker="var-needle").type("^pipe")
            await _settled(lambda: page.state.vars_needle == "^pipe")
            assert page.state.vars_regex is True
            assert page.state.vars_case is True
            await _test_page(shell, user, B)
            other = _page(shell, user)
            assert other.model_id == B and other is not page
            assert other.state.drawer == "variables"
            assert _one(user, "var-needle").value == "^pipe"
            assert _one(user, "var-regex").props["aria-pressed"] == "true"
            assert _one(user, "var-case").props["aria-pressed"] == "true"
            # B's result has no `pipe`: the filter is applied, not only shown
            assert _groups_shown(user) == []

    async def test_the_drawer_lists_the_groups_and_names_its_target(self, drawer_shell):
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert _groups_shown(user) == [
                "var-group:0-Tracked",
                "var-group:1-pipe",
                "var-group:1-port",
                "var-group:2-top-level",
                "var-group:3-Diagnostics",
            ]
            assert (
                _one(user, "var-target").text == "Checkboxes add context to plot 1 of 2"
            )
            assert "pipe.T[1:3]" in _labels_in(
                _one(user, var_marker("var-row", ROW_PIPE))
            )
            assert "port[2, 1].C[1:3]" in _labels_in(
                _one(user, var_marker("var-row", ROW_PORT))
            )
            # x is plot 1's subject: its check is on, and grey.
            assert _checked(user, ROW_X) == "true"
            assert not _one(user, var_marker("var-check", ROW_X)).enabled
            assert _tooltip_text(_one(user, var_marker("var-why", ROW_X))) == (
                "The plot's subject"
            )
            assert _checked(user, ROW_Y) == "false"
            assert _one(user, var_marker("var-check", ROW_Y)).enabled

    async def test_a_checkbox_adds_context_to_the_selected_plot_as_one_op(
        self, drawer_shell
    ):
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            user.find(marker=var_marker("var-check", ROW_Z)).click()
            assert _op_value(shell) == [_ctx("x", "z")]
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            user.find(marker=var_marker("var-check", ROW_PIPE)).click()
            assert _op_value(shell) == [_ctx("x", "z", *PIPE)]
            await _settled(lambda: page.cards[0].resolved.context == ("z", *PIPE))
            await _settled(lambda: _checked(user, ROW_PIPE) == "true")
            # Context, not a criterion: the card's rows and title are x's.
            assert _one(user, "plot-title:0").text == "x"
            assert list(page.cards[0].rows) == [T0]
            assert _one(user, f"context-name:0:{PIPE[0]}").text == PIPE[0]

    async def test_a_family_partly_in_the_context_is_mixed_and_a_click_adds_the_rest(
        self, drawer_shell
    ):
        shell = drawer_shell
        shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("x", PIPE[1])])])
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            assert _checked(user, ROW_PIPE) == "mixed"
            user.find(marker=var_marker("var-check", ROW_PIPE)).click()
            assert _op_value(shell) == [_ctx("x", PIPE[1], PIPE[0], PIPE[2])]

    async def test_unchecking_takes_the_context_off_and_the_emptied_entry_goes(
        self, drawer_shell
    ):
        """x's entry held z alone: with z off it records nothing, and the
        key goes with it (`layout_op`: no extras is a remove) — the entry
        as saved again, so nothing is pending."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            user.find(marker=var_marker("var-check", ROW_Z)).click()
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            user.find(marker=var_marker("var-check", ROW_Z)).click()
            assert shell.session.spec.pending == []
            assert "plots" not in shell.session.spec.entry(A)
            await _settled(lambda: _checked(user, ROW_Z) == "false")
            assert _one(user, var_marker("var-check", ROW_Z)).enabled

    async def test_new_plot_makes_a_watch_plot_after_the_others_and_selects_it(
        self, drawer_shell
    ):
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            user.find(marker=var_marker("var-new", ROW_Z)).click()
            assert _op_value(shell) == [_ctx("z")]
            await _settled(lambda: len(page.cards) == 3)
            assert page.cards[2].resolved.watch
            assert page.selected == 2 and _ringed(page) == [2]
            await _settled(
                lambda: (
                    _one(user, "var-target").text
                    == "Checkboxes add context to plot 3 of 3"
                )
            )
            # z is that plot's subject now: on and grey, and no second plot.
            assert _checked(user, ROW_Z) == "true"
            assert not _one(user, var_marker("var-check", ROW_Z)).enabled
            assert not _one(user, var_marker("var-new", ROW_Z)).enabled
            assert _new_why(user, ROW_Z) == "Already the subject of plot 3"

    async def test_new_plot_is_grey_on_a_subject_and_on_a_family(self, drawer_shell):
        """A watch plot is of ONE variable (C3): a family's row makes none,
        its members, opened, each do. A plot's subject has its plot."""
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert not _one(user, var_marker("var-new", ROW_X)).enabled
            assert _new_why(user, ROW_X) == "Already the subject of plot 1"
            assert _new_why(user, ROW_Y) == "Already the subject of plot 2"
            assert not _one(user, var_marker("var-new", ROW_PIPE)).enabled
            assert _new_why(user, ROW_PIPE) == (
                "A watch plot shows one variable: open the family"
            )
            assert _one(user, var_marker("var-new", ROW_Z)).enabled
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            member = f"{ROW_PIPE}/{PIPE[0]}"
            await _settled(lambda: not _none(user, var_marker("var-new", member)))
            assert _one(user, var_marker("var-new", member)).enabled

    async def test_a_check_on_a_plot_that_has_just_gone_does_nothing(
        self, drawer_shell, caplog
    ):
        """A check clicked after the selected watch plot was removed, before
        the page caught up: the selection names a plot there is no longer,
        and the check does nothing — never an IndexError."""
        shell = drawer_shell
        shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("z")])])
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            with page.client:
                page.select(2)
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            check = user.find(marker=var_marker("var-check", ROW_A0))
            with page.client:
                page.remove_watch(2, "z")
            # The removal put the entry back as saved: nothing is pending.
            assert shell.session.spec.pending == []
            with caplog.at_level(logging.ERROR):
                check.click()  # the row drawn for plot 3, before any rebuild
            assert _raised(caplog) == []
            assert shell.session.spec.pending == []

    async def test_a_stale_check_acts_on_what_the_selected_plot_holds_now(
        self, drawer_shell
    ):
        """ "+ new plot" selects its plot at once; the rows follow on the
        next cycle. A click on z's check in between read "all on" from the
        row drawn for plot 1 and took z off plot 3, which never had it; it
        reads plot 3 as it is, and adds z there."""
        shell = drawer_shell
        shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("x", "z")])])
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            check = user.find(marker=var_marker("var-check", ROW_Z))
            user.find(marker=var_marker("var-new", ROW_A0)).click()
            assert page.selected == 2
            assert _op_value(shell) == [_ctx("x", "z"), _ctx("a0")]
            check.click()  # drawn for plot 1, which shows z
            assert _op_value(shell) == [_ctx("x", "z"), _ctx("a0", "z")]

    async def test_a_failed_run_offers_no_plot_to_make(self, drawer_shell):
        """A result that is not a success has no plots (O2), and a watch
        plot made on it would draw nothing: the drawer says why — the run
        failed — its checks and every "+ new plot" are grey, and a click
        that reaches `_new` anyway — drawn before the run failed — stages
        nothing. The page reads the result, never the manifest: a run that
        left A pending with status.json recording it failed hands in this
        same failed result (D145)."""
        from dstf.authoring.variable_rows import VarRow
        from dstf.backends.types import TestResult

        shell = drawer_shell
        why = "The run failed: no plots"
        result = shell.session.project.results[A]
        shell.session.project.results[A] = TestResult(
            model_id=A, success=False, variables=list(result.variables)
        )
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            assert page.cards == []
            assert _one(user, "var-target").text == why
            assert not _one(user, var_marker("var-new", ROW_Z)).enabled
            assert _new_why(user, ROW_Z) == why
            assert not _one(user, var_marker("var-check", ROW_Z)).enabled
            drawer = page.rail.variables
            assert drawer is not None
            row = VarRow(
                key="z",
                id=ROW_Z,
                members=("z",),
                text="z",
                file_text="z",
                name=None,
                on_selected=0,
                elsewhere=(),
                is_subject=False,
                subject_of=None,
            )
            with page.client:
                drawer._new(row)
            assert shell.session.spec.pending == []

    async def test_a_run_that_fails_moves_the_drawers_signature(self, drawer_shell):
        """Whether the run failed is part of what the drawer is drawn from:
        a test with no plot before and after (a statistics-only tree) moves
        nothing else the rows read, and its "+ new plot" stayed live."""
        from dstf.backends.types import TestResult

        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            with page.client:
                reads = drawer._read()
                before = drawer._signature(reads)
                result = shell.session.project.results[A]
                shell.session.project.results[A] = TestResult(
                    model_id=A, success=False, variables=list(result.variables)
                )
                assert drawer._signature(reads) != before

    async def test_new_plot_on_a_family_row_stages_nothing(self, drawer_shell):
        """C3: a watch plot shows ONE variable. The family row's button is
        grey; a click that reaches `_new` anyway — drawn before the row
        folded — makes no plot of the array."""
        from dstf.authoring.variable_rows import VarRow

        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            row = VarRow(
                key="pipe.T[]",
                id=ROW_PIPE,
                members=tuple(PIPE),
                text="pipe.T[1:3]",
                file_text="pipe.T[1:3]",
                name=None,
                on_selected=0,
                elsewhere=(),
                is_subject=False,
                subject_of=None,
            )
            with page.client:
                drawer._new(row)
            assert shell.session.spec.pending == []

    async def test_new_plot_twice_from_one_row_makes_one_plot(
        self, drawer_shell, caplog
    ):
        """A real double-click on "+ new plot": the second click lands on the
        row drawn BEFORE the first one's edit, whose variable is a watch
        plot's subject by then. It makes no second plot — and never the
        ValueError of a second entry on one variable."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            new = user.find(marker=var_marker("var-new", ROW_Z))
            new.click()
            with caplog.at_level(logging.ERROR):
                new.click()  # the same row, before any rebuild
            assert _raised(caplog) == []
            assert _op_value(shell) == [_ctx("z")]

    async def test_the_filter_globs_regexes_and_says_a_bad_regex(self, drawer_shell):
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            needle = user.find(marker="var-needle")
            needle.type("pipe.T[?]")
            await _settled(lambda: _groups_shown(user) == ["var-group:1-pipe"])
            assert _none(user, "var-filter:why")
            user.find(marker="var-regex").click()
            assert _one(user, "var-regex").props["aria-pressed"] == "true"
            needle.clear().type("(")
            await _settled(lambda: not _none(user, "var-filter:why"))
            assert _one(user, "var-filter:why").text.startswith(
                "not a regular expression: "
            )
            assert _groups_shown(user) == []
            needle.clear().type("^port")
            await _settled(lambda: _groups_shown(user) == ["var-group:1-port"])
            assert _none(user, "var-filter:why")

    async def test_a_regex_that_takes_too_long_says_so_and_draws_no_rows(
        self, drawer_shell, monkeypatch, caplog
    ):
        """A typed regex that outruns its budget is a draw that completed:
        the drawer says so where it says a bad regex, and draws no row —
        never a traceback, never a half-built list. A budget of 0 runs out
        at the first name."""
        from dstf.authoring import needle as needle_module

        monkeypatch.setattr(needle_module, "REGEX_BUDGET", 0.0)
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert _groups_shown(user) != []
            user.find(marker="var-regex").click()
            with caplog.at_level(logging.ERROR):
                user.find(marker="var-needle").type("x")
                await _settled(lambda: not _none(user, "var-filter:why"))
            assert _raised(caplog) == []
            assert (
                _one(user, "var-filter:why").text
                == "the pattern took too long; simplify it"
            )
            assert _groups_shown(user) == []
            drawer = _marks(_one(user, "drawer:variables"))
            assert [m for m in drawer if m.startswith("var-row:")] == []

    async def test_a_familys_swatch_is_four_blocks_of_the_accent_ramp(
        self, drawer_shell
    ):
        """DESIGN.md: no gradients. The owner-approved board drew a family's
        swatch as four discrete blocks, one per stop of the accent ramp,
        light to dark; a `linear-gradient` over the stops was built."""
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            row = _one(user, var_marker("var-row", ROW_PIPE))
            (swatch,) = [e for e in row.descendants() if "dstf-var-ramp" in e.classes]
            assert "gradient" not in str(dict(swatch.style))
            assert [
                dict(block.style).get("background")
                for block in swatch.default_slot.children
            ] == ["#90caf9", "#42a5f5", "#1976d2", "#0d47a1"]
            assert not [
                e
                for e in _one(user, var_marker("var-row", ROW_Z)).descendants()
                if "dstf-var-ramp" in e.classes
            ]  # a lone variable is no family

    async def test_two_rows_sharing_a_label_are_told_apart_by_their_checks_name(
        self, drawer_shell
    ):
        """`x={a.Q, a.Q}`: two rows read "a.Q", each with its name muted
        beside it. A check named by the row's text alone read the same for
        both to a screen reader; it says the muted name too."""
        from test_session import with_expressions

        shell = drawer_shell
        with_expressions(shell.session, A, {"x": "a.Q", "y": "a.Q"})
        shell.session._score(A)
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            assert [
                _one(user, var_marker("var-check", r)).props["aria-label"]
                for r in (ROW_X, ROW_Y, ROW_Z)
            ] == ["a.Q (x)", "a.Q (y)", "z"]

    async def test_a_family_opens_to_its_members(self, drawer_shell):
        """Each member a row of its own: its check and its "+ new plot" act
        on that one variable."""
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            for name in PIPE:
                member = f"{ROW_PIPE}/{name}"
                assert name in _labels_in(_one(user, var_marker("var-row", member)))
                assert _checked(user, member) == "false"
                assert not _none(user, var_marker("var-new", member))
            user.find(marker=var_marker("var-check", f"{ROW_PIPE}/{PIPE[1]}")).click()
            await _settled(lambda: _checked(user, f"{ROW_PIPE}/{PIPE[1]}") == "true")
            # Still open after the rebuild the stage caused.
            assert _checked(user, f"{ROW_PIPE}/{PIPE[0]}") == "false"
            assert _checked(user, ROW_PIPE) == "mixed"
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            assert _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))

    async def test_the_drawer_follows_the_selected_plot(self, drawer_shell):
        """`select` moves the drawer's target and its checks, and a repaint
        stages nothing and says nothing. A check that were a `ui.checkbox`
        repainted by `set_value` (mutation (a)) would stage x as context on
        plot 2 here as its check turned off."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            with page.client:
                page.select(1)
            assert (
                _one(user, "var-target").text == "Checkboxes add context to plot 2 of 2"
            )
            assert _checked(user, ROW_Y) == "true"
            assert _checked(user, ROW_X) == "false"
            assert not _one(user, var_marker("var-check", ROW_Y)).enabled
            assert _one(user, var_marker("var-check", ROW_X)).enabled
            assert shell.session.spec.pending == []
            assert user.notify.messages == []

    async def test_the_drawer_follows_a_layout_staged_elsewhere(self, drawer_shell):
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("x", "z")])])
            await _settled(lambda: _checked(user, ROW_Z) == "true")

    async def test_a_closed_drawer_is_not_rebuilt(self, drawer_shell):
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            user.find(marker="rail:variables").click()
            drawer = page.rail.variables
            assert drawer is not None
            built = []
            real = drawer.rebuild
            drawer.rebuild = lambda: (built.append(1), real())
            with page.client:
                page.select(1)
            shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("x", "z")])])
            await _settled(lambda: len(page.cards[0].resolved.series) == 2)
            assert built == []
            user.find(marker="rail:variables").click()
            assert built == [1]
            assert (
                _one(user, "var-target").text == "Checkboxes add context to plot 2 of 2"
            )

    async def test_a_test_with_no_result_says_so(self, tmp_path):
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path)
        (work / "test_0002" / "result.json").unlink()  # B's
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        shell.load.phase = "done"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user, B)
            # The reader hands a missing result file in as a failed run,
            # and B's manifest says the run finished: the run failed.
            assert _one(user, "var-empty").text == "the run failed"
            assert _one(user, "var-target").text == "The run failed: no plots"

    async def test_the_cap_line(self, tmp_path):
        from dstf.authoring.variable_rows import cap, variable_groups
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path, extra=250)
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        shell.load.phase = "done"
        session = shell.session
        scope = session.variable_texts(A)
        _, shown, total = cap(
            variable_groups(
                names=session.variable_names(A),
                tracked=session.tracked(A),
                diagnostics=session.project.config.diagnostic_variables,
                labels={n: t.label for n, t in scope.items()},
                plots=session.plots(A),
                selected=0,
                keep=lambda text: True,
            ),
            200,
        )
        assert (shown, total > 200) == (200, True)
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            assert _one(user, "var-cap").text == (
                f"Showing 200 of {total:,} rows — narrow the filter"
            )

    async def test_the_open_drawer_rebuilds_only_when_what_it_draws_moved(
        self, drawer_shell
    ):
        """`TestPage.sync` ends by refreshing the rail, and a run's events
        rebuilt an open drawer each time over rows that had not moved. A
        build clears the body first: that is what is counted."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            built = []
            clear = drawer.body.clear
            drawer.body.clear = lambda: (built.append(1), clear())[1]
            synced: list[bool] = []
            sync = page.sync
            page.sync = lambda force=False: (synced.append(force), sync(force))[1]
            with page.client:
                for state in ("queued", "started", "ended"):
                    page.handle({"event": "queue", "state": state, "models": {A}})
            assert synced == [False, True, True] and built == []
            shell.session.stage([PatchOp(A, "add", "/plots", [_ctx("x", "z")])])
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            await asyncio.sleep(0.3)  # the rescore the stage started, and its events
            assert built == [1]
            with page.client:
                page.select(1)
            assert built == [1, 1]
            assert (
                _one(user, "var-target").text == "Checkboxes add context to plot 2 of 2"
            )

    async def test_a_draw_that_raised_is_drawn_again_by_the_next_refresh(
        self, drawer_shell, monkeypatch
    ):
        """`drawn` is what the rows ON SCREEN were built from. A draw that
        raised had recorded it first, so every later refresh saw nothing
        moved and left the body it had cleared empty. (A bad regex is a
        draw that completed: it is drawn as its reason, and the filter test
        shows a fixed regex drawing rows again.)"""
        from dstf.authoring.shell import variables_drawer

        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            real = variables_drawer.variable_groups

            def boom(**kwargs):
                raise RuntimeError("boom")

            monkeypatch.setattr(variables_drawer, "variable_groups", boom)
            with page.client, pytest.raises(RuntimeError):
                drawer.rebuild()
            assert _none(user, var_marker("var-row", ROW_X))
            monkeypatch.setattr(variables_drawer, "variable_groups", real)
            with page.client:
                drawer.refresh()
            assert not _none(user, var_marker("var-row", ROW_X))

    async def test_a_click_on_a_card_builds_the_open_drawer_at_most_once(
        self, drawer_shell
    ):
        """A click on the selected card went through a refresh, and a click
        selecting another builds the open drawer once. A build clears the
        body first: that is what is counted."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            built = []
            clear = drawer.body.clear
            drawer.body.clear = lambda: (built.append(1), clear())[1]
            user.find(marker="plot:0").click()  # the selected card, again
            assert built == []
            user.find(marker="plot:1").click()
            assert page.selected == 1 and built == [1]
            assert (
                _one(user, "var-target").text == "Checkboxes add context to plot 2 of 2"
            )
            user.find(marker="plot:1").click()  # its plot, selected
            assert built == [1]

    async def test_a_row_names_the_other_plots_that_show_it_counting_from_1(
        self, drawer_shell
    ):
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert "plot 2" in _labels_in(_one(user, var_marker("var-row", ROW_Y)))
            assert not any(
                t.startswith("plot ")
                for t in _labels_in(_one(user, var_marker("var-row", ROW_X)))
            )

    async def test_a_lone_row_reads_its_label_with_its_name_muted_beside(
        self, tmp_path
    ):
        from test_session import UT1, _unit_tests_project, with_expressions

        cfg, work = _unit_tests_project(tmp_path)
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        with_expressions(shell.session, A, {UT1: "a.Q"})
        shell.load.phase = "done"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            rows = [
                _one(user, m)
                for m in _marks(_one(user, "drawer:variables"))
                if m.startswith("var-row:")
            ]
            (row,) = [r for r in rows if "a.Q" in _labels_in(r)]
            assert _labels_in(row)[:2] == ["a.Q", UT1]

    async def test_a_rows_text_carries_its_full_text_and_name_as_its_title(
        self, tmp_path
    ):
        """At 320 px a real library's names are cut with an ellipsis (3c's
        gate): the row's text carries all of it as its title, and the name
        beside it when it shows one."""
        from test_session import UT1, UT2, _unit_tests_project, with_expressions

        cfg, work = _unit_tests_project(tmp_path)
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        with_expressions(shell.session, A, {UT1: "a.Q"})
        shell.load.phase = "done"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            titles = {
                e.text: e.props.get("title")
                for e in _one(user, "drawer:variables").descendants()
                if isinstance(e, MiddleCut) and "dstf-var-text" in e.classes
            }
            assert titles == {"a.Q": f"a.Q ({UT1})", UT2: UT2}

    async def test_an_opened_family_shows_its_first_members_and_counts_the_rest(
        self, drawer_shell, monkeypatch
    ):
        """At most `MEMBERS` of a family's own rows (50; 2 here, for the
        fixture's three-member family), and a line counting the rest — once
        it is opened: a closed family counts nothing."""
        from dstf.authoring.shell import variables_drawer

        monkeypatch.setattr(variables_drawer, "MEMBERS", 2)
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            assert "… 1 more" not in _labels_in(_one(user, "drawer:variables"))
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            for name in PIPE[:2]:
                assert not _none(user, var_marker("var-row", f"{ROW_PIPE}/{name}"))
            assert _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[2]}"))
            assert "… 1 more" in _labels_in(_one(user, "drawer:variables"))

    async def test_match_case_is_honoured(self, drawer_shell):
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            user.find(marker="var-needle").type("Z")
            await _settled(lambda: _groups_shown(user) == ["var-group:2-top-level"])
            user.find(marker="var-case").click()
            assert _one(user, "var-case").props["aria-pressed"] == "true"
            assert _groups_shown(user) == []
            user.find(marker="var-case").click()
            assert _groups_shown(user) == ["var-group:2-top-level"]

    async def test_with_no_plot_the_checks_are_grey_and_new_plot_makes_plot_1(
        self, tmp_path
    ):
        """B tracks nothing its result file holds, and has no tree: no plot
        for a check to act on, so each is disabled; "+ new plot" makes the
        first plot — a watch plot — and selects it."""
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path)
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == B:
                entry["variables"] = ["not.in.the.file"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        shell = _opened(cfg, work)
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user, B)
            assert page.cards == []
            assert _one(user, "var-target").text == "No plot yet: + new plot adds one"
            row = "2/(top level)/x"
            assert not _one(user, var_marker("var-check", row)).enabled
            user.find(marker=var_marker("var-new", row)).click()
            (op,) = shell.session.spec.pending
            assert (op.model_id, op.op, op.value) == (B, "add", [_ctx("x")])
            await _settled(lambda: len(page.cards) == 1)
            assert page.selected == 0 and _ringed(page) == [0]
            await _settled(lambda: _checked(user, row) == "true")

    async def test_a_result_with_no_variables_says_so(self, tmp_path):
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"] = {}
        path.write_text(json.dumps(payload), encoding="utf-8")
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        shell.load.phase = "done"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            assert _one(user, "var-empty").text == "the result has no variables"

    async def test_a_marker_names_one_row_whatever_its_spaces(self, tmp_path):
        """NiceGUI splits a marker on whitespace: `port[2, 1].C[]`'s marker
        was three words that `port[2, 3, 1].C[]`'s also carried, so a find
        for the first found both."""
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for k, name in enumerate(PORT3, start=40):
            payload["variables"][name] = [k * v for v in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        shell = Shell(cfg, work_dir=work)
        shell.session.open(cfg, work_dir=work)
        shell.load.phase = "done"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            for row, label in (
                (ROW_PORT, "port[2, 1].C[1:3]"),
                (ROW_PORT3, "port[2, 3, 1].C[1:2]"),
            ):
                assert (
                    len(user.find(marker=var_marker("var-check", row)).elements) == 1
                ), row
                assert label in _labels_in(_one(user, var_marker("var-row", row)))

    async def test_a_gone_page_leaves_the_open_drawer_empty(self, drawer_shell, caplog):
        """The page going empties an open drawer by itself: `sync` returned
        right after `_gone`, never reaching `rail.refresh`, and the drawer
        kept the old test's rows, whose checks read the Session for a model
        it no longer has. The drawer reads nothing on a gone page (`_read`):
        every read raises for it."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            assert not _none(user, var_marker("var-row", ROW_X))
            with caplog.at_level(logging.ERROR, logger="dstf"):
                shell.session.close()
                await _settled(lambda: page.gone)
            assert [
                r for r in caplog.records if "a page failed" in r.getMessage()
            ] == []
            assert _none(user, var_marker("var-row", ROW_X)) and _none(
                user, "var-group:0-Tracked"
            )
            assert _one(user, "var-target").text == ""


@pytest.mark.parametrize("second", ["pipe.T[2]", "pipe.T[1]"])
async def test_unit_tests_elements_are_a_card_each_whatever_their_expressions(
    tmp_path, second
):
    """O3: a UnitTests element is its own test quantity — expressions that
    read as one array (`pipe.T[1]`, `pipe.T[2]`), or as one element twice,
    fold nothing: one card each, titled by its expression, its name muted
    beside it."""
    from test_session import UT1, UT2, _unit_tests_project, with_expressions

    cfg, work = _unit_tests_project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    with_expressions(shell.session, A, {UT1: "pipe.T[1]", UT2: second})
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [(UT1,), (UT2,)]
        assert [_one(user, f"plot-name:{i}").text for i in (0, 1)] == [UT1, UT2]
        assert _one(user, "plot-title:0").text.startswith("pipe.T[1]")
        assert _one(user, "plot-title:1").text.startswith(second)


async def test_the_focus_switch_names_the_plot_as_its_title_does(nested_shell):
    """The switcher names the focused plot "the way the plot names itself":
    a family folds there as on the card's title (the executing review's
    correction 8). The sentence beside it counts, and names nothing — the
    switcher already does, and a long family said it twice."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == "pipe.T[1:2]"
        user.find(marker="focus:0").click()
        switch = _one(user, "focus-switch")
        assert switch.options[switch.value] == "pipe.T[1:2]"
        assert _one(user, "focus-bar:text").text == "Showing 1 of 3 plots"


async def test_the_focus_switch_moves_focus_and_closes_the_old_editor(extras_shell):
    """Q11 c: Focus has a variable switcher — every plot by its folded
    subject, as plain text (C4), a watch plot marked. Choosing one focuses
    it through the same path a card's Focus takes: the editor open on the
    card it hides closes."""
    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.open_criterion(N_KX, 1)
            page.focus(1)
        assert page.open == (1, N_KX)
        switch = _one(user, "focus-switch")
        assert switch.props["aria-label"] == "Plot"
        assert list(switch.options.values()) == [
            "pipe.T[1:2]",
            "x",
            "y",
            "w · watch",
        ]
        assert switch.value == 1
        with page.client:
            switch.set_value(3)
        await _settled(lambda: page.focused == 3)
        assert page.open is None
        assert [c.card.visible for c in page.cards] == [False, False, False, True]
        assert _one(user, "focus-bar:all").text == "Show all 4 plots"
        assert _one(user, "focus-bar:text").text == "Showing 1 of 4 plots"
        # Its own repaint to the plot already focused focuses nothing else:
        # the card's Focus button still reads "Focused".
        assert page.cards[3].focus.text == "Focused"


async def test_the_focus_switch_closes_a_form_on_the_card_it_hides(page_shell):
    """A form row under a criteria row goes with its card, as a card's
    Focus closes it."""
    shell = page_shell
    _only(_RowProbe())
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert page.form is not None
        with page.client:
            _one(user, "focus-switch").set_value(1)
        await _settled(lambda: page.focused == 1)
        assert page.form is None
        assert _none(user, f"form-row:crit:0:{RANGE_X}")


# -- the status strip (Q2 A) -------------------------------------------------


def _strip_buttons(user) -> dict[str, str]:
    """Each TEST-placement action in the strip: ``filled`` / ``quiet`` for a
    button, ``menu`` for an item of the ⋮."""
    out: dict[str, str] = {}
    for name in ("accept", "enqueue"):
        if not _none(user, f"action:{name}"):
            props = _one(user, f"action:{name}").props
            out[name] = "filled" if "unelevated" in props else "quiet"
        elif not _none(user, f"menu-action:{name}"):
            out[name] = "menu"
    return out


async def test_the_strip_says_a_failing_test_and_fills_accept(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        strip = _one(user, "strip")
        assert "dstf-strip-red" in strip.classes
        assert _one(user, "strip-text").text == (
            "Fails 2 of 2 criteria. If the new behaviour is right, accept it as "
            "the baseline."
        )
        assert _strip_buttons(user) == {"accept": "filled", "enqueue": "quiet"}
        assert _none(user, "action-more")
        # In the header, below the sub-header; the sub-header holds no bar.
        header = _one(user, "subheader").parent_slot.parent
        assert header in list(_ancestors(strip))
        assert strip in list(_ancestors(_one(user, "test-actions")))
        assert _one(user, "subheader") not in list(
            _ancestors(_one(user, "test-actions"))
        )
        # Invisible: the user simulation finds no hidden element.
        assert _none(user, "strip-preview") and _none(user, "strip-discard")
        assert page.strip.situation.value == "fail"


async def test_the_strip_on_a_passing_test_tucks_accept_into_its_menu(shell):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert _one(user, "strip-text").text == "Passes against its baseline."
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "quiet"}
        _one(user, "action-more")
        user.find(marker="menu-action:accept").click()
        await user.should_see(marker="panel:accept", retries=20)


async def test_the_strip_on_a_test_with_no_baseline_fills_accept(shell):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, B)
        assert "dstf-strip-amber" in _one(user, "strip").classes
        assert _one(user, "strip-text").text == (
            "No baseline yet — accept this result as the first one."
        )
        assert _strip_buttons(user)["accept"] == "filled"


async def test_the_strip_on_an_unfinished_run_fills_queue(shell):
    """C's last run never finished it (its manifest entry is still pending):
    there is no result to read, and the reader hands in none (D145)."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, C)
        assert shell.session.project.results.get(C) is None
        assert _one(user, "strip-text").text == (
            "No result yet — queue it to simulate it."
        )
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "filled"}
        assert page.strip.situation.value == "no_result"


async def test_the_strip_says_a_simulate_only_test_runs(shell):
    from dataclasses import replace

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        comp = shell.session.comparisons[D]
        shell.session.comparisons[D] = replace(comp, simulate_only=True)
        with page.client:
            page.sync()
        assert _one(user, "strip-text").text == (
            "Simulate-only: it runs, and nothing is compared."
        )
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "quiet", "enqueue": "quiet"}


async def test_the_strip_with_an_edit_offers_preview_and_its_discard(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        other = PatchOp(A, "add", "/comparison", {"tolerance": 1.0})
        shell.session.stage([other])
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
        await _settled(lambda: "dstf-strip-amber" in _one(user, "strip").classes)
        assert _one(user, "strip-text").text == (
            "1 unsaved edit · verdict would change PASS → FAIL"
        )
        preview, discard = _one(user, "strip-preview"), _one(user, "strip-discard")
        assert preview.visible and "unelevated" in preview.props
        assert preview.text == "Preview and save"
        assert discard.visible and "flat" in discard.props
        assert discard.text == "Discard" and discard.enabled
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}
        assert page.strip.situation.value == "edits"
        user.find(marker="strip-preview").click()
        assert page.header.drawer.element.value is True
        # The drawer is the preview, built on opening: this test's section
        # and the other test's.
        await user.should_see(marker=f"drawer-test:{D}", retries=20)
        assert set(page.header.drawer.sections) == {A, D}
        user.find(marker="strip-discard").click()
        await _settled(lambda: "dstf-strip-grey" in _one(user, "strip").classes)
        assert shell.session.spec.pending == [other]  # A's edit stays
        assert _one(user, "strip-text").text == "Passes against its baseline."
        assert _none(user, "strip-preview") and _none(user, "strip-discard")


async def test_the_strip_asks_the_saved_verdict_once_per_paint_and_only_with_edits(
    shell, monkeypatch
):
    calls: list[str] = []
    real = type(shell.session).saved_comparison

    def counting(self, model_id):
        calls.append(model_id)
        return real(self, model_id)

    monkeypatch.setattr(type(shell.session), "saved_comparison", counting)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        assert calls == []
        shell.session.stage([PatchOp(A, "add", "/comparison", {"tolerance": 1.0})])
        await asyncio.sleep(0.2)
        assert calls == []  # A's edit is not this page's
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
        await _settled(lambda: page.strip.situation.value == "edits")
        n = len(calls)
        assert n >= 1 and set(calls) == {D}
        with page.client:
            page.sync()
        assert len(calls) == n + 1


async def test_the_strip_asks_to_queue_the_test_for_a_criterions_new_variable(shell):
    """A criterion staged on a variable the result FILE lacks (D's holds x
    only) scores "not found in the result" until a new run: the strip says
    so and fills Queue alone — Preview and save goes quiet (the owner's O1).
    Saved, it still asks: accepting now would store a baseline without the
    variable."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        leaf = {"metric": "range", "variable": "z", "min_value": 0, "max_value": 9}
        shell.session.stage([PatchOp(D, "add", "/metrics/children/-", leaf)])
        await _settled(lambda: "dstf-strip-amber" in _one(user, "strip").classes)
        assert _one(user, "strip-text").text == (
            "2 unsaved edits · verdict would change PASS → FAIL"
            " · 1 new variable: queue the test to score it"
        )
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "filled"}
        preview, discard = _one(user, "strip-preview"), _one(user, "strip-discard")
        assert preview.visible and "unelevated" not in preview.props
        assert "flat" in preview.props
        assert discard.visible and "flat" in discard.props
        assert page.strip.situation.value == "edits_new"
        shell.session.save()
        await _settled(lambda: not shell.session.spec.pending)
        with page.client:
            page.sync()
        assert _one(user, "strip-text").text == (
            "1 new variable the last run did not record: queue the test to score it."
        )
        assert page.strip.situation.value == "new_variables"
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "filled"}
        assert _none(user, "strip-preview")
        # An edit that names nothing new: Preview and save is filled again.
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1.0})])
        await _settled(lambda: page.strip.situation.value == "edits_new")
        shell.session.discard()
        shell.session.stage(
            [PatchOp(D, "remove", "/metrics/children/1", None)],
        )
        await _settled(lambda: page.strip.situation.value == "edits")
        assert "unelevated" in _one(user, "strip-preview").props
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}


async def test_the_strip_counts_nothing_new_for_a_tracked_variable(shell):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        leaf = {"metric": "range", "variable": "x", "min_value": 0, "max_value": 9}
        shell.session.stage([PatchOp(D, "add", "/metrics/children/-", leaf)])
        await _settled(lambda: "dstf-strip-amber" in _one(user, "strip").classes)
        assert "new variable" not in _one(user, "strip-text").text
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}


async def test_a_criterion_on_a_variable_the_file_has_is_scored_without_a_new_run(
    shell,
):
    """The owner's O2: where the result FILE holds the variable (the Python
    backend records everything), staging re-reads the model and scores it
    at once — the strip does not ask for the test to be queued."""
    path = shell.session.project.config.work_dir / "test_0004" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["variables"]["w"] = [0.5 for _ in payload["time"]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        leaf = {"metric": "range", "variable": "w", "min_value": 0, "max_value": 1}
        shell.session.stage([PatchOp(D, "add", "/metrics/children/-", leaf)])
        await _settled(lambda: "dstf-strip-amber" in _one(user, "strip").classes)
        assert _one(user, "strip-text").text == ("2 unsaved edits · verdict stays PASS")
        assert page.strip.situation.value == "edits"
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}


async def test_the_strips_discard_works_while_the_queue_runs_another_test(shell):
    """Nothing of an edit is refused because the queue is busy (D150): the
    strip's Discard stays live while another test runs, and drops this
    test's edits."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
        await _settled(lambda: page.strip.situation is Situation.EDITS)
        _hold(shell, B, ItemState.RUNNING)
        assert shell.running() and shell.busy_reason() is None
        button = page.strip.discard.button
        assert button.enabled and page.strip.situation is Situation.EDITS
        user.find(marker="strip-discard").click()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: page.strip.situation is Situation.PASS)


def _started(shell, model):
    """``model`` running, its start taken in as the Session takes one: the
    last result goes, the test is rescored and the pages are told."""
    item = fake_item(shell.session, model, ItemState.RUNNING)
    shell.session._item_started(item)
    shell.tick()
    return item


def _ended(shell, item, state: ItemState, *, result=None, message=None) -> None:
    """``item`` ended in ``state``, taken in as the Session takes an end."""
    item.state, item.result, item.message = state, result, message
    if item.started_at is not None:
        item.ended_at = item.started_at + 9.0
    shell.session._item_ended(item)
    shell.tick()


async def test_the_strip_follows_a_test_through_the_queue(shell):
    """Queued, running, ended: the strip says the test's place in the queue
    in grey, offers Remove and then Cancel as its own next action with
    Accept and Queue in the ⋮, and goes back to the verdict of the result
    the run brought."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        strip, session = page.strip, shell.session
        held = session.project.results[A]
        assert (strip.remove.visible, strip.cancel.visible) == (False, False)

        item = _hold(shell, A)
        assert strip.situation is Situation.QUEUED
        assert _one(user, "strip-text").text == "Queued — waiting · next"
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert (strip.remove.visible, strip.cancel.visible) == (True, False)
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}
        assert page.badge.text == "FAIL"  # its last result stands while it waits

        item.state, item.started_at, item.worker_id = ItemState.RUNNING, time.time(), 0
        session._item_started(item)
        shell.tick()
        assert strip.situation is Situation.RUNNING
        text = _one(user, "strip-text").text
        assert text.startswith("Running on worker 1 — ") and text.endswith(" s")
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert (strip.remove.visible, strip.cancel.visible) == (False, True)
        assert strip.cancel_why.visible is False
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "menu"}
        # Its run dropped the last result and every verdict with it — not
        # the page's layout (D151): its plots stay until its own arrives.
        assert page.badge.text == "NOT RUN"
        assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]
        assert page.body.props["data-unscored"] == "waiting for result"

        _ended(shell, item, ItemState.FINISHED, result=held)
        assert strip.situation is Situation.FAIL
        assert "dstf-strip-red" in _one(user, "strip").classes
        assert (strip.remove.visible, strip.cancel.visible) == (False, False)
        assert _strip_buttons(user) == {"accept": "filled", "enqueue": "quiet"}
        assert page.badge.text == "FAIL"
        assert [c.resolved.series for c in page.cards] == [("x",), ("y",)]


async def test_the_strip_of_a_cancelled_run_fills_queue_again(shell):
    """A cancelled run leaves no result — its start dropped the last one —
    and the strip says so; Queue is the next action, and says "again"."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        item = _started(shell, D)
        _ended(shell, item, ItemState.CANCELLED)
        assert page.strip.situation is Situation.CANCELLED
        assert _one(user, "strip-text").text == CANCELLED_SENTENCE
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert (page.strip.remove.visible, page.strip.cancel.visible) == (False, False)
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "filled"}
        assert _one(user, "action:enqueue").text == "Queue again"
        assert page.badge.text == "NOT RUN"
        # Its row cleared, the test is one with no result.
        shell.session.clear_finished()
        shell.tick()
        assert page.strip.situation is Situation.NO_RESULT
        assert _one(user, "action:enqueue").text == "Queue 1"


async def test_the_strip_of_a_test_no_worker_could_start_says_the_backends_message(
    shell,
):
    """No worker could start: the test never ran, its last result stands,
    and the strip says the backend's message in red with Queue filled."""
    message = "No Python worker could start — worker 0: licence server unreachable"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        item = _hold(shell, D)
        _ended(shell, item, ItemState.FAILED, message=message)
        assert page.strip.situation is Situation.START_FAILED
        assert _one(user, "strip-text").text == message
        assert "dstf-strip-red" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "menu", "enqueue": "filled"}
        assert _one(user, "action:enqueue").text == "Queue again"
        assert page.badge.text == "PASS"  # nothing ran: the result stands
        assert [c.resolved.series for c in page.cards] == [("x",)]


async def test_a_running_strip_says_why_it_has_no_cancel(shell, monkeypatch):
    """On a backend that cannot stop a test the strip draws the sentence
    saying so where Cancel would be."""
    why = "Stub cannot stop a running simulation: it runs to its end, or to its timeout"
    monkeypatch.setattr(
        type(shell.session), "cannot_cancel", property(lambda self: why)
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        _started(shell, D)
        assert page.strip.situation is Situation.RUNNING
        assert page.strip.cancel.visible is False
        assert _one(user, "strip-cancel:why").text == why
        assert _none(user, "strip-cancel")  # hidden: the simulation finds none


async def test_the_poll_moves_a_running_strips_seconds_and_nothing_else(shell):
    """Once a second while a test runs the shell says `poll`: the running
    test's strip says its seconds again, and the page syncs nothing."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        other = await _test_page(shell, user, A)  # a second tab, on another test
        item = _started(shell, D)
        synced: list[str] = []

        def watch(p: TestPage) -> None:
            sync = p.sync
            p.sync = lambda force=False: (synced.append(p.model_id), sync(force))[1]

        watch(page)
        watch(other)
        waiting = other.strip.text.text
        item.started_at = time.time() - 42
        for _ in range(10):  # a second of ticks: one poll
            shell.tick()
        assert page.strip.text.text in (
            "Running on worker 1 — 42 s",
            "Running on worker 1 — 43 s",
        )
        assert other.strip.text.text == waiting
        assert synced == []


async def test_a_queue_event_for_another_test_leaves_this_page_alone(shell):
    """A `queue` event names the tests whose place moved: another test's
    start and end repaint nothing of this page but its bar."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        synced: list[bool] = []
        sync = page.sync
        page.sync = lambda force=False: (synced.append(force), sync(force))[1]
        held = shell.session.project.results[B]
        item = _hold(shell, B)
        item.state, item.started_at, item.worker_id = ItemState.RUNNING, time.time(), 0
        shell.session._item_started(item)
        shell.tick()
        assert _one(user, "strip-text").text == "Passes against its baseline."
        assert page.strip.situation is Situation.PASS
        _ended(shell, item, ItemState.FINISHED, result=held)
        assert synced == []
        assert _one(user, "strip-text").text == "Passes against its baseline."


async def test_a_gone_test_leaves_the_strip_blank_and_a_returning_one_fills_it(
    shell, tmp_path
):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        shell.session.close()
        await _settled(lambda: page.gone)
        assert _one(user, "strip-text").text == ""
        assert page.strip.situation is None
        assert _none(user, "action:accept") and _none(user, "action:enqueue")
        cfg, work = _two_var_project(tmp_path / "again")
        shell.session.open(cfg, work_dir=work)
        await _settled(lambda: not page.gone)
        assert "dstf-strip-red" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "filled", "enqueue": "quiet"}


def _save_tree(shell, tree):
    shell.session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(tree))])
    shell.session.save()
    assert shell.session.spec.pending == []


NRMSE_X = {"metric": "nrmse", "variable": "x", "tolerance": 0.01}
#: x rises past 0.5: a range this low fails.
TOO_LOW_X = {"metric": "range", "variable": "x", "max_value": 0.5}


async def test_a_passing_strip_names_its_advisory_failure(shell):
    _save_tree(
        shell,
        {
            "combinator": "and",
            "children": [NRMSE_X, {"combinator": "warn", "children": [TOO_LOW_X]}],
        },
    )
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert _one(user, "strip-text").text == (
            "Passes against its baseline, with 1 advisory failure."
        )


async def test_a_failing_strip_counts_its_failing_criteria_of_all(shell):
    _save_tree(shell, {"combinator": "and", "children": [NRMSE_X, TOO_LOW_X]})
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert _one(user, "strip-text").text.startswith("Fails 1 of 2 criteria.")


def _scoring_raises_for_d(monkeypatch, when=lambda test: True):
    """`compare_one` raises for D's TestModels that ``when`` picks."""
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D and when(test):
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)


async def test_a_test_whose_scoring_raised_at_the_open_says_so_in_red(
    tmp_path, monkeypatch
):
    _scoring_raises_for_d(monkeypatch)
    cfg, work = _two_var_project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async()
        await _settled(lambda: shell.load.phase == "done" and shell.load.failed_models)
        await _test_page(shell, user, D)
        assert _one(user, "strip-text").text == (
            "Scoring failed; the tree card says why."
        )
        assert "dstf-strip-red" in _one(user, "strip").classes


async def test_a_saved_verdict_that_raises_does_not_stop_the_page(
    shell, monkeypatch, caplog
):
    """The strip scores the SAVED spec beside the edited one; that scoring
    raising leaves the page following the edit — its cards and its strip —
    and the strip says the saved verdict failed."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        # The saved entry has no comparison tolerance; the edit gives one.
        _scoring_raises_for_d(
            monkeypatch, when=lambda t: t.comparison_tolerance is None
        )
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        await user.should_see("5.000e-01", retries=20)  # the cards followed
        assert _one(user, "strip-text").text == (
            "1 unsaved edit · verdict would change scoring failed → PASS"
        )
        assert "a page failed" not in caplog.text


async def test_scoring_that_raised_after_the_open_is_not_waiting(shell, monkeypatch):
    """A reload after the open (an Accept's) whose scoring of D raises: the
    load is done, so D is not waiting to be scored."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        _scoring_raises_for_d(monkeypatch)
        with pytest.raises(RuntimeError):
            shell.session._reload_results()
        await _settled(
            lambda: _one(user, "tree:summary").text.endswith("scoring failed")
        )
        assert _one(user, "strip-text").text == (
            "Scoring failed; the tree card says why."
        )


async def test_an_unscored_test_waits_while_the_project_is_read(shell):
    """While the load is in progress a test with no comparison may have no
    result read yet: it waits for the load, and is not "No result yet"."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        shell.load.phase = "results"
        del shell.session.comparisons[D]
        del shell.session.project.results[D]
        with page.client:
            page.sync()
        assert _one(user, "strip-text").text == "Scoring…"


async def test_a_read_but_unscored_test_waits_while_the_project_is_read(shell):
    """Its result is read and its scoring is not: while the load is in
    progress it waits, and is not "Scoring failed"."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        shell.load.phase = "results"
        del shell.session.comparisons[D]
        with page.client:
            page.sync()
        assert _one(user, "strip-text").text == "Scoring…"


async def test_the_badge_of_a_test_not_scored_yet_wears_scoring_not_queued(shell):
    """The header's badge of a test with no verdict yet reads `…` under the
    SCORING class. `queued` is the simulation queue's (D150): a badge
    wearing it says the test waits for a worker."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        assert page.badge.text == "PASS"
        shell.load.phase = "results"
        del shell.session.comparisons[D]
        with page.client:
            page.sync()
        assert page.badge.text == "…"
        assert page.badge._style["background-color"] == "var(--dstf-scoring-fill)"


def _strip_raises(monkeypatch) -> None:
    from dstf.authoring.shell import test_page as test_page_mod

    def boom(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(test_page_mod, "strip_for", boom)


async def test_a_strip_that_raises_does_not_stop_the_sync(shell, monkeypatch, caplog):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        _strip_raises(monkeypatch)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        await user.should_see("5.000e-01", retries=20)  # the cards followed
        assert "the strip of" in caplog.text
        assert "a page failed" not in caplog.text


async def test_a_bar_that_raises_does_not_stop_the_sync(shell, monkeypatch, caplog):
    """The strip's paint refreshes its bar, and the except branch refreshes
    it again: a bar that raises there too (a capability whose `emphasis` is
    not a dict) is logged, and the cards still follow."""
    real = ActionBar.refresh

    def boom(self):
        if self.placement is Placement.TEST:
            raise ValueError("bar boom")
        real(self)

    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        monkeypatch.setattr(ActionBar, "refresh", boom)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        await user.should_see("5.000e-01", retries=20)  # the cards followed
        assert "the bar of" in caplog.text
        assert "a page failed" not in caplog.text


async def test_a_strip_that_raises_still_refreshes_the_bar(shell, monkeypatch):
    """The strip's paint refreshes the bar; when it raises, the bar is
    refreshed anyway: a test the queue took greys its Queue."""
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert _one(user, "action:enqueue").enabled
        _strip_raises(monkeypatch)
        _hold(shell, D)
        assert not _one(user, "action:enqueue").enabled
        assert _one(user, "action:enqueue:why").text == f"{D} is already queued"


async def test_one_results_event_refreshes_the_strips_bar_once(shell, monkeypatch):
    """A `results` event for this test syncs, and the sync's paint refreshes
    the bar: `handle` does not refresh it a second time."""
    refreshes: list[str] = []
    real = ActionBar.refresh

    def counting(self):
        if self.marker == "test-actions":
            refreshes.append(self.marker)
        return real(self)

    monkeypatch.setattr(ActionBar, "refresh", counting)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await asyncio.sleep(0.3)
        refreshes.clear()
        with page.client:
            page.handle({"event": "results", "models": {D}})
        assert len(refreshes) == 1
        refreshes.clear()
        with page.client:
            page.handle({"event": "results", "models": {A}})  # not this test's
        assert len(refreshes) == 1


async def test_the_strips_discard_says_it_is_this_tests(shell):
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
        await _settled(lambda: not _none(user, "strip-discard"))
        assert _one(user, "strip-discard").text == "Discard"
        assert _tooltip_text(_one(user, "strip-discard")) == (
            "Discards this test's edits; the header's Discard drops every test's."
        )


# -- a criterion's + adds the new one right after it (Q3 A) -----------------


async def test_plus_on_a_criterion_adds_right_after_it_and_opens_its_editor(
    page_shell,
):
    """On A's `and[tube·x, range·x, range·y]`, `+` on the tube's row and Add
    stage the new criterion at child 1 — right after the tube — and its
    editor is open under its new row."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_form(user, f"add:{TUBE}", within=f"node:0:{TUBE}")
        user.find(marker=f"apply-add:{TUBE}").click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        (op,) = shell.session.spec.pending
        assert (op.op, op.path) == ("add", "/metrics/children/1")
        await _settled(lambda: page.open == (0, "/metrics/children/1"))
        editor = _one(user, "editor:0:/metrics/children/1")
        assert any("dstf-editor" in e.classes for e in [editor, *_ancestors(editor)])


# -- a combinator's kind is a dropdown in its row (Q3 A) --------------------


async def _open_tree(user):
    user.find(marker="tree:toggle").click()
    await _settled(lambda: not _none(user, "tree-row:/metrics"))


async def test_the_root_rows_kind_is_a_dropdown_and_its_text_the_count(page_shell):
    """A's root `and` of three: the kind is a dropdown between the pill and
    the count; unwrap is not offered (a root of several has no parent to
    take its children); `or` stages the kind and nothing else."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        kind = _one(user, "kind:/metrics")
        assert kind.value == "and"
        assert kind.props.get("aria-label") == "Combinator kind"
        row = _one(user, "tree-row:/metrics")
        lead = kind.parent_slot.parent
        assert "dstf-node-lead" in lead.classes and lead.parent_slot.parent is row
        children = list(row.default_slot.children)
        # The bars, the chevron's place, the grip's place, the pill, then
        # the lead: right after the pill (E4).
        assert "dstf-grip" in children[2].classes
        assert children.index(lead) == 4
        assert _one(user, "tree-text:/metrics").text == "3 criteria"
        assert list(kind.options) == ["and", "or", "k-of-n", "warn", "weighted"]
        assert _none(user, "edit:/metrics")  # an `and` has no fields
        kind.set_value("or")
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        (op,) = shell.session.spec.pending
        assert (op.op, op.path, op.value) == ("replace", "/metrics/combinator", "or")
        await _settled(lambda: _one(user, "kind:/metrics").value == "or")
        assert _one(user, "tree-text:/metrics").text == "3 criteria"


async def test_choosing_k_of_n_opens_the_form_and_stages_nothing_until_apply(
    page_shell,
):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        _one(user, "kind:/metrics").set_value("k-of-n")
        await _settled(lambda: not _none(user, "form-title:tree:/metrics"))
        assert _one(user, "form-title:tree:/metrics").text == "Edit this combinator"
        assert shell.session.spec.pending == []
        form = _one(user, "form-row:tree:/metrics")
        labels = {
            e.props.get("label")
            for e in user.find(kind=ui.number).elements
            if form in list(_ancestors(e))
        }
        assert labels == {"k — how many must pass"}
        assert "Combinator" not in {
            e.props.get("label") for e in user.find(kind=ui.select).elements
        }
        assert _one(user, "kind:/metrics").value == "and"  # nothing staged
        user.find(marker="apply-edit:/metrics").click()
        await _settled(lambda: len(shell.session.spec.pending) == 2)
        assert shell.session.tree(A)["combinator"] == "k-of-n"
        assert shell.session.tree(A)["k"] == 1


async def test_warn_on_a_three_child_root_is_refused_in_the_row(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        kind = _one(user, "kind:/metrics")
        kind.set_value("warn")
        await _settled(lambda: not _none(user, "error:edit-tree"))
        errors = [
            e.text
            for e in user.find(marker="error:edit-tree").elements
            if e.visible and _one(user, "tree:rows") in list(_ancestors(e))
        ]
        assert errors == ["'warn' combinator requires exactly 1 child, got 3"]
        assert shell.session.spec.pending == []
        assert kind.value == "and"


async def test_unwrap_from_the_dropdown_puts_the_child_in_its_place(page_shell):
    shell = page_shell
    _stage_tree(shell, A, {"combinator": "and", "children": [_range(-1)]})
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        kind = _one(user, "kind:/metrics")
        assert kind.options["unwrap…"] == "unwrap — its children take its place"
        kind.set_value("unwrap…")
        await _settled(lambda: "combinator" not in shell.session.tree(A))
        assert shell.session.tree(A)["metric"] == "range"


async def test_a_k_of_n_node_keeps_its_edit_icon_with_no_kind_select(page_shell):
    shell = page_shell
    _stage_tree(
        shell, A, {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(5)]}
    )
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        assert _one(user, "tree-text:/metrics").text == "k=1 · 2 criteria"
        _tree_click(user, "/metrics")  # its row opens its fields
        await _settled(lambda: not _none(user, "apply-edit:/metrics"))
        assert "Combinator" not in {
            e.props.get("label") for e in user.find(kind=ui.select).elements
        }
        assert _none(user, "unwrap-in-edit:/metrics")


async def test_the_weighted_forms_threshold_hint_follows_direction(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_tree(user)
        _one(user, "kind:/metrics").set_value("weighted")
        await _settled(lambda: not _none(user, "threshold-hint"))
        assert _one(user, "threshold-hint").text == (
            "Passes when Σ weight × score over the children is below the "
            "threshold (lower is better)."
        )
        (direction,) = [
            e
            for e in user.find(kind=ui.select).elements
            if e.props.get("label") == "Direction"
        ]
        with page.client:
            direction.set_value("greater")
        await _settled(lambda: "above" in _one(user, "threshold-hint").text)
        assert _one(user, "threshold-hint").text == (
            "Passes when Σ weight × score over the children is above the "
            "threshold (higher is better)."
        )


async def test_a_row_whose_host_cannot_draw_reads_its_kind_in_the_row_text(
    no_tree_shell,
):
    """A host that cannot draw (Edit tree on a test whose tree the Session
    will not materialize) leaves the lead empty, and the row reads the
    kind: it never disappears."""
    shell = no_tree_shell
    shell.session.stage([SHADOW_X])
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        assert _one(user, "tree-text:/metrics").text == "2 criteria"
        assert not _none(user, "kind:/metrics")
        shell.session.discard()
        await _settled(
            lambda: _one(user, "tree-text:/metrics").text == "and · 2 criteria"
        )
        assert _none(user, "kind:/metrics")
        shell.session.stage([SHADOW_X])
        await _settled(lambda: _one(user, "tree-text:/metrics").text == "2 criteria")
        assert not _none(user, "kind:/metrics")


# -- the plot's +, inline; the editor opens after Add (Q3 A) ----------------


async def test_the_plots_plus_opens_the_add_row_inline_and_the_editor_after_add(
    page_shell,
):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        header = _one(user, "criteria-header:0")
        assert _one(user, "plot:0") in list(_ancestors(header))
        assert "Criteria" in [
            e.text for e in header.descendants() if isinstance(e, ui.label)
        ]
        (plus,) = _inside(user, "add-for:x", "criteria-header:0")
        assert _plus_tips(plus) == ["Add a criterion for x"]
        assert _inside(user, "variable:0", "criteria-header:0")
        _click_in(user, "add-for:x", within="criteria-header:0")
        await _settled(lambda: not _none(user, "form-row:plot:0"))
        assert _one(user, "form-title:plot:0").text == "Add a criterion for x"
        # A row under the header, above the criteria — not a dialog.
        form = _one(user, "form-row:plot:0")
        assert _one(user, "plot:0") in list(_ancestors(form))
        assert not [
            e
            for e in page.client.elements.values()
            if isinstance(e, ui.dialog) and e.value
        ]
        assert _one(user, "apply-add:x:variable").value == "x"
        user.find(marker="apply-add:x").click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        (op,) = shell.session.spec.pending
        assert (op.op, op.path) == ("add", "/metrics/children/-")
        await _settled(lambda: page.open == (0, "/metrics/children/3"))
        editor = _one(user, "editor:0:/metrics/children/3")
        assert any("dstf-editor" in e.classes for e in [editor, *_ancestors(editor)])
        assert _none(user, "form-row:plot:0")


WEIGHTED_ROOT = {
    "combinator": "weighted",
    "weights": [1.0, 1.0],
    "threshold": 0.5,
    "children": [_range(-1), _range(5)],
}


async def test_the_plots_plus_is_grey_under_a_weighted_root(page_shell):
    """A weighted root takes no child alone: the `+` is grey with the
    reason. (A `warn` root cannot be staged at all — the validator: a tree
    of warnings alone can never fail.)"""
    shell = page_shell
    _stage_tree(shell, A, WEIGHTED_ROOT)
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        (plus,) = _inside(user, "add-for:x", "criteria-header:0")
        assert not plus.enabled
        assert _plus_tips(plus) == [WEIGHTED_TIP]
        assert plus.props["aria-label"] == WEIGHTED_TIP


async def test_the_plots_plus_follows_the_roots_kind(page_shell):
    """The `+` reads the root's kind when it is drawn, and the card's shape
    does not hold the root's kind: staged to weighted elsewhere, the `+`
    greys without the card being rebuilt, and back to `and` it returns."""
    shell = page_shell
    _stage_tree(shell, A, {"combinator": "and", "children": [_range(-1), _range(5)]})
    to_weighted = [
        PatchOp(A, "replace", "/metrics/combinator", "weighted"),
        PatchOp(A, "add", "/metrics/weights", [1.0, 1.0]),
        PatchOp(A, "add", "/metrics/threshold", 0.5),
    ]
    to_and = [
        PatchOp(A, "replace", "/metrics/combinator", "and"),
        PatchOp(A, "remove", "/metrics/weights"),
        PatchOp(A, "remove", "/metrics/threshold"),
    ]

    def plus(user):
        (found,) = _inside(user, "add-for:x", "criteria-header:0")
        return found

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        card = page.cards[0]
        assert plus(user).enabled
        shell.session.apply(Patch(ops=to_weighted))
        await _settled(lambda: not plus(user).enabled)
        assert _plus_tips(plus(user)) == [WEIGHTED_TIP]
        shell.session.apply(Patch(ops=to_and))
        await _settled(lambda: plus(user).enabled)
        assert page.cards[0] is card  # repainted, never rebuilt


# -- the Part 2 review's corrections ------------------------------------------


async def test_the_plus_on_a_lower_plot_opens_the_editor_on_that_plot(page_shell):
    """Plot 1 is y's: its `+` then Add opens the new criterion's editor on
    plot 1, where it was asked — not on plot 0, above the click."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, "add-for:y", within="criteria-header:1")
        await _settled(lambda: not _none(user, "form-row:plot:1"))
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: page.open is not None)
        assert page.open == (1, "/metrics/children/3")
        assert not _none(user, "editor:1:/metrics/children/3")


async def test_a_criteria_rows_plus_on_a_lower_plot_opens_the_editor_there(
    page_shell,
):
    """`+` on range·y's row of plot 1 and Add open the new criterion's
    editor on plot 1, under the row the click was on."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_form(user, f"add:{RANGE_Y}", within=f"node:1:{RANGE_Y}")
        user.find(marker=f"apply-add:{RANGE_Y}").click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: page.open is not None)
        assert page.open == (1, "/metrics/children/3")


async def test_the_plots_plus_for_another_variable_opens_where_it_is_drawn(
    page_shell,
):
    """Plot 0 draws x alone: choosing y in its form lands the criterion on
    a plot that draws y — the first card that holds the row."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, "add-for:x", within="criteria-header:0")
        await _settled(lambda: not _none(user, "form-row:plot:0"))
        with page.client:
            _one(user, "apply-add:x:variable").set_value("y")
        user.find(marker="apply-add:x").click()
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: page.open is not None)
        assert page.open == (1, "/metrics/children/3")


def test_a_panel_with_no_lead_place_raises():
    from dstf.authoring.shell.actions import ShellPanel, _NoFigure

    panel = ShellPanel(lambda **_: True, _NoFigure())
    with pytest.raises(RuntimeError, match="no lead place"):
        panel.lead(lambda: None)


async def test_a_lead_drawn_twice_holds_only_the_second(shell):
    """The lead place is emptied before it is drawn into: a host rebuilt
    over the same row draws ONE kind dropdown there, not a second beside
    the first."""
    from dstf.authoring.shell.actions import ShellPanel, _NoFigure

    spans: list = []

    def root() -> None:
        span = ui.element("span")
        panel = ShellPanel(lambda **_: True, _NoFigure(), lead=span)
        panel.lead(lambda: ui.label("first"))
        panel.lead(lambda: ui.label("second"))
        spans.append(span)

    async with user_simulation(root=root) as user:
        await user.open("/")
        (span,) = spans
        assert [e.text for e in span.default_slot.children] == ["second"]


async def test_a_leafs_tree_row_has_no_lead_place(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)

        def leads(path: str) -> int:
            row = _one(user, f"tree-row:{path}")
            return sum("dstf-node-lead" in e.classes for e in row.descendants())

        assert leads("/metrics") == 1
        assert [leads(p) for p in (TUBE, RANGE_X, RANGE_Y)] == [0, 0, 0]


async def test_a_greater_weighted_nodes_hint_says_so_at_once(page_shell):
    """The hint starts from the node's own direction, before any Direction
    change: a `greater` node's form read "below the threshold"."""
    shell = page_shell
    _stage_tree(shell, A, {**WEIGHTED_ROOT, "direction": "greater"})
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        _tree_click(user, "/metrics")  # its row opens its fields
        await _settled(lambda: not _none(user, "apply-edit:/metrics"))
        assert _one(user, "threshold-hint").text == (
            "Passes when Σ weight × score over the children is above the "
            "threshold (higher is better)."
        )


async def test_a_tree_rows_icons_are_named_by_their_tooltips(page_shell):
    """A screen reader reads an icon button by its name, and the icon's
    ligature ("add", "tune") names nothing: each is named by its tooltip."""
    shell = page_shell
    _stage_tree(
        shell, A, {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(5)]}
    )
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        leaf = "/metrics/children/0"
        buttons = [
            _inside(user, marker, "tree:rows")[0]
            for marker in (
                "add:/metrics",
                "wrap:/metrics",
                f"add:{leaf}",
                f"wrap:{leaf}",
                f"remove:{leaf}",
            )
        ]
        for button in buttons:
            (tip,) = _plus_tips(button)
            assert button.props.get("aria-label") == tip
        assert _none(user, "edit:/metrics")  # its row's click opens its fields


async def test_the_criteria_header_wraps_a_long_why(no_tree_shell):
    """Where nothing can be added the header's `+` gives way to the reason,
    and the Session's for a tree it will not materialize is longer than a
    card beside a drawer at 900 px: the header wraps rather than push the
    card sideways."""
    shell = no_tree_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        header = _one(user, "criteria-header:0")
        assert _inside(user, "why:edit-tree", "criteria-header:0")
        assert "no-wrap" not in header.classes


def test_the_drawers_controls_are_the_tabs():
    """Every control of both drawers lives in `TabState`, with these
    defaults: a switch of test builds new drawers, and they start from
    it."""
    from dstf.authoring.shell.tabstate import TabState

    t = TabState()
    assert (t.tests_needle, t.tests_regex, t.tests_case) == ("", False, False)
    assert (t.tests_sort, t.tests_grouped, t.tests_short) == ("package", True, True)
    assert t.tests_pills == set()
    assert (t.vars_needle, t.vars_regex, t.vars_case) == ("", False, False)
    assert (t.vars_group, t.vars_names, t.vars_sort) == ("component", "both", "file")
    assert TabState().tests_pills is not t.tests_pills


def _var_rows(user) -> list[list[str]]:
    """The labels of each of the Variables drawer's rows, in order."""
    drawer = _one(user, "drawer:variables")
    return [
        _labels_in(_one(user, m)) for m in _marks(drawer) if m.startswith("var-row:")
    ]


#: A BranchingDynamicPipes-shaped UnitTests expression: its head says what
#: it is, its tail which element (Q20).
CAT3 = "cat(1, pipe2.mediums.T, pipe2.mediums.p)[3]"


def test_a_label_splits_at_its_last_index_or_twelve_from_its_end():
    assert middle_split(CAT3) == ("cat(1, pipe2.mediums.T, pipe2.mediums.p)", "[3]")
    assert middle_split("pipe.medium[2].T[10]") == ("pipe.medium[2].T", "[10]")
    assert middle_split("condenser.heatTransfer.Q_flow_total") == (
        "condenser.heatTransfer.",
        "Q_flow_total",
    )
    assert middle_split("x") == ("", "x")
    # An index more than twelve from the end is no tail: the tail from it
    # took the width and hid the head.
    assert middle_split("volume[1].medium.state.phase.somethingLonger.T") == (
        "volume[1].medium.state.phase.somet",
        "hingLonger.T",
    )
    assert middle_split("abs(a[1] - b.c.d.e.f.g.h.i.j.k.l.m.n.o)") == (
        "abs(a[1] - b.c.d.e.f.g.h.i.",
        "j.k.l.m.n.o)",
    )
    assert middle_split("a.b[1234567890]") == ("a.b", "[1234567890]")
    assert middle_split("a.b[12345678901]") == ("a.b[", "12345678901]")


async def test_variable_labels_are_cut_in_the_middle_the_switcher_is_not(
    drawer_shell,
):
    """Q20: a card's title, a criteria row's name and a Variables-drawer
    row read a variable through a `MiddleCut` — the head, the tail from the
    last `[`, the tail's width as `--tail`, the whole text as its title and
    as its `.text`. The Focus switcher's options stay plain text (C4)."""
    from test_session import with_expressions

    shell = drawer_shell
    with_expressions(shell.session, A, {"x": CAT3})
    shell.session._score(A)
    async with user_simulation(root=shell.index) as user:
        page = await _drawer(shell, user)
        title = _one(user, "plot-title:0")
        assert isinstance(title, MiddleCut)
        assert (title.text, title.props["title"]) == (CAT3, CAT3)
        assert (title.head.text, title.tail.text) == (
            "cat(1, pipe2.mediums.T, pipe2.mediums.p)",
            "[3]",
        )
        assert title.style["--tail"] == "3ch"
        (row,) = page.cards[0].rows.values()
        assert isinstance(row.name, MiddleCut)
        assert (row.name.text, row.name.tail.text) == (f"nrmse · {CAT3}", "[3]")
        (text,) = [
            e
            for e in _one(user, var_marker("var-row", ROW_X)).descendants()
            if isinstance(e, MiddleCut)
        ]
        assert "dstf-var-text" in text.classes
        assert (text.text, text.tail.text) == (CAT3, "[3]")
        # the drawer's title says the muted name too, as it did
        assert text.props["title"] == f"{CAT3} (x)"
        with page.client:
            page.focus(0)
        assert list(_one(user, "focus-switch").options.values())[0] == CAT3


class TestVariablesDrawerControls:
    """The Variables drawer's sort, group and names menus (the owner's
    ruling Q7), each the tab's."""

    async def test_group_and_sort(self, drawer_shell):
        async with user_simulation(root=drawer_shell.index) as user:
            page = await _drawer(drawer_shell, user)
            await _pick(user, "var-group-by", "flat")
            await _settled(lambda: _groups_shown(user) == ["var-group:1-All-variables"])
            assert page.state.vars_group == "flat"
            flat = [labels[0] for labels in _var_rows(user)]
            assert flat[:2] == ["x", "y"]  # result-file order
            await _pick(user, "var-sort", "name")
            await _settled(
                lambda: (
                    [labels[0] for labels in _var_rows(user)]
                    == sorted(flat, key=lambda t: (t.casefold(), t))
                )
            )
            assert page.state.vars_sort == "name"
            await _pick(user, "var-group-by", "tracked")
            await _settled(
                lambda: (
                    _groups_shown(user)
                    == ["var-group:0-Tracked", "var-group:1-Other-variables"]
                )
            )
            item = _one(user, "var-group-by:tracked")
            assert item.props["role"] == "menuitemradio"
            assert item.props["aria-checked"] == "true"

    async def test_the_names_menu_reads_expression_file_name_or_both(
        self, awkward_expression_shell
    ):
        from test_session import AWKWARD_EXPRESSION, UT1

        shell = awkward_expression_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)

            def ut1_row() -> list[str]:
                (labels,) = [
                    labels
                    for labels in _var_rows(user)
                    if AWKWARD_EXPRESSION in labels or UT1 in labels
                ]
                return labels

            assert ut1_row() == [AWKWARD_EXPRESSION, UT1]  # both: today's row
            await _pick(user, "var-names", "file")
            await _settled(lambda: ut1_row() == [UT1])
            assert page.state.vars_names == "file"
            await _pick(user, "var-names", "expression")
            await _settled(lambda: ut1_row() == [AWKWARD_EXPRESSION])
            # The filter still asks the name and the label whatever is shown.
            user.find(marker="var-needle").type("unitTests")
            await _settled(lambda: ut1_row() == [AWKWARD_EXPRESSION])

    async def test_expression_names_keep_the_name_beside_a_label_two_share(
        self, drawer_shell
    ):
        """The one label rule (`variable_texts`): `x={a.Q, a.Q}` reads "a.Q"
        twice, so under Expression each keeps its result-file name muted
        beside it, as Both shows it; a label no other row shares reads
        alone. The scope is the test's, not the drawn rows': with y
        filtered away, x still keeps its name."""
        from test_session import with_expressions

        shell = drawer_shell
        with_expressions(shell.session, A, {"x": "a.Q", "y": "a.Q"})
        shell.session._score(A)
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            await _pick(user, "var-names", "expression")
            await _settled(lambda: page.state.vars_names == "expression")
            rows = {
                r: _labels_in(_one(user, var_marker("var-row", r)))
                for r in (ROW_X, ROW_Y, ROW_Z)
            }
            assert rows == {
                ROW_X: ["a.Q", "x"],
                ROW_Y: ["a.Q", "y", "plot 2"],
                ROW_Z: ["z"],
            }
            await _pick(user, "var-names", "file")
            await _settled(
                lambda: _labels_in(_one(user, var_marker("var-row", ROW_X))) == ["x"]
            )
            await _pick(user, "var-names", "expression")
            await _settled(lambda: page.state.vars_names == "expression")
            user.find(marker="var-regex").click()
            user.find(marker="var-needle").type("^x$")
            await _settled(lambda: _none(user, var_marker("var-row", ROW_Y)))
            assert _labels_in(_one(user, var_marker("var-row", ROW_X))) == ["a.Q", "x"]

    async def test_expression_names_keep_the_name_where_two_drawn_rows_read_alike(
        self, drawer_shell
    ):
        """The one label rule over the DRAWER's rows: x's expression is
        `z`, and the result has an untracked variable named `z` that no
        plot or criterion holds — outside the test's scope, so
        `variable_texts` sees no collision. The drawer draws both reading
        "z": x keeps its name muted beside it; `z`, whose label is its
        name, reads alone."""
        from test_session import with_expressions

        shell = drawer_shell
        with_expressions(shell.session, A, {"x": "z"})
        shell.session._score(A)
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            await _pick(user, "var-names", "expression")
            await _settled(lambda: page.state.vars_names == "expression")
            rows = {
                r: _labels_in(_one(user, var_marker("var-row", r)))
                for r in (ROW_X, ROW_Y, ROW_Z)
            }
            assert rows == {ROW_X: ["z", "x"], ROW_Y: ["y", "plot 2"], ROW_Z: ["z"]}
            # filtered to x alone, nothing drawn reads like it
            user.find(marker="var-needle").type("x")
            await _settled(lambda: _none(user, var_marker("var-row", ROW_Z)))
            assert _labels_in(_one(user, var_marker("var-row", ROW_X))) == ["z"]

    async def test_expression_names_count_the_members_an_opened_family_draws(
        self, drawer_shell
    ):
        """x={pipe.T[1]} beside the result's own pipe.T[1:3]: x is a row of
        its own — rows fold by NAME, as the plots do — reading "pipe.T[1]"
        alone while the family is shut; opened, the result's pipe.T[1] reads
        "pipe.T[1]" too, and x keeps its name."""
        from test_session import with_expressions

        shell = drawer_shell
        with_expressions(shell.session, A, {"x": "pipe.T[1]"})
        shell.session._score(A)
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            await _pick(user, "var-names", "expression")
            await _settled(lambda: page.state.vars_names == "expression")
            row = var_marker("var-row", ROW_X)
            await _settled(lambda: _labels_in(_one(user, row)) == ["pipe.T[1]"])
            assert _labels_in(_one(user, var_marker("var-row", ROW_PIPE))) == [
                "pipe.T[1:3]"
            ]
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            await _settled(
                lambda: not _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))
            )
            assert _labels_in(_one(user, row)) == ["pipe.T[1]", "x"]
            result = _one(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))
            assert _labels_in(result) == ["pipe.T[1]"]

    async def test_an_opened_family_stays_open_across_a_switch_of_group(
        self, drawer_shell
    ):
        """A family is opened by its key and whether it is tracked, not by
        its row's id, which carries the group: under Flat, pipe.T[] is
        still open."""
        async with user_simulation(root=drawer_shell.index) as user:
            await _drawer(drawer_shell, user)
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            await _settled(
                lambda: not _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))
            )
            await _pick(user, "var-group-by", "flat")
            flat = "1/All variables/pipe.T[]"
            await _settled(
                lambda: not _none(user, var_marker("var-row", f"{flat}/{PIPE[0]}"))
            )
            assert _one(user, var_marker("var-open", flat)).props["aria-expanded"] == (
                "true"
            )

    async def test_an_opened_family_stays_open_under_a_filter_that_narrows_it(
        self, drawer_shell
    ):
        """A filter that keeps two of pipe.T[1:3] changes the family's
        members, not the family: it stays open, over what the filter kept."""
        async with user_simulation(root=drawer_shell.index) as user:
            page = await _drawer(drawer_shell, user)
            user.find(marker=var_marker("var-open", ROW_PIPE)).click()
            await _settled(
                lambda: not _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[0]}"))
            )
            user.find(marker="var-regex").click()
            user.find(marker="var-needle").type(r"pipe\.T\[[12]\]")
            await _settled(lambda: page.state.vars_needle == r"pipe\.T\[[12]\]")
            await _settled(lambda: _none(user, var_marker("var-row", ROW_X)))
            opener = _one(user, var_marker("var-open", ROW_PIPE))
            assert opener.props["aria-expanded"] == "true"
            assert not _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[1]}"))
            assert _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[2]}"))

    async def test_a_tracked_family_and_the_results_own_open_apart(self, tmp_path):
        """A tracks pipe.T[1:2]; the result carries pipe.T[3:4] besides. Both
        fold to `pipe.T[]`, one tracked and one not: opening the tracked
        one leaves the result's closed."""
        from test_session import _drawer_project

        cfg, work = _drawer_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["pipe.T[4]"] = [9 * v for v in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == A:
                entry["variables"] = ["x", "y", *PIPE[:2]]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        shell = _opened(cfg, work)
        tracked = "0/Tracked/pipe.T[]"
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            user.find(marker=var_marker("var-open", tracked)).click()
            await _settled(
                lambda: not _none(user, var_marker("var-row", f"{tracked}/{PIPE[0]}"))
            )
            opener = _one(user, var_marker("var-open", ROW_PIPE))
            assert opener.props["aria-expanded"] == "false"
            assert _none(user, var_marker("var-row", f"{ROW_PIPE}/{PIPE[2]}"))

    async def test_the_menus_survive_a_switch_of_test(self, drawer_shell):
        async with user_simulation(root=drawer_shell.index) as user:
            page = await _drawer(drawer_shell, user)
            await _pick(user, "var-group-by", "flat")
            await _pick(user, "var-sort", "name")
            await _pick(user, "var-names", "file")
            await _settled(lambda: page.state.vars_names == "file")
            await _test_page(drawer_shell, user, B)
            assert _page(drawer_shell, user) is not page
            assert _one(user, "var-group-by:flat").props["aria-checked"] == "true"
            assert _one(user, "var-sort:name").props["aria-checked"] == "true"
            assert _one(user, "var-names:file").props["aria-checked"] == "true"
            assert _groups_shown(user) == ["var-group:1-All-variables"]


# -- the drawer: two-line rows, drag to resize (U2, Task 6) ----------------------


async def test_a_resized_drawer_sets_the_drawer_the_panels_and_the_tab(extras_shell):
    """K5 on the server: the release's width (`Rail.resize`) is the drawer's
    `width` prop with the rail, every panel's width and the tab's — a new
    page's drawer opens at it; below 240 px it is floored, and above 60% of
    the window, or the window less the rail and 480 px for the plots, it is
    held there; a closed drawer is the rail alone and the grip goes with the
    panel."""
    from dstf.authoring.shell.rail import MIN_PANEL, RAIL

    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        rail = page.rail
        assert page.state.drawer_width == 320
        rail.open("variables")
        assert rail.grip.visible
        assert rail.drawer.props["width"] == str(RAIL + 320)
        rail.resize(517.4, 1280)
        assert page.state.drawer_width == 517
        assert rail.drawer.props["width"] == str(RAIL + 517)
        # the grip is a focusable separator whose value is the width
        grip = rail.grip.props
        assert (grip["role"], grip["tabindex"]) == ("separator", "0")
        assert (grip["aria-valuemin"], grip["aria-valuenow"]) == (str(MIN_PANEL), "517")
        assert all(p._style.get("width") == "517px" for p in rail.panels.values())
        await _test_page(shell, user, B)
        other = _page(shell, user)
        assert other is not page and other.state.drawer == "variables"
        assert other.rail.drawer.props["width"] == str(RAIL + 517)
        assert other.rail.panels["variables"]._style.get("width") == "517px"
        other.rail.resize(12, 1280)
        assert other.state.drawer_width == MIN_PANEL
        other.rail.resize(3000, 2000)  # 60% of the window
        assert other.state.drawer_width == 1200
        other.rail.resize(2000, 1280)  # 1280 - 40 - 480 < 60% of it
        assert other.state.drawer_width == 760
        other.rail.resize(2000, 900)  # 900 - 40 - 480 for the plots
        assert other.state.drawer_width == 380
        other.rail.resize(2000, 600)  # the floor wins
        assert other.state.drawer_width == MIN_PANEL
        other.rail.resize("not a number", 1280)
        assert other.state.drawer_width == MIN_PANEL
        other.rail.resize(300, None)
        assert other.state.drawer_width == MIN_PANEL
        other.rail.open(None)
        assert other.rail.drawer.props["width"] == str(RAIL)
        assert not other.rail.grip.visible


async def test_a_drawer_rows_label_is_alone_on_its_first_line(extras_shell):
    """2026-09-29: line 1 holds the controls and the label, nothing after
    it; line 2 "plot N" and "+ new plot"."""
    from dstf.authoring.shell.widgets import MiddleCut

    shell = extras_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.rail.open("variables")
        (row,) = user.find(marker=var_marker("var-row", "2/(top level)/w")).elements
        line1, line2 = row.default_slot.children
        assert "dstf-var-line1" in line1.classes and "dstf-var-line2" in line2.classes
        assert isinstance(line1.default_slot.children[-1], MiddleCut)
        texts = [e.text for e in line2.descendants() if hasattr(e, "text")]
        assert "plot 4" in texts and "+ new plot" in texts


async def test_a_drawer_row_with_nothing_but_new_plot_is_one_line(drawer_shell):
    """K6: a second line holding only the hover-only "+ new plot" collapses;
    the `+` sits at line 1's right, after the label."""
    from dstf.authoring.shell.widgets import MiddleCut

    async with user_simulation(root=drawer_shell.index) as user:
        await _drawer(drawer_shell, user)
        (row,) = user.find(marker=var_marker("var-row", ROW_A0)).elements
        (line1,) = row.default_slot.children
        assert "dstf-var-line1" in line1.classes
        kids = line1.default_slot.children
        at = next(i for i, e in enumerate(kids) if isinstance(e, MiddleCut))
        after = [m for e in kids[at + 1 :] for m in _marks(e)]
        assert var_marker("var-new", ROW_A0) in after


# -- the Statistics card (U3) ------------------------------------------------

from dstf.reporting.stats_rows import StatRow  # noqa: E402
from test_session import _stats_project  # noqa: E402

NONLINEAR = "translation.nonlinear_count"


@pytest.fixture
def stats_shell(tmp_path):
    """A's run and baseline carry Dymola-shaped statistics (`_stats_project`):
    three nonlinear systems appeared, CPU time +40%, the total wall +3%."""
    return _opened(*_stats_project(tmp_path))


def _line_text(user) -> str:
    return _one(user, "stats:summary").text


def _stat(path, run, baseline, changed=False, delta="", kind="count", key=None):
    section, _, last = path.rpartition(".")
    return StatRow(
        section or "simulation",
        key or last,
        path,
        # A top-level statistic is labelled by its name, as is (S2).
        key or last.replace("_", " ").capitalize(),
        kind,
        run,
        baseline,
        changed,
        delta,
    )


def test_the_closed_line_names_a_list_once_counts_first_then_more():
    """B6: a list's `_count` / `_total` / `_max` changed together are ONE
    entry, named by the first changed of the three in that order; at most
    three named, then `+N more`, `N` counting entries. The owner's ruling
    of 2026-09-30: the counts part names COUNTS only; a changed time
    follows it, after a `·`."""
    from dstf.authoring.shell.stats_card import line_text

    rows = [
        _stat("translation.nonlinear_count", 3, 0, True, "0→3"),
        _stat("translation.nonlinear_max", 5, 0, True, "0→5"),
        _stat("translation.nonlinear_total", 9, 0, True, "0→9"),
        _stat("CPUtime", 1.12, 0.8, True, "+40%", kind="time", key="CPUtime"),
        _stat("timing.total_wall", 4.02, 3.91, False, "+3%", kind="time"),
    ]
    assert (
        line_text(rows, has_run=True) == "1 changed: Nonlinear count 0→3 · CPUtime +40%"
    )
    # Only the total moved: it names the list.
    rows[0] = _stat("translation.nonlinear_count", 3, 3)
    assert line_text(rows, has_run=True) == (
        "1 changed: Nonlinear total 0→9 · CPUtime +40%"
    )
    many = [
        _stat("timing.a_wall", 2.0, 1.0, True, "+100%", kind="time"),
        _stat("translation.a_count", 1, 0, True, "0→1"),
        _stat("translation.b", 1, 0, True, "0→1"),
        _stat("simulation.c", 1, 0, True, "0→1"),
        _stat("simulation.d", 1, 0, True, "0→1"),
    ]
    assert line_text(many, has_run=True) == (
        "4 changed: A count 0→1, B 0→1, C 0→1 +1 more · A wall +100%"
    )


def test_the_closed_line_names_no_time_among_the_counts():
    """The owner's ruling (2026-09-30): over TRANSFORM CPUtime ran ~2.2x
    its baseline on 324 of 331 tests, so a line naming times among the
    counts named CPUtime everywhere. A changed time never enters the
    counts part, nor its `N`; with no count changed the line says so and
    the time follows."""
    from dstf.authoring.shell.stats_card import line_text

    cpu = _stat("CPUtime", 1.73, 0.8, True, "+116%", kind="time", key="CPUtime")
    assert line_text([cpu], has_run=True) == "no count changed · CPUtime +116%"
    rows = [cpu, _stat("translation.nonlinear_count", 3, 0, True, "0→3")]
    assert line_text(rows, has_run=True) == (
        "1 changed: Nonlinear count 0→3 · CPUtime +116%"
    )
    # Nothing changed, no count and no time: `all match`.
    same = [_stat("simulation.state_events", 2, 2),
            _stat("CPUtime", 0.81, 0.8, False, "+1%", kind="time", key="CPUtime")]  # fmt: skip
    assert line_text(same, has_run=True) == "all match"


def test_the_closed_line_names_two_times_largest_move_first():
    """At most two changed times, by |relative change|, largest first — not
    the rows' order, not the signed change — then `+N more`."""
    from dstf.authoring.shell.stats_card import line_text

    rows = [
        _stat("timing.sim_wall", 1.3, 1.0, True, "+30%", kind="time"),
        _stat("timing.translation_wall", 0.5, 1.0, True, "-50%", kind="time"),
        _stat("CPUtime", 1.73, 0.8, True, "+116%", kind="time", key="CPUtime"),
    ]
    assert line_text(rows, has_run=True) == (
        "no count changed · CPUtime +116%, Translation wall -50% +1 more"
    )
    assert line_text(rows[:2], has_run=True) == (
        "no count changed · Translation wall -50%, Sim wall +30%"
    )


def test_the_closed_line_ranks_a_time_off_a_zero_baseline_first():
    """A time off a zero baseline has no relative change: it moved
    farthest, and is named first."""
    from dstf.authoring.shell.stats_card import line_text

    rows = [
        _stat("CPUtime", 1.73, 0.8, True, "+116%", kind="time", key="CPUtime"),
        _stat("timing.other_wall", 0.05, 0.0, True, "+0.05 s", kind="time"),
    ]
    assert line_text(rows, has_run=True) == (
        "no count changed · Other wall +0.05 s, CPUtime +116%"
    )


def test_the_closed_line_says_why_it_names_nothing():
    from dstf.authoring.shell.stats_card import line_text

    same = [_stat("simulation.state_events", 2, 2)]
    assert line_text(same, has_run=True) == "all match"
    assert line_text([], has_run=True) == "nothing recorded"
    text = StatRow("translation", "nonlinear", "translation.nonlinear", "N",
                   "text", [1], [], True, "")  # fmt: skip
    assert line_text([text], has_run=True) == "nothing recorded"
    assert line_text([_stat("simulation.state_events", None, 2)], has_run=False) == (
        "no run result"
    )
    # FakeLib's baseline stores no statistics: "all match" said nothing true.
    assert line_text([_stat("simulation.wall_time", 0.4, None, kind="time")],
                     has_run=True) == "no baseline statistics"  # fmt: skip


def test_a_scored_rows_summary_says_the_requirement():
    """B11: the criterion in a word or two."""
    from dstf.authoring.shell.stats_card import summary

    def leaf(**params):
        return {"params": {"quantity": NONLINEAR, **params}}

    assert summary(leaf(tolerance=0, tolerance_mode="abs")) == "exact"
    assert summary(leaf(tolerance=0)) == "exact"
    assert summary(leaf(tolerance=0.5, tolerance_mode="abs")) == "±0.5"
    assert summary(leaf(tolerance=0.1, tolerance_mode="rel")) == "±10%"
    assert summary(leaf(min_value=1, max_value=4)) == "in [1, 4]"
    assert summary(leaf(min_value=1)) == "≥ 1"
    assert summary(leaf(max_value=4)) == "≤ 4"
    # A leaf that leaves its tolerance out reads the scalar mode's default,
    # and a pair says each side: equal sides as the number does.
    assert summary({"params": {}}) == "±0.0001"
    assert summary(leaf()) == "±0.0001"
    assert summary(leaf(tolerance={"lower": 0.5, "upper": 0.5})) == "±0.5"
    assert summary(leaf(tolerance={"lower": 0.5, "upper": 1})) == "−0.5 / +1"
    assert (
        summary(leaf(tolerance={"lower": 0.05, "upper": 0.1}, tolerance_mode="rel"))
        == "−5% / +10%"
    )
    assert summary(leaf(tolerance={"lower": 0, "upper": 0})) == "exact"
    # A negative side is its edge, signed, abs and rel alike (`sides_text`):
    # "−-1% / +5%" said nothing a person can read.
    rel = {"tolerance_mode": "rel"}
    assert summary(leaf(tolerance={"lower": -0.01, "upper": 0.05}, **rel)) == (
        "+1% / +5%"
    )
    assert summary(leaf(tolerance={"lower": 0.01, "upper": -0.05}, **rel)) == (
        "−1% / −5%"
    )
    assert summary(leaf(tolerance={"lower": -0.5, "upper": 1})) == "+0.5 / +1"
    assert summary(leaf(tolerance={"lower": -0.0, "upper": 0.05}, **rel)) == (
        "−0% / +5%"
    )
    # A negative NUMBER is left as it reads.
    assert summary(leaf(tolerance=-0.05, tolerance_mode="abs")) == "±-0.05"


def test_a_requirements_words_read_a_tolerance_as_a_number_or_a_pair():
    """What a `+` says it adds reads the leaf's tolerance through
    `parse_sides`: a seed's number as it always read, an equal pair as that
    number, and an authored pair with two sides as its two edges — `float()`
    of a pair raised `TypeError`."""
    from dstf.authoring.builtin.edit_tree import _requirement

    def leaf(**fields):
        return {"metric": "scalar", "quantity": NONLINEAR, **fields}

    rel = {"tolerance_mode": "rel"}
    said = "Add a requirement: "
    assert _requirement(leaf(tolerance=0)) == said + "exact match"
    assert _requirement(leaf()) == said + "exact match"
    assert _requirement(leaf(tolerance=None)) == said + "exact match"
    assert _requirement(leaf(tolerance=0.5)) == said + "within ±0.5"
    assert _requirement(leaf(tolerance=0.1, **rel)) == said + "within 10%"
    pair = {"lower": 0.5, "upper": 0.5}
    assert _requirement(leaf(tolerance=pair)) == said + "within ±0.5"
    assert _requirement(leaf(tolerance={"lower": 0, "upper": 0})) == (
        said + "exact match"
    )
    pair = {"lower": 0.5, "upper": 1}
    assert _requirement(leaf(tolerance=pair)) == said + "within −0.5 / +1"
    pair = {"lower": 0.05, "upper": 0.1}
    assert _requirement(leaf(tolerance=pair, **rel)) == said + "within −5% / +10%"
    warn = {"combinator": "warn", "children": [leaf(tolerance=pair, **rel)]}
    assert _requirement(warn) == said + "within −5% / +10%, as a warning"


async def test_the_statistics_card_sits_before_the_tree_card_closed(stats_shell):
    async with user_simulation(root=stats_shell.index) as user:
        page = await _test_page(stats_shell, user)
        marks = _marks(user.client.layout)
        assert marks.index("stats") < marks.index("tree")
        assert page.stats is not None and not page.stats.is_open
        assert not page.stats.rows.visible
        assert _line_text(user) == "1 changed: Nonlinear count 0→3 · CPUtime +40%"
        assert "Total wall" not in _line_text(user)


async def test_the_open_card_lists_every_numeric_statistic_by_section(stats_shell):
    """S1: every count and time, never a list or a text; under Translation /
    Simulation / Timing, as the report's table has them; a changed row wears
    the report's warn tint, an unchanged one does not."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        _click(user, _one(user, "stats:toggle"))
        await _settled(lambda: page.stats.is_open)
        rows = shell.session.statistics(A)
        numeric = [r for r in rows if r.kind != "text"]
        assert len(numeric) == 20 and len(rows) == 23
        for row in rows:
            assert _none(user, f"stats:row:{row.path}") == (row.kind == "text")
        heads = [
            e.text
            for e in page.stats.rows.descendants()
            if "stats:section" in e._markers
        ]
        assert heads == ["Translation", "Simulation", "Timing"]
        assert [e.text for e in _one(user, "stats:head").descendants()
                if isinstance(e, ui.label)] == ["Statistic", "Run", "Baseline"]  # fmt: skip
        tinted = [r.path for r in numeric
                  if "dstf-stat-changed" in _one(user, f"stats:row:{r.path}").classes]  # fmt: skip
        assert tinted == [
            "translation.nonlinear_count",
            "translation.nonlinear_max",
            "translation.nonlinear_total",
            "CPUtime",
        ]
        texts = [e.text for e in _one(user, "stats:row:CPUtime").descendants()
                 if isinstance(e, ui.label)]  # fmt: skip
        assert texts[:4] == ["CPUtime", "1.12 s", "0.800 s", "+40%"]
        texts = [e.text for e in _one(user, f"stats:row:{NONLINEAR}").descendants()
                 if isinstance(e, ui.label)]  # fmt: skip
        assert texts[:4] == ["Nonlinear count", "3", "0", "0→3"]


async def test_a_plus_adds_a_requirement_at_the_roots_end_and_shows_its_pill(
    stats_shell,
):
    """The `+` is EditTree's at the STATISTIC placement: one count leaf at
    the END of the root; the row then shows the criterion's pill — FAIL,
    since "changed" means the criterion a click adds would fail (B11) — and
    its summary, and no `+`."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        before = len(shell.session.tree(A)["children"])
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert plus.props["aria-label"] == "Add a requirement: exact match"
        _click(user, plus)
        await _settled(lambda: len(shell.session.tree(A)["children"]) == before + 1)
        assert shell.session.tree(A)["children"][-1] == {
            "metric": "scalar",
            "quantity": NONLINEAR,
            "tolerance": 0,
            "tolerance_mode": "abs",
        }
        await _settled(lambda: not _none(user, f"stats:pill:{NONLINEAR}"))
        assert _one(user, f"stats:pill:{NONLINEAR}").text == "fail"
        assert _one(user, f"stats:rule:{NONLINEAR}").text == "exact"
        assert _none(user, f"add-statistic:{NONLINEAR}")
        assert page.stats.is_open


async def test_a_times_plus_says_it_warns_only_under_an_and(stats_shell):
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        tip = "Add a requirement: within 10%, as a warning"
        assert _one(user, "add-statistic:CPUtime").props["aria-label"] == tip
        _stage_tree(shell, A, {"combinator": "or", "children": [
            {"metric": "nrmse", "variable": "x"}]})  # fmt: skip
        await _settled(
            lambda: (
                not _none(user, "add-statistic:CPUtime")
                and _one(user, "add-statistic:CPUtime").props["aria-label"]
                == "Add a requirement: within 10%"
            )
        )


async def test_a_scored_row_shows_its_criterion_and_no_plus(stats_shell):
    """S2: U2's add form writes `CPUtime.final`; it is the CPUtime row. Two
    leaves on one statistic: the first's pill, and `+1` beside it (B10)."""
    shell = stats_shell
    cpu = {"metric": "scalar", "variable": "CPUtime", "quantity": "CPUtime.final",
           "tolerance": 0.1, "tolerance_mode": "rel"}  # fmt: skip
    _stage_tree(shell, A, {"combinator": "and", "children": [
        {"metric": "nrmse", "variable": "x"}, cpu,
        {"metric": "scalar", "quantity": "CPUtime", "max_value": 5}]})  # fmt: skip
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        assert _one(user, "stats:pill:CPUtime").text == "fail"
        assert _one(user, "stats:rule:CPUtime").text == "±10%"
        assert _one(user, "stats:more:CPUtime").text == "+1"
        assert _none(user, "add-statistic:CPUtime")
        assert _none(user, "stats:row:CPUtime.final")
        assert not _none(user, f"add-statistic:{NONLINEAR}")


async def test_a_plus_is_grey_with_the_add_forms_reason_where_nothing_is_scored(
    stats_shell,
):
    """A9: with no tree `EditTree.act` has nothing to add to — B has no
    baseline, so nothing is scored: the `+` is drawn grey with why."""
    shell = stats_shell
    assert shell.session.tree(B) is None
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        assert _line_text(user) == "no baseline statistics"
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert not plus.enabled
        assert plus.props["aria-label"] == "nothing is scored"


async def test_a_sync_with_nothing_moved_repaints_the_rows_in_place(stats_shell):
    """Rows are rebuilt only when their shape moves (the TreeCard rule): an
    edit elsewhere repaints the pill it moved and keeps every row."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        row = _one(user, f"stats:row:{NONLINEAR}")
        with page.client:
            page.sync()
        assert _one(user, f"stats:row:{NONLINEAR}") is row


async def test_fakelibs_own_statistics_render(tmp_path):
    """Nothing injected: what the Python backend records — `simulation.
    wall_time` from the run, the walls every backend gets."""
    cfg, work = _two_var_project(tmp_path)
    (work / "test_0001" / "run_stats.json").write_text(
        json.dumps({"model_id": A, "translation_wall": 0.0, "sim_wall": 0.41,
                    "elapsed": 0.52,
                    "statistics": {"simulation": {"wall_time": 0.41}}}),
        encoding="utf-8",
    )  # fmt: skip
    shell = _opened(cfg, work)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _line_text(user) == "no baseline statistics"
        page.stats.toggle()
        _one(user, "stats:row:simulation.wall_time")
        _one(user, "stats:row:timing.total_wall")


def _stats_leaf_staged(shell, quantity=NONLINEAR) -> str:
    """A count requirement on ``quantity`` at the end of A's root; its path."""
    leaf = {"metric": "scalar", "quantity": quantity, "tolerance": 0}
    shell.session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(
        {"combinator": "and", "children": [
            {"metric": "nrmse", "variable": "x"}, leaf]}))])  # fmt: skip
    return "/metrics/children/1"


async def test_a_click_on_a_scored_statistic_opens_its_editor_under_it(stats_shell):
    """Task 6: the card hosts a statistics criterion's editor, at its own
    placement — EditOnPlot, a CRITERION capability, says nothing there
    (B2) — and an edit keeps the quantity the form does not show (B3)."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        editor = _one(user, f"stats-editor:{leaf}")
        assert page.stats.slots[NONLINEAR] in list(_ancestors(editor))
        assert "dstf-crit-open" in _one(user, f"stats:row:{NONLINEAR}").classes
        assert "why:edit-on-plot" not in _marks(editor)
        (tolerance,) = [e for e in editor.descendants() if isinstance(e, ui.number)
                        and e.props.get("label") == "− Tolerance"]  # fmt: skip
        n = len(shell.session.spec.pending)
        tolerance.set_value(2)
        assert len(shell.session.spec.pending) == n + 1
        op = shell.session.spec.pending[-1]
        assert (op.op, op.path) == ("replace", leaf)
        assert op.value["quantity"] == NONLINEAR and op.value["tolerance"] == 2
        assert page.open == (STATS, leaf)  # the editor stays for the next edit
        user.find(marker=f"stats-editor-done:{leaf}").click()
        await _settled(lambda: page.open is None)
        assert _none(user, f"stats-editor:{leaf}")
        # the row's own click toggles it, as a criteria row's does
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open is None)


async def test_one_editor_page_wide_across_the_card_and_the_plots(stats_shell):
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert _none(user, f"stats-editor:{leaf}")
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        assert _none(user, f"editor:0:{T0}")
        # a form row is the same one thing
        _click_in(user, f"wrap:{T0}", within=f"node:0:{T0}")
        await _settled(lambda: page.form is not None)
        assert page.open is None and _none(user, f"stats-editor:{leaf}")
        # Focus shows one plot: the card's editor goes (B9)
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert page.open is None and _none(user, f"stats-editor:{leaf}")
        # closing the card closes its editor
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        _click(user, _one(user, "stats:toggle"))
        await _settled(lambda: page.open is None)


async def test_a_plus_opens_the_new_criterions_editor_under_its_row(stats_shell):
    """A time lands inside a `warn` under an `and` root: the editor opens on
    the LEAF, under the CPUtime row; the card stays open."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        before = len(shell.session.tree(A)["children"])
        _click(user, _one(user, "add-statistic:CPUtime"))
        leaf = f"/metrics/children/{before}/children/0"
        await _settled(lambda: page.open == (STATS, leaf))
        assert page.stats.is_open
        assert page.stats.slots["CPUtime"] in list(
            _ancestors(_one(user, f"stats-editor:{leaf}"))
        )


async def test_the_card_editor_follows_an_edit_staged_elsewhere(stats_shell):
    """A Discard under the open editor: its fields show what the leaf holds
    now, never the value the Discard undid."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))

        def tolerance():
            editor = _one(user, f"stats-editor:{leaf}")
            (field,) = [e for e in editor.descendants() if isinstance(e, ui.number)
                        and e.props.get("label") == "− Tolerance"]  # fmt: skip
            return field

        tolerance().set_value(2)
        shell.session.stage([PatchOp(A, "replace", f"{leaf}/tolerance", 5)])
        await _settled(lambda: tolerance().value == 5)
        assert page.open == (STATS, leaf)


# -- U3 fix batch 2 -----------------------------------------------------------


async def test_a_statistics_plus_rebuilds_no_plot(stats_shell, monkeypatch):
    """A statistics criterion changes no plot: a `+` in the card rebuilt
    every plot card all the same — the page's shape keyed on every leaf —
    which ended Focus, lost a zoom and re-sent every figure."""
    shell = stats_shell
    built: list = []
    init = plot_card.PlotCard.__init__

    def counted(self, *a, **k):
        built.append(self)
        return init(self, *a, **k)

    monkeypatch.setattr(plot_card.PlotCard, "__init__", counted)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        user.find(marker="focus:1").click()
        await _settled(lambda: page.focused == 1)
        cards, n = list(page.cards), len(built)
        page.stats.toggle()
        _click(user, _one(user, f"add-statistic:{NONLINEAR}"))
        await _settled(lambda: page.open is not None and page.open[0] == STATS)
        assert page.focused == 1
        assert page.cards == cards and len(built) == n


async def test_a_large_count_reads_whole_on_the_card(stats_shell):
    """A count is an integer: `1234567`, never `1.23457e+06`."""
    shell = stats_shell
    result = shell.session.project.results[A]
    stats = copy.deepcopy(result.statistics)
    stats["simulation"]["accepted_steps"] = 1234567
    result.statistics = stats
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        texts = [e.text for e in _one(user, "stats:row:simulation.accepted_steps")
                 .descendants() if isinstance(e, ui.label)]  # fmt: skip
        assert texts[:3] == ["Accepted steps", "1234567", "25"]


async def test_a_row_the_authored_tree_scores_is_pending_with_no_verdict(stats_shell):
    """Which rows are scored is the entry's tree, not the tree view: B has
    no baseline, so nothing is compared and there is no view — its staged
    count requirement still holds the row, with a pending pill and no `+`
    (a click on one staged the same leaf again)."""
    shell = stats_shell
    leaf = "/metrics/children/1"
    _stage_tree(shell, B, {"combinator": "and", "children": [
        {"metric": "range", "variable": "x", "min_value": -1},
        {"metric": "scalar", "quantity": NONLINEAR, "tolerance": 0}]})  # fmt: skip
    assert shell.session.tree_view(B) is None
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        page.stats.toggle()
        assert _one(user, f"stats:pill:{NONLINEAR}").text == "pending"
        assert _one(user, f"stats:rule:{NONLINEAR}").text == "exact"
        assert _none(user, f"add-statistic:{NONLINEAR}")
        # and its row opens its editor, as a scored row's does — with its
        # fields: the leaf is read from the authored tree, not the view
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        assert _stats_tolerance(user, leaf).value == 0


def _stats_tolerance(user, leaf: str):
    """The tolerance's first field in the card's open editor on ``leaf`` —
    a scalar's tolerance has two sides, and linked, either field sets both."""
    editor = _one(user, f"stats-editor:{leaf}")
    (field,) = [e for e in editor.descendants() if isinstance(e, ui.number)
                and e.props.get("label") == "− Tolerance"]  # fmt: skip
    return field


def _ref_override_project(tmp_path, on="x"):
    """`_two_var_project` with A's baseline carrying a reference-JSON
    override on ``on`` the spec does not shadow: A is compared, has a tree
    view, and the Session will not materialize its flat entry's tree. A's
    run and baseline carry `_stats_project`'s statistics, so its Statistics
    rows have a `+`."""
    import numpy as np

    from dstf.backends.types import TestResult, VariableResult
    from dstf.config import Config
    from dstf.storage.reference_store import ReferenceStore
    from test_session import DYMOLA_BASE, DYMOLA_RUN, make_test_model

    cfg, work = _two_var_project(tmp_path, ref_models=())
    (work / "test_0001" / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": A,
                "translation_wall": None,
                "sim_wall": None,
                "elapsed": 0.0,
                "statistics": DYMOLA_RUN,
            }
        ),
        encoding="utf-8",
    )
    t = np.asarray([i / 4 for i in range(5)])
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(
            A,
            variable_patterns=["x", "y"],
            variable_overrides={on: {"tolerance": 0.5}},
        ),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=t, values=t, name="x"),
                VariableResult(index=2, time=t, values=2.1 * t, name="y"),
            ],
            statistics=copy.deepcopy(DYMOLA_BASE),
        ),
    )
    return cfg, work


#: The spec override that shadows `_ref_override_project`'s reference one:
#: staged, the Session materializes A's tree again; discarded, it refuses —
#: and the tree's shape moves neither way. What a host that is never
#: rebuilt has to FOLLOW.
SHADOW_X = PatchOp(
    A, "add", "/comparison", {"variable_overrides": {"x": {"tolerance": 0.5}}}
)


@pytest.fixture
def no_tree_shell(tmp_path):
    """A shell over `_ref_override_project`: A is compared and drawn, and
    the Session will not materialize its tree until `SHADOW_X` is staged."""
    return _opened(*_ref_override_project(tmp_path))


async def test_a_plot_editor_on_a_test_with_no_tree_says_why(tmp_path):
    """A is compared (its leaves are on its plots, with verdicts), but the
    Session refuses to materialize its tree — a baseline override the spec
    does not shadow — and refuses every per-leaf edit. Its editor said
    "this leaf is not in the scored tree", false of a leaf on the plot; it
    says the Session's own refusal, once, and draws no field."""
    from dstf.authoring.session import NO_TREE
    from dstf.discovery.patch_apply import PatchError

    shell = _opened(*_ref_override_project(tmp_path))
    assert shell.session.tree_view(A) is not None
    assert shell.session.tree(A) is None
    with pytest.raises(PatchError) as refused:
        shell.session.stage([PatchOp(A, "add", "/metrics/children/0/tolerance", 1)])
    reason = str(refused.value).removeprefix(f"{A}: ")
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        editor = _one(user, f"editor:0:{T0}")
        texts = [getattr(e, "text", "") for e in editor.descendants()]
        # Edit criteria says it, on its own why-line; Edit on plot, refused
        # for the same reason, does not say it a second time.
        (why,) = _inside(user, "why:edit-criteria", f"editor:0:{T0}")
        assert why.text == reason == NO_TREE
        assert texts.count(NO_TREE) == 1
        assert "this leaf is not in the scored tree" not in texts
        assert not [e for e in editor.descendants() if isinstance(e, ui.number)]


def test_edit_tree_says_the_sessions_reason_for_no_tree(tmp_path):
    """Edit tree's why read "no tree to edit (nothing scored)" on A, which
    IS scored (a baseline override the spec does not shadow is what keeps
    its tree); it says the Session's one reason, as `stage` refuses with,
    and a test with nothing scored (B, no baseline) says so."""
    import re

    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import NO_TREE, NOTHING_SCORED, SessionView
    from dstf.discovery.patch_apply import PatchError

    shell = _opened(*_ref_override_project(tmp_path))
    session = shell.session

    def available(model_id):
        return EditTree().available(
            SessionView(session, Selection(tests=frozenset({model_id})))
        )

    assert session.tree_or_reason(A) == (None, NO_TREE)
    assert available(A) == NO_TREE
    with pytest.raises(PatchError, match=f"{A}: {re.escape(NO_TREE)}$"):
        session.stage([PatchOp(A, "add", "/metrics/children/0/tolerance", 1)])
    assert session.tree_or_reason(B) == (None, NOTHING_SCORED)
    assert available(B) == NOTHING_SCORED


def test_edit_on_plot_says_the_sessions_reason_for_no_tree(tmp_path):
    """A final-value check on x in the spec and a baseline override on y
    the spec does not shadow: x's check is on the plot with its handle, but
    the Session will not materialize the tree. Edit on plot read the comparison's tree
    and offered the handles, whose every edit raised "has no tree to edit";
    it says the Session's reason, and an edit reaching it is refused with
    that reason as a patch — as Edit tree's add is."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot, _leaf_paths
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import NO_TREE, SessionView
    from dstf.discovery.patch_apply import PatchError

    cfg, work = _ref_override_project(tmp_path, on="y")
    spec = cfg.parent / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    points = {"mode": "points", "points": []}
    for entry in doc["tests"]:
        if entry["model"] == A:
            entry["comparison"] = {"variable_overrides": {"x": points}}
    spec.write_text(json.dumps(doc), encoding="utf-8")
    session = _opened(cfg, work).session
    assert session.tree_or_reason(A) == (None, NO_TREE)
    ((path, mode),) = _leaf_paths(
        SessionView(session, Selection(tests=frozenset({A}))), A, "x"
    )
    assert mode == "points"
    sel = Selection(tests=frozenset({A}), variable="x", leaf_path=path)
    view = SessionView(session, sel)
    assert EditOnPlot().available(view) == NO_TREE
    (leaf,) = [lv for lv in session.view(A, "x").leaves if lv.path == path]
    handle = next(p.id for p in leaf.geometry if p.kind == "handle")
    for inputs in (
        {"kind": "rows", "field": "points", "rows": []},
        {"kind": "handle", "variable": "x", "handle": handle, "shape": {}},
    ):
        with pytest.raises(PatchError) as refused:
            EditOnPlot().act(view, model_id=A, path=path, **inputs)
        assert refused.value.reason == NO_TREE
    with pytest.raises(PatchError) as refused:
        EditTree().act(
            view, action="add", model_id=A, path=T0, variable="y", metric="nrmse"
        )
    assert refused.value.reason == NO_TREE
    assert session.spec.pending == []


async def test_a_statistics_plus_says_the_sessions_reason_for_no_tree(tmp_path):
    """A is scored but the Session will not materialize its tree: a
    Statistics row's `+` is grey, and its accessible name and its tooltip
    are the Session's reason, not "nothing is scored"."""
    from dstf.authoring.session import NO_TREE

    shell = _opened(*_ref_override_project(tmp_path))
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert not plus.enabled
        assert plus.props["aria-label"] == NO_TREE
        tips = [e for e in plus.parent_slot.parent.descendants()
                if isinstance(e, ui.tooltip)]  # fmt: skip
        assert [t.text for t in tips] == [NO_TREE]


async def test_a_plus_on_a_test_with_no_baseline_opens_a_usable_editor(stats_shell):
    """B has a tree but no baseline: its range leaf is scored, but with the
    count requirement added nothing is (no tree view), and the new
    criterion's editor read its leaf from the view and drew "this leaf is
    not in the scored tree" and no field."""
    shell = stats_shell
    _stage_tree(shell, B, {"combinator": "and", "children": [
        {"metric": "range", "variable": "x", "min_value": -1}]})  # fmt: skip
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        page.stats.toggle()
        _click(user, _one(user, f"add-statistic:{NONLINEAR}"))
        leaf = "/metrics/children/1"
        await _settled(lambda: page.open == (STATS, leaf))
        assert shell.session.tree_view(B) is None
        editor = _one(user, f"stats-editor:{leaf}")
        texts = [getattr(e, "text", "") for e in editor.descendants()]
        assert "this leaf is not in the scored tree" not in texts
        field = _stats_tolerance(user, leaf)
        assert field.value == 0
        field.set_value(2)
        op = shell.session.spec.pending[-1]
        assert (op.op, op.path) == ("replace", leaf)
        assert op.value["quantity"] == NONLINEAR and op.value["tolerance"] == 2


async def test_the_card_editor_follows_an_edit_staged_elsewhere_with_no_view(
    stats_shell,
):
    """With no tree view (B has no baseline) the open editor compared
    against the view's leaf, found none, and never redrew: fields showing
    an undone value stage it back."""
    shell = stats_shell
    leaf = "/metrics/children/1"
    _stage_tree(shell, B, {"combinator": "and", "children": [
        {"metric": "range", "variable": "x", "min_value": -1},
        {"metric": "scalar", "quantity": NONLINEAR, "tolerance": 0}]})  # fmt: skip
    assert shell.session.tree_view(B) is None
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        shell.session.stage([PatchOp(B, "replace", f"{leaf}/tolerance", 5)])
        await _settled(lambda: _stats_tolerance(user, leaf).value == 5)
        assert page.open == (STATS, leaf)


# -- the mutation review's survivors (U3 Part B) ------------------------------


def test_the_closed_line_counts_changes_when_one_side_lacks_a_statistic():
    """A baseline written before a statistic existed (no `timing`) still has
    statistics: the line names what changed, not "no baseline"."""
    from dstf.authoring.shell.stats_card import line_text

    rows = [
        _stat("translation.nonlinear_count", 3, 0, True, "0→3"),
        _stat("timing.total_wall", 4.0, None, kind="time"),
    ]
    assert line_text(rows, has_run=True) == "1 changed: Nonlinear count 0→3"


def test_a_zero_bound_is_a_bound():
    """`min_value: 0` is the commonest bound on a count."""
    from dstf.authoring.shell.stats_card import summary

    def leaf(**params):
        return {"params": {"quantity": NONLINEAR, **params}}

    assert summary(leaf(min_value=0, max_value=4)) == "in [0, 4]"
    assert summary(leaf(min_value=0)) == "≥ 0"
    assert summary(leaf(max_value=0)) == "≤ 0"


def test_only_a_final_folds_onto_its_statistics_row():
    """S2: `CPUtime.final` is the CPUtime row; `CPUtime.max` is not the
    number that row shows — it is its own row (S4)."""
    from dstf.authoring.shell.stats_card import row_of

    assert row_of("CPUtime.final", ["CPUtime"]) == "CPUtime"
    assert row_of("CPUtime.max", ["CPUtime"]) == "CPUtime.max"


async def test_a_row_that_stops_changing_loses_its_tint(stats_shell):
    """The tint repaints in place both ways (an accept or a new run makes
    the run match; the row stands)."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        row = _one(user, f"stats:row:{NONLINEAR}")
        assert "dstf-stat-changed" in row.classes
        result = shell.session.project.results[A]
        stats = copy.deepcopy(result.statistics)
        stats["translation"].update(
            nonlinear=[], nonlinear_count=0, nonlinear_total=0, nonlinear_max=0
        )
        result.statistics = stats
        with page.client:
            page.sync()
        assert _one(user, f"stats:row:{NONLINEAR}") is row
        assert "dstf-stat-changed" not in row.classes


async def test_removing_the_edited_statistics_criterion_closes_its_editor(
    stats_shell,
):
    """The rows rebuild; the page's one editor goes with them."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        shell.session.stage([PatchOp(A, "remove", leaf)])
        await _settled(lambda: _none(user, f"stats-editor:{leaf}"))
        assert page.open is None


async def test_the_card_editors_own_edit_keeps_its_field(stats_shell):
    """The field being typed in survives its own edit's sync."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))

        def tolerance():
            editor = _one(user, f"stats-editor:{leaf}")
            (field,) = [e for e in editor.descendants() if isinstance(e, ui.number)
                        and e.props.get("label") == "− Tolerance"]  # fmt: skip
            return field

        field = tolerance()
        field.set_value(2)
        await asyncio.sleep(0.3)
        assert tolerance() is field


async def test_a_plot_rebuild_leaves_the_cards_editor_open(stats_shell):
    """The Statistics card's editor hangs under no plot card."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["y"]}])]
        )
        await _settled(
            lambda: page.cards and page.cards[0].resolved.series == ("x", "y")
        )
        assert page.open == (STATS, leaf)
        assert not _none(user, f"stats-editor:{leaf}")


async def test_a_plus_is_grey_on_a_simulate_only_test(stats_shell):
    """A criterion staged there is never scored."""
    shell = stats_shell
    shell.session.project.test(A).simulate_only = True
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert not plus.enabled
        assert plus.props["aria-label"] == "simulate-only: nothing is scored"


async def test_a_plus_is_grey_under_a_weighted_root(stats_shell):
    """A weighted node takes no child alone."""
    shell = stats_shell
    shell.session.stage([PatchOp(A, "add", "/metrics", {
        "combinator": "weighted", "weights": [1.0, 1.0], "threshold": 0.5,
        "children": [{"metric": "range", "variable": "x", "min_value": -1},
                     {"metric": "range", "variable": "y", "min_value": -1}]})])  # fmt: skip
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert not plus.enabled
        assert plus.props["aria-label"].startswith("a weighted node")


async def test_a_gone_test_empties_the_statistics_card(stats_shell, tmp_path):
    """The card says nothing for a test the project no longer has."""
    shell = stats_shell
    cfg2, work2 = _project_without(tmp_path, A)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        assert not _none(user, f"add-statistic:{NONLINEAR}")
        shell.session.open(cfg2, work_dir=work2)
        await _settled(lambda: page.gone)
        assert _none(user, "stats:summary")
        assert _none(user, f"add-statistic:{NONLINEAR}")
        assert not page.stats.is_open


async def test_a_simulate_only_test_with_no_tree_says_simulate_only(stats_shell):
    """The specific cause first — B has no baseline (no tree) AND is
    simulate-only; the add form says simulate-only, so does the `+`."""
    shell = stats_shell
    shell.session.project.test(B).simulate_only = True
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        assert not plus.enabled
        assert plus.props["aria-label"] == "simulate-only: nothing is scored"


async def test_opening_the_cards_editor_closes_a_form_row(stats_shell):
    """One thing open page-wide, in this direction too."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click_in(user, f"wrap:{T0}", within=f"node:0:{T0}")
        await _settled(lambda: page.form is not None)
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        assert page.form is None


async def test_a_plus_scrolls_the_new_editor_into_view(stats_shell, monkeypatch):
    """The reveal brings the card's row and editor into view, as a plot's
    does — BY THEIR IDS (K5): a class query can find an editor the next
    update takes away."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        sent: list[str] = []
        monkeypatch.setattr(
            page.client, "run_javascript", lambda code, *a, **k: sent.append(code)
        )
        _click(user, _one(user, f"add-statistic:{NONLINEAR}"))
        await _settled(lambda: page.open is not None and page.open[0] == STATS)
        await _settled(lambda: any("scrollIntoView" in c for c in sent))
        row = page.stats.rows_by_path[NONLINEAR]
        editor = page.stats.editor_box
        assert editor is not None
        (scroll,) = [c for c in sent if "scrollIntoView" in c]
        assert row.html_id in scroll and editor.html_id in scroll, scroll
        assert "querySelector" not in scroll, scroll


async def test_a_criterion_on_a_list_statistic_has_a_home_on_the_card(
    stats_shell, monkeypatch
):
    """S4: every statistics criterion has a row on the card — one reading
    `translation.nonlinear` (a list: a text row, which the card skips)
    too: its jump opens its editor there."""
    from dstf.authoring.shell import test_page as page_mod

    shell = stats_shell
    _stage_tree(shell, A, {"combinator": "and", "children": [
        {"metric": "nrmse", "variable": "x"},
        {"metric": "scalar", "quantity": "translation.nonlinear", "tolerance": 0}]})  # fmt: skip
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        monkeypatch.setattr(page_mod.ui, "run_javascript", lambda *a, **k: None)
        with page.client:
            page.jump_to(T1)
        await _settled(lambda: page.open == (STATS, T1))
        assert user.notify.messages == []


# -- the mutation review's survivors (U3 fix batch 2) -------------------------


async def test_a_reduce_scalar_on_a_plotted_variable_is_a_row_of_its_plot(
    stats_shell,
):
    """The page's shape leaves out STATISTICS leaves only: a `scalar` that
    reduces a variable (`{"reduce": "max"}` on x) is a criterion of x's
    plot, and staging one reshapes that plot's rows."""
    shell = stats_shell
    _stage_tree(shell, A, {"combinator": "and", "children": [
        {"metric": "nrmse", "variable": "x"}]})  # fmt: skip
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",)]
        shell.session.stage([PatchOp(A, "add", "/metrics/children/-", {
            "metric": "scalar", "variable": "x", "quantity": {"reduce": "max"},
            "tolerance": 0.1})])  # fmt: skip
        await _settled(lambda: "/metrics/children/1" in page.cards[0].rows)
        assert "/metrics/children/1" in page.cards[0].rows


async def test_a_sync_with_nothing_moved_keeps_the_plus(stats_shell):
    """The `+`s are redrawn only when what one MAY do moved; a sync with
    nothing moved follows them in place — the same element."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        plus = _one(user, f"add-statistic:{NONLINEAR}")
        with page.client:
            page.sync()
            page.sync()
        assert _one(user, f"add-statistic:{NONLINEAR}") is plus


async def test_a_side_with_no_value_reads_a_dash(stats_shell):
    """B has no baseline: its Baseline column reads `—`, never blank."""
    shell = stats_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, model=B)
        page.stats.toggle()
        texts = [e.text for e in _one(user, f"stats:row:{NONLINEAR}").descendants()
                 if isinstance(e, ui.label)]  # fmt: skip
        assert texts[1:3] == ["3", "—"]


# -- move a subtree (U4): Move to… in the tree card ------------------------

N_K = "/metrics/children/1"


def _move_items(user, path: str) -> list[tuple[str, str, str | None, bool]]:
    """The Move to… menu of ``path``'s row, in order: each destination's
    node text, its place — "— before" and the row it names, cut in the
    middle, read as one — its reason line (None) and whether it is
    enabled."""
    out = []
    i = 0
    while not _none(user, f"move-dest:{path}:{i}"):
        item = _one(user, f"move-dest:{path}:{i}")
        text = next(e.text for e in item.descendants() if isinstance(e, MiddleCut))
        (place,) = [e for e in item.descendants() if "dstf-move-where" in e.classes]
        parts = place.default_slot.children
        assert all(isinstance(e, (ui.label, MiddleCut)) for e in parts)
        where = " ".join(e.text for e in parts)
        why = [e.text for e in item.descendants() if "move-why" in e._markers]
        out.append((text, where, why[0] if why else None, item.enabled))
        i += 1
    return out


async def _open_move_menu(user, path: str) -> None:
    _click(user, _one(user, f"move:{path}"))
    await _settled(lambda: not _none(user, f"move-dest:{path}:0"))


async def test_move_to_lists_every_destination_in_tree_order(nested_shell):
    """y's menu: every place between rows — named by the row it goes
    before — and every criterion to wrap with, in tree order; the places
    beside y read as ONE greyed "where it is", with no reason. Built at the
    click, never with the rows (Q6)."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        assert _none(user, f"move-dest:{N_Y}:0")
        await _open_move_menu(user, N_Y)
        root, k = "and · 5 criteria (root)", "k-of-n · 2 criteria · k=1"
        assert _move_items(user, N_Y) == [
            (root, "— before range · pipe.T[2]", None, True),
            ("range · pipe.T[2]", "— wrap both in and", None, True),
            (root, f"— before {k}", None, True),
            (k, "— before range · x", None, True),
            ("range · x", "— wrap both in and", None, True),
            (k, "— before range · pipe.T[1]", None, True),
            ("range · pipe.T[1]", "— wrap both in and", None, True),
            (k, "— at the end", None, True),
            (root, "— before range · x", None, True),
            ("range · x", "— wrap both in and", None, True),
            (root, "— where it is", None, False),
        ]


async def test_a_refused_destination_is_greyed_with_its_reason(nested_shell):
    """The k-of-n's menu: its own subtree is listed, greyed, each with the
    reason as a second line; a click there stages nothing."""
    from dstf.authoring.tree_move import INTO_ITSELF

    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        await _open_move_menu(user, N_K)
        items = _move_items(user, N_K)
        inside = [item for item in items if item[2] is not None]
        assert len(inside) == 5
        assert all(why == INTO_ITSELF and not on for _t, _w, why, on in inside)
        _click(user, _one(user, f"move-dest:{N_K}:3"))  # the k-of-n, at 1
        await asyncio.sleep(0.2)
        assert shell.session.spec.pending == []


async def test_choosing_a_destination_stages_the_move_and_the_rows_follow(
    nested_shell,
):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        await _open_move_menu(user, N_Y)
        _click(user, _one(user, f"move-dest:{N_Y}:0"))  # the root, at 1
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        (op,) = shell.session.spec.pending
        assert (op.op, op.path) == ("add", "/metrics")
        kids = shell.session.tree(A)["children"]
        assert [c.get("variable") for c in kids] == ["y", "pipe.T[2]", None, "x"]
        await _settled(
            lambda: _one(user, "tree-text:/metrics/children/0").text == "nrmse · y"
        )


async def test_move_to_is_on_the_tree_rows_only_and_never_on_the_root(nested_shell):
    """V2: a plot's criteria row hosts the same NODE controls and moves
    nothing; the root has nowhere to go."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        assert _none(user, "move:/metrics")
        for path in (N_T2, N_K, N_KX, N_KT1, N_X2, N_Y):
            (button,) = user.find(marker=f"move:{path}").elements
            assert _one(user, f"tree-row:{path}") in list(_ancestors(button))
            assert button.props["aria-label"] == "Move to…"
        page.open_criterion(N_Y)
        await _settled(lambda: page.open is not None)
        hosts = [row.host for card in page.cards for row in card.rows.values()]
        assert hosts  # not vacuous: the criteria rows are drawn
        for host in hosts:
            marks = _marks(host.column)
            assert any(m.startswith("wrap:") for m in marks)  # the controls are
            assert not [m for m in marks if m.startswith("move:")]


# -- move a subtree (U4): the drag's two ends, without a browser -----------


def _drop(page, src: str, target: str, zone: str) -> None:
    """A drop as the browser sends it: with the revision of the rows its
    drag started on (`TreeCard.revision`)."""
    page.tree.dropped(
        {"src": src, "target": target, "zone": zone, "rev": str(page.tree.revision)}
    )


@pytest.mark.parametrize(
    ("src", "target", "zone", "order"),
    [
        # before a row: at its index in its parent (the top half)
        (N_Y, N_T2, "before", ["y", "pipe.T[2]", None, "x"]),
        (N_T2, N_Y, "before", [None, "x", "pipe.T[2]", "y"]),
        # onto a combinator: at its end (the bottom half)
        (N_Y, N_K, "onto", ["pipe.T[2]", None, "x"]),
        # onto a criterion: both wrapped in `and`, the target first
        (N_Y, N_T2, "onto", [None, None, "x"]),
        # onto the root, its only zone: at its end
        (N_T2, "/metrics", "onto", [None, "x", "y", "pipe.T[2]"]),
    ],
)
async def test_a_drop_maps_its_zone_to_a_place(nested_shell, src, target, zone, order):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, src, target, zone)
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        kids = shell.session.tree(A)["children"]
        assert [c.get("variable") for c in kids] == order


async def test_a_drop_where_no_place_is_stages_nothing(nested_shell):
    """Beside the root, an "after" zone the owner's ruling took away, and
    a place the tree refuses (into the k-of-n's own criterion): nothing is
    staged, and the refusal is said under the row."""
    from dstf.authoring.tree_move import INTO_ITSELF

    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, N_Y, "/metrics", "before")
        _drop(page, N_T2, N_Y, "after")
        _drop(page, N_K, N_KX, "onto")
        await _settled(lambda: _one(user, f"tree-row:{N_KX}") is not None)
        assert shell.session.spec.pending == []
        errors = page.tree.targets[N_KX].slot.descendants()
        assert [e.text for e in errors if isinstance(e, ui.label) and e.visible] == [
            INTO_ITSELF
        ]


async def test_a_drop_on_rows_rebuilt_since_its_drag_started_is_refused(
    nested_shell, caplog
):
    """A drag carries the rows' revision from its start. An edit staged
    mid-drag rebuilt the rows, and the drag's paths name the nodes of a
    tree that is gone: its drop is refused under its row — a refusal said,
    never logged as a bug — and stages nothing. The same drop from the
    rows as they are goes through."""
    from dstf.authoring.tree_move import TREE_CHANGED

    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        started = str(page.tree.revision)
        shell.session.stage([PatchOp(A, "remove", N_Y)])
        await _settled(lambda: str(page.tree.revision) != started)
        page.tree.dropped({"src": N_T2, "target": N_K, "zone": "onto", "rev": started})
        await asyncio.sleep(0.2)
        assert len(shell.session.spec.pending) == 1  # the remove alone
        errors = page.tree.targets[N_K].slot.descendants()
        assert [e.text for e in errors if isinstance(e, ui.label) and e.visible] == [
            TREE_CHANGED
        ]
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        _drop(page, N_T2, N_K, "onto")
        await _settled(lambda: len(shell.session.tree(A)["children"]) == 2)
        kids = shell.session.tree(A)["children"][0]["children"]
        assert [c["variable"] for c in kids] == ["x", "pipe.T[1]", "pipe.T[2]"]


async def test_a_drags_zones_are_its_sources_read_at_its_start(nested_shell):
    """The map a drag draws by: the dragged row's own, from the tree as it
    is when the drag starts — a second drag from another row reads ITS
    map, and one after a move reads the moved tree."""
    from dstf.authoring.tree_move import INTO_ITSELF

    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        zones = page.tree.zones_for(N_K)
        assert zones["/metrics"] == {"onto": None}
        assert zones[N_K] == {"before": "", "onto": INTO_ITSELF}
        assert zones[N_KX] == {"before": INTO_ITSELF, "onto": INTO_ITSELF}
        assert zones[N_Y] == {"before": None, "onto": None}
        zones = page.tree.zones_for(N_Y)
        assert zones[N_Y] == {"before": "", "onto": INTO_ITSELF}
        assert zones[N_KX] == {"before": None, "onto": None}
        assert page.tree.zones_for("/metrics") == {}  # the root is no source
        _drop(page, N_Y, N_T2, "before")
        await _settled(lambda: len(shell.session.spec.pending) == 1)
        await _settled(lambda: page.tree.targets[N_Y].zones is not None)
        # y is the first child now: the k-of-n is child 2, its rows keyed so.
        zones = page.tree.zones_for(N_T2)
        assert zones[N_T2] == {"before": "", "onto": INTO_ITSELF}
        assert "/metrics/children/2/children/1" in zones


async def test_only_the_tree_rows_take_a_drag(nested_shell):
    """V2: every tree row takes a drop and every one but the root's is
    draggable, its handle shown; a plot's criteria row takes neither."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        targets = page.tree.targets
        assert targets["/metrics"].on_drop is not None
        assert targets["/metrics"].zones is None and targets["/metrics"].grip is None
        root_grip = list(_one(user, "tree-row:/metrics").default_slot.children)[2]
        assert "dstf-grip" in root_grip.classes and "draggable" not in root_grip.props
        assert "dstf-grip-off" in root_grip.classes  # hidden, its place kept
        for path in (N_T2, N_K, N_KX, N_KT1, N_X2, N_Y):
            assert targets[path].on_drop is not None
            assert targets[path].zones is not None
            grip = _one(user, f"grip:{path}")
            assert "dstf-grip-off" not in grip.classes
            assert grip.props["draggable"] == "true"
            assert grip.props["aria-label"] == "Drag to move"
        page.open_criterion(N_Y)
        await _settled(lambda: page.open is not None)
        rows = [row for card in page.cards for row in card.rows.values()]
        assert rows  # not vacuous: the criteria rows are drawn
        for row in rows:
            assert not row.target.drag and row.target.on_drop is None
            assert row.target.zones is None


async def test_a_rows_handle_goes_with_its_controls(no_tree_shell):
    """A host rebuilt to its why-line — Edit tree's, once the Session will
    not materialize the tree — takes what its panel registered with it: the
    row takes no drop and offers no handle until the refusal goes."""
    shell = no_tree_shell
    shell.session.stage([SHADOW_X])
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        grip = _one(user, f"grip:{T1}")
        assert "dstf-grip-off" not in grip.classes
        assert page.tree.targets[T1].on_drop is not None
        shell.session.discard()
        await _settled(lambda: "dstf-grip-off" in grip.classes)
        assert page.tree.targets[T1].on_drop is None
        assert page.tree.targets[T1].zones is None
        shell.session.stage([SHADOW_X])
        await _settled(lambda: "dstf-grip-off" not in grip.classes)
        assert page.tree.targets[T1].zones is not None


# -- move a subtree (U4): after a move -------------------------------------


def _leaf(variable: str, metric: str = "range", **fields) -> dict:
    return {"metric": metric, "variable": variable, **fields}


#: Two criteria on x trading places move no shape of the page.
SWAP = {
    "combinator": "and",
    "children": [
        _leaf("x", min_value=-1.0),
        _leaf("x", max_value=100.0),
        _leaf("y", "nrmse"),
    ],
}


async def test_a_move_closes_the_open_editor_rather_than_rebind_it(nested_shell):
    """The swap hazard: the second criterion on x moved above the first —
    the page's shape is what it was, and an editor left open at
    /children/0 would show the OTHER criterion. The move closes it."""
    shell = nested_shell
    _stage_tree(shell, A, SWAP)
    first = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        page.open_criterion(first)
        await _settled(lambda: page.open is not None and page.open[1] == first)
        shape = page.shape(page._view())
        _drop(page, "/metrics/children/1", first, "before")
        await _settled(
            lambda: shell.session.tree(A)["children"][0].get("max_value") == 100.0
        )
        assert page.shape(page._view()) == shape  # the swap moved no shape
        await _settled(lambda: not _none(user, "moved-note"))
        assert page.open is None


async def test_a_move_closes_an_open_form_row(nested_shell):
    """The swap again, with a form open under the first criterion's row:
    no row is rebuilt, and the form left open would follow its path to
    the OTHER criterion. The move closes it."""
    shell = nested_shell
    _stage_tree(shell, A, SWAP)
    first = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _click_in(user, f"wrap:{first}", f"node:{first}")
        await _settled(lambda: page.form is not None)
        _drop(page, "/metrics/children/1", first, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        assert page.form is None


@pytest.mark.parametrize(
    ("tree", "src", "target", "zone", "note"),
    [
        (
            # out of a k-of-n 3 of 3: k comes down with it
            {
                "combinator": "and",
                "children": [
                    _leaf("y", "nrmse"),
                    {
                        "combinator": "k-of-n",
                        "k": 3,
                        "children": [
                            _leaf("x", min_value=float(-i)) for i in (1, 2, 3)
                        ],
                    },
                ],
            },
            "/metrics/children/1/children/2",
            "/metrics/children/0",
            "before",
            "Moved. k 3 → 2.",
        ),
        (
            # out of a warn: the warn goes
            {
                "combinator": "and",
                "children": [
                    _leaf("y", "nrmse"),
                    {"combinator": "warn", "children": [_leaf("x", min_value=-1.0)]},
                ],
            },
            "/metrics/children/1/children/0",
            "/metrics",
            "onto",
            "Moved. The empty warn was removed.",
        ),
        (
            # into a weighted: weight 1
            {
                "combinator": "and",
                "children": [
                    _leaf("y", "nrmse"),
                    _leaf("x", min_value=-1.0),
                    {
                        "combinator": "weighted",
                        "weights": [2.0, 3.0],
                        "threshold": 10.0,
                        "children": [_leaf("x", max_value=9.0), _leaf("y", "nrmse")],
                    },
                ],
            },
            "/metrics/children/1",
            "/metrics/children/2/children/1",
            "before",
            "Moved. Weight 1 given.",
        ),
        (
            # out of a weighted: its weight goes with it
            {
                "combinator": "and",
                "children": [
                    _leaf("y", "nrmse"),
                    {
                        "combinator": "weighted",
                        "weights": [2.0, 3.0],
                        "threshold": 10.0,
                        "children": [_leaf("x", max_value=9.0), _leaf("y", "nrmse")],
                    },
                ],
            },
            "/metrics/children/1/children/0",
            "/metrics/children/0",
            "before",
            "Moved. Weight 2 went with it.",
        ),
    ],
)
async def test_the_moved_row_says_what_changed(
    nested_shell, tree, src, target, zone, note
):
    """One line under the moved node's row, in its NEW place: planned
    before the move went through (afterwards ``src`` names another node,
    and its plan another move)."""
    from dstf.authoring.tree_move import Dest, plan_move

    shell = nested_shell
    _stage_tree(shell, A, tree)
    landed = plan_move(tree, src, Dest("at", "/metrics", 0)).path
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, src, target, zone)
        await _settled(lambda: not _none(user, "moved-note"))
        line = _one(user, "moved-note")
        await _settled(lambda: line.text == note)
        if zone == "before" and target == "/metrics/children/0":
            assert page.tree.targets[landed].slot in list(_ancestors(line))


async def test_a_move_is_told_to_the_tree_cards_one_live_region(nested_shell):
    """ONE `role="status"` region in the tree card, there and empty from
    its build — a live region is read out when its text CHANGES, and one
    that arrives holding it is not. A move's line is said into it; the
    line under the row is born holding its text and is no live region. A
    second "Moved." in a row still changes the region's text."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        live = _one(user, "moved-live")
        assert live is page.tree.live and live.text == ""
        assert (live.props["role"], live.props["aria-live"]) == ("status", "polite")
        await _open_tree(user)
        _drop(page, N_Y, N_T2, "before")  # [y, T2, k, x2]
        await _settled(lambda: not _none(user, "moved-note"))
        line = _one(user, "moved-note")
        assert line.text == "Moved." and "role" not in line.props
        assert live.text == "Moved."
        _drop(page, N_Y, N_T2, "before")  # [x2, y, T2, k]
        await _settled(lambda: live.text != "Moved.")
        assert live.text == "Moved.\u200b"
        assert _one(user, "moved-note").text == "Moved."
        assert _one(user, "moved-live") is live


async def test_the_moved_line_goes_with_the_next_form(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, N_Y, N_T2, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        line = _one(user, "moved-note")
        await _settled(lambda: line.text == "Moved.")
        # y is the first child now: the line is under ITS row.
        assert page.tree.targets[N_T2].slot in list(_ancestors(line))
        _click_in(user, f"wrap:{N_T2}", f"node:{N_T2}")
        await _settled(lambda: _none(user, "moved-note"))


async def test_the_moved_line_goes_with_the_next_editor(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, N_Y, N_T2, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        page.open_criterion(N_T2)
        await _settled(lambda: _none(user, "moved-note"))


async def test_the_moved_line_goes_with_the_next_edit(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, N_Y, N_T2, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        await asyncio.sleep(0.3)  # the move's own events have all arrived
        assert not _none(user, "moved-note")
        # x's max is the last child now: y moved to the front.
        shell.session.stage([PatchOp(A, "replace", f"{N_Y}/max_value", 50.0)])
        await _settled(lambda: _none(user, "moved-note"))


async def test_a_move_by_the_menu_says_it_too(nested_shell):
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        await _open_move_menu(user, N_Y)
        _click(user, _one(user, f"move-dest:{N_Y}:0"))  # the root, at 1
        await _settled(lambda: not _none(user, "moved-note"))
        await _settled(lambda: _one(user, "moved-note").text == "Moved.")


async def test_a_move_the_submit_refuses_says_nothing_and_closes_nothing(
    nested_shell, monkeypatch
):
    """The move planned, its submit refused (an edit that raced the drop):
    no "Moved." line, and the open editor stays open — only a move that
    went through closes what is open."""
    from dstf.discovery.patch_apply import PatchError

    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        page.open_criterion(N_T2)
        await _settled(lambda: page.open is not None)

        def refuse(outcome):
            raise PatchError("the document refused it")

        monkeypatch.setattr(shell.session, "apply", refuse)
        _drop(page, N_Y, N_T2, "before")
        await asyncio.sleep(0.3)
        monkeypatch.undo()
        assert shell.session.spec.pending == []
        assert _none(user, "moved-note")
        assert page.open is not None and page.pending_moved is None


async def test_a_move_menu_reopened_lists_each_place_once(nested_shell):
    """The menu is filled at each click, emptied first: a second opening
    lists the same eleven places, not twenty-two."""
    shell = nested_shell

    def places() -> int:
        return sum(
            1
            for e in _one(user, f"move-menu:{N_Y}").descendants()
            if any(m.startswith("move-dest:") for m in e._markers)
        )

    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        await _open_move_menu(user, N_Y)
        first = places()
        _click(user, _one(user, f"move:{N_Y}"))
        await asyncio.sleep(0.3)
        assert first == places() == 11


async def test_a_chosen_place_closes_the_menu_and_its_button_says_so(nested_shell):
    """A swap (`SWAP`) rebuilds no row, so the menu chosen from is the one
    still there: choosing closes it, the button says it is closed, and its
    tooltip — hidden while the menu was open — is back."""
    shell = nested_shell
    _stage_tree(shell, A, SWAP)
    second = "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        button = _one(user, f"move:{second}")
        (tip,) = [e for e in button.descendants() if isinstance(e, ui.tooltip)]
        assert tip.visible
        await _open_move_menu(user, second)
        menu = _one(user, f"move-menu:{second}")
        assert menu.value is True and button.props["aria-expanded"] == "true"
        assert not tip.visible
        _click(user, _one(user, f"move-dest:{second}:0"))  # the root, at 1
        await _settled(
            lambda: shell.session.tree(A)["children"][0].get("max_value") == 100.0
        )
        await _settled(lambda: not _none(user, "moved-note"))
        assert not menu.is_deleted  # no rebuild: the same menu
        assert menu.value is False
        assert button.props["aria-expanded"] == "false"
        assert tip.visible


async def test_a_move_closes_the_statistics_cards_editor(stats_shell):
    """Two statistics criteria on one quantity trading places move no
    shape: the Statistics card's open editor would show the other one, so
    the move closes it as it closes a plot's."""
    shell = stats_shell
    leaf = {"metric": "scalar", "quantity": NONLINEAR}
    swap = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {**leaf, "tolerance": 0},
            {**leaf, "tolerance": 5},
        ],
    }
    shell.session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(swap))])
    first = "/metrics/children/1"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        page.open_criterion(first)
        await _settled(lambda: page.open == (STATS, first))
        _drop(page, "/metrics/children/2", first, "before")
        await _settled(lambda: shell.session.tree(A)["children"][1]["tolerance"] == 5)
        await _settled(lambda: not _none(user, "moved-note"))
        assert page.open is None
        assert page.stats.open_path is None


async def test_an_event_ahead_of_the_moves_own_spec_event_keeps_the_line(
    nested_shell,
):
    """A `results` event queued in the same tick as the move, ahead of its
    own `spec` event, syncs first and says the line; the move's `spec`
    event that follows is still the move's, and must not take it away."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        await asyncio.sleep(0.3)
        shell._events.put({"event": "results", "models": {A}})
        _drop(page, N_Y, N_T2, "before")
        await asyncio.sleep(0.6)  # both events have been handled
        assert page.pending_moved is None
        assert not _none(user, "moved-note")
        # The next edit's `spec` event still takes it away.
        shell.session.stage([PatchOp(A, "replace", f"{N_Y}/max_value", 50.0)])
        await _settled(lambda: _none(user, "moved-note"))


async def test_two_moves_in_one_tick_keep_the_second_ones_line(nested_shell):
    """Two moves before the Shell's next tick: both `spec` events are
    queued, and the first one's — handled with the second move pending —
    is no edit after it. The second move's line stands."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        await asyncio.sleep(0.3)
        _drop(page, N_Y, N_T2, "before")  # [y, T2, k, x2]
        _drop(page, N_Y, N_T2, "before")  # [x2, y, T2, k]
        await asyncio.sleep(0.6)  # both events have been handled
        # Both went through: x's max is the first child now.
        assert shell.session.tree(A)["children"][0].get("max_value") == 100.0
        assert page.pending_moved is None
        assert not _none(user, "moved-note")


async def test_an_edit_staged_just_before_a_move_keeps_the_moves_line(
    nested_shell,
):
    """An edit and a move in one tick: the edit's `spec` event, handled
    first, is no edit AFTER the move, and the move's own that follows is
    the move's. Neither takes its line away."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        await asyncio.sleep(0.3)
        shell.session.stage([PatchOp(A, "replace", f"{N_T2}/min_value", -2.0)])
        _drop(page, N_Y, N_T2, "before")
        await asyncio.sleep(0.6)  # both events have been handled
        assert page.pending_moved is None
        assert not _none(user, "moved-note")
        # The next edit's `spec` event still takes it away.
        shell.session.stage([PatchOp(A, "replace", f"{N_Y}/max_value", 50.0)])
        await _settled(lambda: _none(user, "moved-note"))


async def test_a_failed_runs_statistics_line_says_there_is_no_run_result(
    stats_shell,
):
    """A run that failed recorded no statistics: nothing on its side was
    compared, so its closed line is no "all match" — it says the run has
    no result to compare, as a test with no run does."""
    from dstf.backends.types import TestResult

    shell = stats_shell
    result = shell.session.project.results[A]
    shell.session.project.results[A] = TestResult(
        model_id=A, success=False, variables=list(result.variables)
    )
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert _line_text(user) == "no run result"


# -- U5 Task 3: a click on a tree row ----------------------------------------

from dstf.authoring.shell.test_page import TREE as TREE_OPEN  # noqa: E402

#: Every kind of group, each over criteria on x and y (A tracks both).
GROUPS = {
    "combinator": "and",
    "children": [
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [
                _range(-1),
                {"metric": "range", "variable": "y", "min_value": -1},
            ],
        },
        {
            "combinator": "weighted",
            "weights": [1.0, 2.0],
            "threshold": 0.5,
            "children": [_range(-2), {"metric": "nrmse", "variable": "y"}],
        },
        {"combinator": "warn", "children": [_range(-3)]},
        {"combinator": "or", "children": [_range(-4), _range(-5)]},
    ],
}
G_K, G_W, G_WARN, G_OR = (f"/metrics/children/{i}" for i in range(4))


def _tree_click(user, path: str) -> None:
    _click(user, _one(user, f"tree-row:{path}"))


async def test_a_tree_leafs_click_opens_its_editor_under_the_tree_row(shell):
    """Q5 / Y4: the leaf's editor opens in its TREE row's slot, at
    TREE_CRITERION — Edit criteria, and no Edit on plot (it needs a
    figure) — and a second click closes it."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        editor = _one(user, f"tree-editor:{T1}")
        assert page.tree.targets[T1].slot in list(_ancestors(editor))
        assert [m for m in _marks(editor) if m.startswith("panel:")] == [
            "panel:edit-criteria"
        ]
        assert "dstf-crit-open" in _one(user, f"tree-row:{T1}").classes
        assert _none(user, f"editor:0:{T1}")  # nothing on the plot
        _tree_click(user, T1)
        await _settled(lambda: page.open is None)
        assert _none(user, f"tree-editor:{T1}")
        assert "dstf-crit-open" not in _one(user, f"tree-row:{T1}").classes
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        user.find(marker=f"tree-editor-done:{T1}").click()
        await _settled(lambda: page.open is None)


async def test_one_editor_page_wide_between_the_tree_and_the_plot(shell):
    """Q11: opening a criterion in the tree closes it on its plot, and
    the other way round."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        assert _none(user, f"editor:0:{T1}")
        assert "dstf-crit-open" not in page.cards[0].rows[T1].row.classes
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        assert _none(user, f"tree-editor:{T1}")
        assert "dstf-crit-open" not in _one(user, f"tree-row:{T1}").classes


async def test_a_tolerance_typed_in_the_tree_updates_the_plot_and_both_readouts(
    scored_shell,
):
    """Q11: live — one replace staged; the plot's figure re-sent, its row's
    readout and the tree row's readout repainted; the editor (its field)
    not rebuilt by its own edit."""
    shell = scored_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.cards[0].show()
        sent = _counted(page.cards[0])
        await _open_tree(user)
        _tree_click(user, T0)
        await _settled(lambda: page.open == (TREE_OPEN, T0))
        (lower,) = _numbers(page, "Lower bound (optional)")
        tree_readout = page.tree.painters[T0].readout
        plot_readout = page.cards[0].rows[T0].readout
        before = (tree_readout.text, plot_readout.text)
        with page.client:
            lower.set_value(5.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("min_value") == 5.0)
        await _settled(lambda: tree_readout.text != before[0])
        await _settled(lambda: plot_readout.text != before[1])
        assert tree_readout.text == plot_readout.text
        assert sent[0] >= 1
        assert len([op for op in shell.session.spec.pending if op.path == T0]) == 1
        assert _numbers(page, "Lower bound (optional)") == [lower]  # not rebuilt
        assert page.open == (TREE_OPEN, T0)


async def test_a_discard_elsewhere_rebuilds_the_tree_editor_once(scored_shell):
    shell = scored_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T0)
        await _settled(lambda: page.open == (TREE_OPEN, T0))
        (upper,) = _numbers(page, "Upper bound (optional)")
        with page.client:
            upper.set_value(5.0)
        await _settled(lambda: (_pending(shell, T0) or {}).get("max_value") == 5.0)
        shell.session.discard()
        await _settled(
            lambda: _numbers(page, "Upper bound (optional)")[0].value is None
        )
        rebuilt = _numbers(page, "Upper bound (optional)")[0]
        with page.client:
            page.sync()
        assert _numbers(page, "Upper bound (optional)") == [rebuilt]


async def test_edit_on_its_plot_is_offered_where_the_plot_has_handles(stats_shell):
    """Q12: a tube (handles) shows the link, which moves the editor to its
    plot's row; an nrmse (no handles) and a statistics leaf (no plot) do
    not."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)  # and[nrmse · x, scalar · statistic]
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        nrmse = "/metrics/children/0"
        _tree_click(user, nrmse)
        await _settled(lambda: page.open == (TREE_OPEN, nrmse))
        assert _none(user, f"tree-editor-on-plot:{nrmse}")
        _tree_click(user, leaf)
        await _settled(lambda: page.open == (TREE_OPEN, leaf))
        assert _none(user, f"tree-editor-on-plot:{leaf}")
        # The statistics card's editor is not open beside it.
        assert page.stats.editor is None


async def test_edit_on_its_plot_moves_a_tubes_editor_to_its_plot(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        tube = T0
        _tree_click(user, tube)
        await _settled(lambda: page.open == (TREE_OPEN, tube))
        user.find(marker=f"tree-editor-on-plot:{tube}").click()
        await _settled(lambda: page.open is not None and page.open[0] >= 0)
        assert page.open[1] == tube
        assert _none(user, f"tree-editor:{tube}")
        assert not _none(user, f"editor:{page.open[0]}:{tube}")


async def test_a_k_of_n_and_a_weighted_rows_click_opens_their_fields(nested_shell):
    """Q6: k for a k-of-n; weights, threshold and direction for a weighted;
    and / or / warn open nothing; a second click closes the form."""
    shell = nested_shell
    _stage_tree(shell, A, GROUPS)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _tree_click(user, G_K)
        await _settled(lambda: page.form is not None)
        assert page.form[0].key == f"tree:{G_K}"
        assert _one(user, f"form-title:tree:{G_K}").text == "Edit this combinator"
        labels = {e.props.get("label") for e in user.find(kind=ui.number).elements
                  if _one(user, f"form-row:tree:{G_K}") in list(_ancestors(e))}  # fmt: skip
        assert labels == {"k — how many must pass"}
        _tree_click(user, G_K)  # the open row's click closes its form
        await _settled(lambda: page.form is None)
        _tree_click(user, G_W)
        await _settled(
            lambda: page.form is not None and page.form[0].key == f"tree:{G_W}"
        )
        form = _one(user, f"form-row:tree:{G_W}")
        fields = {e.props.get("label") for e in form.descendants()
                  if isinstance(e, (ui.number, ui.input, ui.select))}  # fmt: skip
        assert {"Weights, one per child", "Threshold"} <= fields, fields
        assert any("irection" in str(f) for f in fields), fields
        for path in (G_WARN, G_OR, "/metrics"):
            _tree_click(user, path)
            await asyncio.sleep(0.1)
            assert page.form is None or page.form[0].key == f"tree:{G_W}"
            assert page.open is None


async def test_a_k_of_n_roots_click_opens_its_k(page_shell):
    """The root has fields when it is a k-of-n or a weighted: with the
    `tune` icon gone, its row's click is the one way to them."""
    shell = page_shell
    _stage_tree(
        shell, A, {"combinator": "k-of-n", "k": 1, "children": [_range(-1), _range(5)]}
    )
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_tree(user)
        _tree_click(user, "/metrics")
        await _settled(lambda: page.form is not None)
        assert page.form[0].key == "tree:/metrics"


async def test_the_tune_icon_is_gone(nested_shell):
    shell = nested_shell
    _stage_tree(shell, A, GROUPS)
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        await _open_tree(user)
        for path in (G_K, G_W):
            assert _none(user, f"edit:{path}")


async def test_a_rows_controls_stop_the_click_in_the_browser(nested_shell):
    """The kind dropdown's lead, the grip and the icon cluster stop a click
    before it reaches the row (Y5) — said by the handlers they carry; the
    user simulation's click never bubbles, so Chromium says the rest."""
    shell = nested_shell
    _stage_tree(shell, A, GROUPS)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        target = page.tree.targets[G_K]

        def stops(element) -> bool:
            return any(
                "stopPropagation" in (listener.js_handler or "")
                for listener in element._event_listeners.values()
                if listener.type == "click"
            )

        assert stops(target.lead)
        assert stops(target.grip)


async def test_a_move_closes_the_tree_editor_rather_than_rebind_it(nested_shell):
    """U4's swap hazard for the tree editor: a swap of two criteria on x
    moves no shape of the PLOTS; the tree rows are rebuilt, and an editor
    left open at /children/0 would show the other criterion."""
    shell = nested_shell
    _stage_tree(shell, A, SWAP)
    first = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _tree_click(user, first)
        await _settled(lambda: page.open == (TREE_OPEN, first))
        _drop(page, "/metrics/children/1", first, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        assert page.open is None
        assert _none(user, f"tree-editor:{first}")
        assert page.tree.editor is None


async def test_hiding_the_tree_closes_its_editor_and_focus_does_not(shell):
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        with page.client:
            page.focus(0)
        assert page.open == (TREE_OPEN, T1)
        with page.client:
            page.show_all()
        user.find(marker="tree:toggle").click()
        await _settled(lambda: page.open is None)
        assert page.tree.editor is None


async def test_a_plot_rebuild_keeps_the_tree_editor(nested_shell):
    """A context series staged elsewhere rebuilds the plot cards, not the
    tree rows: the editor under a tree row stays."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _tree_click(user, N_X2)
        await _settled(lambda: page.open == (TREE_OPEN, N_X2))
        shape = page._shape
        shell.session.stage(
            [PatchOp(A, "add", "/plots", [{"var": "x", "context": ["w"]}])]
        )
        await _settled(lambda: page._shape != shape)
        assert page.open == (TREE_OPEN, N_X2)
        assert not _none(user, f"tree-editor:{N_X2}")


async def test_a_rebuild_of_the_tree_rows_closes_the_tree_editor(shell):
    """An add staged elsewhere moves the tree's shape: the rows are rebuilt
    and the editor under one of them goes with them — the page's one
    editor is closed, not left naming a row that is gone."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        revision = page.tree.revision
        shell.session.stage([PatchOp(D, "add", "/metrics/children/-", _range(-9))])
        await _settled(lambda: page.tree.revision != revision)
        assert page.open is None
        assert page.tree.editor is None and page.tree.editor_path is None


def _clicks(page) -> dict[str, tuple]:
    """Each tree row's role, tab stop and what it says is open under it."""
    return {
        path: (
            row.props.get("role"),
            row.props.get("tabindex"),
            row.props.get("aria-expanded"),
        )
        for path, row in page.tree.rows_by_path.items()
    }


async def test_a_row_with_a_click_is_a_button_that_says_what_is_open(nested_shell):
    """F5: a criterion's row and a k-of-n's or a weighted's are buttons to
    the keyboard — a tab stop, and whether its editor or its fields are
    open under it — and an and / or / warn row, whose click opens nothing,
    is neither."""
    shell = nested_shell
    _stage_tree(shell, A, GROUPS)
    leaf = f"{G_K}/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        clicks = _clicks(page)
        for path in (G_K, G_W, leaf, f"{G_OR}/children/1"):
            assert clicks[path] == ("button", "0", "false"), path
        for path in ("/metrics", G_WARN, G_OR):
            assert clicks[path] == (None, None, None), path
        _tree_click(user, leaf)
        await _settled(lambda: _clicks(page)[leaf][2] == "true")
        _tree_click(user, G_K)  # the editor goes, the fields open
        await _settled(lambda: _clicks(page)[G_K][2] == "true")
        assert _clicks(page)[leaf][2] == "false"
        _tree_click(user, G_K)
        await _settled(lambda: _clicks(page)[G_K][2] == "false")


async def test_a_tree_editor_opens_the_path_to_it_and_a_fold_over_it_closes_it(
    shell,
):
    """F9 (R2, E6): an editor opened under a row inside a folded group
    unfolds the path to it — and no other folded group; Collapse all, which
    hides its row, takes it away, as Hide tree does."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        leaf = f"{G2}/children/0"
        _fold(user, G3)
        _fold(user, G1)
        await _settled(lambda: len(page.state.tree_folded.get(A, [])) == 2)
        page.open_tree_criterion(leaf)
        await _settled(lambda: page.open == (TREE_OPEN, leaf))
        assert page.state.tree_folded[A] == [G3]
        assert leaf not in _hidden(page)
        user.find(marker="tree:collapse-all").click()
        await _settled(lambda: page.open is None)
        assert page.tree.editor is None and _none(user, f"tree-editor:{leaf}")


async def test_edit_on_its_plot_goes_when_the_mode_has_no_handles(shell):
    """F2: the link is repainted with the editor — a tube switched to nrmse
    in the tree editor has nothing to drag on its plot."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T0)
        await _settled(lambda: page.open == (TREE_OPEN, T0))
        assert not _none(user, f"tree-editor-on-plot:{T0}")
        (mode,) = [
            e
            for e in user.find(kind=ui.select).elements
            if e.props.get("label") == "mode"
        ]
        with page.client:
            mode.set_value("nrmse")
        await _settled(lambda: (_pending(shell, T0) or {}).get("metric") == "nrmse")
        await _settled(lambda: _none(user, f"tree-editor-on-plot:{T0}"))
        assert page.open == (TREE_OPEN, T0)


#: A criterion that scores a statistic: on no plot.
STAT_X = {"metric": "scalar", "variable": "x", "quantity": NONLINEAR}


async def test_edit_on_its_plot_opens_on_the_plot_that_holds_it_at_the_click(
    page_shell,
):
    """J3: "Edit on its plot" finds its plot when it is CLICKED. The tree
    refreshes before the cards rebuild in a sync, so a link bound to the
    index it read then named the plot the criterion WAS on until a later
    sync drew it again: x's criteria made statistics, x's plot goes and
    y's moves from 1 to 0, and a click landing right after that sync (the
    review's sequence) opened nothing."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_tree(user)
        _tree_click(user, RANGE_Y)
        await _settled(lambda: page.open == (TREE_OPEN, RANGE_Y))
        assert next(c.index for c in page.cards if RANGE_Y in c.rows) == 1
        real_sync, opened = page.sync, []

        def sync(force: bool = False) -> None:
            shape = page._shape
            real_sync(force)
            if page._shape != shape and not opened:
                # The click, before any later sync can draw the link again.
                assert next(c.index for c in page.cards if RANGE_Y in c.rows) == 0
                assert page.open == (TREE_OPEN, RANGE_Y)
                with page.client:
                    user.find(marker=f"tree-editor-on-plot:{RANGE_Y}").click()
                opened.append(page.open)

        page.sync = sync
        shell.session.stage(
            [
                PatchOp(A, "replace", TUBE, dict(STAT_X)),
                PatchOp(A, "replace", RANGE_X, dict(STAT_X)),
            ]
        )
        await _settled(lambda: opened)
        assert opened == [(0, RANGE_Y)]


async def test_edit_on_its_plot_scrolls_to_the_plots_row(shell):
    """Q12: the link opens the criterion on its plot AND brings that row to
    the middle of the viewport, by its id."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T0)
        await _settled(lambda: page.open == (TREE_OPEN, T0))
        sent = _js_sent(page)
        user.find(marker=f"tree-editor-on-plot:{T0}").click()
        await _settled(lambda: page.open is not None and page.open[0] >= 0)
        assert page.open is not None
        row = page.cards[page.open[0]].rows[T0].row
        assert any(row.html_id in js and "scrollIntoView" in js for js in sent), sent


async def test_a_tree_editor_closes_the_statistics_cards(stats_shell):
    """Q11, one editor page-wide: a statistics criterion open in the
    Statistics card closes when the tree opens it under its row."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        await _open_tree(user)
        _tree_click(user, leaf)
        await _settled(lambda: page.open == (TREE_OPEN, leaf))
        assert page.stats.editor is None and _none(user, f"stats-editor:{leaf}")
        assert "dstf-crit-open" not in _one(user, f"stats:row:{NONLINEAR}").classes


async def test_a_statistics_editor_closes_the_tree_editor(stats_shell):
    """Q11, the other way: the tree's editor closes when the Statistics card
    opens the same criterion under its row."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _tree_click(user, leaf)
        await _settled(lambda: page.open == (TREE_OPEN, leaf))
        page.stats.toggle()
        _click(user, _one(user, f"stats:row:{NONLINEAR}"))
        await _settled(lambda: page.open == (STATS, leaf))
        assert page.tree.editor is None and _none(user, f"tree-editor:{leaf}")
        assert page.tree.editor_path is None


async def test_the_show_icon_flashes_a_family_plots_row(nested_shell):
    """Q7 on a family's plot: the ⤓ of each criterion on it — at two depths
    — draws the plot, scrolls to its row and flashes it; nothing opens."""
    shell = nested_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        for path in (N_T2, N_KT1):
            sent = _js_sent(page)
            user.find(marker=f"show:{path}").click()
            await _settled(lambda: page.cards[0].plot is not None)
            html_id = page.cards[0].rows[path].row.html_id

            def flashed(html_id=html_id, sent=sent) -> bool:
                return any(
                    html_id in js and 'classList.add("dstf-flash")' in js for js in sent
                )

            await _settled(flashed)
            assert page.open is None


async def test_moving_the_editor_into_the_tree_resends_its_plot(shell):
    """Q11: the plot's editor draws its rings; opening the same criterion in
    the tree closes it there, and the plot is re-sent without them."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        with page.client:
            page.cards[0].show()
        await _open_tree(user)
        user.find(marker=f"criterion:0:{T1}").click()
        await _settled(lambda: page.open == (0, T1))
        sent = _counted(page.cards[0])
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        assert sent[0] >= 1


async def test_a_leaf_click_with_a_k_of_n_form_open_opens_its_editor(nested_shell):
    """F8: a click closes a form only when the form is under the clicked
    row; a criterion clicked while its k-of-n's fields are open opens its
    editor, and the fields go (one thing open)."""
    shell = nested_shell
    _stage_tree(shell, A, GROUPS)
    leaf = f"{G_K}/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _tree_click(user, G_K)
        await _settled(lambda: page.form is not None)
        _tree_click(user, leaf)
        await _settled(lambda: page.open == (TREE_OPEN, leaf))
        assert page.form is None


async def test_opening_a_tree_editor_takes_the_moved_line_away(nested_shell):
    """A move's line stays until the next form, editor or edit: a tree
    editor opened takes it, as a plot's does."""
    shell = nested_shell
    _stage_tree(shell, A, SWAP)
    first = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        _drop(page, "/metrics/children/1", first, "before")
        await _settled(lambda: not _none(user, "moved-note"))
        _tree_click(user, first)
        await _settled(lambda: page.open == (TREE_OPEN, first))
        assert _none(user, "moved-note")


def _js_sent(page) -> list[str]:
    """Every script the page sends its client, recorded from now on."""
    sent: list[str] = []
    real = page.client.run_javascript

    def recording(code, *args, **kwargs):
        sent.append(code)
        return real(code, *args, **kwargs)

    page.client.run_javascript = recording
    return sent


async def test_a_jump_from_an_open_tree_editor_scrolls_to_the_plots_row(shell):
    """F3: the closed line's jump finds the row it opened BY ITS ID — a
    `.dstf-crit-open` query found the tree row whose editor it had just
    closed, the class going with an update the script overtook."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        sent = _js_sent(page)
        with page.client:
            page.jump_to(T1)
        assert page.open is not None and page.open[0] >= 0
        row = page.cards[page.open[0]].rows[T1].row
        scrolls = [js for js in sent if "scrollIntoView" in js]
        assert scrolls and all(row.html_id in js for js in scrolls), sent
        assert not any(".dstf-crit-open" in js for js in sent)


async def test_a_reveal_from_an_open_tree_editor_scrolls_to_the_plots_row(shell):
    """H6 (F3's hazard in `_reveal`): an add's reveal, with a tree editor
    open, finds the row it opened and its editor BY THEIR IDS — a
    `.dstf-crit-open` query found the tree row the open had just closed,
    and a `.dstf-editor` one the tree's editor, going with the same
    update the script can overtake."""
    _stage_tree(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        await _open_tree(user)
        _tree_click(user, T1)
        await _settled(lambda: page.open == (TREE_OPEN, T1))
        sent = _js_sent(page)
        with page.client:
            page._reveal(T1, None)
        assert page.open is not None and page.open[0] >= 0
        card = page.cards[page.open[0]]
        row, editor = card.rows[T1].row, card.editor_box
        assert editor is not None
        scrolls = [js for js in sent if "scrollIntoView" in js]
        assert scrolls, sent
        assert all(row.html_id in js and editor.html_id in js for js in scrolls), sent
        assert not any("querySelector" in js for js in scrolls), sent


# -- U5 Task 4: the jump icon ------------------------------------------------


async def test_the_jump_icon_shows_a_criterion_on_its_plot_and_opens_nothing(
    page_shell,
):
    """Q7: every criterion row has one, no group row does; it draws a lazy
    plot, scrolls to the criterion's row and flashes it — no editor opens,
    and an open one is not moved."""
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_tree(user)
        assert _none(user, "show:/metrics")
        for path in (TUBE, RANGE_X, RANGE_Y):
            icon = _one(user, f"show:{path}")
            assert icon.props.get("aria-label") == "Show on its plot"
        card = page.cards[1]
        assert card.plot is None  # y's plot is not drawn yet
        sent = _js_sent(page)
        user.find(marker=f"show:{RANGE_Y}").click()
        await _settled(lambda: card.plot is not None)
        row = card.rows[RANGE_Y].row
        assert any(row.html_id in js and "dstf-flash" in js for js in sent), sent
        assert page.open is None and page.form is None
        # An editor open in the tree stays where it is.
        _tree_click(user, RANGE_X)
        await _settled(lambda: page.open == (TREE_OPEN, RANGE_X))
        user.find(marker=f"show:{TUBE}").click()
        await asyncio.sleep(0.1)
        assert page.open == (TREE_OPEN, RANGE_X)


async def test_the_jump_icon_ends_a_focus_that_hides_its_plot(page_shell):
    shell = page_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        await _open_tree(user)
        with page.client:
            page.focus(0)
        assert not page.cards[1].card.visible
        user.find(marker=f"show:{RANGE_Y}").click()
        await _settled(lambda: page.focused is None)
        assert page.cards[1].card.visible
        # A focus that shows the plot is kept.
        with page.client:
            page.focus(1)
        user.find(marker=f"show:{RANGE_Y}").click()
        await asyncio.sleep(0.1)
        assert page.focused == 1


async def test_a_statistics_criterions_jump_opens_the_card_at_its_row(stats_shell):
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        icon = _one(user, f"show:{leaf}")
        assert icon.props.get("aria-label") == "Show on the Statistics card"
        assert not page.stats.is_open
        sent = _js_sent(page)
        user.find(marker=f"show:{leaf}").click()
        await _settled(lambda: page.stats.is_open)
        row = page.stats.rows_by_path[NONLINEAR]
        assert any(row.html_id in js and "dstf-flash" in js for js in sent), sent
        assert page.open is None and page.stats.editor is None


async def test_the_jump_icons_name_follows_a_mode_switch(stats_shell):
    """F8: the ⤓'s name is repainted, never fixed at build — a mode switch
    moves no shape of the tree, so the row stands."""
    shell = stats_shell
    leaf = _stats_leaf_staged(shell)
    nrmse = "/metrics/children/0"
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        await _open_tree(user)
        assert _one(user, f"show:{nrmse}").props["aria-label"] == "Show on its plot"
        revision = page.tree.revision
        stat = {"metric": "scalar", "variable": "x", "quantity": NONLINEAR}
        shell.session.stage([PatchOp(A, "replace", nrmse, stat)])
        await _settled(
            lambda: (
                _one(user, f"show:{nrmse}").props["aria-label"]
                == "Show on the Statistics card"
                or page.tree.revision != revision
            )
        )
        assert page.tree.revision == revision  # repainted, not rebuilt
        assert _plus_tips(_one(user, f"show:{nrmse}")) == [
            "Show on the Statistics card"
        ]
        _ = leaf


async def test_the_jump_icon_leaves_the_folds_alone(shell):
    """R2: the ⤓ shows a plot's row, not a tree row — a folded group over
    its criterion stays folded."""
    async with user_simulation(root=shell.index) as user:
        page = await _deep_open(shell, user)
        _fold(user, G1)
        await _settled(lambda: page.state.tree_folded.get(A) == [G1])
        with page.client:
            page.show_criterion(f"{G1}/children/0")
        assert page.state.tree_folded[A] == [G1]
        assert f"{G1}/children/0" in _hidden(page)


# -- the tolerance grips' hover module (D147) ---------------------------------


async def test_the_test_page_loads_the_grips_module_and_serves_it(shell):
    """`grips.js` is in the page's FIRST html — NiceGUI inserts head HTML
    after the handshake through insertAdjacentHTML, which runs no script —
    and the route it names serves the file beside `suite_grid.js`."""
    from dstf.authoring.shell.plot_card import GRIPS_JS, GRIPS_URL

    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert page.client.head_html.count(f'<script src="{GRIPS_URL}"></script>') == 1
        served = await user.http_client.get(GRIPS_URL)
        assert served.status_code == 200
        assert served.text == GRIPS_JS.read_text(encoding="utf-8")
        assert "window.DstfGrips" in served.text


async def test_a_drawn_plot_installs_its_grips_and_a_dropped_card_takes_them_back(
    shell,
):
    """One instance of the hover module per DRAWN plot, named by the plot
    element's id; a card that is dropped takes its own back — its document
    and window listeners with it — and one never drawn has none."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        sent = _js_sent(page)
        card = page.cards[0]
        assert card.plot is None
        card.show()
        plot_id = card.plot.id
        assert [c for c in sent if "DstfGrips" in c] == [
            f"DstfGrips.install({plot_id})"
        ]
        # Drawn once: a second ask installs nothing again.
        card.show()
        card.redraw()
        assert sum("DstfGrips.install" in c for c in sent) == 1
        del sent[:]
        card.dispose()
        assert [c for c in sent if "DstfGrips" in c] == [
            f"DstfGrips.destroy({plot_id})"
        ]


async def test_a_card_never_drawn_has_no_grips_to_take_back(shell):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        sent = _js_sent(page)
        assert page.cards[0].plot is None
        page.cards[0].dispose()
        assert not [c for c in sent if "DstfGrips" in c]


def test_the_stylesheet_hides_a_grip_at_rest_by_display_and_shows_it_on_touch():
    """`display: none`, never `visibility` or `opacity`: Plotly writes
    inline `opacity: 1; pointer-events: all` on every editable shape, so a
    grip hidden either way is still grabbed. Where there is no hover (a
    touch screen) the open criterion's grips are always shown — but an
    arrow marked OFF the plot (`data-grip-off`), which is displayed
    nowhere: its own rule hides it in a shown group, and the touch rule
    leaves it out."""
    import re

    from dstf.authoring.shell.style import STYLE

    css = re.sub(r"\s+", " ", STYLE)
    hidden = ".js-plotly-plot .shapelayer path[data-grip]:not([data-grip-on])"
    assert f"{hidden} {{ display: none; }}" in css
    assert ".js-plotly-plot .shapelayer path[data-grip-off] { display: none; }" in css
    assert (
        "@media (hover: none), (pointer: coarse) {"
        f" {hidden}:not([data-grip-off]) {{ display: inline; }} }}"
    ) in css
    assert css.index(f"{hidden} {{ display: none; }}") < css.index(
        "@media (hover: none)"
    )
    # Nothing else of the stylesheet shows a grip.
    assert css.count("display: inline;") == 1


def test_the_stylesheet_alone_takes_plotlys_clip_off_a_grip_in_two_rules():
    """Plotly clips a shape to the plot area by the `clip-path` ATTRIBUTE —
    on its group as drawn, on the path itself while it is held — and a
    grip's arrow stands outside its edge. ONE mechanism un-clips it: the
    stylesheet, whose property beats the attribute wherever and whenever
    it is written; `grips.js` takes no attribute off. A rule for the path
    and one for its group, and no third. Neither names an arrow marked off
    the plot (`data-grip-off`): that one is not displayed, and a clip on
    what is not displayed is nothing — the mark is read by the two
    `display` rules and by no other. (What a person gets is read in the
    browser: `test_an_arrow_pressed_in_the_margin_of_a_zoomed_plot_stays_
    painted_while_held`, `test_an_arrow_whose_anchor_is_off_the_plot_is_
    hidden_until_it_is_panned_back`.)"""
    import re

    from dstf.authoring.shell.plot_card import GRIPS_JS
    from dstf.authoring.shell.style import STYLE

    css = re.sub(r"\s+", " ", STYLE)
    on = "path[data-grip]"
    assert f" .js-plotly-plot .shapelayer {on} {{ clip-path: none; }}" in css
    assert (
        f" .js-plotly-plot .shapelayer g.shape-group:has(> {on}) {{ clip-path: none; }}"
    ) in css
    assert css.count("clip-path: none") == 2
    assert not re.search(r"Attribute\(\s*[\"']clip-path", GRIPS_JS.read_text("utf-8"))
    rules = re.findall(r"[^{}/]*\[data-grip-off\][^{}]*\{[^{}]*\}", css)
    assert len(rules) == 2 and all("display:" in rule for rule in rules), rules


def test_the_stylesheet_moves_a_staggered_arrow_by_the_modules_own_step():
    """A colliding arrow is moved across its axis by whole steps:
    `grips.js` works out, at STEP px a step, which of its LEVELS clears
    the arrows already placed, and marks the path with the level
    (`data-grip-shift`); the stylesheet turns the level into pixels. Two
    files, one number: every level has its rule, at the module's step, and
    a time arrow is moved in y, a value arrow in x. (What a person gets is
    read in the browser: `test_colliding_arrows_are_staggered_and_each_
    takes_its_own_press`.)"""
    import re

    from dstf.authoring.shell.plot_card import GRIPS_JS
    from dstf.authoring.shell.style import STYLE

    js = GRIPS_JS.read_text("utf-8")
    step = int(re.search(r"const STEP = (\d+);", js).group(1))
    levels = json.loads(re.search(r"const LEVELS = (\[[^\]]+\]);", js).group(1))
    assert (step, levels) == (9, [-1, 1, -3, 3])
    css = re.sub(r"\s+", " ", STYLE)
    path = ".js-plotly-plot .shapelayer path"
    for level in levels:
        assert (
            f'{path}[data-grip-shift="{level}"] {{ --grip-across: {level * step}px; }}'
            in css
        )
    assert css.count("--grip-across:") == len(levels)
    assert (
        f'{path}[data-grip-axis="x"][data-grip-shift]'
        " { transform: translate(0, var(--grip-across)); }"
    ) in css
    assert (
        f'{path}[data-grip-axis="y"][data-grip-shift]'
        " { transform: translate(var(--grip-across), 0); }"
    ) in css
    assert css.count("transform: translate(") == 2


def test_an_arrows_cursor_rule_carries_no_important_and_the_inert_rule_does():
    """plotly.js sets a draggable shape's cursor by CLASS — `setCursor` adds
    `cursor-move`, styled by `.js-plotly-plot .plotly .cursor-move`
    (specificity 0,3,0) — never inline: the arrows' own rule, two classes,
    an attribute and an element (0,3,1), outranks it as it stands. The INERT
    rule's `!important` is needed: Plotly writes `pointer-events: all`
    inline on every editable shape. (The cursor a person gets is read in
    the browser: `test_an_arrow_says_what_it_sets_and_shows_which_way_it_
    moves`.)"""
    import re

    from dstf.authoring.shell.style import STYLE

    css = re.sub(r"\s+", " ", STYLE)
    arrow = '.js-plotly-plot .shapelayer path[data-grip-axis="{}"]'
    assert f"{arrow.format('y')} {{ cursor: ns-resize; }}" in css
    assert f"{arrow.format('x')} {{ cursor: ew-resize; }}" in css
    assert (
        ".js-plotly-plot .shapelayer [data-inert],"
        " .js-plotly-plot .shapelayer [data-inert] *"
        " { pointer-events: none !important; }"
    ) in css
