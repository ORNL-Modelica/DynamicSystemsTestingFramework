"""The simulation pool (`dstf.backends.pool`): a queue of tests that stays
open — first in first out, never queued twice, remove while waiting, cancel
while running, one callback when an item starts and one when it ends, a
worker count that can change, and a close that leaves nothing behind.

The unit tier drives it over fake persistent `Worker`s (the pool's threads
run the same `WorkerRunner` the CLI's dispatch does) and over a backend with
no workers at all, each item through the real per-item run path
(`pipeline.ItemRun`) in a temp work dir. The integration tier runs real
Python-backend subprocesses and compares the results with `run_pipeline`'s.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psutil
import pytest

from conftest import make_test_model
from dstf.backends.base import Backend, ItemRunner
from dstf.backends.persistent import (
    STARTUP_TIMEOUT_HINT,
    PersistentBackendBase,
    Worker,
)
from dstf.backends.pool import (
    ItemState,
    QueueItem,
    Refused,
    SimulationPool,
    StartingWorker,
)
from dstf.backends.types import Capability, TestResult, TestRunResult
from dstf.pipeline import ItemRun, open_pool, run_pipeline


def _wait_until(pred, timeout: float = 15.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError(f"{what} not reached")


def _gone(proc: psutil.Process) -> bool:
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True  # reaped between the two looks


def _ended(*items: QueueItem) -> bool:
    return all(
        item.state not in (ItemState.WAITING, ItemState.RUNNING) for item in items
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWorker(Worker):
    """A template worker whose `run_test` waits on the backend's gate for
    the model (none: returns at once) and fails the models told to fail."""

    _START_POLL_SECONDS = 0.02
    _CHILD_WATCH_SECONDS = 0.05
    launch_grace = 0.0  # these starts launch nothing

    def __init__(self, worker_id, config, backend):
        super().__init__(worker_id, config)
        self.b = backend
        self.handle: object | None = None
        self.released = threading.Event()

    def start(self):
        self.b.starts.append(self.worker_id)
        script = self.b.start_script
        if script is not None:
            script(self)
        self.released = threading.Event()
        self.handle = object()

    def is_alive(self):
        return self.handle is not None and not self.tool_gone()

    def _detach_interface(self):
        handle, self.handle = self.handle, None
        return handle

    def _shutdown_interface(self, handle, grace):
        self.b.closes.append(self.worker_id)
        self.released.set()  # a closed tool lets go of the call parked in it

    def run_test(self, test, test_key, progress=None):
        self.b.ran.append((test.model_id, self.worker_id))
        gate, released = self.b.gates.get(test.model_id), self.released
        while gate is not None and not gate.is_set() and not released.is_set():
            time.sleep(0.005)
        if test.model_id in self.b.raises:
            raise RuntimeError("pipe broken")
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=test.model_id not in self.b.fails and not released.is_set(),
            error_message="solver failed" if test.model_id in self.b.fails else None,
        )


class _FakeBackend(PersistentBackendBase):
    backend_label = "Stub"
    restart_retry_backoff_seconds = 0.01
    capabilities = frozenset({Capability.PERSISTENT_WORKERS, Capability.CANCEL})

    def __init__(self, config):
        super().__init__(config)
        self.quiet = True
        self.gates: dict[str, threading.Event] = {}
        self.fails: set[str] = set()
        self.raises: set[str] = set()
        self.ran: list[tuple[str, int]] = []
        self.starts: list[int] = []
        self.closes: list[int] = []
        self.start_script = None
        self.workers: list[_FakeWorker] = []

    def make_worker(self, worker_id):
        worker = _FakeWorker(worker_id, self.config, self)
        self.workers.append(worker)
        return worker

    def read_result(self, test, test_key, run_result):
        return TestResult(model_id=test.model_id, success=True)

    def gate(self, *model_ids: str) -> None:
        for model_id in model_ids:
            self.gates[model_id] = threading.Event()


_STORE = SimpleNamespace(index=SimpleNamespace(get_id=lambda model_id: None))


def _config(tmp_path, **kw):
    kw.setdefault("parallel", 1)
    return SimpleNamespace(
        work_dir=tmp_path / "work", timeout=30.0, backend="Stub", **kw
    )


class _Events:
    """Both callbacks, recording what was called, for which model, on which
    thread."""

    def __init__(self):
        self.log: list[tuple[str, str]] = []
        self.threads: set[str] = set()
        self.states: dict[str, ItemState] = {}
        self._lock = threading.Lock()

    def on_start(self, item):
        with self._lock:
            self.log.append(("start", item.model_id))
            self.threads.add(threading.current_thread().name)

    def on_end(self, item):
        with self._lock:
            self.log.append(("end", item.model_id))
            self.threads.add(threading.current_thread().name)
            self.states[item.model_id] = item.state

    def of(self, model_id: str) -> list[str]:
        return [what for what, model in self.log if model == model_id]


@pytest.fixture
def stub(tmp_path):
    """`(backend, pool, events)` over fake workers; the pool is closed and
    its threads checked gone when the test ends."""
    before = set(threading.enumerate())
    backend = _FakeBackend(_config(tmp_path))
    events = _Events()
    pool = SimulationPool(
        backend,
        ItemRun(backend.config, backend=backend, store=_STORE),
        on_start=events.on_start,
        on_end=events.on_end,
    )
    yield backend, pool, events
    for gate in backend.gates.values():
        gate.set()
    pool.close()
    _wait_until(
        lambda: set(threading.enumerate()) <= before, what="the pool's threads gone"
    )


def _t(name: str):
    return make_test_model(f"Lib.{name}")


def _manifest(config) -> dict[str, str]:
    data = json.loads((config.work_dir / "batch_manifest.json").read_text())
    return {entry["model_id"]: entry.get("last_run_status") for entry in data.values()}


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


class TestQueue:
    def test_no_worker_exists_before_the_first_submit(self, stub):
        backend, pool, _ = stub
        assert backend.starts == [] and pool.workers == 1

        item = pool.submit(_t("A"))
        _wait_until(lambda: _ended(item))

        assert backend.starts == [0]
        assert item.state is ItemState.FINISHED and item.worker_id == 0
        assert item.test_key == "test_0001" and item.run_result.success
        assert item.result.success and item.message is None
        assert _manifest(backend.config) == {"Lib.A": "passed"}

    def test_first_in_first_out(self, stub):
        backend, pool, _ = stub
        backend.gate("Lib.A")
        items = [pool.submit(_t(name)) for name in "ABCDE"]
        assert [i.model_id for i in pool.waiting][-4:] == [f"Lib.{n}" for n in "BCDE"]
        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(*items))

        assert [model for model, _ in backend.ran] == [f"Lib.{n}" for n in "ABCDE"]

    def test_an_item_submitted_while_another_runs_is_appended(self, stub):
        backend, pool, _ = stub
        backend.gate("Lib.A")
        a, b = pool.submit(_t("A")), pool.submit(_t("B"))
        _wait_until(lambda: a.state is ItemState.RUNNING)
        c = pool.submit(_t("C"))
        assert pool.running == (a,) and pool.waiting == (b, c)

        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(a, b, c))
        assert [model for model, _ in backend.ran] == ["Lib.A", "Lib.B", "Lib.C"]

    def test_a_test_is_never_queued_twice(self, stub):
        backend, pool, _ = stub
        backend.gate("Lib.A")
        a, b = pool.submit(_t("A")), pool.submit(_t("B"))
        _wait_until(lambda: a.state is ItemState.RUNNING)

        with pytest.raises(Refused, match="Lib.A is already running"):
            pool.submit(_t("A"))
        with pytest.raises(Refused, match="Lib.B is already queued"):
            pool.submit(_t("B"))

        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(a, b))
        again = pool.submit(_t("A"))  # ended: it may run again
        _wait_until(lambda: _ended(again))
        assert [model for model, _ in backend.ran].count("Lib.A") == 2

    def test_a_waiting_item_is_removed_and_never_runs(self, stub):
        backend, pool, events = stub
        backend.gate("Lib.A")
        a, b, c = (pool.submit(_t(name)) for name in "ABC")
        _wait_until(lambda: a.state is ItemState.RUNNING)

        assert pool.remove(b) is True
        assert b.state is ItemState.REMOVED and pool.waiting == (c,)
        assert pool.remove(b) is False  # already out
        assert pool.remove(a) is False  # running: not removed
        assert a.state is ItemState.RUNNING

        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(a, c))
        assert "Lib.B" not in [model for model, _ in backend.ran]
        assert events.of("Lib.B") == []
        assert "Lib.B" not in _manifest(backend.config)
        pool.submit(_t("B"))  # removed: it may be queued again

    def test_a_waiting_item_s_test_is_replaced_until_it_starts(self, stub):
        """`update` hands a waiting item the test as it stands now — the
        tool's queued test runs with the spec as it is when it STARTS — and
        keeps its place. Once it runs, or has ended, nothing is changed."""
        backend, pool, _ = stub
        backend.gate("Lib.A", "Lib.B")
        ran_with: list[float] = []
        run_test = _FakeWorker.run_test

        def recording(worker, test, test_key, progress=None):
            ran_with.append(test.stop_time)
            return run_test(worker, test, test_key, progress)

        _FakeWorker.run_test = recording
        try:
            a, b, c = (pool.submit(_t(name)) for name in "ABC")
            _wait_until(lambda: a.state is ItemState.RUNNING)

            later = make_test_model("Lib.B", stop_time=7.0)
            assert pool.update(b, later) is True and b.test is later
            assert pool.waiting == (b, c)  # its place is kept
            assert pool.update(a, make_test_model("Lib.A", stop_time=7.0)) is False
            with pytest.raises(ValueError, match="Lib.C is not Lib.B"):
                pool.update(b, _t("C"))

            backend.gates["Lib.A"].set()
            _wait_until(lambda: b.state is ItemState.RUNNING)
            assert pool.update(b, _t("B")) is False and b.test is later
            backend.gates["Lib.B"].set()
            _wait_until(lambda: _ended(a, b, c))
            assert pool.update(c, _t("C")) is False
        finally:
            _FakeWorker.run_test = run_test
        assert ran_with == [1.0, 7.0, 1.0]

    def test_a_failed_simulation_is_failed_with_the_backend_s_message(self, stub):
        backend, pool, _ = stub
        backend.fails.add("Lib.A")
        item = pool.submit(_t("A"))
        _wait_until(lambda: _ended(item))

        assert item.state is ItemState.FAILED and item.message == "solver failed"
        assert item.result.success is False
        assert _manifest(backend.config) == {"Lib.A": "failed"}


class TestCallbacks:
    def test_each_item_gets_one_start_then_one_end_off_the_caller_s_thread(self, stub):
        backend, pool, events = stub
        backend.fails.add("Lib.B")
        backend.gate("Lib.C")
        pool.workers = 2
        items = [pool.submit(_t(name)) for name in "ABCD"]
        _wait_until(lambda: items[2].state is ItemState.RUNNING)
        pool.cancel(items[2])
        _wait_until(lambda: _ended(*items))

        for name in "ABCD":
            assert events.of(f"Lib.{name}") == ["start", "end"], name
        assert events.states == {
            "Lib.A": ItemState.FINISHED,
            "Lib.B": ItemState.FAILED,
            "Lib.C": ItemState.CANCELLED,
            "Lib.D": ItemState.FINISHED,
        }
        assert threading.current_thread().name not in events.threads
        assert all("stub-pool-" in name for name in events.threads)

    def test_on_end_runs_outside_the_pool_s_lock(self, tmp_path):
        """A callback that takes its time must not hold the queue: another
        thread reads it and submits to it meanwhile."""
        backend = _FakeBackend(_config(tmp_path))
        in_callback, release = threading.Event(), threading.Event()

        def on_end(item):
            in_callback.set()
            release.wait(15)

        pool = SimulationPool(
            backend,
            ItemRun(backend.config, backend=backend, store=_STORE),
            on_end=on_end,
        )
        try:
            a = pool.submit(_t("A"))
            assert in_callback.wait(15)
            box: dict = {}

            def read():
                box.update(waiting=pool.waiting, running=pool.running)
                box["b"] = pool.submit(_t("B"))

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            reader.join(5)

            assert not reader.is_alive(), "on_end ran under the pool's lock"
            assert box["waiting"] == () and box["running"] == ()
            assert a.state is ItemState.FINISHED  # final before its callback
            assert box["b"].state is ItemState.WAITING  # A's thread is in on_end
        finally:
            release.set()
            pool.close()

    def test_a_callback_may_submit_and_one_that_raises_costs_nothing(self, tmp_path):
        backend = _FakeBackend(_config(tmp_path))
        box: dict = {}

        def on_end(item):
            if item.model_id == "Lib.A":
                box["b"] = pool.submit(_t("B"))
                raise RuntimeError("the page is gone")

        pool = SimulationPool(
            backend,
            ItemRun(backend.config, backend=backend, store=_STORE),
            on_end=on_end,
        )
        try:
            a = pool.submit(_t("A"))
            _wait_until(lambda: "b" in box and _ended(a, box["b"]))
            assert box["b"].state is ItemState.FINISHED
        finally:
            pool.close()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_in_each_state(self, stub):
        backend, pool, events = stub
        backend.gate("Lib.A")
        a, b = pool.submit(_t("A")), pool.submit(_t("B"))
        _wait_until(lambda: ("Lib.A", 0) in backend.ran)

        assert pool.cancel(b) is False  # waiting: remove is the verb
        assert b.state is ItemState.WAITING
        assert pool.cancel(a) is True
        _wait_until(lambda: _ended(a, b))

        assert a.state is ItemState.CANCELLED and a.message == "Cancelled"
        assert a.run_result.cancelled and a.result is None
        assert pool.cancel(a) is False  # ended
        assert b.state is ItemState.FINISHED  # the queue went on
        assert pool.cancel(b) is False
        assert events.of("Lib.A") == ["start", "end"]

    def test_an_item_asked_to_stop_says_so_until_it_ends(self, stub, monkeypatch):
        """A cancel is a request. A simulation that takes it — and one that
        never looks at it, which runs on until its worker's grace is over
        (Dymola, a model blocked in an external call: about 11 s) — is
        still RUNNING meanwhile: the item says a stop was asked, for the
        queue's row to read."""
        backend, pool, _ = stub
        # The tool says it will come back on its own, and takes its time.
        monkeypatch.setattr(_FakeWorker, "cancel_running", lambda self, key: True)
        backend.gate("Lib.A")
        a = pool.submit(_t("A"))
        assert a.stopping is False  # waiting
        _wait_until(lambda: ("Lib.A", 0) in backend.ran)
        assert a.state is ItemState.RUNNING and a.stopping is False

        assert pool.cancel(a) is True
        time.sleep(0.3)  # well past a watchdog tick: it is still running
        assert a.state is ItemState.RUNNING and a.stopping is True

        backend.gates["Lib.A"].set()
        _wait_until(lambda: _ended(a))
        assert a.stopping is False

    def test_a_cancelled_item_is_recorded_cancelled_never_failed(self, stub):
        backend, pool, _ = stub
        backend.gate("Lib.A")
        a = pool.submit(_t("A"))
        _wait_until(lambda: ("Lib.A", 0) in backend.ran)
        # The hazard: an earlier run's "failed" row for the same model.
        (backend.config.work_dir / "status.json").write_text(
            json.dumps({"tests": [{"model_id": "Lib.A", "status": "failed"}]})
        )
        pool.cancel(a)
        _wait_until(lambda: _ended(a))

        assert _manifest(backend.config) == {"Lib.A": "cancelled"}
        assert backend.read_last_results([_t("A")]) == {}

    def test_a_cancel_before_the_item_reached_its_worker_still_lands(self, tmp_path):
        """Asked between "taken from the queue" and "handed to the runner":
        the item ends cancelled without its simulation having started."""
        backend = _FakeBackend(_config(tmp_path))
        entered, release = threading.Event(), threading.Event()

        class _SlowBegin(ItemRun):
            def begin(self, test):
                entered.set()
                release.wait(15)
                return super().begin(test)

        pool = SimulationPool(
            backend, _SlowBegin(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            assert entered.wait(15) and item.state is ItemState.RUNNING
            assert pool.cancel(item) is True
            release.set()
            _wait_until(lambda: _ended(item))

            assert item.state is ItemState.CANCELLED
            assert backend.ran == []
            assert _manifest(backend.config) == {"Lib.A": "cancelled"}
        finally:
            release.set()
            pool.close()

    def test_a_cancel_after_the_key_and_before_the_run_still_lands(
        self, tmp_path, monkeypatch
    ):
        """The item has its key and its runner expects it; the run itself
        has not begun. The stop is kept for it: it ends cancelled without
        its simulation having started."""
        backend = _FakeBackend(_config(tmp_path))
        keyed, go = threading.Event(), threading.Event()
        real_run = ItemRunner.run

        def held(runner, test, test_key):
            keyed.set()
            go.wait(15)
            return real_run(runner, test, test_key)

        monkeypatch.setattr(ItemRunner, "run", held)
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            assert keyed.wait(15) and item.test_key == "test_0001"
            assert pool.cancel(item) is True
            go.set()
            _wait_until(lambda: _ended(item))

            assert item.state is ItemState.CANCELLED
            assert backend.ran == []
            assert _manifest(backend.config) == {"Lib.A": "cancelled"}
        finally:
            go.set()
            pool.close()

    def test_a_cancel_during_a_restart_is_heard_when_the_test_would_start(self, stub):
        backend, pool, _ = stub
        backend.raises.add("Lib.A")  # leaves the worker dead
        a = pool.submit(_t("A"))
        _wait_until(lambda: _ended(a))
        restarting, release = threading.Event(), threading.Event()
        backend.start_script = lambda worker: (restarting.set(), release.wait(15))
        try:
            b = pool.submit(_t("B"))
            assert restarting.wait(15)
            _wait_until(lambda: b.test_key is not None)
            (starting,) = pool.starting  # a restart is a start, and shows
            assert starting.worker_id == 0 and starting.restart is True

            assert pool.cancel(b) is True
            release.set()
            _wait_until(lambda: _ended(b))

            assert b.state is ItemState.CANCELLED
            assert [model for model, _ in backend.ran] == ["Lib.A"]  # B never ran
            assert backend.starts == [0, 0] and pool.starting == ()
        finally:
            release.set()

    def test_a_cancel_that_arrives_late_does_not_stop_the_test_s_next_run(
        self, stub, monkeypatch
    ):
        """The stop is the ITEM's. Asked of item one and held up on its way
        to the runner, it finds the same test running again — same key,
        same worker — as item two, which nobody asked to stop."""
        backend, pool, _ = stub
        backend.gate("Lib.A")
        on_its_way, go = threading.Event(), threading.Event()
        real_cancel = ItemRunner.cancel

        def held(runner, token):
            on_its_way.set()
            go.wait(15)
            return real_cancel(runner, token)

        monkeypatch.setattr(ItemRunner, "cancel", held)
        one = pool.submit(_t("A"))
        _wait_until(lambda: backend.ran == [("Lib.A", 0)])
        box: dict = {}
        asker = threading.Thread(target=lambda: box.update(asked=pool.cancel(one)))
        asker.start()
        try:
            assert on_its_way.wait(15)
            backend.gates["Lib.A"].set()  # item one ends by itself meanwhile
            _wait_until(lambda: _ended(one))
            backend.gate("Lib.A")
            two = pool.submit(_t("A"))
            _wait_until(lambda: backend.ran == [("Lib.A", 0)] * 2)
            assert two.test_key == one.test_key and two.worker_id == one.worker_id

            go.set()
            asker.join(15)
            time.sleep(0.3)  # a stop would have landed by now
            assert two.state is ItemState.RUNNING and backend.closes == []
            backend.gates["Lib.A"].set()
            _wait_until(lambda: _ended(two))

            assert box["asked"] is True  # one WAS running when it was asked
            assert one.state is ItemState.FINISHED  # ...and had finished anyway
            assert two.state is ItemState.FINISHED and two.result.success
        finally:
            go.set()

    def test_a_stop_asked_as_the_simulation_finished_keeps_its_result(self, tmp_path):
        """`cancel` says the item was asked to stop, not that it will end
        cancelled: the stop reached the tool as its simulation completed,
        and a simulation that finished is a result."""

        class _FinishesAnyway(_FakeWorker):
            def cancel_running(self, test_key):
                self.b.gates["Lib.A"].set()  # the tool comes back, done
                return True

        class _Backend(_FakeBackend):
            def make_worker(self, worker_id):
                worker = _FinishesAnyway(worker_id, self.config, self)
                self.workers.append(worker)
                return worker

        backend = _Backend(_config(tmp_path))
        backend.gate("Lib.A")
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            _wait_until(lambda: ("Lib.A", 0) in backend.ran)
            assert pool.cancel(item) is True
            _wait_until(lambda: _ended(item))

            assert item.state is ItemState.FINISHED and item.message is None
            assert item.run_result.success and not item.run_result.cancelled
            assert item.result.success and backend.closes == []
            assert _manifest(backend.config) == {"Lib.A": "passed"}
        finally:
            pool.close()

    def test_a_backend_without_the_flag_refuses_with_a_sentence(self, tmp_path):
        class _NoCancel(_FakeBackend):
            capabilities = frozenset({Capability.PERSISTENT_WORKERS})

        backend = _NoCancel(_config(tmp_path))
        backend.gate("Lib.A")
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            a, b = pool.submit(_t("A")), pool.submit(_t("B"))
            _wait_until(lambda: a.state is ItemState.RUNNING)
            assert pool.can_cancel is False

            with pytest.raises(
                Refused,
                match="Stub cannot stop a running simulation: Lib.A runs to its end",
            ):
                pool.cancel(a)
            assert a.state is ItemState.RUNNING
            assert pool.remove(b) is True  # removing a waiting item still works

            backend.gates["Lib.A"].set()
            _wait_until(lambda: _ended(a))
            assert a.state is ItemState.FINISHED
        finally:
            backend.gates["Lib.A"].set()
            pool.close()


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


class TestWorkerCount:
    def test_the_count_comes_from_the_config(self, tmp_path):
        backend = _FakeBackend(_config(tmp_path, parallel=3))
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            assert pool.workers == 3
            for name in "ABCDE":
                backend.gate(f"Lib.{name}")
            items = [pool.submit(_t(name)) for name in "ABCDE"]
            _wait_until(lambda: len(pool.running) == 3)
            assert sorted(backend.starts) == [0, 1, 2]
            assert len(pool.waiting) == 2
            for gate in backend.gates.values():
                gate.set()
            _wait_until(lambda: _ended(*items))
        finally:
            pool.close()

    @pytest.mark.parametrize("asked", [0, -3])
    def test_a_count_below_one_is_one(self, tmp_path, asked):
        """However it is given — the config's, the constructor's, set later
        — a queue with no worker would hold its items for good."""
        backend = _FakeBackend(_config(tmp_path, parallel=asked))
        path = ItemRun(backend.config, backend=backend, store=_STORE)
        from_config = SimulationPool(backend, path)
        given = SimulationPool(backend, path, workers=asked)
        try:
            assert from_config.workers == 1 and given.workers == 1
            given.workers = 3
            given.workers = asked
            assert given.workers == 1

            items = [from_config.submit(_t("A")), given.submit(_t("B"))]
            _wait_until(lambda: _ended(*items))
            assert [item.state for item in items] == [ItemState.FINISHED] * 2
        finally:
            from_config.close()
            given.close()

    def test_only_as_many_workers_as_there_are_items(self, tmp_path):
        backend = _FakeBackend(_config(tmp_path, parallel=4))
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            _wait_until(lambda: _ended(item))
            assert backend.starts == [0]
        finally:
            pool.close()

    def test_raised_and_lowered_mid_queue(self, stub):
        backend, pool, _ = stub
        for name in "ABCDEF":
            backend.gate(f"Lib.{name}")
        items = [pool.submit(_t(name)) for name in "ABCDEF"]
        _wait_until(lambda: items[0].state is ItemState.RUNNING)
        assert backend.starts == [0] and len(pool.waiting) == 5

        pool.workers = 3  # raised: two more start and take the next two
        _wait_until(lambda: len(pool.running) == 3)
        assert sorted(backend.starts) == [0, 1, 2]
        # The NEXT two, whichever of the new workers took which.
        assert {i.model_id for i in pool.running} == {"Lib.A", "Lib.B", "Lib.C"}

        pool.workers = 1  # lowered: nobody is interrupted
        assert len(pool.running) == 3
        for name in "ABC":
            backend.gates[f"Lib.{name}"].set()
        _wait_until(lambda: _ended(*items[:3]))
        _wait_until(lambda: len(pool._slots) == 1, what="the surplus workers gone")
        _wait_until(lambda: len(backend.closes) == 2)
        _wait_until(lambda: len(pool.running) == 1)
        assert len(pool.waiting) == 2  # one at a time from here on

        for name in "DEF":
            backend.gates[f"Lib.{name}"].set()
        _wait_until(lambda: _ended(*items))
        assert all(item.state is ItemState.FINISHED for item in items)
        assert len({worker for _, worker in backend.ran[3:]}) == 1


class TestStartBound:
    def _pool(self, tmp_path, script, **kw):
        backend = _FakeBackend(_config(tmp_path, worker_startup_timeout=0.2, **kw))
        backend.start_script = script
        events = _Events()
        pool = SimulationPool(
            backend,
            ItemRun(backend.config, backend=backend, store=_STORE),
            on_end=events.on_end,
        )
        return backend, pool, events

    def test_a_start_that_never_returns_fails_the_waiting_items(self, tmp_path):
        release = threading.Event()
        backend, pool, events = self._pool(
            tmp_path, lambda worker: release.wait(30), parallel=2
        )
        try:
            items = [pool.submit(_t(name)) for name in "ABC"]
            _wait_until(lambda: _ended(*items))

            reason = f"did not start within 0.2 s {STARTUP_TIMEOUT_HINT}"
            message = (
                f"No Stub worker could start — worker 0: {reason}; worker 1: {reason}"
            )
            assert pool.stopped == message
            for item in items:
                assert item.state is ItemState.FAILED and item.message == message
                assert item.worker_id is None and item.run_result is None
                assert events.of(item.model_id) == ["end"]  # it never started
            assert backend.ran == []
            with pytest.raises(Refused, match="No Stub worker could start"):
                pool.submit(_t("D"))  # the queue has stopped
        finally:
            release.set()
            pool.close()

    def test_a_worker_coming_up_is_listed_with_its_phase_and_seconds(self, tmp_path):
        """What a queue shows while a large library loads: "starting worker
        0 — loadFile(...) · 85 s". Gone once the worker is up."""
        release, loading = threading.Event(), threading.Event()

        def start(worker):
            worker._step = 'loadFile("/lib/package.mo")'
            loading.set()
            release.wait(30)
            worker._step = ""

        backend, pool, _ = self._pool(tmp_path, start)
        backend.config.worker_startup_timeout = 60.0
        try:
            assert pool.starting == ()  # no worker exists before a submit
            item = pool.submit(_t("A"))
            assert loading.wait(15)
            (starting,) = pool.starting
            assert isinstance(starting, StartingWorker)
            assert starting.worker_id == 0 and starting.restart is False
            assert starting.phase == 'loadFile("/lib/package.mo")'
            assert 0.0 <= starting.seconds < 15.0
            assert item.state is ItemState.WAITING  # nothing took it yet

            release.set()
            _wait_until(lambda: _ended(item))
            assert item.state is ItemState.FINISHED and pool.starting == ()
        finally:
            release.set()
            pool.close()

    def test_a_start_whose_process_died_fails_them_at_once(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        release = threading.Event()

        def start(worker):
            worker.pids = {proc.pid}
            proc.kill()
            proc.wait()
            release.wait(30)

        backend, pool, _ = self._pool(tmp_path, start)
        backend.config.worker_startup_timeout = 60.0
        try:
            t0 = time.monotonic()
            item = pool.submit(_t("A"))
            _wait_until(lambda: _ended(item))

            assert time.monotonic() - t0 < 10.0
            assert item.state is ItemState.FAILED
            assert item.message == (
                "No Stub worker could start — worker 0: its process exited during start"
            )
        finally:
            release.set()
            pool.close()

    def test_one_worker_that_starts_is_enough(self, tmp_path):
        def start(worker):
            if worker.worker_id == 0:
                raise RuntimeError("licence seat taken")

        backend, pool, _ = self._pool(tmp_path, start, parallel=2)
        backend.gate("Lib.A", "Lib.B")
        try:
            items = [pool.submit(_t(name)) for name in "ABC"]
            _wait_until(lambda: ("Lib.A", 1) in backend.ran)
            for gate in backend.gates.values():
                gate.set()
            _wait_until(lambda: _ended(*items))

            assert pool.stopped is None
            assert all(item.state is ItemState.FINISHED for item in items)
            assert {worker for _, worker in backend.ran} == {1}
        finally:
            pool.close()


_TOOL = (
    "import subprocess, sys, time;"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
    "print(c.pid, flush=True); time.sleep(120)"
)


class TestWorkerDeath:
    def test_a_worker_that_raises_fails_its_item_and_the_queue_goes_on(self, stub):
        """Exactly the CLI dispatch's handling: the item fails with the
        worker's exception, the worker is restarted, the next item runs."""
        backend, pool, _ = stub
        backend.raises.add("Lib.A")
        a, b = pool.submit(_t("A")), pool.submit(_t("B"))
        _wait_until(lambda: _ended(a, b))

        assert a.state is ItemState.FAILED
        assert a.message == "Worker exception: RuntimeError: pipe broken"
        assert b.state is ItemState.FINISHED
        assert backend.starts == [0, 0]  # restarted for B
        assert _manifest(backend.config) == {"Lib.A": "failed", "Lib.B": "passed"}

    def test_a_tool_that_dies_mid_item_takes_its_children_with_it(self, stub):
        backend, pool, _ = stub
        tool = subprocess.Popen([sys.executable, "-c", _TOOL], stdout=subprocess.PIPE)
        child = psutil.Process(int(tool.stdout.readline()))
        backend.start_script = lambda worker: setattr(
            worker, "pids", {tool.pid} if tool.poll() is None else set()
        )
        backend.gate("Lib.A")
        try:
            a, b = pool.submit(_t("A")), pool.submit(_t("B"))
            _wait_until(lambda: ("Lib.A", 0) in backend.ran)
            _wait_until(
                lambda: any(
                    pid == child.pid
                    for pid, _ in tuple(backend.workers[0]._orphan_children)
                ),
                30,
                what="a watch tick recording the child",
            )
            tool.kill()
            tool.wait()
            _wait_until(lambda: _ended(a, b), 30)

            assert a.state is ItemState.FAILED
            assert a.message == "Worker process exited during the test"
            assert b.state is ItemState.FINISHED and backend.starts == [0, 0]
            _wait_until(lambda: _gone(child), 30, what="the orphaned child killed")
        finally:
            for proc in (child, tool):
                try:
                    proc.kill()
                except (psutil.Error, OSError):
                    pass

    def test_a_tool_that_died_while_idle_fails_nothing(self, stub):
        """One item done, none waiting, and the worker's tool is killed. The
        item queued next is not failed for a death it had no part in: the
        worker is restarted — listed as `starting` meanwhile — and runs it.
        The item that had finished keeps its verdict."""
        backend, pool, events = stub
        tools: list[subprocess.Popen] = []
        restart = threading.Event()

        def launch(worker):
            if tools:
                restart.wait(30)
            tools.append(
                subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            )
            worker.pids = {tools[-1].pid}

        backend.start_script = launch
        try:
            a = pool.submit(_t("A"))
            _wait_until(lambda: _ended(a))
            assert a.state is ItemState.FINISHED
            tools[0].kill()
            tools[0].wait()

            b = pool.submit(_t("B"))
            _wait_until(lambda: pool.starting != (), what="the restart in flight")
            (starting,) = pool.starting
            assert starting.worker_id == 0 and b.state is ItemState.RUNNING
            assert backend.ran == [("Lib.A", 0)]  # B waits for the new tool
            restart.set()
            _wait_until(lambda: _ended(b))

            assert b.state is ItemState.FINISHED and b.message is None
            assert backend.ran == [("Lib.A", 0), ("Lib.B", 0)]
            assert backend.starts == [0, 0] and backend.closes == [0]
            assert pool.starting == () and pool.stopped is None
            assert events.states == {
                "Lib.A": ItemState.FINISHED,
                "Lib.B": ItemState.FINISHED,
            }
            assert _manifest(backend.config) == {"Lib.A": "passed", "Lib.B": "passed"}
        finally:
            restart.set()
            for tool in tools:
                tool.kill()

    def test_the_stopped_queue_names_the_failures_that_stand(self, tmp_path):
        """Worker 0 could not start, was started again later and worked,
        then left as surplus. When worker 1 dies for good the queue stops
        with worker 1's reason — not with one worker 0 had recovered from."""
        backend = _FakeBackend(_config(tmp_path, parallel=2))
        refuse: set[int] = {0}

        def start(worker):
            if worker.worker_id in refuse:
                raise RuntimeError("licence seat taken")

        backend.start_script = start
        backend.gate("Lib.A", "Lib.B")
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            a, b = pool.submit(_t("A")), pool.submit(_t("B"))
            _wait_until(lambda: ("Lib.A", 1) in backend.ran)
            _wait_until(lambda: list(pool._slots) == [1], what="worker 0 gone")
            assert pool._failures == {0: "licence seat taken"}

            refuse.clear()
            pool.workers = 2  # worker 0 again: this time it starts, and takes B
            _wait_until(lambda: ("Lib.B", 0) in backend.ran)
            backend.gates["Lib.B"].set()
            _wait_until(lambda: _ended(b))
            pool.workers = 1  # the idle one, worker 0, leaves — no failure
            _wait_until(lambda: list(pool._slots) == [1], what="worker 0 gone again")

            backend.raises.add("Lib.A")  # worker 1 dies...
            refuse.add(1)  # ...and cannot come back
            backend.gates["Lib.A"].set()
            c = pool.submit(_t("C"))
            _wait_until(lambda: _ended(a, c))

            assert b.state is ItemState.FINISHED and c.state is ItemState.FAILED
            assert pool.stopped == (
                "No Stub worker could start — worker 1: Worker dead; restart exhausted"
            )
        finally:
            for gate in backend.gates.values():
                gate.set()
            pool.close()

    def test_a_worker_that_cannot_be_restarted_stops_the_queue(self, stub):
        backend, pool, events = stub
        backend.raises.add("Lib.A")  # leaves the worker dead...
        a = pool.submit(_t("A"))
        _wait_until(lambda: _ended(a))

        def no_more_starts(worker):
            raise RuntimeError("licence server down")

        backend.start_script = no_more_starts  # ...and it cannot come back
        backend.gate("Lib.C")
        b, c = pool.submit(_t("B")), pool.submit(_t("C"))
        _wait_until(lambda: _ended(b, c))

        assert b.state is ItemState.FAILED
        assert b.message == "Worker dead; restart exhausted"  # the CLI's words
        message = (
            "No Stub worker could start — worker 0: Worker dead; restart exhausted"
        )
        assert c.state is ItemState.FAILED and c.message == message
        assert pool.stopped == message
        assert events.of("Lib.C") == ["end"]


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_with_items_waiting_and_one_running(self, tmp_path):
        before = threading.active_count()
        backend = _FakeBackend(_config(tmp_path, parallel=2))
        events = _Events()
        pool = SimulationPool(
            backend,
            ItemRun(backend.config, backend=backend, store=_STORE),
            on_start=events.on_start,
            on_end=events.on_end,
        )
        backend.gate("Lib.A", "Lib.B")
        try:
            a, b, c, d = (pool.submit(_t(name)) for name in "ABCD")
            _wait_until(lambda: len(pool.running) == 2 and len(backend.ran) == 2)
            t0 = time.monotonic()
        finally:
            pool.close()

        assert time.monotonic() - t0 < 10.0
        assert a.state is b.state is ItemState.CANCELLED
        assert c.state is d.state is ItemState.REMOVED
        assert events.of("Lib.A") == ["start", "end"]
        assert events.of("Lib.C") == []  # removed by the caller's own close
        assert _manifest(backend.config) == {"Lib.A": "cancelled", "Lib.B": "cancelled"}
        assert sorted(backend.closes) == [0, 1]  # every worker closed
        assert all(not worker.is_alive() for worker in backend.workers)
        # No thread of the pool, of a worker's watchdog or of a start is left.
        _wait_until(lambda: threading.active_count() == before, what="no leaked thread")
        with pytest.raises(Refused, match="closed"):
            pool.submit(_t("E"))
        pool.close()  # idempotent

    @pytest.mark.parametrize("cancelled_first", [False, True])
    def test_close_does_not_wait_out_a_soft_stop_s_grace(
        self, tmp_path, cancelled_first
    ):
        """A soft stop gives the tool `cancel_grace` to come back on its
        own. A close asks hard: the worker is killed now, also when a soft
        stop is already waiting for the tool."""

        class _IgnoresTheStop(_FakeWorker):
            cancel_grace = 60.0

            def cancel_running(self, test_key):
                return True  # asked; the tool does not come back

        class _Backend(_FakeBackend):
            def make_worker(self, worker_id):
                worker = _IgnoresTheStop(worker_id, self.config, self)
                self.workers.append(worker)
                return worker

        backend = _Backend(_config(tmp_path))
        backend.gate("Lib.A")
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            _wait_until(lambda: ("Lib.A", 0) in backend.ran)
            if cancelled_first:
                assert pool.cancel(item) is True
                time.sleep(0.2)  # the watchdog is inside the grace
                assert item.state is ItemState.RUNNING
            t0 = time.monotonic()
        finally:
            pool.close()

        assert time.monotonic() - t0 < 10.0  # not the 60 s of the grace
        assert item.state is ItemState.CANCELLED and backend.closes == [0]

    def test_close_while_a_worker_is_still_starting(self, tmp_path):
        before = threading.active_count()
        release = threading.Event()
        backend = _FakeBackend(_config(tmp_path, worker_startup_timeout=60))
        backend.start_script = lambda worker: release.wait(30)
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            item = pool.submit(_t("A"))
            _wait_until(lambda: backend.starts == [0])
            t0 = time.monotonic()
        finally:
            pool.close()

        assert time.monotonic() - t0 < 10.0  # not the 60 s of the bound
        assert item.state is ItemState.REMOVED
        release.set()
        _wait_until(lambda: threading.active_count() == before, what="no leaked thread")

    def test_close_from_a_callback_is_refused(self, tmp_path):
        backend = _FakeBackend(_config(tmp_path))
        box: dict = {}

        def on_end(item):
            try:
                pool.close()
            except RuntimeError as exc:
                box["error"] = str(exc)

        pool = SimulationPool(
            backend,
            ItemRun(backend.config, backend=backend, store=_STORE),
            on_end=on_end,
        )
        try:
            item = pool.submit(_t("A"))
            _wait_until(lambda: "error" in box and _ended(item))
            assert "from a pool callback" in box["error"]
            assert pool.submit(_t("B")).state is ItemState.WAITING  # still open
        finally:
            pool.close()


# ---------------------------------------------------------------------------
# A backend without persistent workers
# ---------------------------------------------------------------------------


class _SingleTestBackend(Backend):
    """No workers: each item goes through `run_single_test` on a pool
    thread."""

    def __init__(self, config):
        super().__init__(config)
        self.quiet = True
        self.threads: list[str] = []
        self.gate = threading.Event()

    def run_single_test(self, test, test_key, index, total):
        self.threads.append(threading.current_thread().name)
        if test.model_id.endswith("Slow"):
            self.gate.wait(15)
        return TestRunResult(model_id=test.model_id, test_key=test_key, success=True)

    def read_result(self, test, test_key, run_result):
        return TestResult(model_id=test.model_id, success=True)


class TestWithoutWorkers:
    def test_items_run_through_the_single_test_path(self, tmp_path):
        backend = _SingleTestBackend(_config(tmp_path, parallel=2))
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            slow, a, b = (pool.submit(_t(name)) for name in ("Slow", "A", "B"))
            _wait_until(lambda: _ended(a, b))
            assert slow.state is ItemState.RUNNING
            with pytest.raises(Refused, match="Stub cannot stop a running simulation"):
                pool.cancel(slow)  # no Capability.CANCEL

            backend.gate.set()
            _wait_until(lambda: _ended(slow))
            assert {i.state for i in (slow, a, b)} == {ItemState.FINISHED}
            assert all("stub-pool-" in name for name in backend.threads)
        finally:
            backend.gate.set()
            pool.close()

    def test_close_lets_a_test_that_cannot_be_stopped_finish(self, tmp_path):
        backend = _SingleTestBackend(_config(tmp_path))
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            slow = pool.submit(_t("Slow"))
            _wait_until(lambda: backend.threads != [])
        finally:
            threading.Timer(0.3, backend.gate.set).start()
            pool.close()

        assert slow.state is ItemState.FINISHED  # it succeeded: a result

    def test_close_stops_a_running_test_on_a_backend_that_can(self, tmp_path):
        """No worker to kill: the close goes through the backend's own stop,
        for the run that is on the thread now."""

        class _Stoppable(_SingleTestBackend):
            capabilities = frozenset({Capability.CANCEL})

            def __init__(self, config):
                super().__init__(config)
                self.stopped: list[str] = []

            def cancel_test(self, test_key):
                self.stopped.append(test_key)
                self.gate.set()
                return True

            def run_single_test(self, test, test_key, index, total):
                self.threads.append(threading.current_thread().name)
                self.gate.wait(60)
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    error_message="killed",
                )

        backend = _Stoppable(_config(tmp_path))
        pool = SimulationPool(
            backend, ItemRun(backend.config, backend=backend, store=_STORE)
        )
        try:
            slow = pool.submit(_t("Slow"))
            _wait_until(lambda: backend.threads != [])
            t0 = time.monotonic()
        finally:
            pool.close()

        assert time.monotonic() - t0 < 10.0
        assert backend.stopped == ["test_0001"]
        assert slow.state is ItemState.CANCELLED

    def test_a_backend_that_only_runs_batches_is_refused_up_front(self, tmp_path):
        class _BatchOnly(Backend):
            def run_tests(self, tests):  # pragma: no cover
                return []

            def read_result(self, test, test_key, run_result):  # pragma: no cover
                raise NotImplementedError

        backend = _BatchOnly(_config(tmp_path))
        with pytest.raises(RuntimeError, match="has no single-test path"):
            SimulationPool(
                backend, ItemRun(backend.config, backend=backend, store=_STORE)
            )


class TestItemRun:
    """What happens around one item's run on disk."""

    def test_a_rerun_wipes_what_the_test_s_last_run_left(self, stub):
        """Its report directory (the sidecar the dashboard reads a verdict
        from) and its simulation directory — and nobody else's."""
        backend, pool, _ = stub
        work = backend.config.work_dir
        first, other = pool.submit(_t("A")), pool.submit(_t("B"))
        _wait_until(lambda: _ended(first, other))
        stale = [
            work / "reports" / "test_0001" / "comparison_data.json",
            work / "test_0001" / "result.mat",
        ]
        kept = [
            work / "reports" / "test_0002" / "comparison_data.json",
            work / "test_0002" / "result.mat",
        ]
        for path in (*stale, *kept):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("from the last run")

        again = pool.submit(_t("A"))
        _wait_until(lambda: _ended(again))

        assert again.test_key == "test_0001" and again.state is ItemState.FINISHED
        assert [path.exists() for path in stale] == [False, False]
        assert [path.exists() for path in kept] == [True, True]


# ---------------------------------------------------------------------------
# Python backend: real subprocesses
# ---------------------------------------------------------------------------

_FAST = """\
import math


def simulate(stop_time, tolerance):
    time = [stop_time * i / 200 for i in range(201)]
    return {{"time": time, "variables": {{"x": [math.sin({rate} * t) for t in time]}}}}
"""

_SLOW = """\
import time


def simulate(stop_time, tolerance):
    time.sleep(120)
    return {"time": [0.0, 1.0], "variables": {"x": [0.0, 1.0]}}
"""


def _python_project(tmp_path: Path, work: str):
    """A Python-backend project of four quick tests and a slow one, and its
    discovered tests by short name."""
    from dstf.config import Config
    from dstf.discovery.test_registry import discover_tests

    lib = tmp_path / "lib"
    if not lib.exists():
        (lib / "refs").mkdir(parents=True)
        names = ["Fast1", "Fast2", "Fast3", "Fast4"]
        for rate, name in enumerate(names, start=1):
            (lib / f"{name}.py").write_text(_FAST.format(rate=rate))
        (lib / "Slow.py").write_text(_SLOW)
        (lib / "refs" / "testing.json").write_text(
            json.dumps(
                {
                    "source_type": "python",
                    "source_path": "..",
                    "library_name": "QueueLib",
                    "test_spec": "./test_spec.json",
                    "reference_root": "./",
                    "backends": {"Python": [sys.executable]},
                    "backend": "Python",
                }
            )
        )
        (lib / "refs" / "test_spec.json").write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": f"QueueLib.{name}",
                            "source": f"../{name}.py",
                            "variables": ["x"],
                            "simulation": {"stop_time": 2.0, "timeout": 300},
                        }
                        for name in [*names, "Slow"]
                    ]
                }
            )
        )
    config = Config(config_file=lib / "refs" / "testing.json", work_dir=tmp_path / work)
    return config, {t.model_id.rsplit(".", 1)[-1]: t for t in discover_tests(config)}


def test_the_python_backend_through_the_pool_equals_run_pipeline(tmp_path):
    """Two items, one appended while they run, one removed while it waits,
    one cancelled mid-simulation: the finished ones equal what `run_pipeline`
    reads for the same tests, bit for bit, and the cancelled one is recorded
    cancelled."""
    from dstf.backends import select_backend
    from dstf.storage.reference_store import ReferenceStore

    config, tests = _python_project(tmp_path, "pool")
    backend = select_backend(config, persistent=True)
    backend.quiet = True
    events = _Events()
    box: dict = {}

    def on_end(item):
        events.on_end(item)
        if item.model_id.endswith("Fast1"):
            box["appended"] = pool.submit(tests["Fast3"])  # appended while running

    pool = open_pool(
        config,
        backend=backend,
        store=ReferenceStore(config),
        on_start=events.on_start,
        on_end=on_end,
        workers=1,
    )
    try:
        slow = pool.submit(tests["Slow"])
        fast1, fast2 = pool.submit(tests["Fast1"]), pool.submit(tests["Fast2"])
        removed = pool.submit(tests["Fast4"])
        assert pool.remove(removed) is True
        _wait_until(lambda: slow.test_key in getattr(backend, "_procs", {}))
        child = psutil.Process(backend._procs[slow.test_key].pid)

        t0 = time.monotonic()
        assert pool.cancel(slow) is True
        _wait_until(lambda: _ended(slow))
        cancel_seconds = time.monotonic() - t0
        _wait_until(lambda: "appended" in box and _ended(fast1, fast2, box["appended"]))
        finished = [fast1, fast2, box["appended"]]
        pool.path.write_status([slow, removed, *finished])
    finally:
        pool.close()

    assert slow.state is ItemState.CANCELLED and cancel_seconds < 10.0
    assert _gone(child)
    assert removed.state is ItemState.REMOVED and removed.test_key is None
    assert [item.state for item in finished] == [ItemState.FINISHED] * 3
    assert [model for what, model in events.log if what == "start"] == [
        f"QueueLib.{name}" for name in ("Slow", "Fast1", "Fast2", "Fast3")
    ]  # first in, first out; the removed one never started

    # The same tests through the run pipeline, in another work dir.
    ref_config, ref_tests = _python_project(tmp_path, "reference")
    ref_backend = select_backend(ref_config, persistent=True)
    ref_backend.quiet = True
    outcome = run_pipeline(
        ref_config,
        [ref_tests[name] for name in ("Fast1", "Fast2", "Fast3")],
        backend=ref_backend,
        store=ReferenceStore(ref_config),
    )
    for item in finished:
        expected = outcome.results[item.model_id]
        assert expected.success and item.result.success
        assert [v.name for v in item.result.variables] == ["x"]
        for got, want in zip(item.result.variables, expected.variables, strict=True):
            assert got.name == want.name and got.index == want.index
            assert np.array_equal(got.time, want.time)
            assert np.array_equal(got.values, want.values)
            assert got.values.size == 201

    assert _manifest(config) == {
        "QueueLib.Slow": "cancelled",
        "QueueLib.Fast1": "passed",
        "QueueLib.Fast2": "passed",
        "QueueLib.Fast3": "passed",
    }
    reloaded = backend.read_last_results(list(tests.values()))
    assert sorted(reloaded) == [f"QueueLib.Fast{i}" for i in (1, 2, 3)]
    assert all(result.success for result in reloaded.values())

    status = json.loads((config.work_dir / "status.json").read_text())
    assert {row["model_id"]: row["status"] for row in status["tests"]} == {
        "QueueLib.Slow": "cancelled",
        "QueueLib.Fast1": "passed",
        "QueueLib.Fast2": "passed",
        "QueueLib.Fast3": "passed",
    }
    assert status["metadata"]["backend_name"] == "Python"
    assert status["metadata"]["tool_version"].startswith("Python 3")
    assert (config.work_dir / "dashboard.html").exists()


def _comparable(value):
    """*value* with what differs between two runs of one test taken out:
    wall-clock numbers. Arrays become lists, dataclasses dicts."""
    import dataclasses

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {
            key: "<seconds>"
            if key in _WALL_CLOCK and isinstance(inner, (int, float))
            else _comparable(inner)
            for key, inner in value.items()
        }
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_comparable(inner) for inner in value]
    return value


#: Statistics' timings, and the manifest's stamp of when a run ended.
_WALL_CLOCK = {
    "wall_time",
    "elapsed",
    "sim_wall",
    "translation_wall",
    "total_wall",
    "last_run_at",
}


def test_a_pool_run_of_a_test_with_a_baseline_equals_run_pipeline_s(tmp_path):
    """A test that HAS a baseline is known by its reference id: its report
    directory is `ref_NNNN`, the work dir carries the id mapping, the
    manifest entry its `ref_id`. Through the pool, each of these — and the
    whole result — is what `run_pipeline` leaves for the same tests."""
    from dstf.backends import select_backend
    from dstf.storage.accept import accept_run_results
    from dstf.storage.reference_store import ReferenceStore

    def project(work: str):
        config, tests = _python_project(tmp_path, work)
        backend = select_backend(config, persistent=True)
        backend.quiet = True
        return config, tests, backend, ReferenceStore(config)

    # Fast1 gets a baseline; Fast2 has none.
    config, tests, backend, store = project("seed")
    seeded = run_pipeline(config, [tests["Fast1"]], backend=backend, store=store)
    assert (
        accept_run_results(store, [tests["Fast1"]], seeded.results, config.work_dir)[0]
        == 1
    )

    # Fast2 first: Fast1's test key (test_0002) is then not its ref id.
    order = ("Fast2", "Fast1")
    config, tests, backend, store = project("pool")
    assert store.index.get_id("QueueLib.Fast1") == "0001"
    stale = config.work_dir / "reports" / "ref_0001" / "comparison_data.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("an earlier run's verdict")
    pool = open_pool(config, backend=backend, store=store, workers=1)
    try:
        items = [pool.submit(tests[name]) for name in order]
        _wait_until(lambda: _ended(*items))
        pool.path.write_status(items)
    finally:
        pool.close()
    assert not stale.exists()  # wiped under the name the report is kept by

    cli_config, cli_tests, cli_backend, cli_store = project("cli")
    outcome = run_pipeline(
        cli_config,
        [cli_tests[name] for name in order],
        backend=cli_backend,
        store=cli_store,
    )

    def on_disk(config):
        manifest = json.loads((config.work_dir / "batch_manifest.json").read_text())
        status = json.loads((config.work_dir / "status.json").read_text())
        return {
            "manifest": _comparable(manifest),
            "id mapping": (config.work_dir / "reference_manifest.json").read_text(),
            "report dirs": {
                row["model_id"]: row["report_dir"] for row in status["tests"]
            },
        }

    assert on_disk(config) == on_disk(cli_config)
    assert on_disk(config)["report dirs"] == {
        "QueueLib.Fast2": "test_0001",
        "QueueLib.Fast1": "ref_0001",
    }
    assert backend.ref_id_map == cli_backend.ref_id_map
    assert backend.ref_id_map == {"QueueLib.Fast1": "ref_0001"}
    for item in items:
        assert item.state is ItemState.FINISHED
        expected = outcome.results[item.model_id]
        assert _comparable(item.result) == _comparable(expected), item.model_id
        assert item.result.variables[0].values.size == 201
