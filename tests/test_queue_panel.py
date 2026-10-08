"""The simulation queue on the page (D150), in NiceGUI's user simulation:
the queue panel's rows and the header indicator's words in each state, and
the Test page's strip for a test the queue holds. The states are put there
with `test_session_queue.fake_item` — no pool, no simulation — and said to
the pages as the Session says them; the real queue is `test_session_queue`'s
and the browser's `test_author_queue_playwright`'s."""

from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Artifact  # noqa: E402
from dstf.authoring.session import QueueError  # noqa: E402
from dstf.authoring.shell.shell import TICK, Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.authoring.shell.test_page import TestPage  # noqa: E402
from dstf.authoring.situation import Situation  # noqa: E402
from dstf.backends.pool import ItemState, StartingWorker  # noqa: E402
from test_session import A, B, D, _project  # noqa: E402
from test_session_queue import fake_item  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
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


def _has(user, marker: str) -> bool:
    """Whether the page draws an element marked ``marker``. `user.find`
    raises when nothing matches, so absence is read off the client's
    elements."""
    return any(marker in e._markers for e in user.client.elements.values())


async def _open(user, path: str = "/") -> None:
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _hold(shell, model_id: str, state: ItemState):
    """``model_id`` in the queue in ``state``, said as the Session says it."""
    item = fake_item(shell.session, model_id, state)
    shell.session._emit(
        "queue",
        state="started" if state is ItemState.RUNNING else "queued",
        models={model_id},
    )
    shell.tick()
    return item


async def test_the_panel_and_the_indicator_say_a_running_and_a_waiting_test(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "queue-indicator").text == "idle"
        assert _one(user, "queue-counts").text == "queue empty"
        assert shell.stop_reasons() == [] and shell.running() is False

        _hold(shell, A, ItemState.RUNNING)
        _hold(shell, B, ItemState.WAITING)

        assert _one(user, "queue-indicator").text == "1 running · 1 queued"
        assert _one(user, "queue-counts").text == "1 running · 1 queued"
        assert _one(user, f"queue-status:{A}").text == "RUNNING"
        words = _one(user, f"queue-words:{A}").text
        assert words.startswith("running · ") and words.endswith(" s · worker 1")
        assert _has(user, f"queue-cancel:{A}") and not _has(user, f"queue-remove:{A}")
        assert _one(user, f"queue-status:{B}").text == "QUEUED"
        assert _one(user, f"queue-words:{B}").text == "waiting · 1 ahead"
        assert _has(user, f"queue-remove:{B}") and not _has(user, f"queue-cancel:{B}")
        assert not _has(user, f"queue-open:{A}")
        assert shell.stop_reasons() == ["2 tests are queued or running — stop anyway?"]
        assert shell.running() is True


async def test_an_ended_row_offers_its_test_and_the_suite_s_actions(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        item = _hold(shell, D, ItemState.RUNNING)
        # ended, as the pool ends a cancelled item and the Session takes it in
        item.state, item.ended_at = ItemState.CANCELLED, item.started_at
        del shell.session._live[D]
        shell.session._emit("queue", state="ended", models={D})
        shell.tick()

        assert _one(user, f"queue-status:{D}").text == "CANCELLED"
        assert _one(user, f"queue-words:{D}").text == "cancelled"
        for marker in ("queue-open", "queue-accept", "queue-enqueue"):
            assert _has(user, f"{marker}:{D}"), marker
        assert not _has(user, f"queue-cancel:{D}")
        assert _one(user, "queue-indicator").text == "idle"

        shell.session.clear_finished()
        shell.tick()
        assert not _has(user, f"queue-row:{D}")
        assert _one(user, "queue-counts").text == "queue empty"


async def test_the_strip_of_a_queued_and_of_a_running_test(shell):
    """Remove is the queued test's next action and Cancel the running
    one's; neither is shown for a test the queue does not hold. Read off
    the strip's own widgets: `user.find` sees no hidden element, so it
    cannot tell a hidden button from one that was never drawn."""
    async with user_simulation(root=shell.index) as user:
        # The Test page's route is the shell's to mount, once: the root page
        # the simulation was given does not carry it.
        shell.mount()
        await _open(user, f"/test/{A}")
        await _settled(lambda: _has(user, "strip-text"))
        (page,) = [
            p
            for p in shell.pages
            if isinstance(p, TestPage) and p.client is user.client
        ]
        strip = page.strip
        await _settled(lambda: strip.text.text != "")
        assert (strip.remove.visible, strip.cancel.visible) == (False, False)
        assert strip.cancel_why.visible is False

        item = _hold(shell, A, ItemState.WAITING)
        assert strip.situation is Situation.QUEUED
        assert strip.text.text == "Queued — waiting · next"
        assert (strip.remove.visible, strip.cancel.visible) == (True, False)

        item.state, item.started_at, item.worker_id = ItemState.RUNNING, 1.0, 1
        shell.session._emit("queue", state="started", models={A})
        shell.tick()
        assert strip.situation is Situation.RUNNING
        text = strip.text.text
        assert text.startswith("Running on worker 2 — ") and text.endswith(" s")
        assert (strip.remove.visible, strip.cancel.visible) == (False, True)
        assert _one(user, "queue-indicator").text == "1 running"


def _suite(shell, user) -> SuitePage:
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


async def test_a_stopped_pool_turns_the_indicator_red_until_cleared(shell):
    """The message the pool stopped with: its first words in the header,
    red, the whole of it in the panel — until Clear finished."""
    message = "No Stub worker could start — worker 0: licence server unreachable"
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        assert panel.error.visible is False

        # as `_item_ended` leaves it for an item no worker could start
        item = fake_item(shell.session, A, ItemState.WAITING)
        item.state, item.message, item.ended_at = ItemState.FAILED, message, 2.0
        del shell.session._live[A]
        shell.session.queue_stopped = message
        shell.session._emit("queue", state="ended", models={A})
        shell.tick()

        link = _one(user, "queue-indicator")
        assert "dstf-indicator-failed" in link.classes
        assert link.text == "No Stub worker could start — worker 0: licence s…"
        assert message in _one(user, "queue-indicator:tip").text
        assert panel.error.visible and panel.error.text == message
        assert _one(user, f"queue-status:{A}").text == "NOT RUN"
        assert _one(user, f"queue-words:{A}").text == f"failed: {message}"
        assert panel.clear.visible

        user.find(marker="queue-clear").click()
        shell.tick()
        link = _one(user, "queue-indicator")
        assert link.text == "idle" and "dstf-indicator-failed" not in link.classes
        assert "dstf-indicator-idle" in link.classes
        assert panel.error.visible is False and not _has(user, f"queue-row:{A}")


async def test_the_tick_moves_a_running_row_s_seconds_and_nothing_else(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        item = _hold(shell, A, ItemState.RUNNING)
        _hold(shell, B, ItemState.WAITING)
        panel = _suite(shell, user).queue
        row_a, waiting = panel._moving[A][1], _one(user, f"queue-words:{B}")
        assert set(panel._moving) == {A, B}

        item.started_at -= 42.0
        for page in shell.pages:
            page.handle({"event": "poll"})

        assert row_a.text.startswith("running · 42 s") or row_a.text.startswith(
            "running · 43 s"
        )
        # the rows were not rebuilt: the same labels stand
        assert panel._moving[A][1] is row_a
        assert _one(user, f"queue-words:{B}") is waiting


async def test_the_shell_polls_only_while_a_test_runs(shell):
    seen: list[str] = []

    class Listener:
        client = type("C", (), {"is_deleted": False})()

        def handle(self, event):
            seen.append(event["event"])

        def flush(self):
            pass

    shell.register(Listener())
    every = round(1.0 / TICK)
    for _ in range(3 * every):
        shell.tick()
    assert "poll" not in seen

    item = fake_item(shell.session, A, ItemState.WAITING)
    for _ in range(3 * every):
        shell.tick()
    assert "poll" not in seen  # a waiting test has no clock to move

    item.state, item.started_at = ItemState.RUNNING, 1.0
    for _ in range(3 * every):
        shell.tick()
    assert seen.count("poll") == 3


class _StartingPool:
    """A pool that is bringing its workers up, and nothing else of one."""

    can_cancel = True
    stopped = None

    def __init__(self, *workers: StartingWorker) -> None:
        self.starting = workers


async def test_a_starting_worker_is_listed_and_the_next_row_says_so(shell):
    """A library's load is minutes: a bare "waiting · next" for that long
    reads as a hang (item 8 of D151)."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        assert panel.starting.visible is False
        _hold(shell, A, ItemState.WAITING)
        _hold(shell, B, ItemState.WAITING)
        assert _one(user, f"queue-words:{A}").text == "waiting · next"

        pool = shell.session._pool = _StartingPool(
            StartingWorker(0, 'loadFile("/libs/Big/package.mo")', 85.2)
        )
        try:
            assert shell.clocked() and not shell.running()
            for page in shell.pages:
                page.handle({"event": "poll"})
            assert _one(user, "queue-starting-worker").text == (
                'starting worker 1 — loadFile("…/package.mo") · 85 s'
            )
            assert panel.starting.visible
            assert _one(user, f"queue-words:{A}").text == "waiting · worker starting"
            assert _one(user, f"queue-words:{B}").text == "waiting · 1 ahead"

            # the seconds move on the tick, on the label that stands
            line = _one(user, "queue-starting-worker")
            pool.starting = (StartingWorker(0, "its launch", 86.0),)
            for page in shell.pages:
                page.handle({"event": "poll"})
            assert line.text == "starting worker 1 — its launch · 86 s"
            assert _one(user, "queue-starting-worker") is line

            # up: the line goes, and the row is next again
            pool.starting = ()
            for page in shell.pages:
                page.handle({"event": "poll"})
            assert panel.starting.visible is False
            assert not _has(user, "queue-starting-worker")
            assert _one(user, f"queue-words:{A}").text == "waiting · next"
            assert not shell.clocked()
        finally:
            shell.session._pool = None


async def test_a_store_write_s_line_is_shown_in_the_panel(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        assert panel.notes.visible is False

        shell.session.notices.append("Accept: stored 1 baseline(s) to /refs")
        shell.session._emit("notice", message="Accept: stored 1 baseline(s) to /refs")
        shell.tick()

        assert panel.notes.visible
        assert _one(user, "notice").text == "Accept: stored 1 baseline(s) to /refs"


async def test_the_workers_field_sets_the_session_s_workers(shell):
    if (os.cpu_count() or 1) < 2:
        pytest.skip("one core: the field cannot be raised above its start")
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        assert panel.workers.value == shell.session.workers == 1
        assert _one(user, "queue-note").text == "of 1 in testing.json"

        panel.workers.set_value(2)
        shell.tick()

        assert shell.session.workers == 2
        panel.workers.set_value(None)  # the field cleared mid-typing: nothing set
        assert shell.session.workers == 2
        # above the machine's cores: held to them
        panel.workers.set_value(10_000)
        assert shell.session.workers == os.cpu_count()


async def test_the_panel_s_stop_workers_never_raises_into_the_page(shell):
    """The Session refuses Stop workers while a test runs (its sentence is
    `test_session_queue`'s); the panel's handler says a refusal and raises
    nothing, and the queue is as it was. What it SAYS is a notification,
    which the simulation does not show: the browser test reads it."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        panel._stop_workers()  # no pool: nothing to stop, no error
        assert shell.session.queue == []

        _hold(shell, A, ItemState.RUNNING)
        with pytest.raises(QueueError, match="1 test is running"):
            shell.session.stop_workers()
        panel._stop_workers()  # the same refusal, through the handler
        shell.tick()
        assert [i.model_id for i in shell.session.queue] == [A]
        assert shell.session.queue_state(A) is ItemState.RUNNING


async def test_an_artifact_is_linked_in_the_panel(shell, tmp_path):
    """An artifact says nothing but its `notice`: no result moved, so a
    panel that ignored the event would never draw the link."""
    path = tmp_path / "export.json"
    path.write_text("{}", encoding="utf-8")
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        panel = _suite(shell, user).queue
        assert panel.notes.visible is False

        shell.session.apply(Artifact(paths=[path], label="patch"))
        shell.tick()

        assert panel.notes.visible
        await user.should_see("patch: export.json")


async def test_the_backend_cannot_change_while_the_queue_holds_a_test(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        header = _suite(shell, user).header
        assert header._backend_why() is None

        item = _hold(shell, A, ItemState.WAITING)
        assert header._backend_why() == (
            "backend change waits: 1 test is queued or running"
        )
        assert header.backend_why.text == header._backend_why()
        assert header.backend.enabled is False

        shell.session._live.pop(A)
        shell.session.queue.remove(item)
        shell.session._emit("queue", state="removed", models={A})
        shell.tick()
        assert header._backend_why() is None and header.backend.enabled
