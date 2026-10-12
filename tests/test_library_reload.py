"""A worker that stays alive reads the library again before it runs a test
queued against a newer state of the source files — once per state, not per
item — and a library that does not load is that test's failure.

Over the fake persistent workers of `test_simulation_pool` (the pool's
threads run the real `WorkerRunner`): one whose tool reads the files again
in place (an override of `Worker.reload`), and the plain one, which keeps
the default — a restart."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from conftest import make_test_model
from dstf.backends.persistent import LibraryReloadError
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
    # Fixed on disk: the next click's item reads everything that differs
    # from what the worker holds — S1 still — and runs.
    backend.broken = None
    d = pool.submit(_t("D"), S3)
    _wait_until(lambda: _ended(d))
    assert d.state is ItemState.FINISHED
    assert _kinds(backend.log) == ["run", "reload", "reload", "reload", "run"]
    assert backend.log[3][1] == S3.changes_since(S1)


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
