"""Live results on the Test page (D151), in NiceGUI's user simulation over
REAL runs of the Python backend made live (`live_fake`): the page's words
from a run's start to its end, which variables it asks the reader for, and
what a cancelled run leaves on it. What only a browser shows — the trace
growing, a zoom surviving, the pills gone — is
`test_author_live_playwright`'s."""

from __future__ import annotations

import asyncio
import json
import re

import numpy as np
import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring import live_rows  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.accept import PARTIAL  # noqa: E402
from dstf.authoring.live_rows import NOT_AVAILABLE, NOT_SCORED, WAITING  # noqa: E402
from dstf.authoring.queue_rows import one_line  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell.shell import TICK, Shell  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
from dstf.authoring.situation import Situation, strip_for  # noqa: E402
from dstf.backends.common.live_mat import LiveMatReader  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from dstf.reporting.status import SIM_FAIL, classify  # noqa: E402
from live_fake import live_project, make_live, steer  # noqa: E402
from test_session import A  # noqa: E402

EVERY = round(1.0 / TICK)  # shell ticks to its once-a-second poll
#: The times of a 21-row run over one second, as the fake writes them.
STEPS = np.linspace(0.0, 1.0, 21).tolist()


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


def _shell(tmp_path) -> tuple[Shell, object]:
    cfg, work, examples = live_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.session.rescan_done.wait(30)
    s.load.phase = "done"
    return s, examples


def _closed(shell: Shell, examples) -> None:
    for hold in examples.glob("*.hold"):
        hold.unlink()
    shell.session.stop_queue()
    shell.session.wait_background(30)


@pytest.fixture
def live(tmp_path, monkeypatch):
    make_live(monkeypatch.setattr)
    shell, examples = _shell(tmp_path)
    yield shell, examples
    _closed(shell, examples)


@pytest.fixture
def plain(tmp_path):
    shell, examples = _shell(tmp_path)
    yield shell, examples
    _closed(shell, examples)


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


def _texts(user, prefix: str) -> list[str]:
    """The text of every visible element whose marker starts ``prefix``."""
    return [
        e.text
        for e in user.client.elements.values()
        if any(m.startswith(prefix) for m in e._markers) and e.visible
    ]


async def _test_page(shell: Shell, user, model: str = A) -> TestPage:
    shell.mount()
    await user.open(f"/test/{model}")
    await user.should_see(marker="wordmark", retries=20)
    (page,) = [
        p for p in shell.pages if isinstance(p, TestPage) and p.client is user.client
    ]
    return page


async def _until(shell: Shell, pred, what: str, tries: int = 1200) -> None:
    """Tick the shell — its posts, its events, its once-a-second poll —
    until ``pred``."""
    for _ in range(tries):
        shell.tick()
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{what} not reached")


async def _started(shell: Shell, page: TestPage) -> None:
    """Until the page's test runs AND the Session took its start in — the
    pool marks an item running a tick before its result is dropped."""
    results = shell.session.project.results
    await _until(
        shell,
        lambda: (
            page.strip.situation is Situation.RUNNING and page.model_id not in results
        ),
        "the run's start",
    )


async def _second(shell: Shell) -> None:
    """One poll of the shell's, and the tick after it."""
    for _ in range(EVERY + 1):
        shell.tick()
    await asyncio.sleep(0)


def _polls(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []
    poll = LiveMatReader.poll

    def spy(self, names=()):
        seen.append(list(names))
        return poll(self, names)

    monkeypatch.setattr(LiveMatReader, "poll", spy)
    return seen


def _strip(user) -> str:
    return _one(user, "strip-text").text


async def test_the_page_s_words_through_a_whole_run(live):
    shell, examples = live
    session = shell.session
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        assert _strip(user).startswith("Fails 2 of 2 criteria.")
        assert page.body.props.get("data-unscored") is None
        readouts = _texts(user, "criterion-readout:")
        assert len(readouts) == 2 and all("NRMSE" in r for r in readouts)
        count = _one(user, "plots:count").text
        for card in page.cards:
            card.show()

        steer(examples, A, hold=5)
        session.enqueue([A])
        await _started(shell, page)
        # started, no row yet: the layout stays, no verdict stands
        assert re.fullmatch(
            r"Running on worker 1 — (starting|t = [\d.]+ / 1 s)", _strip(user)
        )
        assert _one(user, "status").text == "NOT RUN"
        assert page.body.props["data-unscored"] == WAITING
        assert _texts(user, "criterion-readout:") == [WAITING, WAITING]
        assert _one(user, "tree:summary").text == WAITING
        assert _one(user, "plots:count").text == count and len(page.cards) == 2
        assert all(card.plot is not None for card in page.cards)
        assert not page.strip.note.visible

        await _until(
            shell,
            lambda: _strip(user) == "Running on worker 1 — t = 0.2 / 1 s",
            "t = 0.2",
        )
        # the drawn plot's Actual is the rows read so far
        await _second(shell)
        actual = _actual(page.cards[0])
        assert list(actual["x"]) == STEPS[:5]
        assert page.cards[0].plot.figure["layout"]["xaxis"]["range"] == [0.0, 1.0]

        steer(examples, A, hold=11)
        await _until(
            shell,
            lambda: _strip(user) == "Running on worker 1 — t = 0.5 / 1 s",
            "t = 0.5",
        )
        await _second(shell)
        assert len(_actual(page.cards[0])["x"]) == 11
        assert _texts(user, "criterion-readout:") == [WAITING, WAITING]

        steer(examples, A, hold=None)
        await _until(
            shell, lambda: page.strip.situation is Situation.FAIL, "the end's verdict"
        )
        # the finished result, scored: every verdict is back
        assert page.body.props.get("data-unscored") is None
        readouts = _texts(user, "criterion-readout:")
        assert len(readouts) == 2 and all("NRMSE" in r for r in readouts)
        assert _one(user, "status").text == "FAIL"
        assert len(_actual(page.cards[0])["x"]) == 21
        assert "range" not in page.cards[0].plot.figure["layout"]["xaxis"]


def _actual(card) -> dict:
    return next(tr for tr in card.plot.figure["data"] if tr["meta"]["id"] == "actual")


async def test_only_the_drawn_plots_variables_are_asked_for(live, monkeypatch):
    shell, examples = live
    seen = _polls(monkeypatch)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        steer(examples, A, hold=5)
        shell.session.enqueue([A])
        await _started(shell, page)
        # no plot is drawn: the file is read for how far it has got, alone
        assert all(card.plot is None for card in page.cards)
        await _second(shell)
        await _second(shell)
        assert seen and all(names == [] for names in seen)

        page.cards[1].show()  # y scrolls into view
        del seen[:]
        await _second(shell)
        await _second(shell)
        assert seen[-1] == ["y"]
        assert page.live.names() == {"y"}

        # Focus hides the other plot: its variable is not asked for
        page.cards[0].show()
        await _second(shell)
        await _second(shell)
        assert seen[-1] == ["x", "y"]
        page.focus(0)
        await _second(shell)
        await _second(shell)
        await _second(shell)
        assert seen[-1] == ["x"]


async def test_a_plot_with_no_new_sample_is_not_sent_again(live):
    shell, examples = live
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        for card in page.cards:
            card.show()
        steer(examples, A, hold=5)
        shell.session.enqueue([A])
        await _until(
            shell,
            lambda: _strip(user) == "Running on worker 1 — t = 0.2 / 1 s",
            "t = 0.2",
        )
        await _second(shell)
        await _second(shell)
        sent = [card._revision for card in page.cards]
        for _ in range(3):  # the run is held: nothing new arrives
            await _second(shell)
        assert [card._revision for card in page.cards] == sent


async def test_a_backend_without_a_live_view_says_so_once(plain, monkeypatch):
    shell, examples = plain
    seen = _polls(monkeypatch)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        for card in page.cards:
            card.show()
        assert not page.strip.note.visible
        steer(examples, A, hold=5)
        shell.session.enqueue([A])
        await _started(shell, page)
        for _ in range(3):
            await _second(shell)

        note = _one(user, "strip-note")
        assert note.visible and note.text == NOT_AVAILABLE
        strip = _strip(user)
        assert strip.startswith("Running on worker 1 — ") and strip.endswith(" s")
        assert "t =" not in strip
        assert seen == []  # no file was read
        # its plots stay, the baseline drawn and no Actual
        assert len(page.cards) == 2
        assert _texts(user, "criterion-readout:") == [WAITING, WAITING]
        ids = [tr["meta"]["id"] for tr in page.cards[0].plot.figure["data"]]
        assert "reference" in ids and "actual" not in ids

        steer(examples, A, hold=None)
        await _until(
            shell, lambda: page.strip.situation is Situation.FAIL, "the end's verdict"
        )
        assert not page.strip.note.visible
        assert len(_actual(page.cards[0])["x"]) == 21


async def test_a_cancelled_run_s_page_shows_what_it_wrote(live):
    shell, examples = live
    session = shell.session
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        for card in page.cards:
            card.show()
        steer(examples, A, hold=7)
        (item,) = session.enqueue([A])
        await _until(
            shell,
            lambda: _strip(user) == "Running on worker 1 — t = 0.3 / 1 s",
            "t = 0.3",
        )
        user.find(marker="strip-cancel").click()
        await _until(
            shell, lambda: item.state is ItemState.CANCELLED, "the cancel", tries=3000
        )
        await _until(
            shell, lambda: page.strip.situation is Situation.CANCELLED, "its strip"
        )

        assert _strip(user) == "Cancelled at t = 0.3 of 1 s — not scored"
        assert "dstf-strip-grey" in _one(user, "strip").classes
        assert page.body.props["data-unscored"] == NOT_SCORED
        assert _texts(user, "criterion-readout:") == [NOT_SCORED, NOT_SCORED]
        assert _one(user, "tree:summary").text == NOT_SCORED
        assert _one(user, "status").text == "NOT RUN"
        figure = page.cards[0].plot.figure
        assert list(_actual(page.cards[0])["x"]) == STEPS[:7]
        (line,) = [
            sh
            for sh in figure["layout"]["shapes"]
            if sh.get("name") == live_rows.STOPPED
        ]
        assert line["x0"] == STEPS[6]
        assert figure["layout"]["annotations"][0]["text"] == "stopped at t = 0.3 s"
        # Accept is the strip's, and says why it will not
        view = shell.view_for(Selection(tests=frozenset({A})))
        (accept,) = [c for c in caps.registered_capabilities() if c.name == "accept"]
        assert accept.available(view) == PARTIAL


# -- a failed run's own reason, on its page ---------------------------------------

FIX_IT = " — fix the model or its settings, then queue it again."


def _timeout(shell: Shell, seconds: int) -> None:
    """A's own timeout in the spec, read by the Session."""
    spec_path = shell.session.spec.path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["simulation"]["timeout"] = seconds
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    shell.session.reload()
    shell.tick()


async def test_a_timed_out_run_s_strip_says_why_and_keeps_saying_it(live):
    """The reason was on the queue's row alone, and Clear finished removes
    the row. The strip reads the stored run result's message."""
    shell, examples = live
    session = shell.session
    _timeout(shell, 8)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        steer(examples, A, hold=4)  # held at t = 0.15 until the timeout
        (item,) = session.enqueue([A])
        await _until(
            shell,
            lambda: _strip(user).endswith("t = 0.15 / 1 s"),
            "t = 0.15",
        )
        await _until(
            shell, lambda: item.state is ItemState.FAILED, "the timeout", tries=3000
        )
        await _until(
            shell, lambda: page.strip.situation is Situation.SIM_FAIL, "its strip"
        )

        message = session.comparisons[A].error_message
        assert "8.0s timeout" in message
        said = f"Timed out at t = 0.15 of 1 s — not scored. {message}{FIX_IT}"
        assert _strip(user) == said
        assert "dstf-strip-red" in _one(user, "strip").classes
        assert page.strip.tip.visible and page.strip.tip.text == message
        assert _one(user, "status").text == "SIM_FAIL"

        session.clear_finished()  # the queue's row, and its copy of the reason
        shell.tick()
        assert session.queue == [] and _strip(user) == said
        assert page.strip.tip.text == message


async def test_a_crashed_run_s_strip_says_why_after_the_row_and_the_tool_went(plain):
    """A run that raised, on a backend with no partial result to show: the
    backend's message, one line of it, then the next action — after Clear
    finished, and in a tool started again on the same work dir."""
    shell, examples = plain
    session = shell.session
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        steer(examples, A, boom="")
        (item,) = session.enqueue([A])
        await _until(
            shell, lambda: item.state is ItemState.FAILED, "the crash", tries=3000
        )
        await _until(
            shell, lambda: page.strip.situation is Situation.SIM_FAIL, "its strip"
        )

        message = session.comparisons[A].error_message
        assert "Traceback" in message and "boom in simulate" in message
        line = one_line(message)
        assert line.endswith("RuntimeError: boom in simulate") and "\n" not in line
        said = f"{line}{FIX_IT}"
        assert _strip(user) == said
        assert page.strip.tip.visible and page.strip.tip.text == message
        assert session.partial(A) is None  # nothing of it is drawn

        session.clear_finished()
        shell.tick()
        assert session.queue == [] and _strip(user) == said

    # A tool started again reads the reason from the run's stored result.
    session.wait_background(30)
    again = Shell(session.spec.path.parent / "testing.json", work_dir=_work(session))
    again.session.open(
        session.spec.path.parent / "testing.json", work_dir=_work(session)
    )
    again.session.rescan_done.wait(30)
    comp = again.session.comparisons[A]
    # (The JSON driver reads the failure back from result.json, without the
    # "Python execution failed:" its run result led with: the same reason.)
    comp = again.session.comparisons[A]
    assert classify(comp) == SIM_FAIL
    assert comp.error_message.rstrip().endswith("RuntimeError: boom in simulate")
    strip = strip_for(status=SIM_FAIL, has_result=True, failure=comp.error_message)
    assert strip.sentence == f"RuntimeError: boom in simulate{FIX_IT}"
    assert strip.tip == comp.error_message and "Traceback" in strip.tip
    again.session.stop_queue()


def _work(session):
    return session.project.config.work_dir
