"""The simulation pool: a queue of tests that stays open.

``Backend.run_tests`` runs one fixed list and closes its workers. The
authoring tool queues tests while others run, takes one back before it
starts, stops one mid-simulation and wants each result the moment its test
ends — so this is the same dispatch kept open: first in, first out, a test
never queued twice, workers that live until :meth:`SimulationPool.close`.

How ONE test runs is not decided here. Each pool thread drives an
:class:`~dstf.backends.base.ItemRunner` it gets from the backend
(``Backend.item_runner``): a persistent worker with its restart accounting
for Dymola / OpenModelica / Julia — the object ``run_tests`` drives, so a
test fails, retries and restarts identically in both — and the backend's own
single-test path for a backend without workers. What happens around the run
on disk (the test's key, its wipe, its recorded verdict, its result read
back) is the :class:`ItemPath` the pool is given;
:func:`dstf.pipeline.open_pool` builds the pool with the run pipeline's.
"""

from __future__ import annotations

import collections
import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from .base import ItemRunner, no_worker_message
from .types import Capability, TestResult, TestRunResult

if TYPE_CHECKING:
    from ..discovery.library_state import LibraryState
    from ..discovery.test_registry import TestModel
    from .base import Backend

logger = logging.getLogger(__name__)


class ItemState(str, Enum):
    """Where a :class:`QueueItem` is. The last four are final."""

    WAITING = "waiting"
    RUNNING = "running"
    #: Its simulation ran and its result was read back (``item.result``).
    FINISHED = "finished"
    #: Its simulation failed or timed out, its result could not be read, or
    #: no worker could start (``item.message`` says which).
    FAILED = "failed"
    #: Stopped while running, by :meth:`SimulationPool.cancel` or by
    #: :meth:`SimulationPool.close`. Recorded as cancelled; no result.
    CANCELLED = "cancelled"
    #: Taken out while waiting, by :meth:`SimulationPool.remove` or by
    #: :meth:`SimulationPool.close`. It never ran and nothing was recorded.
    REMOVED = "removed"


class Refused(RuntimeError):
    """The pool did not do what was asked; ``str()`` is the sentence why."""


@dataclass(eq=False)
class QueueItem:
    """One submitted test and what became of it. The pool writes the fields
    (``test`` too: :meth:`SimulationPool.update`); a caller reads them —
    after ``on_end``, all of them are final."""

    test: TestModel
    #: The source files as swept at the Queue click that submitted it: it
    #: runs on a worker that has read them as they were then, or later.
    #: ``None``: nothing is asked of the worker (a source no worker keeps
    #: loaded).
    library: LibraryState | None = None
    state: ItemState = ItemState.WAITING
    #: The test's key in the work dir, once it started.
    test_key: str | None = None
    #: The worker (pool thread) it ran on.
    worker_id: int | None = None
    #: What the backend's run returned; ``None`` while it has not ended, and
    #: for an item that never ran.
    run_result: TestRunResult | None = None
    #: The result read back; ``None`` unless the item FINISHED or its
    #: simulation FAILED (then it carries the failure).
    result: TestResult | None = None
    #: The backend's message for a FAILED item, ``"Cancelled"`` for a
    #: cancelled one.
    message: str | None = None
    #: Wall-clock (``time.time()``) stamps.
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    # A stop was asked of it while RUNNING (cancel or close): a run that then
    # does not succeed is cancelled, whatever the backend called it.
    _stop_asked: bool = field(default=False, repr=False)

    @property
    def model_id(self) -> str:
        return self.test.model_id

    @property
    def stopping(self) -> bool:
        """A stop was asked of it and it has not ended: still RUNNING, on
        its way to CANCELLED (or FINISHED, where its simulation got there
        first). For as long as the backend takes — a simulation that does
        not look at the request runs on until its worker's grace is over
        and the worker is killed."""
        return self._stop_asked and self.state is ItemState.RUNNING


class ItemPath(Protocol):
    """What happens around one item's run, on disk."""

    def begin(self, test: TestModel) -> str:
        """Before the run: the test's key, with its state from an earlier
        run cleared."""
        ...

    def end(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
        started_at: float,
    ) -> TestResult | None:
        """After the run that began at *started_at* (``time.time()``): record
        what it came to, and read the result back (``None`` for a cancelled
        run)."""
        ...


@dataclass(frozen=True)
class StartingWorker:
    """A worker that is not ready for a test right now: being brought up —
    its first start, or a restart after a death — or up and reading the
    library again for the item it is about to run (``reload``)."""

    worker_id: int
    #: The step the start (or the reload) is in, in the backend's own words
    #: (``loadFile("/lib/package.mo")``, ``its launch``); ``""`` when the
    #: backend names none.
    phase: str
    #: Seconds since this start (or reload) began.
    seconds: float
    #: Whether it is being brought BACK: a worker that died is restarted
    #: under the item it is to run, which is running meanwhile; a first
    #: start has no item yet.
    restart: bool = False
    #: Whether it is up and reading the library again: its files changed
    #: since it read them, and its item is running meanwhile.
    reload: bool = False


@dataclass(eq=False)
class _Slot:
    """One pool thread and the runner it drives."""

    id: int
    thread: threading.Thread | None = None
    runner: ItemRunner | None = None
    item: QueueItem | None = None


class SimulationPool:
    """A FIFO queue of tests over a backend's runners.

    ``submit`` appends and returns the item — with the state of the source
    files it is to run against, which a live worker that read them earlier
    reads again first; ``update`` hands a waiting item its test as it
    stands now; ``remove`` takes a waiting item back;
    ``cancel`` stops a running one where the backend can
    (:attr:`Capability.CANCEL`); ``close`` ends everything. All five are
    safe from any thread and return promptly (``close`` waits for the
    threads).

    **Callbacks.** ``on_start(item)`` once when an item starts running,
    ``on_end(item)`` once when it has ended — FINISHED, FAILED or CANCELLED,
    its result read back and every field final. Both run on a POOL thread,
    never the caller's: the one that ran the item, ``on_start`` before
    ``on_end``, outside the pool's lock; callbacks of different items may
    run at the same time on different threads. A callback may call
    ``submit`` / ``remove`` / ``cancel`` and set ``workers``; it must not
    call ``close`` (which joins the thread it is on). An item that never
    started gets no ``on_start``, and gets ``on_end`` only when the POOL
    failed it (no worker could start; on the thread of the last worker to
    leave) — not when the caller removed it or closed the pool.

    **Workers.** None exists until the first ``submit``; then as many as
    there are items to run, up to ``workers`` (``Config.parallel`` unless
    given). Setting ``workers`` higher starts more as items need them;
    lower lets the surplus go as each finishes its item. ``starting`` lists
    the ones still coming up, and the ones reading the library again, with
    the phase each is in. They live until
    ``close`` — which the owner must call: the pool's threads are daemons
    and do not keep a dying process open, but nothing else ends the
    workers' tool processes.

    **A backend that cannot start.** A worker whose start fails or which
    cannot be restarted leaves. When none is left, every waiting item FAILS
    with the backend's own message (``No OpenModelica worker could start —
    worker 0: ...``), ``stopped`` holds that message and ``submit`` refuses
    with it: a new pool is the retry.
    """

    def __init__(
        self,
        backend: Backend,
        path: ItemPath,
        *,
        on_start: Callable[[QueueItem], None] | None = None,
        on_end: Callable[[QueueItem], None] | None = None,
        workers: int | None = None,
    ):
        """Raises ``RuntimeError`` when *backend* cannot run one test at a
        time (``Backend.prepare_item_runners``)."""
        self.backend = backend
        #: The pool's :class:`ItemPath`, for what it offers beyond the two
        #: steps (the run pipeline's writes ``status.json`` when asked).
        self.path = path
        self._on_start = on_start
        self._on_end = on_end
        self._cond = threading.Condition()
        self._waiting: collections.deque[QueueItem] = collections.deque()
        #: Waiting or running, by model id — the "never queued twice" rule.
        self._active: dict[str, QueueItem] = {}
        self._slots: dict[int, _Slot] = {}
        self._target = max(
            1, int(backend.config.parallel if workers is None else workers)
        )
        self._closing = False
        self._stopped: str | None = None
        #: Why each worker that left for good did, by worker id.
        self._failures: dict[int, str] = {}
        #: The latest state an item was submitted with: what a worker that
        #: starts NOW reads at least.
        self._library: LibraryState | None = None
        backend.prepare_item_runners()

    # -- the caller's side ---------------------------------------------

    @property
    def workers(self) -> int:
        """How many workers the pool may run at once."""
        return self._target

    @workers.setter
    def workers(self, count: int) -> None:
        with self._cond:
            self._target = max(1, int(count))
            self._spawn_slots()
            self._cond.notify_all()

    @property
    def can_cancel(self) -> bool:
        """Whether a running item can be stopped on this backend."""
        return Capability.CANCEL in self.backend.capabilities

    @property
    def stopped(self) -> str | None:
        """The message the pool stopped with, or ``None`` while it takes
        items."""
        return self._stopped

    @property
    def waiting(self) -> tuple[QueueItem, ...]:
        """The waiting items, next to run first."""
        with self._cond:
            return tuple(self._waiting)

    @property
    def running(self) -> tuple[QueueItem, ...]:
        """The running items, by worker id."""
        with self._cond:
            return tuple(
                slot.item
                for _, slot in sorted(self._slots.items())
                if slot.item is not None
            )

    @property
    def starting(self) -> tuple[StartingWorker, ...]:
        """The workers being brought up right now and the ones reading the
        library again, by worker id — empty once each is ready or has
        failed. A large library is minutes of load in a live tool: this is
        what says so while the items wait."""
        with self._cond:
            runners = [
                (slot_id, slot.runner, slot.item is not None)
                for slot_id, slot in sorted(self._slots.items())
                if slot.runner is not None
            ]
        found = []
        for slot_id, runner, restart in runners:
            state = runner.starting()
            if state is not None:
                found.append(StartingWorker(slot_id, *state, restart=restart))
                continue
            state = runner.reloading()
            if state is not None:
                found.append(StartingWorker(slot_id, *state, reload=True))
        return tuple(found)

    def submit(self, test: TestModel, library: LibraryState | None = None) -> QueueItem:
        """Append *test*. Raises :class:`Refused` when it is already waiting
        or running, and when the pool is closed or stopped.

        *library* is the state of the source files as the caller swept them
        for this submit (one state per Queue click, shared by its items).
        Before the item runs, a worker that read the files at an older
        state reads them again (``ItemRunner.run``); one that read them at
        this state or a later one does nothing."""
        with self._cond:
            if self._closing:
                raise Refused("the simulation queue is closed")
            if self._stopped is not None:
                raise Refused(self._stopped)
            active = self._active.get(test.model_id)
            if active is not None:
                doing = "running" if active.state is ItemState.RUNNING else "queued"
                raise Refused(f"{test.model_id} is already {doing}")
            item = QueueItem(test, library)
            self._note(library)
            self._active[test.model_id] = item
            self._waiting.append(item)
            self._spawn_slots()
            self._cond.notify()
        return item

    def update(
        self, item: QueueItem, test: TestModel, library: LibraryState | None = None
    ) -> bool:
        """Hand a waiting *item* the test as it stands now — the same model,
        its settings as they are at this call — keeping its place: an item
        runs with the last test it was given before it started. ``False``
        once it has started (or ended): nothing is changed.

        Its library state stays the one it was submitted with — a test's
        settings follow the spec, the files it was queued against do not —
        unless *library* is given and is a later one: a click that read
        the test's file again hands the item what it read, and the state
        it read it at. A state never moves back."""
        if test.model_id != item.model_id:
            raise ValueError(f"{test.model_id} is not {item.model_id}")
        with self._cond:
            if item.state is not ItemState.WAITING:
                return False
            item.test = test
            if library is not None and (
                item.library is None or library.serial > item.library.serial
            ):
                item.library = library
                self._note(library)
        return True

    def _note(self, library: LibraryState | None) -> None:
        """Under the lock: *library* is a state an item carries."""
        if library is not None and (
            self._library is None or library.serial > self._library.serial
        ):
            self._library = library

    def remove(self, item: QueueItem) -> bool:
        """Take a waiting *item* out. ``False`` once it has started (or
        ended): nothing is changed."""
        with self._cond:
            if item.state is not ItemState.WAITING:
                return False
            self._waiting.remove(item)
            del self._active[item.model_id]
            item.state, item.ended_at = ItemState.REMOVED, time.time()
        return True

    def cancel(self, item: QueueItem) -> bool:
        """Ask a running *item*'s simulation to stop: ``True`` when it was
        asked. It then ends CANCELLED — unless its simulation had already
        finished, which stays the result it is, FINISHED (``on_end`` follows
        from its thread either way). ``False`` when it is not running —
        waiting (``remove`` it) or already ended. Raises :class:`Refused`
        when it runs on a backend that cannot stop a test."""
        with self._cond:
            if item.state is not ItemState.RUNNING:
                return False
            if not self.can_cancel:
                raise Refused(
                    f"{self.backend.label} cannot stop a running simulation: "
                    f"{item.model_id} runs to its end, or to its timeout"
                )
            item._stop_asked = True
            slot = self._slots[item.worker_id]  # type: ignore[index]
            runner, test_key = slot.runner, item.test_key
        # Before its key exists the item has not reached its runner: the
        # flag set above is what its thread sees. After, the runner expects
        # the item (announced under this lock), so the stop reaches its run
        # — and no other: by now the item may have ended and the same test
        # be running again, under the same key.
        if runner is not None and test_key is not None:
            runner.cancel(item)
        return True

    def close(self) -> None:
        """End the pool: waiting items are REMOVED, running ones stopped
        (CANCELLED, with their ``on_end``), every worker closed and every
        pool thread joined. A backend that cannot stop a test finishes the
        one it is running first. Idempotent; not callable from a callback.
        """
        with self._cond:
            if any(
                slot.thread is threading.current_thread()
                for slot in self._slots.values()
            ):
                raise RuntimeError("SimulationPool.close() called from a pool callback")
            self._closing = True
            for item in self._waiting:
                del self._active[item.model_id]
                item.state, item.ended_at = ItemState.REMOVED, time.time()
            self._waiting.clear()
            slots = list(self._slots.values())
            stops = []
            for slot in slots:
                if slot.item is not None:
                    slot.item._stop_asked = True
                if slot.runner is not None:
                    key = slot.item.test_key if slot.item is not None else None
                    stops.append((slot.runner, key))
            self._cond.notify_all()
        for runner, test_key in stops:
            try:
                runner.stop(test_key)
            except Exception:
                logger.exception("pool: stopping worker %s raised", runner.worker_id)
        for slot in slots:
            if slot.thread is not None:
                slot.thread.join()

    # -- the pool's threads --------------------------------------------

    def _spawn_slots(self) -> None:
        """Under the lock: one thread per item there is to run, up to the
        target."""
        busy = sum(1 for slot in self._slots.values() if slot.item is not None)
        want = min(self._target, len(self._waiting) + busy)
        while len(self._slots) < want:
            slot = _Slot(next(i for i in range(want) if i not in self._slots))
            slot.thread = threading.Thread(
                target=self._slot_loop,
                args=(slot,),
                name=f"{self.backend.label.lower().replace(' ', '-')}-pool-{slot.id}",
                # An owner that dies without close() must not be held open
                # by an idle queue. Its workers' tool processes are then
                # left running: close() is what ends them.
                daemon=True,
            )
            self._slots[slot.id] = slot
            slot.thread.start()

    def _slot_loop(self, slot: _Slot) -> None:
        left_because: str | None = None
        try:
            try:
                runner = self.backend.item_runner(slot.id)
                with self._cond:
                    slot.runner = runner
                    if self._closing:
                        return
                    # Read BEFORE the start reads the files: each is then
                    # read as it was at this state or later. (A click
                    # during the start is a later state, and the worker
                    # reads the library again for its items.)
                    library = self._library
                runner.start()
                runner.library = library
            except Exception as exc:
                left_because = str(exc) or type(exc).__name__
                return
            with self._cond:
                self._failures.pop(slot.id, None)
            while True:
                item = self._take(slot)
                if item is None:
                    return
                self._run_item(slot, runner, item)
                if runner.retired:
                    left_because = runner.retired
                    return
        finally:
            if slot.runner is not None:
                try:
                    slot.runner.close()
                except Exception:
                    logger.exception("pool: closing worker %s raised", slot.id)
            self._slot_left(slot, left_because)

    def _take(self, slot: _Slot) -> QueueItem | None:
        """The next item for *slot*, marked RUNNING — or ``None`` when the
        slot is to leave (the pool closing or stopped, or more workers than
        the target)."""
        with self._cond:
            while True:
                if self._closing or self._stopped is not None:
                    return None
                if len(self._slots) > self._target:
                    del self._slots[slot.id]
                    return None
                if self._waiting:
                    item = self._waiting.popleft()
                    item.state, item.worker_id = ItemState.RUNNING, slot.id
                    item.started_at = time.time()
                    slot.item = item
                    return item
                self._cond.wait()

    def _slot_left(self, slot: _Slot, because: str | None) -> None:
        """A thread is gone. One that left because its worker could not
        start, or could not be brought back, says why; when it was the last,
        the pool stops and the waiting items fail with the reasons."""
        with self._cond:
            if self._slots.get(slot.id) is slot:
                del self._slots[slot.id]
            if because is not None:
                self._failures[slot.id] = because
            if self._slots or because is None or self._closing:
                return
            self._stopped = no_worker_message(self.backend.label, self._failures)
            failed = list(self._waiting)
            self._waiting.clear()
            for item in failed:
                del self._active[item.model_id]
                item.state, item.message = ItemState.FAILED, self._stopped
                item.ended_at = time.time()
            self._cond.notify_all()
        for item in failed:
            self._fire(self._on_end, item)

    def _run_item(self, slot: _Slot, runner: ItemRunner, item: QueueItem) -> None:
        self._fire(self._on_start, item)
        run_result: TestRunResult | None = None
        result: TestResult | None = None
        error: str | None = None
        try:
            test_key = self.path.begin(item.test)
            with self._cond:
                item.test_key = test_key
                stopped_early = item._stop_asked
                if not stopped_early:
                    runner.expect(test_key, item)
            if stopped_early:
                run_result = TestRunResult(
                    model_id=item.model_id,
                    test_key=test_key,
                    success=False,
                    error_message="Cancelled",
                    cancelled=True,
                )
            else:
                run_result = runner.run(item.test, test_key, item.library)
            if item._stop_asked and not run_result.success:
                # Whatever a stopped run reports — a failed record, a dead
                # pipe — is the stop's doing, not a failure of the test.
                run_result = dataclasses.replace(
                    run_result, cancelled=True, error_message="Cancelled"
                )
            assert item.started_at is not None  # stamped by `_take`
            result = self.path.end(item.test, test_key, run_result, item.started_at)
        except Exception as exc:
            logger.exception("pool: running %s raised", item.model_id)
            error = f"{type(exc).__name__}: {exc}"
        with self._cond:
            item.run_result, item.result = run_result, result
            if error is not None or run_result is None:
                item.state, item.message = ItemState.FAILED, error
            elif run_result.cancelled:
                item.state, item.message = ItemState.CANCELLED, "Cancelled"
            elif run_result.success and result is not None and result.success:
                item.state = ItemState.FINISHED
            else:
                item.state = ItemState.FAILED
                item.message = (
                    (result.error_message if result is not None else None)
                    or run_result.error_message
                    or "Simulation failed"
                )
            item.ended_at = time.time()
            del self._active[item.model_id]
            slot.item = None
        self._fire(self._on_end, item)

    def _fire(
        self, callback: Callable[[QueueItem], None] | None, item: QueueItem
    ) -> None:
        """A callback that raises costs its own effect, never the thread."""
        if callback is None:
            return
        try:
            callback(item)
        except Exception:
            logger.exception("pool: a callback raised for %s", item.model_id)


__all__ = [
    "ItemPath",
    "ItemState",
    "QueueItem",
    "Refused",
    "SimulationPool",
    "StartingWorker",
]
