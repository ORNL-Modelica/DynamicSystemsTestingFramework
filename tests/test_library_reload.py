"""A worker that stays alive reads the library again before it runs a test
queued against a newer state of the source files — once per state, not per
item — and a library that does not load is that test's failure.

Over the fake persistent workers of `test_simulation_pool` (the pool's
threads run the real `WorkerRunner`): one whose tool reads the files again
in place (an override of `Worker.reload`), and the plain one, which keeps
the default — a restart."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from conftest import make_test_model
from dstf.backends.persistent import STARTUP_TIMEOUT_HINT, LibraryReloadError
from dstf.backends.pool import ItemState, SimulationPool, StartingWorker
from dstf.discovery.library_state import LibraryChanges, LibraryState
from dstf.pipeline import ItemRun
from test_simulation_pool import (
    _STORE,
    _config,
    _ended,
    _FakeBackend,
    _FakeWorker,
    _t,
    _wait_until,
)

ROOT = Path("/lib")
A_MO, B_MO, NEW_MO = ROOT / "A.mo", ROOT / "B.mo", ROOT / "New.mo"

S1 = LibraryState(1, {A_MO: (1, 10), B_MO: (1, 10)})
S2 = LibraryState(2, {A_MO: (2, 11), B_MO: (1, 10), NEW_MO: (1, 5)})
S3 = LibraryState(3, {A_MO: (3, 12), NEW_MO: (1, 5)})
S4 = LibraryState(4, {A_MO: (4, 13), NEW_MO: (1, 5)})


class _ReloadingWorker(_FakeWorker):
    """Its tool reads the files again in place. Logs each reload and each
    run in the order the tool saw them, with the thread it was on."""

    def reload(self, changes):
        self._step = 'loadFile("/lib/package.mo")'
        self.b.log.append(("reload", changes, threading.current_thread().name))
        gate, released = self.b.reload_gate, self.released
        if gate is not None:
            self.b.reloading.set()
            while not gate.is_set() and not released.is_set():
                gate.wait(0.005)
        if self.b.broken:
            raise LibraryReloadError(self.b.broken)

    def run_test(self, test, test_key, progress=None):
        self.b.log.append(("run", test.model_id, test.stop_time))
        return super().run_test(test, test_key, progress)


class _ReloadingBackend(_FakeBackend):
    worker_type: type[_FakeWorker] = _ReloadingWorker

    def __init__(self, config):
        super().__init__(config)
        self.log: list[tuple] = []
        self.broken: str | None = None
        self.reload_gate: threading.Event | None = None
        self.reloading = threading.Event()

    def make_worker(self, worker_id):
        worker = self.worker_type(worker_id, self.config, self)
        self.workers.append(worker)
        return worker


class _RestartingBackend(_ReloadingBackend):
    """Workers that keep `Worker.reload`'s default."""

    worker_type = _FakeWorker


def _pool(tmp_path, backend_cls, **config):
    backend = backend_cls(_config(tmp_path, **config))
    pool = SimulationPool(
        backend, ItemRun(backend.config, backend=backend, store=_STORE)
    )
    return backend, pool


@pytest.fixture
def reloading(tmp_path):
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _ReloadingBackend)
    yield backend, pool
    _close(backend, pool, before)


@pytest.fixture
def restarting(tmp_path):
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _RestartingBackend)
    yield backend, pool
    _close(backend, pool, before)


def _close(backend, pool, before):
    for gate in backend.gates.values():
        gate.set()
    if backend.reload_gate is not None:
        backend.reload_gate.set()
    pool.close()
    _wait_until(lambda: set(threading.enumerate()) <= before, what="threads gone")


def _kinds(log):
    return [entry[0] for entry in log]


def _runs(log):
    return [entry[1] for entry in log if entry[0] == "run"]


# ---------------------------------------------------------------------------
# A worker whose tool reads the files again in place
# ---------------------------------------------------------------------------


def test_one_reload_before_a_newer_state_and_none_between_items_of_one(reloading):
    backend, pool = reloading
    a, b = pool.submit(_t("A"), S1), pool.submit(_t("B"), S1)
    _wait_until(lambda: _ended(a, b))
    # The worker STARTED for these items: it read the library at S1.
    assert _kinds(backend.log) == ["run", "run"]

    c, d = pool.submit(_t("C"), S2), pool.submit(_t("D"), S2)
    _wait_until(lambda: _ended(c, d))
    e = pool.submit(_t("E"), S2)  # the next click found nothing changed
    _wait_until(lambda: _ended(e))
    assert backend.starts == [0]  # the SAME worker throughout
    assert _kinds(backend.log) == ["run", "run", "reload", "run", "run", "run"]
    assert backend.log[2][1] == LibraryChanges(changed=(A_MO,), added=(NEW_MO,))
    assert all(i.state is ItemState.FINISHED for i in (a, b, c, d, e))


def test_the_reload_runs_on_the_tests_thread_under_its_watchdog(reloading):
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert backend.log[1][2] == "worker-exec-0"


def test_a_worker_that_sat_out_a_state_gets_every_change_since_it_read(reloading):
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    # Two clicks' worth of edits before this worker takes its next item.
    b = pool.submit(_t("B"), S3)
    _wait_until(lambda: _ended(b))
    assert backend.log[1][1] == LibraryChanges(
        changed=(A_MO,), added=(NEW_MO,), removed=(B_MO,)
    )


def test_two_workers_each_read_the_library_again(tmp_path):
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _ReloadingBackend, parallel=2)
    try:
        backend.gate("Lib.A", "Lib.B")
        a, b = pool.submit(_t("A"), S1), pool.submit(_t("B"), S1)
        _wait_until(lambda: len(backend.ran) == 2)  # both workers are up
        for gate in backend.gates.values():
            gate.set()
        _wait_until(lambda: _ended(a, b))
        backend.gate("Lib.C", "Lib.D")
        c, d = pool.submit(_t("C"), S2), pool.submit(_t("D"), S2)
        _wait_until(lambda: len(backend.ran) == 4)
        for gate in backend.gates.values():
            gate.set()
        _wait_until(lambda: _ended(c, d))
        reloads = [entry for entry in backend.log if entry[0] == "reload"]
        assert sorted(entry[2] for entry in reloads) == [
            "worker-exec-0",
            "worker-exec-1",
        ]
        assert sorted(backend.starts) == [0, 1]
    finally:
        _close(backend, pool, before)


def test_a_worker_started_after_the_change_does_not_read_it_again(reloading):
    backend, pool = reloading
    # The first item of the pool carries S2: the worker starts for it, and
    # a start reads the files as they are.
    a = pool.submit(_t("A"), S2)
    _wait_until(lambda: _ended(a))
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert _kinds(backend.log) == ["run", "run"]


def test_a_second_worker_started_later_holds_the_latest_state_the_pool_was_told(
    tmp_path,
):
    """The pool keeps the NEWEST state an item carried: a worker started
    after S2 was submitted read the files at S2 or later, whatever older
    state the pool had seen before it."""
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _ReloadingBackend)
    try:
        backend.gate("Lib.A")
        a = pool.submit(_t("A"), S1)
        _wait_until(lambda: len(backend.ran) == 1)  # worker 0 holds S1, and is busy
        b = pool.submit(_t("B"), S2)
        c = pool.submit(_t("C"), S1)  # an older state, told after the newer
        pool.workers = 2  # worker 1 starts now, and takes B
        _wait_until(lambda: _ended(b, c))
        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(a))
        assert sorted(backend.starts) == [0, 1]
        # Nothing was read again: worker 1 started on the files as they are.
        assert "reload" not in _kinds(backend.log)
        assert pool._library is S2
    finally:
        _close(backend, pool, before)


def test_a_state_told_while_a_worker_starts_is_read_again_by_it(tmp_path):
    """The worker is held to the state the pool knew BEFORE its start began:
    a click during the start may have found files the start had already
    read, so its items make the worker read again."""
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _ReloadingBackend)
    starting, go = threading.Event(), threading.Event()
    backend.start_script = lambda worker: (starting.set(), go.wait(30))
    try:
        a = pool.submit(_t("A"), S1)
        assert starting.wait(15)
        b = pool.submit(_t("B"), S2)  # during the start
        go.set()
        _wait_until(lambda: _ended(a, b))
        assert _kinds(backend.log) == ["run", "reload", "run"]
        assert backend.log[1][1] == S2.changes_since(S1)
    finally:
        go.set()
        _close(backend, pool, before)


def test_a_worker_brought_back_from_a_death_read_the_files_as_they_are(reloading):
    backend, pool = reloading
    backend.raises.add("Lib.A")  # leaves the worker dead
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    c = pool.submit(_t("C"), S2)
    _wait_until(lambda: _ended(c))
    assert backend.starts == [0, 0]
    assert _kinds(backend.log) == ["run", "run", "run"]


def test_a_library_that_does_not_load_fails_the_item_and_is_asked_again(reloading):
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    backend.broken = "Lib does not load: [syntax:error:2] No viable alternative"
    b, c = pool.submit(_t("B"), S2), pool.submit(_t("C"), S2)
    _wait_until(lambda: _ended(b, c))
    # Neither ran: the tool still holds the OLD A, and a run would pass on it.
    assert _runs(backend.log) == ["Lib.A"]
    assert [i.state for i in (b, c)] == [ItemState.FAILED, ItemState.FAILED]
    assert b.message == backend.broken and c.message == backend.broken
    assert backend.starts == [0] and backend.workers[0].is_alive()
    # Fixed on disk. What the worker holds after a reload that failed is
    # unknown — some of S2's files, maybe — so no list of changes is right
    # for it: every reload from the failure on is asked for everything.
    backend.broken = None
    d = pool.submit(_t("D"), S3)
    _wait_until(lambda: _ended(d))
    assert d.state is ItemState.FINISHED
    assert _kinds(backend.log) == ["run", "reload", "reload", "reload", "run"]
    assert [entry[1] for entry in backend.log if entry[0] == "reload"] == [
        S2.changes_since(S1),
        None,
        None,
    ]
    # Read whole, it holds S3: the next state is a list of changes again.
    e = pool.submit(_t("E"), S4)
    _wait_until(lambda: _ended(e))
    assert backend.log[-2][:2] == ("reload", S4.changes_since(S3))


def test_files_put_back_as_they_were_are_still_read_after_a_reload_that_failed(
    reloading,
):
    """The state after the failure equals the one the worker read before
    it, file for file — and the worker took SOME of the failed one's
    files. "Nothing differs from what it read" must not be the answer."""
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    backend.broken = "Lib does not load: B.mo: [syntax:error:2]"
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.FAILED
    backend.broken = None
    restored = LibraryState(3, dict(S1.stamps))
    assert not restored.changes_since(S1)
    c = pool.submit(_t("C"), restored)
    _wait_until(lambda: _ended(c))
    assert c.state is ItemState.FINISHED
    assert backend.log[-2][:2] == ("reload", None)


def test_an_item_with_no_state_asks_nothing_of_the_worker(reloading):
    """A source no worker keeps loaded; `run_tests`' own dispatch."""
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    b = pool.submit(_t("B"))
    _wait_until(lambda: _ended(a, b))
    assert _kinds(backend.log) == ["run", "run"]


def test_an_update_keeps_the_items_state_unless_it_is_handed_a_later_one(reloading):
    backend, pool = reloading
    backend.gate("Lib.A")
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: a.state is ItemState.RUNNING)
    b = pool.submit(_t("B"), S2)
    later = make_test_model("Lib.B", stop_time=7.0)
    # A spec edit: the test's settings follow, the state does not move.
    assert pool.update(b, later) is True
    assert b.test is later and b.library is S2
    # A state never moves back...
    assert pool.update(b, later, S1) is True and b.library is S2
    # ...and a later click that read the test's file again raises it.
    assert pool.update(b, later, S3) is True and b.library is S3
    backend.gates["Lib.A"].set()
    _wait_until(lambda: _ended(a, b))
    assert backend.log[0][:2] == ("run", "Lib.A")
    assert backend.log[1][:2] == ("reload", S3.changes_since(S1))
    assert backend.log[2] == ("run", "Lib.B", 7.0)


def test_while_it_reads_the_pool_says_so_with_the_tools_step(reloading):
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    backend.reload_gate = threading.Event()
    b = pool.submit(_t("B"), S2)
    assert backend.reloading.wait(15)
    (reading,) = pool.starting
    assert isinstance(reading, StartingWorker)
    assert (reading.worker_id, reading.reload, reading.restart) == (0, True, False)
    assert reading.phase == 'loadFile("/lib/package.mo")'
    assert b.state is ItemState.RUNNING
    backend.reload_gate.set()
    _wait_until(lambda: _ended(b))
    assert pool.starting == () and b.state is ItemState.FINISHED


def test_a_cancel_while_it_reads_ends_the_item_and_the_next_runs_fresh(reloading):
    backend, pool = reloading
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    backend.reload_gate = threading.Event()
    b = pool.submit(_t("B"), S2)
    assert backend.reloading.wait(15)
    assert pool.cancel(b) is True
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.CANCELLED and _runs(backend.log) == ["Lib.A"]
    # The stop killed the tool mid-read: the next item's is a new one,
    # which read the files as they are.
    backend.reload_gate = None
    c = pool.submit(_t("C"), S2)
    _wait_until(lambda: _ended(c))
    assert c.state is ItemState.FINISHED and backend.starts == [0, 0]
    assert _kinds(backend.log) == ["run", "reload", "run"]


def test_a_read_that_hangs_is_the_items_timeout(tmp_path):
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _ReloadingBackend)
    try:
        a = pool.submit(_t("A"), S1)
        _wait_until(lambda: _ended(a))
        backend.reload_gate = threading.Event()
        b = pool.submit(make_test_model("Lib.B", timeout=0.2), S2)
        _wait_until(lambda: _ended(b))
        assert b.state is ItemState.FAILED and b.run_result.timed_out
        assert _runs(backend.log) == ["Lib.A"]
    finally:
        _close(backend, pool, before)


# ---------------------------------------------------------------------------
# The default: a worker that cannot read in place is restarted
# ---------------------------------------------------------------------------


def test_the_default_reload_restarts_the_worker_once_per_state(restarting):
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    assert backend.starts == [0] and backend.closes == []
    b, c = pool.submit(_t("B"), S2), pool.submit(_t("C"), S2)
    _wait_until(lambda: _ended(b, c))
    assert backend.starts == [0, 0] and backend.closes == [0]
    assert [i.state for i in (a, b, c)] == [ItemState.FINISHED] * 3
    # Not a death: nothing is charged against the worker's restart budget.
    assert backend.workers[0]._n_restarts == 0


def test_the_default_reload_is_a_close_and_a_bounded_start(restarting):
    backend, _pool = restarting
    worker = backend.make_worker(0)
    assert not worker.reloads_in_place
    worker.start_within()
    worker.reload(None)
    assert backend.starts == [0, 0] and backend.closes == [0]
    assert worker.is_alive()
    worker.close()


def test_a_restart_to_read_again_is_not_charged_to_the_restart_budget(restarting):
    """A worker at the end of its budget — every death so far was real — is
    still restarted for a changed library: nothing died."""
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    backend.workers[0]._n_restarts = backend.max_restarts_per_worker
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.FINISHED, b.message
    assert backend.starts == [0, 0]


def test_a_restart_to_read_again_shows_as_a_restart_under_its_item(restarting):
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    restarting_now, release = threading.Event(), threading.Event()
    backend.start_script = lambda worker: (restarting_now.set(), release.wait(15))
    try:
        b = pool.submit(_t("B"), S2)
        assert restarting_now.wait(15)
        (starting,) = pool.starting
        assert (starting.restart, starting.reload) == (True, False)
    finally:
        release.set()
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.FINISHED


def test_a_restart_to_read_again_that_fails_is_retried_as_a_dead_workers(restarting):
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    fails = [True]

    def flaky(worker):
        if fails:
            fails.pop()
            raise RuntimeError("licence seat not free")

    backend.start_script = flaky
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.FINISHED
    assert backend.starts == [0, 0, 0]


def test_a_restart_to_read_again_that_fails_says_the_backends_reason(restarting):
    """Both sentences carry it: the item's, and what every waiting item is
    failed with once no worker is left."""
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))

    def no_licence(worker):
        raise RuntimeError("no licence: FlexNet error -15")

    backend.start_script = no_licence
    b, c = pool.submit(_t("B"), S2), pool.submit(_t("C"), S2)
    _wait_until(lambda: _ended(b, c))
    dead = "Worker dead; restart exhausted — no licence: FlexNet error -15"
    assert (b.state, b.message) == (ItemState.FAILED, dead)
    stopped = f"No Stub worker could start — worker 0: {dead}"
    assert (c.state, c.message) == (ItemState.FAILED, stopped)
    assert pool.stopped == stopped


def test_a_restart_to_read_again_that_never_returns_says_its_bound(tmp_path):
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _RestartingBackend, worker_startup_timeout=0.2)
    release = threading.Event()
    try:
        a = pool.submit(_t("A"), S1)
        _wait_until(lambda: _ended(a))
        backend.start_script = lambda worker: release.wait(60)
        b = pool.submit(_t("B"), S2)
        _wait_until(lambda: _ended(b))
        assert b.state is ItemState.FAILED and b.message == (
            "Worker dead; restart exhausted — did not start within 0.2 s "
            f"{STARTUP_TIMEOUT_HINT}"
        )
        assert backend.starts == [0, 0]  # the one given up is not tried again
    finally:
        release.set()
        _close(backend, pool, before)


def test_a_start_that_then_works_forgets_the_reason_of_the_one_that_failed(
    restarting,
):
    """A worker retired at its budget, with no start attempted, says no
    reason of a start it had recovered from."""
    backend, pool = restarting
    a = pool.submit(_t("A"), S1)
    _wait_until(lambda: _ended(a))
    fail = [True]

    def once(worker):
        if fail.pop() if fail else False:
            raise RuntimeError("licence seat taken")

    backend.start_script = once  # the reload's restart fails, the retry works
    b = pool.submit(_t("B"), S2)
    _wait_until(lambda: _ended(b))
    assert b.state is ItemState.FINISHED, b.message
    (slot,) = pool._slots.values()
    assert slot.runner._start_failure is None and slot.runner._dead() == (
        "Worker dead; restart exhausted"
    )


def test_a_cancel_while_the_worker_restarts_ends_the_item_at_once(tmp_path):
    """The start's bound is minutes (600 s by default): a cancel asked
    while the worker is brought back gives that start up, and the item
    ends CANCELLED within the start's poll — not at the bound. The worker
    is not retired for it: the next item runs, on a new tool."""
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _RestartingBackend, worker_startup_timeout=60.0)
    release = threading.Event()
    try:
        a = pool.submit(_t("A"), S1)
        _wait_until(lambda: _ended(a))
        restarting = threading.Event()
        backend.start_script = lambda worker: (restarting.set(), release.wait(60))
        b = pool.submit(_t("B"), S2)
        assert restarting.wait(15)
        first = backend.workers[0]
        began = time.monotonic()
        assert pool.cancel(b) is True
        _wait_until(lambda: _ended(b), what="the cancel")
        assert time.monotonic() - began < 5.0  # of a 60 s bound
        assert (b.state, b.message) == (ItemState.CANCELLED, "Cancelled")
        assert [model for model, _ in backend.ran] == ["Lib.A"]
        assert pool.stopped is None and len(pool._slots) == 1

        # The worker whose start was given up is spent; its slot has a new
        # one, started for the next item, which read the files as they are.
        backend.start_script = None
        c = pool.submit(_t("C"), S2)
        _wait_until(lambda: _ended(c))
        assert c.state is ItemState.FINISHED, c.message
        (slot,) = pool._slots.values()
        assert slot.runner.worker is backend.workers[-1] is not first
        assert first._start_abandoned and not slot.runner.worker._start_abandoned
        assert [model for model, _ in backend.ran] == ["Lib.A", "Lib.C"]
        # The start that was given up comes back: it closes what it brought
        # up, and touches nothing of the worker that replaced it.
        release.set()
        _wait_until(lambda: not first.is_alive(), what="the old start closed")
        assert slot.runner.worker.is_alive()
        d = pool.submit(_t("D"), S2)
        _wait_until(lambda: _ended(d))
        assert d.state is ItemState.FINISHED
    finally:
        release.set()
        _close(backend, pool, before)


def test_a_cancel_while_a_restart_fails_is_cancelled_and_retires_nothing(tmp_path):
    """The start raised by itself while a cancel was on record: the item
    is cancelled all the same, with no retry and no pause for it, and the
    next item tries the worker again."""
    before = set(threading.enumerate())
    backend, pool = _pool(tmp_path, _RestartingBackend)
    backend.restart_retry_backoff_seconds = 30.0
    try:
        a = pool.submit(_t("A"), S1)
        _wait_until(lambda: _ended(a))
        entered, go = threading.Event(), threading.Event()

        def fails(worker):
            entered.set()
            assert go.wait(30)
            raise RuntimeError("licence seat taken")

        backend.start_script = fails
        b = pool.submit(_t("B"), S2)
        assert entered.wait(15)
        worker = backend.workers[0]
        with worker._run_lock:  # the request, without the give-up it causes
            worker._cancel = "soft"
        worker._START_POLL_SECONDS = 30.0
        go.set()
        _wait_until(lambda: _ended(b), what="the cancel")
        assert (b.state, b.message) == (ItemState.CANCELLED, "Cancelled")
        assert pool.stopped is None
        backend.start_script = None
        c = pool.submit(_t("C"), S2)
        _wait_until(lambda: _ended(c))
        assert c.state is ItemState.FINISHED and backend.workers == [worker]
    finally:
        _close(backend, pool, before)
