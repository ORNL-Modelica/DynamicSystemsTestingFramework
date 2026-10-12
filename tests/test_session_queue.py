"""The Session's simulation queue (D150): one FIFO per tool process over a
pool that stays open. Pure Python — no UI — against the FakeLib project of
`test_session`, its `simulate` scripts replaced by ones a test can hold, so
every state of the queue is reached without a sleep that guesses.

The pool's callbacks arrive on its own threads; `Loop` stands in for the
shell's UI loop and runs what they post on the test's thread, as the
shell's tick does."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path

import psutil
import pytest

from dstf.authoring.builtin.accept import Accept
from dstf.authoring.builtin.enqueue import EnqueueTests
from dstf.authoring.capabilities import Enqueue
from dstf.authoring.queue_rows import (
    counts_text,
    indicator,
    queue_row,
    queue_rows,
    stop_reason,
)
from dstf.authoring.session import (
    PendingEditsError,
    QueueError,
    Selection,
    Session,
    SessionView,
)
from dstf.authoring.situation import Situation, strip_for
from dstf.authoring.spec_document import PatchOp
from dstf.authoring.suite_rows import suite_row
from dstf.backends import BackendUnavailableError
from dstf.backends.pool import ItemState, QueueItem
from dstf.backends.python.backend import PythonBackend
from dstf.discovery.patch_apply import PatchError
from dstf.reporting.status import (
    CANCELLED,
    FAIL,
    NOT_RUN,
    PASS,
    QUEUED,
    RUNNING,
    classify,
)
from test_session import LOOSEN, A, B, C, D, _project

#: A `simulate` a test can hold (`<name>.hold` beside it), fail (`.boom`)
#: and scale (`.scale`): x = scale·t, y = 2t — y is in the result file and
#: tracked by no test.
SIMULATE = """
import pathlib
import time

HERE = pathlib.Path(__file__)


def simulate(stop_time, tolerance):
    while HERE.with_suffix(".hold").exists():
        time.sleep(0.01)
    if HERE.with_suffix(".boom").exists():
        raise RuntimeError("boom in simulate")
    scale = HERE.with_suffix(".scale")
    k = float(scale.read_text()) if scale.exists() else 1.0
    t = [i / 4 for i in range(5)]
    return {"time": t, "variables": {"x": [k * v for v in t], "y": [2 * v for v in t]}}
"""


def gated_project(tmp_path: Path) -> tuple[Path, Path]:
    """`test_session._project` with `SIMULATE` as every test's script.
    Returns the config file and the library's Examples directory."""
    cfg, _work = _project(tmp_path)
    examples = tmp_path / "FakeLib" / "Examples"
    for script in examples.glob("*.py"):
        script.write_text(SIMULATE, encoding="utf-8")
    # A's result differs from its baseline (x = t), as `_project`'s does.
    (examples / "A.scale").write_text("1.25", encoding="utf-8")
    return cfg, examples


def _short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


def fake_item(session: Session, model_id: str, state: ItemState) -> QueueItem:
    """An item of ``model_id`` in ``session``'s queue in ``state`` (WAITING
    or RUNNING), with no pool behind it: what a test of something ELSE —
    a page's words, a file read under a running model — puts in the queue.
    `drop_item` takes it out again."""
    test = session.project.test(model_id)
    item = QueueItem(test, state=state)
    if state is ItemState.RUNNING:
        item.started_at, item.worker_id = time.time(), 0
    session._live[model_id] = item
    session.queue.append(item)
    return item


def drop_item(session: Session, item: QueueItem) -> None:
    session._live.pop(item.model_id, None)
    session.queue.remove(item)


class Loop:
    """The UI loop a test drives: what other threads post is run by `pump`,
    on the thread that calls it."""

    def __init__(self) -> None:
        self.posted: queue.Queue = queue.Queue()
        #: The name of the thread behind each post.
        self.threads: list[str] = []

    def post(self, fn) -> None:
        self.threads.append(threading.current_thread().name)
        self.posted.put(fn)

    def pump(self) -> None:
        while True:
            try:
                fn = self.posted.get_nowait()
            except queue.Empty:
                return
            fn()

    def until(self, pred, timeout: float = 60.0, what: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if pred():
                return
            time.sleep(0.01)
        raise AssertionError(f"{what} not reached")


class Harness:
    def __init__(self, tmp_path: Path, theme: str | None = None) -> None:
        self.cfg, self.examples = gated_project(tmp_path)
        if theme is not None:
            data = json.loads(self.cfg.read_text(encoding="utf-8"))
            self.cfg.write_text(json.dumps({**data, "theme": theme}), encoding="utf-8")
        self.work = tmp_path / "work"
        self.loop = Loop()
        self.session = Session(post=self.loop.post)
        self.session.open(self.cfg, work_dir=self.work)
        self.session.rescan_done.wait(30)
        self.loop.pump()

    def hold(self, *models: str) -> None:
        for m in models:
            (self.examples / f"{_short(m)}.hold").write_text("", encoding="utf-8")

    def release(self, *models: str) -> None:
        for m in models:
            (self.examples / f"{_short(m)}.hold").unlink()

    def state(self, model_id: str) -> ItemState | None:
        return self.session.queue_state(model_id)

    def running(self, *models: str) -> None:
        """Until every one of ``models`` is running AND its start was taken
        in (its old result dropped)."""
        s = self.session
        self.loop.until(
            lambda: all(
                self.state(m) is ItemState.RUNNING and m not in s.project.results
                for m in models
            ),
            what=f"{models} running",
        )

    def ended(self, *models: str) -> None:
        self.loop.until(
            lambda: all(
                self.state(m) not in (ItemState.WAITING, ItemState.RUNNING)
                for m in models
            ),
            what=f"{models} ended",
        )

    def view(self, *models: str) -> SessionView:
        return SessionView(self.session, Selection(tests=frozenset(models)))

    def manifest(self) -> dict[str, str]:
        data = json.loads((self.work / "batch_manifest.json").read_text())
        return {e["model_id"]: e.get("last_run_status") for e in data.values()}


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    for hold in harness.examples.glob("*.hold"):
        hold.unlink()
    harness.session.stop_queue()
    harness.session.wait_background(30)
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]


# ---------------------------------------------------------------------------
# enqueue, order, duplicates, remove, cancel
# ---------------------------------------------------------------------------


def test_the_queue_runs_first_in_first_out_and_numbers_its_rows(h):
    h.hold(A)
    items = h.session.enqueue([A, B, D])
    assert [i.model_id for i in h.session.queue] == [A, B, D] and len(items) == 3
    h.running(A)

    rows = queue_rows(h.session)
    assert [(r.number, r.model_id, r.status) for r in rows] == [
        (1, A, RUNNING),
        (2, B, QUEUED),
        (3, D, QUEUED),
    ]
    assert rows[0].words.startswith("running · ") and rows[0].worker == 1
    assert [r.words for r in rows[1:]] == ["waiting · 1 ahead", "waiting · 2 ahead"]
    assert [r.actions for r in rows] == [("cancel",), ("remove",), ("remove",)]
    assert counts_text(rows) == "1 running · 2 queued"
    assert indicator(h.session).text == "1 running · 2 queued"
    assert indicator(h.session).tone == "running"
    assert stop_reason(h.session) == "3 tests are queued or running"
    # the Suite rows follow
    assert [suite_row(h.session, m).status for m in (A, B, D)] == [
        RUNNING,
        QUEUED,
        QUEUED,
    ]

    h.release(A)
    h.ended(A, B, D)
    started = [i.started_at for i in items]
    assert started == sorted(started)
    rows = queue_rows(h.session)
    assert [(r.number, r.status) for r in rows] == [(1, FAIL), (2, "NO_REF"), (3, PASS)]
    assert all(r.words.startswith("finished · ") for r in rows)
    assert all(r.actions == ("open",) for r in rows)
    assert indicator(h.session).text == "idle" and stop_reason(h.session) is None


def test_only_the_latest_row_of_a_test_carries_its_verdict_and_its_actions(h):
    """A verdict is the TEST's, as it stands: two rows of one test both
    read the later run's, the earlier one beside the words of a run that
    had ended otherwise — and an Accept on that earlier row would accept
    the later run's results."""
    h.session.enqueue([D])
    h.ended(D)
    (only,) = queue_rows(h.session)
    assert (only.latest, only.status, only.actions) == (True, PASS, ("open",))

    h.hold(D)
    h.session.enqueue([D])  # the same test again: its second item
    h.running(D)
    first, second = queue_rows(h.session)
    # From the moment a later item is listed, the earlier row is a run's
    # record: how it ended, its Test page, nothing else.
    assert (first.latest, first.status, first.actions) == (False, None, ("open",))
    assert first.words.startswith("finished · ")
    assert (second.latest, second.status) == (True, RUNNING)
    h.release(D)
    h.ended(D)
    first, second = queue_rows(h.session)
    assert (first.latest, first.status) == (False, None)
    assert (second.latest, second.status, second.actions) == (True, PASS, ("open",))
    assert queue_row(h.session, D) == second  # the strip's row is the latest
    assert counts_text([first, second]) == "2 finished"

    # An earlier run that was cancelled keeps its own word — CANCELLED is
    # how THAT run ended, no verdict of the test — and is not the latest.
    h.hold(A)
    (a,) = h.session.enqueue([A])
    h.running(A)
    h.session.cancel(a)
    h.ended(A)
    h.release(A)
    h.session.enqueue([A])
    h.ended(A)
    rows = [r for r in queue_rows(h.session) if r.model_id == A]
    assert [(r.latest, r.status) for r in rows] == [(False, "CANCELLED"), (True, FAIL)]


def test_a_test_is_never_queued_twice(h):
    h.hold(A)
    h.session.enqueue([A, B])
    h.running(A)

    with pytest.raises(QueueError, match="FakeLib.A is already running"):
        h.session.enqueue([A])
    with pytest.raises(QueueError, match="FakeLib.B is already queued"):
        h.session.enqueue([B])
    # the ones it holds are skipped, the rest appended
    (added,) = h.session.enqueue([A, D, B])
    assert added.model_id == D
    assert [i.model_id for i in h.session.queue] == [A, B, D]

    # the capability says so before the click, and counts what is left
    queue_cap = EnqueueTests()
    assert queue_cap.available(h.view(A)) == "FakeLib.A is already running"
    assert queue_cap.available(h.view(B)) == "FakeLib.B is already queued"
    assert (
        queue_cap.available(h.view(A, B))
        == "all 2 selected tests are already queued or running"
    )
    assert queue_cap.available(h.view(A, B, C)) is True
    assert queue_cap.caption(h.view(A, B, C)) == "Queue 1 (2 already queued)"
    assert queue_cap.act(h.view(A, B, C)) == Enqueue(models=(C,))
    h.release(A)
    h.ended(A, B, D)
    # ended: it may run again
    assert queue_cap.caption(h.view(A, B)) == "Queue 2"
    # Through the loop: the click's read of the library runs off it, and the
    # test is queued when that is back.
    h.session.apply(queue_cap.act(h.view(A)))
    assert len(h.session.queue) == 3
    h.loop.until(lambda: len(h.session.queue) == 4, what="the click queued")
    h.ended(A)
    assert [i.model_id for i in h.session.queue] == [A, B, D, A]


def test_a_waiting_item_is_removed_and_a_running_one_is_not(h):
    h.hold(A)
    a, b, d = h.session.enqueue([A, B, D])
    h.running(A)

    assert h.session.remove(d) is True
    assert [i.model_id for i in h.session.queue] == [A, B]
    assert h.state(D) is None and suite_row(h.session, D).status == PASS
    assert h.session.remove(a) is False and h.state(A) is ItemState.RUNNING
    assert h.session.cancel(b) is False  # waiting: remove it

    h.release(A)
    h.ended(A, B)
    assert d.state is ItemState.REMOVED and d.started_at is None
    assert h.manifest()[D] == "passed"  # its last run's, untouched


def test_a_cancelled_run_leaves_no_result_and_says_cancelled(h):
    h.hold(A)
    (a,) = h.session.enqueue([A])
    h.running(A)

    assert h.session.cancel(a) is True
    h.ended(A)

    assert a.state is ItemState.CANCELLED
    assert A not in h.session.project.results
    assert classify(h.session.comparisons[A]) == NOT_RUN
    row = queue_row(h.session, A)
    assert (row.status, row.words, row.actions) == (CANCELLED, "cancelled", ("open",))
    assert suite_row(h.session, A).status == CANCELLED
    assert h.manifest()[A] == "cancelled"
    assert EnqueueTests().caption(h.view(A)) == "Queue again"
    # the row cleared, the test is what a reader of the work dir finds
    h.session.clear_finished()
    assert h.session.queue == [] and suite_row(h.session, A).status == NOT_RUN


def test_a_simulation_that_raises_fails_with_the_backend_s_message(h):
    (h.examples / "B.boom").write_text("", encoding="utf-8")
    (b,) = h.session.enqueue([B])
    h.ended(B)

    assert b.state is ItemState.FAILED
    row = queue_row(h.session, B)
    assert row.status == "SIM_FAIL" and row.started
    # one line: what was raised, not the traceback's first line
    assert (
        row.words == "failed: Python execution failed: RuntimeError: boom in simulate"
    )
    assert "Traceback (most recent call last):" in row.message
    assert classify(h.session.comparisons[B]) == "SIM_FAIL"


# ---------------------------------------------------------------------------
# one result read back, one test rescored
# ---------------------------------------------------------------------------


def test_a_finished_item_is_read_and_rescored_alone(h, monkeypatch):
    """No other test's result is read again and no other test is rescored:
    a reload per item read TRANSFORM's 331 results for each."""
    session = h.session
    before = dict(session.project.results)
    verdicts = dict(session.comparisons)
    reads: list[str] = []
    read_result = PythonBackend.read_result

    def counting(self, test, test_key, run_result):
        reads.append(test.model_id)
        return read_result(self, test, test_key, run_result)

    monkeypatch.setattr(PythonBackend, "read_result", counting)
    reloads: list[int] = []
    monkeypatch.setattr(
        PythonBackend,
        "read_last_results",
        lambda self, *a, **k: reloads.append(1),
    )
    scored: list[str] = []
    score = session._score
    monkeypatch.setattr(session, "_score", lambda m: (scored.append(m), score(m))[1])

    session.enqueue([D])
    h.ended(D)

    assert reads == [D] and reloads == []
    assert set(scored) == {D}
    for model_id in (A, B):
        assert session.project.results[model_id] is before[model_id]
        assert session.comparisons[model_id] is verdicts[model_id]
    assert session.project.results[D] is not before[D]
    assert session.project.results[D] is session.queue[0].result


def test_a_running_test_has_no_result_until_its_own_arrives(h):
    """Its run cleared the last one on disk: the test reads NOT RUN while
    it runs, as one never run does."""
    h.hold(A)
    h.session.enqueue([A])
    h.running(A)

    assert A not in h.session.project.results
    assert classify(h.session.comparisons[A]) == NOT_RUN
    # Its tracked names stay with its page's layout (D151); the file — it
    # holds a `y` nothing tracks — is not read mid-run on this backend.
    assert h.session.variable_names(A) == ["x"]
    h.release(A)
    h.ended(A)
    assert classify(h.session.comparisons[A]) == FAIL


# ---------------------------------------------------------------------------
# editing while queued or running
# ---------------------------------------------------------------------------


def test_a_criterion_edited_while_its_test_runs_is_scored_when_it_ends(h):
    """Not refused — a running test was locked against every edit — and
    scored with the criteria as they are when the result arrives. The edit
    is one a test with no result takes: the running test has none (its
    run cleared it), so a flat entry's per-leaf edit is refused there with
    "nothing is scored", exactly as on a test never run."""
    h.hold(A)
    h.session.enqueue([A])
    h.running(A)

    h.session.stage([PatchOp(A, "add", "/comparison/tolerance", 0.5)])
    assert [op.model_id for op in h.session.spec.pending] == [A]
    with pytest.raises(PatchError, match="nothing is scored"):
        h.session.stage([LOOSEN])
    h.release(A)
    h.ended(A)

    # x = 1.25 t against x = t fails at 0.01 and passes at the staged 0.5
    assert classify(h.session.comparisons[A]) == PASS
    assert len(h.session.spec.pending) == 1  # still pending: Save is the user's
    h.session.discard()
    assert classify(h.session.comparisons[A]) == FAIL


def test_a_running_test_s_simulation_settings_are_locked(h):
    h.hold(A)
    h.session.enqueue([A, B])
    h.running(A)
    stop_time = PatchOp(A, "replace", "/simulation/stop_time", 2.0)

    with pytest.raises(QueueError) as refused:
        h.session.stage([stop_time])
    assert str(refused.value) == (
        "FakeLib.A is running: its simulation settings are locked until its run ends"
    )
    assert h.session.spec.pending == []
    # A queued test is not locked. `/simulation` is not stageable for ANY
    # test today — `patch_apply`'s whitelist, the `spec-update` seam — so
    # this is the whitelist's refusal, not the queue's.
    with pytest.raises(PatchError, match="whitelist"):
        h.session.stage([PatchOp(B, "replace", "/simulation/stop_time", 2.0)])
    h.release(A)
    h.ended(A, B)


def test_a_queued_test_runs_with_the_spec_as_it_stands_when_it_starts(h, monkeypatch):
    """An edit that changes what the run records — a criterion on a
    variable the test did not track — staged while the test waits is the
    run's: the item's test is the edited one, and its result holds the
    variable with no second read."""
    reloads: list[int] = []
    read_last = PythonBackend.read_last_results

    def counting(self, *a, **k):
        reloads.append(1)
        return read_last(self, *a, **k)

    h.hold(A)
    _a, b = h.session.enqueue([A, B])
    h.running(A)
    assert "y" not in {v.name for v in h.session.project.results[B].variables}

    h.session.stage(
        [
            PatchOp(
                B,
                "add",
                "/metrics",
                {"metric": "range", "variable": "y", "min_value": 0, "max_value": 3},
            )
        ]
    )
    assert b.test is h.session.project.test(B)
    monkeypatch.setattr(PythonBackend, "read_last_results", counting)
    h.release(A)
    h.ended(A, B)

    assert reloads == []
    assert "y" in {v.name for v in h.session.project.results[B].variables}
    (leaf,) = h.session.comparisons[B].variables
    assert leaf.name == "y" and leaf.passed


def test_save_and_discard_are_not_refused_while_the_queue_runs(h):
    h.hold(A)
    h.session.enqueue([A, D])
    h.running(A)

    h.session.stage([PatchOp(D, "add", "/comparison/tolerance", 0.5)])
    h.session.discard()
    h.session.stage([PatchOp(D, "add", "/comparison/tolerance", 0.5)])
    h.session.save()
    assert h.session.spec.pending == []
    assert h.state(A) is ItemState.RUNNING and h.state(D) is ItemState.WAITING
    h.release(A)
    h.ended(A, D)
    assert classify(h.session.comparisons[D]) == PASS


def test_accept_refuses_a_queued_or_running_test_with_a_sentence(h):
    h.hold(A)
    h.session.enqueue([A, D])
    h.running(A)
    accept = Accept()

    assert accept.available(h.view(D)) == (
        "FakeLib.D is queued: accept it when its run has ended"
    )
    assert accept.available(h.view(A)) == (
        "FakeLib.A is running: accept it when its run has ended"
    )
    refs = sorted(p.name for p in (h.cfg.parent / "Python").rglob("ref_*.json"))
    with pytest.raises(QueueError, match="FakeLib.D is queued: accept it when"):
        h.session.apply(accept.act(h.view(D)))
    assert sorted(p.name for p in (h.cfg.parent / "Python").rglob("ref_*.json")) == refs
    assert accept.available(h.view(B)) is True  # not in the queue
    h.release(A)
    h.ended(A, D)
    assert accept.available(h.view(D)) is True


def test_a_reopen_is_refused_while_the_queue_holds_a_test(h):
    h.hold(A)
    h.session.enqueue([A])
    h.running(A)
    with pytest.raises(QueueError, match="1 test is queued or running"):
        h.session.open(h.cfg, work_dir=h.work)
    with pytest.raises(QueueError, match="1 test is queued or running"):
        h.session.close()
    h.release(A)
    h.ended(A)
    h.session.stage([LOOSEN])
    with pytest.raises(PendingEditsError):
        h.session.close()


# ---------------------------------------------------------------------------
# workers
# ---------------------------------------------------------------------------


def test_two_workers_run_two_tests_at_once(h):
    assert h.session.workers == 1
    h.session.set_workers(2)
    h.hold(A, B)
    h.session.enqueue([A, B, D])
    h.running(A, B)

    rows = queue_rows(h.session)
    assert [r.status for r in rows] == [RUNNING, RUNNING, QUEUED]
    assert sorted(r.worker for r in rows[:2]) == [1, 2]
    assert rows[2].words == "waiting · 2 ahead"
    h.release(A, B)
    h.ended(A, B, D)


def test_stop_workers_is_refused_while_a_test_runs_and_closes_the_pool(h):
    h.hold(A)
    h.session.enqueue([A])
    h.running(A)
    with pytest.raises(QueueError) as refused:
        h.session.stop_workers()
    assert str(refused.value) == (
        "1 test is running — cancel it or wait, then stop the workers"
    )
    h.release(A)
    h.ended(A)

    pool = h.session._pool
    h.session.stop_workers()
    assert h.session._pool is None
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]
    assert [i.model_id for i in h.session.queue] == [A]  # the row stays
    h.session.enqueue([D])  # the next enqueue starts new workers
    assert h.session._pool is not None and h.session._pool is not pool
    h.ended(D)


# ---------------------------------------------------------------------------
# the pool's threads, the stop, the drain
# ---------------------------------------------------------------------------


def test_the_pool_s_callbacks_reach_the_session_on_the_loop_only(h):
    """Every callback arrives on a pool thread and does nothing there but
    post: every event the Session emits, it emits on the loop's thread."""
    main = threading.current_thread()
    emitted: list[tuple[str, threading.Thread]] = []
    for event in ("queue", "results", "spec"):
        h.session.on(
            event, lambda e: emitted.append((e["event"], threading.current_thread()))
        )

    h.session.enqueue([B, D])
    h.ended(B, D)

    # two starts and two ends, each posted from the pool's thread
    assert h.loop.threads.count("python-pool-0") == 4
    assert main.name not in h.loop.threads
    assert [state for state, _ in emitted].count("queue") >= 5
    assert {thread for _, thread in emitted} == {main}


def test_stopping_the_queue_leaves_no_simulation_behind(h):
    me = psutil.Process()
    h.hold(A)
    a, b = h.session.enqueue([A, B])
    h.running(A)
    children = [p for p in me.children(recursive=True) if p.is_running()]
    assert children, "the held simulation is a child process"

    h.session.stop_queue()

    assert a.state is ItemState.CANCELLED and b.state is ItemState.REMOVED
    gone, alive = psutil.wait_procs(children, timeout=10)
    assert alive == []
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]
    assert h.session._pool is None
    # the removed row is gone; the cancelled one stays, and reads cancelled
    assert [(i.model_id, i.state) for i in h.session.queue] == [
        (A, ItemState.CANCELLED)
    ]
    assert stop_reason(h.session) is None
    assert classify(h.session.comparisons[A]) == NOT_RUN
    assert h.session.project.results[B].success  # never started: its result stands


def test_status_json_is_written_when_the_queue_drains_for_the_items_that_ran(h):
    status = h.work / "status.json"
    before = status.read_bytes()
    h.hold(A, B)
    _a, _b, d = h.session.enqueue([A, B, D])
    h.running(A)
    assert h.session.remove(d)

    h.release(A)
    h.loop.until(
        lambda: h.state(A) is ItemState.FINISHED and h.state(B) is ItemState.RUNNING
    )
    assert h.session.wait_background(30)
    assert status.read_bytes() == before  # one item ended: nothing is written yet

    h.release(B)
    h.ended(B)
    assert h.session.wait_background(30)
    written = json.loads(status.read_text())
    assert [(t["model_id"], t["status"]) for t in written["tests"]] == [
        (A, "passed"),
        (B, "passed"),
    ]
    page = (h.work / "dashboard.html").read_text(encoding="utf-8")
    assert A in page and D in page  # the page lists every test the Session scored

    # the next drain writes the next batch alone
    (h.examples / "D.hold").write_text("", encoding="utf-8")
    (d2,) = h.session.enqueue([D])
    h.running(D)
    h.session.cancel(d2)
    h.ended(D)
    assert h.session.wait_background(30)
    written = json.loads(status.read_text())
    assert [(t["model_id"], t["status"]) for t in written["tests"]] == [
        (D, "cancelled")
    ]


def test_a_run_asked_to_stop_reads_cancelling_until_it_ends(h):
    """Cancel was clicked and the simulation has not ended — a model
    blocked in an external call runs on for the worker's grace (Dymola:
    about 11 s). Its row went on reading `running · 42 s` beside a Cancel
    button, and the strip `Running on worker 1`: nothing said the click
    had been taken."""
    s = h.session
    item = fake_item(s, A, ItemState.RUNNING)
    try:
        row = queue_row(s, A)
        assert row.words.startswith("running · ") and not row.cancelling
        assert row.actions == ("cancel",)
        assert strip_for(queue=row).sentence.startswith("Running on worker 1 — ")

        item._stop_asked = True  # what the pool's `cancel` sets
        row = queue_row(s, A)
        assert (row.words, row.cancelling, row.actions) == ("cancelling…", True, ())
        assert row.status == RUNNING and row.state is ItemState.RUNNING
        strip = strip_for(queue=row)
        assert strip.sentence == "Cancelling on worker 1…"
        assert strip.situation is Situation.RUNNING and strip.note == ""
        assert counts_text(queue_rows(s)) == "1 running"  # it has not ended
    finally:
        drop_item(s, item)


def test_a_cancel_that_was_taken_is_said_at_once(h):
    """The row and the strip are repainted by a `queue` event; a cancel
    said none, so they changed at the next second's poll — or, the Cancel
    button being part of the row, not at all."""
    s = h.session
    heard: list[tuple[str, frozenset]] = []
    s.on("queue", lambda e: heard.append((e["state"], frozenset(e["models"]))))
    h.hold(A)
    (item,) = s.enqueue([A])
    h.running(A)
    heard.clear()

    assert s.cancel(item) is True
    assert heard[0] == ("cancelling", frozenset({A}))
    h.ended(A)
    assert heard[-1][0] == "ended" and not item.stopping
    assert s.cancel(item) is False  # ended: nothing is asked, nothing is said
    assert [state for state, _ in heard].count("cancelling") == 1


def test_the_drain_s_page_links_no_wiped_report_and_says_how_long_it_ran(
    h, monkeypatch
):
    """A run wipes its test's report dir. The page the drain wrote still
    linked the test's name to `reports/<dir>/interactive.html` — gone —
    and its header read "last run: 1 test in 0.0s" whatever the run took:
    the elapsed was the time it took to WRITE the snapshot."""
    from dstf.cli import main

    main(["--config", str(h.cfg), "compare", "--work-dir", str(h.work), "--report"])
    page = h.work / "dashboard.html"

    def report_links() -> dict[str, str]:
        html = page.read_text(encoding="utf-8")
        cells = re.findall(r'<td class="model-cell" title="([^"]+)">(.*?)</td>', html)
        return {
            model: found.group(1)
            for model, cell in cells
            if (found := re.search(r'href="(reports/[^"]+)"', cell))
        }

    before = report_links()
    assert {A, D} <= set(before) and (h.work / before[D]).exists()

    h.hold(D)
    (item,) = h.session.enqueue([D])
    h.running(D)
    time.sleep(0.5)
    h.release(D)
    h.ended(D)
    assert h.session.wait_background(30)

    after = report_links()
    assert D not in after and not (h.work / before[D]).exists()
    assert after[A] == before[A]  # not run again: its page is still there
    assert all((h.work / link).exists() for link in after.values())
    # the page says the wall time of the items it reports
    wall = item.ended_at - item.started_at
    written = json.loads((h.work / "status.json").read_text(encoding="utf-8"))
    assert wall >= 0.5 and written["elapsed"] == pytest.approx(wall)
    assert written["start_wall"] == item.started_at
    html = page.read_text(encoding="utf-8")
    assert f"last run: 1 test in {wall:.1f}s" in html


def test_a_drain_of_two_tests_ran_from_the_first_start_to_the_last_end(h):
    """The snapshot's wall time is its items': the first one's start to the
    last one's end. With one item, as above, its start is also the last
    start and its end the first end."""
    h.hold(A, D)
    first, second = h.session.enqueue([A, D])
    h.running(A)
    time.sleep(0.3)
    h.release(A)
    h.running(D)  # one worker: A has ended
    time.sleep(0.3)
    h.release(D)
    h.ended(A, D)
    assert h.session.wait_background(30)

    assert first.started_at < first.ended_at <= second.started_at < second.ended_at
    written = json.loads((h.work / "status.json").read_text(encoding="utf-8"))
    assert [t["model_id"] for t in written["tests"]] == [A, D]
    wall = second.ended_at - first.started_at
    assert wall >= 0.6
    assert written["start_wall"] == first.started_at
    assert written["elapsed"] == pytest.approx(wall)
    html = (h.work / "dashboard.html").read_text(encoding="utf-8")
    assert f"last run: 2 tests in {wall:.1f}s" in html


def test_the_drain_writes_both_its_pages_in_the_project_s_theme(tmp_path):
    """`testing.json` says dark: the dashboard a drained queue leaves is
    dark — the final page the Session renders, and the live one the
    pool's `write_status` renders before it. Every other fixture is
    default-themed, where a literal `"default"` in either passes."""
    dark = "--dstf-page: #121212;"
    h = Harness(tmp_path, theme="dark")
    try:
        (item,) = h.session.enqueue([D])
        h.ended(D)
        assert h.session.wait_background(30)
        page = h.work / "dashboard.html"
        assert dark in page.read_text(encoding="utf-8")  # the final page

        page.unlink()
        h.session._write_status([item])  # the pool's own: the live page
        assert dark in page.read_text(encoding="utf-8")
    finally:
        h.session.stop_queue()
        h.session.wait_background(30)


# ---------------------------------------------------------------------------
# a backend that cannot start, or cannot be queued on
# ---------------------------------------------------------------------------


def _stub_backend(h, monkeypatch, tmp_path):
    """The queue's backend replaced by `test_simulation_pool`'s fake
    persistent one — the Python backend has no worker to fail to start."""
    from types import SimpleNamespace

    from test_simulation_pool import _FakeBackend

    config = h.session.project.config
    stub = _FakeBackend(
        SimpleNamespace(
            work_dir=config.work_dir, timeout=30.0, backend="Stub", parallel=1
        )
    )
    monkeypatch.setattr(
        "dstf.authoring.session.select_backend", lambda config, persistent: stub
    )
    return stub


def test_no_worker_can_start_fails_the_waiting_items_with_the_message(
    h, monkeypatch, tmp_path
):
    stub = _stub_backend(h, monkeypatch, tmp_path)

    def cannot_start(worker):
        raise RuntimeError("licence server unreachable")

    stub.start_script = cannot_start
    before = dict(h.session.project.results)
    a, b = h.session.enqueue([A, B])
    h.ended(A, B)

    message = "No Stub worker could start — worker 0: licence server unreachable"
    assert (a.state, b.state) == (ItemState.FAILED, ItemState.FAILED)
    assert a.message == b.message == h.session.queue_stopped == message
    rows = queue_rows(h.session)
    assert [(r.status, r.started, r.words) for r in rows] == [
        (NOT_RUN, False, f"failed: {message}")
    ] * 2
    assert all(r.message == message for r in rows)
    # nothing ran: every result stands, and nothing is queued forever
    assert h.session.project.results == before
    assert stop_reason(h.session) is None
    state = indicator(h.session)
    assert state.tone == "failed" and state.text.startswith(
        "No Stub worker could start"
    )
    assert message in state.tip
    assert (
        not (h.work / "status.json").read_text().count("Stub")
    )  # nothing ran: no write

    # Clear finished dismisses it...
    h.session.clear_finished()
    assert h.session.queue_stopped is None and indicator(h.session).text == "idle"
    # ...and so does the next start: the stopped pool is replaced
    h.session.enqueue([A])
    h.ended(A)
    assert h.session.queue_stopped == message
    stub.start_script = None
    h.session.enqueue([B])
    h.ended(B)
    assert h.session.queue_stopped is None
    assert h.state(B) is ItemState.FINISHED


def test_a_backend_that_cannot_be_queued_on_disables_queue_with_its_sentence(
    h, monkeypatch
):
    sentence = "Persistent-worker Python unavailable."

    def unavailable(config, persistent):
        raise BackendUnavailableError(sentence)

    monkeypatch.setattr("dstf.authoring.session.select_backend", unavailable)
    queue_cap = EnqueueTests()
    assert queue_cap.available(h.view(A)) is True  # nothing has said so yet

    with pytest.raises(QueueError, match=sentence):
        h.session.apply(queue_cap.act(h.view(A)))

    assert h.session.queue == [] and h.session.queue_refusal == sentence
    assert queue_cap.available(h.view(A)) == sentence


# ---------------------------------------------------------------------------
# what the single job's tests said, of the queue
# ---------------------------------------------------------------------------


def test_a_reopen_drops_the_queue_s_rows_and_a_failed_one_keeps_them(h, tmp_path):
    """The queue is the open project's: its ended rows, the stopped pool's
    message and its workers go at a reopen's commit point — and stay when
    the open fails before it."""
    h.session.enqueue([D])
    h.ended(D)
    h.session.queue_stopped = "said of this project"
    h.session.set_workers(2)
    pool = h.session._pool

    with pytest.raises(Exception, match="nope"):
        h.session.open(tmp_path / "nope" / "testing.json")
    assert [i.model_id for i in h.session.queue] == [D]
    assert h.session._pool is pool and h.session.workers == 2
    assert h.session.queue_stopped == "said of this project"

    h.session.open(h.cfg, work_dir=h.work)
    h.session.rescan_done.wait(30)
    assert h.session.queue == [] and h.session._pool is None
    assert h.session.queue_stopped is None and h.session.workers == 1
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]


def test_a_listener_that_raises_does_not_stop_the_queue(h, caplog):
    heard: list[str] = []

    def boom(event):
        raise RuntimeError("a deleted element")

    h.session.on("queue", boom)
    h.session.on("queue", lambda e: heard.append(e["state"]))

    h.session.enqueue([D])
    h.ended(D)

    assert heard == ["queued", "started", "ended"]
    assert h.state(D) is ItemState.FINISHED
    assert "listener for 'queue' failed" in caplog.text


def test_reload_is_not_refused_while_the_queue_runs(h):
    h.hold(A)
    h.session.enqueue([A])
    h.running(A)
    h.session.stage([PatchOp(D, "add", "/comparison/tolerance", 0.5)])

    h.session.reload()  # what a refused Save offers: the spec from disk

    assert h.session.spec.pending == [] and h.state(A) is ItemState.RUNNING
    h.release(A)
    h.ended(A)
    assert classify(h.session.comparisons[A]) == FAIL


# ---------------------------------------------------------------------------
# the seams between the pool's threads and the loop
# ---------------------------------------------------------------------------


def test_removing_the_last_waiting_item_writes_what_ran(h):
    """A ended and B never started: with B taken out the queue has drained,
    and `status.json` is owed for A — nothing else will say so, no item is
    left to end. B is kept waiting by holding the pool's thread in the post
    of A's end, the instant before it would take B."""
    status = h.work / "status.json"
    before = status.read_bytes()
    posted, release = threading.Event(), threading.Event()
    put = h.loop.posted.put

    armed: list[bool] = []

    def put_and_wait(fn) -> None:
        put(fn)
        if armed and threading.current_thread().name.startswith("python-pool"):
            posted.set()
            release.wait(30)

    h.loop.posted.put = put_and_wait
    h.hold(A)
    a, b = h.session.enqueue([A, B])
    h.running(A)
    try:
        armed.append(True)
        h.release(A)
        assert posted.wait(30), "A's end was never posted"
        h.loop.pump()  # the Session takes A's end in; B is still live
        assert h.state(A) is ItemState.FINISHED and b.state is ItemState.WAITING
        assert h.session.wait_background(30) and status.read_bytes() == before

        assert h.session.remove(b) is True

        assert h.session.wait_background(30)
        written = json.loads(status.read_text())
        assert [(t["model_id"], t["status"]) for t in written["tests"]] == [
            (A, "passed")
        ]
    finally:
        release.set()
    assert h.session.queue == [a] and b.state is ItemState.REMOVED


def test_an_item_the_pool_ended_reads_running_until_the_session_took_it_in(h):
    """The pool ends an item on its own thread; the Session reads its result
    a tick later. In between the item is FINISHED and the test still has no
    result here: every reader of the queue is told RUNNING, not an ended
    row whose badge would be the verdict of nothing."""
    h.hold(A)
    (a,) = h.session.enqueue([A])
    h.running(A)

    h.release(A)
    deadline = time.monotonic() + 30
    while a.state is ItemState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)  # no pump: the end stays posted, not taken in
    assert a.state is ItemState.FINISHED and a.result is not None

    assert h.session.item_state(a) is ItemState.RUNNING
    assert h.state(A) is ItemState.RUNNING
    row = queue_row(h.session, A)
    assert (row.state, row.status) == (ItemState.RUNNING, RUNNING)
    assert suite_row(h.session, A).status == RUNNING
    assert A not in h.session.project.results

    h.loop.pump()
    assert h.session.item_state(a) is ItemState.FINISHED
    assert queue_row(h.session, A).status == FAIL


def test_an_item_the_pool_failed_unstarted_reads_waiting_until_taken_in(
    h, monkeypatch, tmp_path
):
    """The same seam for an item no worker could start: FAILED in the pool,
    still waiting here until its end is taken in."""
    stub = _stub_backend(h, monkeypatch, tmp_path)

    def cannot_start(worker):
        raise RuntimeError("licence server unreachable")

    stub.start_script = cannot_start
    (a,) = h.session.enqueue([A])
    deadline = time.monotonic() + 30
    while a.state is ItemState.WAITING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert a.state is ItemState.FAILED and a.started_at is None

    assert h.session.item_state(a) is ItemState.WAITING
    row = queue_row(h.session, A)
    assert (row.status, row.actions) == (QUEUED, ("remove",))

    h.loop.pump()
    assert h.session.item_state(a) is ItemState.FAILED


def test_a_running_row_offers_no_cancel_where_the_backend_cannot_stop_a_test(
    h, monkeypatch
):
    """FMPy simulates in-process: nothing can stop its test. The row offers
    no Cancel and carries the sentence saying why — the panel and the strip
    draw it where the button would be — and a cancel asked all the same is
    the pool's refusal, a sentence."""
    from dstf.backends.pool import Refused
    from dstf.backends.types import Capability

    h.hold(A)
    (a,) = h.session.enqueue([A])
    h.running(A)
    row = queue_row(h.session, A)
    assert (row.actions, row.cancel_why) == (("cancel",), None)

    backend = h.session._pool.backend
    monkeypatch.setattr(
        backend, "capabilities", frozenset(backend.capabilities) - {Capability.CANCEL}
    )

    why = (
        "Python cannot stop a running simulation: it runs to its end, or to its timeout"
    )
    assert h.session.cannot_cancel == why
    row = queue_row(h.session, A)
    assert (row.actions, row.cancel_why) == ((), why)
    with pytest.raises(Refused, match="Python cannot stop a running simulation"):
        h.session.cancel(a)
    assert h.state(A) is ItemState.RUNNING
    # a waiting row has nothing to cancel: no sentence
    (b,) = h.session.enqueue([B])
    assert queue_row(h.session, B).cancel_why is None

    h.session.remove(b)
    h.release(A)
    h.ended(A)
