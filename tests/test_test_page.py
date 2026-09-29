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

from dstf.authoring import capabilities  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Artifact, Job, Patch, Placement  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell import plot_card  # noqa: E402
from dstf.authoring.shell.actions import ActionBar, InlineHost  # noqa: E402
from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
from dstf.authoring.shell.variables_drawer import marker as var_marker  # noqa: E402
from dstf.authoring.shell.widgets import MiddleCut, middle_split  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
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
    _Gate,
    _two_var_project,
)


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
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, C)
        assert _one(user, "plots:count").text == "no plots: this test has no result"
        assert page.cards == []


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
        assert _one(user, "plots:count").text == "no plots: this test has no result"


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
        assert _one(user, "plots:count").text == "no plots: this test has no result"


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
        (why,) = _inside(user, "why:edit-tree", "criteria-header:0")
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
        select = _one(user, "apply-add:pipe.T[1]:variable")
        assert select.value == "pipe.T[1]"
        assert set(select.options) == {"x", "y", "pipe.T[1]", "pipe.T[2]"}


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
        # w is not tracked: its `+` is there, grey, and says when (C2).
        (plus,) = _inside(user, "add-for:w", "criteria-header:3")
        assert not plus.enabled
        assert _plus_tips(plus) == [
            "Criteria on an untracked variable come with the next step (U2)"
        ]
        user.find(marker="remove-plot:3").click()
        assert _pending(shell, "/plots") == [{"var": "x", "context": ["z"]}]
        await _settled(lambda: len(page.cards) == 3)


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


async def test_the_plots_plus_follows_the_job_that_locked_the_test(shell):
    """The criteria header is painted, not rebuilt, so what its `+` MAY do
    has to be re-read on every Session event: a rerun takes it and says
    why, and the end of the job brings it back."""
    gate = _Gate()
    header = "criteria-header:0"

    def why(user) -> str:
        found = _inside(user, "why:edit-tree", header)
        return found[0].text if found else ""

    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user)
        assert why(user) == "" and _inside(user, "add-for:x", header)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await _settled(lambda: why(user) == f"rerun is running on {A}")
        assert _inside(user, "add-for:x", header) == []
        gate.release.set()
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
    from dstf.authoring.builtin import edit_tree

    shell = _watch_shell(tmp_path, n - 2)
    session = shell.session
    calls = _count_reads(session)
    # A card's `+` asks whether its variable is tracked: it reads the list
    # the page hands down (`Selection.tracked`), never its own.
    listed = [0]
    real_names = edit_tree._variable_names

    def counting_names(*a, **k):
        listed[0] += 1
        return real_names(*a, **k)

    monkeypatch.setattr(edit_tree, "_variable_names", counting_names)
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
        assert listed[0] == 0, (listed, n)
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
            if s.get("type") == "circle"
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


async def test_a_jump_to_a_criterion_on_no_plot_says_so(shell, monkeypatch):
    """A criterion whose variable is on no plot of this test has no row to
    open: the jump said nothing and — with another editor open — scrolled to
    THAT row instead."""
    from dstf.authoring.shell import test_page as page_mod

    _stage_tree(shell, A, ON_NO_PLOT)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert [c.resolved.subject for c in page.cards] == [("x",)]
        user.find(marker=f"criterion:0:{T0}").click()
        await _settled(lambda: page.open == (0, T0))
        scrolled: list[str] = []
        monkeypatch.setattr(page_mod.ui, "run_javascript", scrolled.append)
        with page.client:
            page.jump_to(T1)
        await _settled(lambda: user.notify.messages != [])
        assert user.notify.messages == ["y is on no plot of this test"]
        assert scrolled == []  # never the OTHER editor's row
        assert page.open == (0, T0)


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


async def test_the_focus_bar_and_a_jump_read_the_label(
    awkward_expression_shell, monkeypatch
):
    """Two places that name a variable, on the page whose titles already
    read its expression: the focus switcher's option, and the jump's "is on
    no plot". Each said `unitTests.x[1]`, which names nothing a person
    recognises."""
    from dstf.authoring.shell import test_page as page_mod
    from test_session import AWKWARD_EXPRESSION, UT1

    shell = awkward_expression_shell
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _one(user, "plot-title:0").text == AWKWARD_EXPRESSION
        user.find(marker="focus:0").click()
        await _settled(lambda: page.focused == 0)
        assert _one(user, "focus-bar:text").text == "Showing 1 of 2 plots"
        assert _one(user, "focus-switch").options[0] == AWKWARD_EXPRESSION
        # A criterion whose variable is on no plot: the same name, read the
        # same way. A stored-scalar leaf names a statistic and derives no
        # plot, and nothing else scores the UnitTests variable here.
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "range", "variable": "y", "min_value": -1},
                {
                    "metric": "scalar",
                    "variable": UT1,
                    "quantity": "CPUtime.final",
                    "max_value": 1e9,
                },
            ],
        }
        _stage_tree(shell, A, tree)
        await _settled(
            lambda: [c.resolved.series for c in _page(shell, user).cards] == [("y",)]
        )
        monkeypatch.setattr(page_mod.ui, "run_javascript", lambda *a, **k: None)
        page = _page(shell, user)
        with page.client:
            page.jump_to(T1)
        await _settled(lambda: user.notify.messages != [])
        assert user.notify.messages == [
            f"{AWKWARD_EXPRESSION} is on no plot of this test"
        ]


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
        assert not any(row.swatch.visible for row in card.rows.values())
        with page.client:
            card.show()
        assert {p for p, row in card.rows.items() if row.swatch.visible} == {
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
        assert card.rows[windowed].swatch.visible


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
    return any(s.get("type") == "circle" for s in card.plot.figure["layout"]["shapes"])


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


class _LockedRowProbe(_RowProbe):
    """`_RowProbe` whose `available` follows the job lock, the way Edit
    tree's does: under a rerun its host is rebuilt to the sentence."""

    def available(self, s):
        model = next(iter(s.selection.tests))
        if s.running is not None and s.is_locked(model):
            return f"{s.running.label} is running on {model}"
        return True


async def test_a_form_row_goes_when_its_host_is_rebuilt_under_a_lock(page_shell):
    """Under a job lock the host said "rerun is running on …" while its row
    stayed open with live buttons, and an act from it staged nothing and
    showed nothing: the error label had gone with the host's rebuild."""
    shell = page_shell
    _only(_LockedRowProbe())
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        _click_in(user, f"probe-open:{RANGE_X}", within=f"criterion:0:{RANGE_X}")
        await user.should_see(marker=f"form-row:crit:0:{RANGE_X}")
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        try:
            await _settled(
                lambda: bool(_inside(user, "why:row-probe", f"node:0:{RANGE_X}"))
            )
            assert page.form is None
            assert _none(user, f"form-row:crit:0:{RANGE_X}")
            row = page.cards[0].rows[RANGE_X].row
            assert "dstf-row-open" not in row.classes
        finally:
            gate.release.set()


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
        _click_in(user, "edit:/metrics", within=within)
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
        await _settled(lambda: not _none(user, "edit:/metrics"))
        _click_in(user, "edit:/metrics", within=within)
        await user.should_see(marker="form-row:tree:/metrics")
        assert k_field().value == 1  # the node's, not the discarded 2
        # Now with the form OPEN across the edit and its Discard.
        with page.client:
            k_field().set_value(2)
        user.find(marker="apply-edit:/metrics").click()
        await _settled(lambda: shell.session.tree(A).get("k") == 2)
        _click_in(user, "edit:/metrics", within=within)
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
    the tab's. The fixture's verdicts: A FAIL, B NO_REF, C SIM_FAIL, D PASS,
    G PASS."""

    async def test_sort_by_name_and_by_status_failures_first(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            assert _shown(shell, user) == [A, B, C, D, G]
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, C, B, D, G])
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
        """FAIL, SIM_FAIL, scoring failed, NO_REF, PASS, then a test with no
        verdict yet — not `STATUS_ORDER`, which lists PASS first."""
        async with user_simulation(root=shell.index) as user:
            await _tests_drawer(shell, user)
            shell.load.failed_models.append(G)
            del shell.session.comparisons[G]
            shell.session.comparisons.pop(D)
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, C, G, B, D])

    async def test_the_status_sort_follows_a_rescore(self, shell):
        async with user_simulation(root=shell.index) as user:
            page = await _tests_drawer(shell, user)
            await _pick(user, "tests-drawer:sort", "status")
            await _settled(lambda: _shown(shell, user) == [A, C, B, D, G])
            # D's nrmse 6e-4 fails at 1e-5: D moves up among the failures
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            drawer = page.rail.tests
            await _settled(lambda: drawer.list.rows[D].verdict == "FAIL")
            await _settled(lambda: _shown(shell, user) == [A, D, C, B, G])

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
            assert _one(user, "tests-drawer:pill:SIM_FAIL").text == "SIM_FAIL 1"
            assert _one(user, "tests-drawer:pill:NO_REF").text == "NO_REF 1"
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
        a job's poll repainted the rows every second."""
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
            await _settled(lambda: _shown(shell, user) == [A, C, B, D, G])
            asked.clear()
            # D's nrmse 6e-4 fails at 1e-5: D moves up among the failures
            shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
            await _settled(lambda: _shown(shell, user) == [A, D, C, B, G])
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
        from dstf.authoring.session import LockedError

        shell = page_shell
        error = LockedError([A], "Rerun")

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
        Reset stages its removal after it — two ops, since a remove after an
        add is kept as two — and the button goes again, because the
        EFFECTIVE entry has no key, whatever the pending list still holds."""
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
            assert [
                (op.model_id, op.op, op.path) for op in shell.session.spec.pending
            ] == [
                (B, "add", "/plots"),
                (B, "remove", "/plots"),
            ]
            await _settled(lambda: _none(user, "tree:reset-layout"))
            await _settled(lambda: page.cards[0].resolved.context == ())

    async def test_a_refused_reset_is_said_and_changes_nothing(
        self, page_shell, monkeypatch
    ):
        from dstf.authoring.session import LockedError

        shell = page_shell
        error = LockedError([A], "Rerun")

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
        key goes with it (`layout_op`: no extras is a remove)."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            await _drawer(shell, user)
            user.find(marker=var_marker("var-check", ROW_Z)).click()
            await _settled(lambda: _checked(user, ROW_Z) == "true")
            user.find(marker=var_marker("var-check", ROW_Z)).click()
            assert [op.op for op in shell.session.spec.pending] == ["add", "remove"]
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
            (op,) = [op for op in shell.session.spec.pending if op.op == "remove"]
            with caplog.at_level(logging.ERROR):
                check.click()  # the row drawn for plot 3, before any rebuild
            assert _raised(caplog) == []
            assert shell.session.spec.pending[-1] == op

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
            assert _one(user, "var-empty").text == "this test has no result"
            assert _one(user, "var-target").text == "No plot yet: + new plot adds one"

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
        """`TestPage.sync` ends by refreshing the rail, and a Rerun's five
        job events rebuilt an open drawer five times over rows that had not
        moved. A build clears the body first: that is what is counted."""
        shell = drawer_shell
        async with user_simulation(root=shell.index) as user:
            page = await _drawer(shell, user)
            drawer = page.rail.variables
            assert drawer is not None
            built = []
            clear = drawer.body.clear
            drawer.body.clear = lambda: (built.append(1), clear())[1]
            with page.client:
                for state in ("started", "running", "finished"):
                    page.handle({"event": "job", "state": state})
            assert built == []
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
    for name in ("accept", "rerun"):
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
        assert _strip_buttons(user) == {"accept": "filled", "rerun": "quiet"}
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
        assert _strip_buttons(user) == {"accept": "menu", "rerun": "quiet"}
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


async def test_the_strip_on_an_unfinished_run_fills_rerun(shell):
    """C's last run never finished it (its manifest entry is still pending):
    there is no result to read, whatever the loaded TestResult says."""
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, C)
        assert shell.session.project.results.get(C) is not None
        assert _one(user, "strip-text").text == (
            "No result yet — rerun to simulate it."
        )
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "menu", "rerun": "filled"}
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
        assert _strip_buttons(user) == {"accept": "quiet", "rerun": "quiet"}


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
        assert _strip_buttons(user) == {"accept": "menu", "rerun": "menu"}
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


async def test_the_strips_discard_is_refused_while_a_job_runs(shell):
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, D)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})])
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({B}), reloads_results=False)
        )
        await _settled(lambda: shell.session.running is not None)
        button = page.strip.discard.button
        await _settled(lambda: not button.enabled)
        with page.client:
            page.strip._discard()  # a click in the tick the job started in
        assert len(shell.session.spec.pending) == 1
        gate.release.set()
        await _settled(lambda: shell.session.running is None)
        await _settled(lambda: button.enabled)


async def test_the_strip_says_a_running_job_on_this_test(shell):
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({A}), reloads_results=False)
        )
        await _settled(
            lambda: _one(user, "strip-text").text == "Probe is running on this test."
        )
        assert "dstf-strip-grey" in _one(user, "strip").classes
        gate.release.set()
        await _settled(lambda: "dstf-strip-red" in _one(user, "strip").classes)


async def test_a_gone_test_leaves_the_strip_blank_and_a_returning_one_fills_it(
    shell, tmp_path
):
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user, A)
        shell.session.close()
        await _settled(lambda: page.gone)
        assert _one(user, "strip-text").text == ""
        assert page.strip.situation is None
        assert _none(user, "action:accept") and _none(user, "action:rerun")
        cfg, work = _two_var_project(tmp_path / "again")
        shell.session.open(cfg, work_dir=work)
        await _settled(lambda: not page.gone)
        assert "dstf-strip-red" in _one(user, "strip").classes
        assert _strip_buttons(user) == {"accept": "filled", "rerun": "quiet"}


async def test_a_job_on_another_test_leaves_this_strip_alone(shell):
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({B}), reloads_results=False)
        )
        await _settled(lambda: shell.session.running is not None)
        await asyncio.sleep(0.3)
        text = _one(user, "strip-text").text
        gate.release.set()
        await _settled(lambda: shell.session.running is None)
        assert text == "Passes against its baseline."


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
    """A reload after the open (a Rerun's, an Accept's) whose scoring of D
    raises: the load is done, so D is not waiting to be scored."""
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


async def test_a_strip_that_raises_still_locks_the_bar(shell, monkeypatch):
    """The strip's paint refreshes the bar; when it raises, the bar is
    refreshed anyway: a job on this test greys its Rerun."""
    from dstf.authoring.shell import shell as shell_mod

    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, D)
        assert _one(user, "action:rerun").enabled
        # No 1 s poll during the job: its refresh of the bar would hide the
        # one the paint owes (the timer's interval was set at mount).
        monkeypatch.setattr(shell_mod, "TICK", 1e-6)
        _strip_raises(monkeypatch)
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({D}), reloads_results=False)
        )
        await _settled(lambda: shell.session.running is not None)
        await _settled(lambda: not _one(user, "action:rerun").enabled)
        gate.release.set()
        await _settled(lambda: shell.session.running is None)


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
        assert children.index(lead) == 1  # right after the pill
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
        await _open_form(user, "edit:/metrics", within="node:/metrics")
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


async def test_a_locked_test_reads_its_kind_in_the_row_text(page_shell):
    """A host that cannot draw (Edit tree under a job's lock) leaves the
    lead empty, and the row reads the kind: it never disappears."""
    shell = page_shell
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        await _open_tree(user)
        assert _one(user, "tree-text:/metrics").text == "3 criteria"
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({A}), reloads_results=False)
        )
        await _settled(
            lambda: _one(user, "tree-text:/metrics").text == "and · 3 criteria"
        )
        assert _none(user, "kind:/metrics")
        gate.release.set()
        await _settled(lambda: _one(user, "tree-text:/metrics").text == "3 criteria")
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
        await _open_form(user, "edit:/metrics", within="node:/metrics")
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
                "edit:/metrics",
                f"add:{leaf}",
                f"wrap:{leaf}",
                f"remove:{leaf}",
            )
        ]
        for button in buttons:
            (tip,) = _plus_tips(button)
            assert button.props.get("aria-label") == tip
        assert _plus_tips(buttons[2]) == ["Change this combinator's fields"]


async def test_the_criteria_header_wraps_a_long_why(page_shell):
    """Under a lock the header's `+` gives way to "… is running on <model>",
    which a long model id makes longer than a card beside a drawer at
    900 px: the header wraps rather than push the card sideways."""
    shell = page_shell
    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _test_page(shell, user, A)
        header = _one(user, "criteria-header:0")
        assert "no-wrap" not in header.classes
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({A}), reloads_results=False)
        )
        await _settled(
            lambda: bool(_inside(user, "why:edit-tree", "criteria-header:0"))
        )
        gate.release.set()


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
