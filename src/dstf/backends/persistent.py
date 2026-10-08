"""The persistent-worker lane: the long-lived-process contract and the pool
orchestration that drives it.

:class:`Worker` is one simulator subprocess wrapped as a test-running unit —
it owns the bounded start, the watchdog, the timeout kill, the cancel and
the disk rescue as template methods, so a backend supplies hooks rather than
a copy of the loop. :class:`WorkerRunner` is what one dispatch thread does
with a worker for each test: the restart and its accounting.
:class:`PersistentBackendBase` owns ``run_tests``' dispatch of a fixed list:
parallel startup, the shared queue + sentinel dispatch. The simulation pool
(:mod:`dstf.backends.pool`) is that dispatch kept open, over the same
runners.

Split out of :mod:`dstf.backends.base` — the batch lane and the persistent
lane share only :class:`Backend` and the progress printers, both
imported from there.
"""

import contextlib
import logging
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Optional

from ..config import Config
from ..discovery.test_registry import TestModel
from ..storage.batch_manifest import BatchManifest
from .base import (
    Backend,
    ItemRunner,
    _print_progress,
    _print_run_header,
    no_worker_message,
)
from .common.proc_kill import (
    created_at,
    kill_process_tree,
    orphans_in,
    process_gone,
    snapshot_children,
)
from .types import TestRunResult, timeout_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract persistent-worker interface
# ---------------------------------------------------------------------------


class WorkerStartError(RuntimeError):
    """A worker could not start. ``str()`` is the reason in the backend's own
    words — what its ``start()`` raised, or what :meth:`Worker.start_within`
    saw (the process gone, the bound passed) with
    :meth:`Worker.process_detail` beside it."""


#: The ``testing.json`` key that bounds a worker's start, as a sentence that
#: ends every "did not start within" message: the bound is a guess about the
#: library, and the reader of the message is the one who can correct it.
STARTUP_TIMEOUT_HINT = (
    '(raise "worker_startup_timeout" in testing.json if this library needs longer)'
)


def start_timed_out_message(bound: float, detail: str = "") -> str:
    """``did not start within 600 s — <detail> (raise "worker_startup_timeout"
    ...)``: what a start that outlived its bound is failed with. *detail* is
    the backend's :meth:`Worker.process_detail` — whether the tool still
    runs, and the step it is in."""
    return (
        f"did not start within {bound:g} s"
        + (f" — {detail}" if detail else "")
        + f" {STARTUP_TIMEOUT_HINT}"
    )


class Worker(ABC):
    """One long-lived simulator subprocess wrapped as a test-running unit.

    Each persistent-worker backend (Dymola / OpenModelica / Julia today)
    has its own concrete subclass that owns a real OS process — a Dymola
    instance, an ``OMCSessionZMQ``, a ``julia --project=...`` subprocess.
    The shape declared here is what :class:`PersistentBackendBase` (Phase 4)
    will rely on to orchestrate them generically.

    Subclasses extend ``__init__`` with backend-specific config (e.g.
    ``DymolaConfig``, an interface class to instantiate). They must always
    call ``super().__init__(worker_id, config)`` so the framework's
    invariants (worker_id stable across restarts; ``self.config`` available
    to dispatch logic; ``self.pids`` and the restart counter present for
    the watchdog and the dispatch loop) hold.

    Lifecycle contract:
        ``__init__`` → ``start_within()`` → ``run_test_with_timeout()`` × N
            → ``close()``
    ``start_within`` is ``start()`` bounded (``worker_startup_timeout``): the
    pool never calls ``start()`` bare, since a tool that died or stopped
    answering while loading would be waited on for good.
    ``close()`` MUST be idempotent — :class:`PersistentBackendBase`'s teardown
    calls it on every worker including ones that never started successfully.

    On failure, ``start()`` raises (typically :class:`RuntimeError`); the
    backend records the failure and excludes that worker from the live pool.
    Inside the dispatch loop, ``run_test_with_timeout`` MUST NOT raise on
    test-level failures — return a ``TestRunResult`` with ``success=False``
    instead. Reserve raises for worker-level catastrophes (subprocess died,
    pipe broken) so the caller's restart logic can intervene.

    ``run_test_with_timeout`` and ``close`` are template methods implemented
    here: the watchdog thread, the timeout kill, the disk rescue and the
    restart-accounting flags are identical across backends and only the
    backend-specific pieces are hooks (``run_test``, ``_evaluate_from_disk``,
    ``_shutdown_interface``, ``_detach_interface``, ``_quiet``). A backend
    whose execution model doesn't fit a blocking call on a thread (Julia
    polls a response queue against a deadline) overrides
    ``run_test_with_timeout`` wholesale but still uses the shared
    ``close`` / ``_kill_tracked_pids``.
    """

    #: PID name guard for :func:`kill_process_tree` — a tracked PID may have
    #: been recycled by an unrelated process, so nothing is killed unless the
    #: name still matches. ``None`` disables the guard.
    kill_name_hint: str | None = None
    #: Grace given to the graceful shutdown on the *timeout* path, before
    #: PIDs are killed. Backends that need their log files flushed raise it.
    timeout_kill_grace: float = 1.0
    #: Prefix for the watchdog's exec-thread name (``<prefix>-exec-<id>``).
    exec_thread_prefix: str = "worker"
    #: How often ``close()`` re-walks the process tree while the graceful
    #: shutdown runs, looking for children spawned inside that window.
    _ORPHAN_POLL_SECONDS: float = 0.25
    #: Seconds a start gets when ``testing.json`` sets no
    #: ``worker_startup_timeout``. The bound is for a tool that hangs: one
    #: that DIED is failed at once, whatever this says (:meth:`start_within`
    #: watches the process). So it is generous — a large library is minutes
    #: of ``loadFile`` in a live omc, and a bound below that fails a run that
    #: only had to wait. A backend with a reason for another sets its own.
    startup_timeout: float = 600.0
    #: How often :meth:`start_within` looks at a start in flight.
    _START_POLL_SECONDS: float = 0.25
    #: A start given up with NO process on record may be inside its launch
    #: call (OMPython launches omc and waits for its port file in one call,
    #: ~0.1 s): how long the give-up waits for that call to hand the process
    #: over — to :meth:`_track_launch`, which kills it — before it returns.
    #: Without the wait, a caller that exits right after the give-up takes
    #: the start's thread with it and leaves the tool running.
    launch_grace: float = 2.0
    #: How often the watchdog looks at the tracked process while a test runs:
    #: is it still there, and which children does it have. A walk of the
    #: process table per tick and worker (tens of ms on Windows), so not the
    #: 0.25 s of the close path; a death is noticed within one tick, and a
    #: child born after the last one is on no record (see
    #: :meth:`_record_orphans` for what finds it all the same).
    _CHILD_WATCH_SECONDS: float = 0.5
    #: After :meth:`cancel_running` said the tool will come back on its own:
    #: how long it gets before the worker is killed instead.
    cancel_grace: float = 10.0

    def __init__(self, worker_id: int, config: Config):
        self.worker_id = worker_id
        self.config = config
        #: PIDs attributed to this worker's simulator process.
        self.pids: set[int] = set()
        #: ``(pid, create_time)`` of descendants snapshotted before a
        #: graceful shutdown — the parent may exit and orphan them, putting
        #: them out of reach of a tree walk. The create time is what makes
        #: the recorded PID safe to kill afterwards.
        self._orphan_children: set[tuple[int, float]] = set()
        #: Consecutive restarts charged against this worker by the dispatch
        #: loop's budget; reset when a test succeeds without a kill.
        self._n_restarts: int = 0
        #: The test the watchdog is running, the cancel asked of it ("soft" /
        #: "hard") and the event that wakes the watchdog — one lock, since
        #: :meth:`request_cancel` comes from another thread.
        self._run_lock = threading.Lock()
        self._running_key: str | None = None
        self._cancel: str | None = None
        self._wake: threading.Event | None = None
        #: Set once a start was given up while its thread was still inside
        #: ``start()``: that thread may still write this object's state, so
        #: it is never started again.
        self._start_abandoned = False
        self._start_abort = threading.Event()
        #: The tool call in flight, in the tool's own spelling
        #: (``loadModel(Modelica)``) — what :meth:`process_detail` says the
        #: process died or stalled during, and :meth:`starting` the phase of
        #: a start. A backend sets it as it goes.
        self._step: str = ""
        #: ``time.monotonic()`` of the :meth:`start_within` in flight.
        self._start_began: float | None = None

    @abstractmethod
    def start(self) -> None:
        """Bring the worker process up and apply startup (library loads,
        framework settings). Raise on failure.
        """

    @abstractmethod
    def is_alive(self) -> bool:
        """True iff the worker can accept another test. Backends without
        watchable subprocess state can implement this as ``self._handle is
        not None``.
        """

    # -- template-method hooks ----------------------------------------

    def run_test(
        self,
        test: TestModel,
        test_key: str,
        progress: Optional["object"] = None,
    ) -> "TestRunResult":
        """Run one test to completion in this worker, blocking.

        The hook the shared ``run_test_with_timeout`` executes on its
        watchdog thread. Backends that override ``run_test_with_timeout``
        wholesale never reach this.

        *progress* is the optional :class:`ProgressReporter` so the worker
        can emit ``on_phase`` updates (translation vs simulation) for the
        live dashboard. Backends that don't have meaningful sub-phases
        accept and ignore the parameter.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement run_test() or override "
            f"run_test_with_timeout()."
        )

    def _evaluate_from_disk(
        self,
        test: TestModel,
        test_key: str,
        elapsed: float,
    ) -> "TestRunResult | None":
        """Rebuild a result from on-disk artifacts after the in-process flow
        couldn't finish (timeout / worker exception).

        A simulation often completes and writes its result file just before
        (or during) the kill, so the watchdog trusts disk over the abandoned
        call. ``None`` means the backend has no disk evidence to offer.
        """
        return None

    def _detach_interface(self) -> object | None:
        """Clear the live-handle attribute and return what it held.

        Runs synchronously inside ``close()`` — before the graceful attempt
        — so ``is_alive()`` reports dead the instant close() is entered and
        an abandoned exec thread can tell "my interface" from the restarted
        worker's. ``None`` means there was nothing live to shut down.
        """
        return None

    def _shutdown_interface(self, handle: object, grace: float) -> None:
        """Ask *handle* to shut down gracefully (``close()``, ``quit()``, …).

        Runs on a daemon thread that ``close()`` abandons after *grace*
        seconds, so it may still be in-flight when the PID kill lands.
        Only reached when ``_detach_interface`` handed back a handle, so a
        backend that overrides one owes the other.
        """
        raise NotImplementedError(
            f"{type(self).__name__} detached a live handle but implements no "
            f"_shutdown_interface()."
        )

    def _quiet(self) -> "AbstractContextManager[object]":
        """Context manager wrapping the watchdog's teardown calls.

        Backends whose interface spews connection noise while its process
        is being killed return a suppressor here.
        """
        return contextlib.nullcontext()

    def process_detail(self) -> str:
        """What the backend itself can say about its process when a start or
        a test did not come back: its exit code or that it still runs, the
        step it was in, its log's tail. Read BEFORE the kill. ``""`` when it
        has nothing to add.
        """
        return ""

    def cancel_running(self, test_key: str) -> bool:
        """Stop only the simulation of the running test, leaving the tool
        alive: ``True`` when that was asked of it and ``run_test`` will
        return on its own (the watchdog gives it :attr:`cancel_grace`),
        ``False`` when there is no such stop right now — the watchdog then
        kills the worker, and the dispatch loop restarts it uncharged.

        Called on the watchdog's thread while ``run_test`` is parked on the
        exec thread. Only reached on a backend that declares
        :attr:`Capability.CANCEL`.
        """
        return False

    # -- template methods ---------------------------------------------

    def startup_bound(self) -> float:
        """Seconds a start of this worker gets: ``worker_startup_timeout``
        from ``testing.json``, else the class's :attr:`startup_timeout`."""
        configured = getattr(self.config, "worker_startup_timeout", None)
        return float(configured) if configured is not None else self.startup_timeout

    def start_within(self, timeout: float | None = None) -> None:
        """:meth:`start`, bounded by *timeout* seconds (default
        :meth:`startup_bound`). Raises :class:`WorkerStartError` — and leaves
        the worker closed — when ``start()`` raises, when the tracked process
        is gone while ``start()`` still waits on it (at once, not at the
        bound), or when the bound passes.

        ``start()`` runs on a daemon thread. One given up on is closed again
        if it ever returns, and this object is not started a second time: a
        thread still inside ``start()`` writes its state. The process it
        launched is killed at the give-up when it is on record by then, by
        :meth:`_track_launch` when it comes on record later — which the
        give-up waits for, :attr:`launch_grace` at most.
        """
        if self._start_abandoned:
            raise WorkerStartError("its previous start never returned")
        bound = self.startup_bound() if timeout is None else float(timeout)
        self._start_began = time.monotonic()
        try:
            self._start_bounded(bound)
        finally:
            self._start_began = None

    def starting(self) -> tuple[str, float] | None:
        """While a :meth:`start_within` is in flight: the step it is in, in
        the tool's own spelling (``loadFile("...")``; ``""`` before the
        backend named one), and the seconds since it began. ``None`` when no
        start is in flight. Read from any thread — it is what a queue shows
        for a worker that is slow to come up."""
        began = self._start_began
        if began is None:
            return None
        return self._step, time.monotonic() - began

    def _start_bounded(self, bound: float) -> None:
        done = threading.Event()
        lock = threading.Lock()
        box: dict[str, BaseException] = {}
        abandoned = [False]

        def _body() -> None:
            try:
                self.start()
            except BaseException as exc:
                box["exc"] = exc
            finally:
                with lock:
                    done.set()
                    orphaned = abandoned[0]
                if orphaned:
                    # Nobody waits for this start any more: whatever it
                    # brought up is nobody's.
                    self._close_quietly()

        threading.Thread(
            target=_body,
            daemon=True,
            name=f"{self.exec_thread_prefix}-start-{self.worker_id}",
        ).start()
        deadline = time.monotonic() + bound
        reason: str | None = None
        while reason is None:
            remaining = deadline - time.monotonic()
            if done.wait(max(0.0, min(self._START_POLL_SECONDS, remaining))):
                break
            if self._start_abort.is_set():
                reason = "its start was abandoned"
            elif process_gone(self.pids):
                reason = self._safe_detail() or "its process exited during start"
            elif remaining <= 0:
                reason = start_timed_out_message(bound, self._safe_detail())
        if reason is not None:
            with lock:
                abandoned[0] = not done.is_set()
            if abandoned[0]:
                self._start_abandoned = True
                launched = bool(self.pids)
                self._close_quietly()
                if not launched:
                    done.wait(self.launch_grace)
                raise WorkerStartError(reason)
            # It returned in the same instant: judged by its own outcome.
        exc = box.get("exc")
        if exc is None:
            return
        # review 2026-07-06 (finding 24): a worker that failed mid-start may
        # already have spawned its subprocess; close it so the process (and
        # its license seat) doesn't leak. close() is contractually
        # idempotent + safe on never-started workers.
        self._close_quietly()
        if not isinstance(exc, Exception):
            raise exc
        raise WorkerStartError(str(exc)) from exc

    def _track_launch(self, pid: int | None) -> None:
        """The process this start launched EXISTS: record it. ``start()``
        calls this the moment it does — before the first wait on the tool,
        not when the launch call hands back a finished session — because a
        start is given up from another thread, and the give-up can only
        kill what is on record.

        And the other way round: when the start was given up BEFORE the
        process existed, nobody is left to kill it, so this kills it and
        raises :class:`WorkerStartError`. (``_start_abandoned`` is set
        before the give-up reads ``pids``, and ``pids`` is written here
        before the flag is read: one of the two always sees the process.)
        *pid* is ``None`` when the backend could not learn it.
        """
        self.pids = {int(pid)} if pid else set()
        if self._start_abandoned:
            if pid:
                # No name guard: the launch call handed this PID over a
                # moment ago and still holds its handle — it is not reused.
                kill_process_tree(int(pid))
            raise WorkerStartError("its start was given up")

    def abandon_start(self) -> None:
        """Give up this worker's :meth:`start_within`, from another thread
        (the pool closing): the one in flight, and any later one — it is not
        taken back."""
        self._start_abort.set()

    def arm(self, test_key: str) -> None:
        """*test_key* is about to run here: :meth:`request_cancel` for it is
        heard from now on, before the watchdog is up. The dispatch step arms
        first, so a cancel that arrives during a restart is not lost."""
        with self._run_lock:
            if self._running_key != test_key:
                self._running_key, self._cancel = test_key, None
                self._wake = threading.Event()

    def disarm(self) -> None:
        with self._run_lock:
            self._running_key = self._wake = self._cancel = None

    def _safe_detail(self) -> str:
        try:
            return self.process_detail()
        except Exception:
            logger.debug("process_detail raised", exc_info=True)
            return ""

    def _close_quietly(self) -> None:
        """:meth:`close` for a worker being given up on: short grace, the
        teardown noise muted, and a raise logged rather than passed on."""
        try:
            with self._quiet():
                self.close(grace=1.0)
        except Exception:
            logger.debug(
                "Worker %s: close() of a given-up worker raised",
                self.worker_id,
                exc_info=True,
            )

    def request_cancel(self, test_key: str, *, hard: bool = False) -> bool:
        """Ask the watchdog to stop *test_key*, from any thread. ``False``
        when this worker is not running it (nothing is asked). *hard* skips
        :meth:`cancel_running` and kills the worker.

        The test's result then comes back ``cancelled`` from
        :meth:`run_test_with_timeout` — unless the simulation finished
        anyway, which stays the result it is.
        """
        with self._run_lock:
            if self._running_key != test_key or self._wake is None:
                return False
            if self._cancel != "hard":
                self._cancel = "hard" if hard else "soft"
            self._wake.set()
            return True

    def close(self, grace: float = 5.0) -> None:
        """Tear the worker down: graceful first, then kill tracked PIDs.

        The graceful attempt runs on a daemon thread — a wedged interface
        must not block the pool's teardown — and is abandoned after *grace*
        seconds. Idempotent: ``_detach_interface`` hands back the handle
        once, and killing an already-dead PID is a no-op.
        """
        handle = self._detach_interface()
        if handle is not None:
            if type(self)._shutdown_interface is Worker._shutdown_interface:
                # Caught here rather than inside the daemon thread, whose
                # exceptions are logged and dropped: a worker that detaches
                # a handle and never shuts it down would silently skip every
                # graceful close and hard-kill its way through the run.
                raise TypeError(
                    f"{type(self).__name__} overrides _detach_interface() but "
                    f"not _shutdown_interface(handle, grace) — a detached "
                    f"handle has no graceful shutdown."
                )
            # Record descendants while the parent is still up: a graceful
            # shutdown that succeeds orphans them, and an orphan can't be
            # reached from the (now gone) parent afterwards.
            self._snapshot_orphan_children()
            done = threading.Event()

            def _graceful():
                try:
                    self._shutdown_interface(handle, grace)
                except Exception as exc:
                    # Named, never swallowed — but the stack stays at DEBUG.
                    # A raise here can be routine (a REQ socket still awaiting
                    # its reply refuses the send with EFSM, "operation cannot
                    # be accomplished in current state"), and a 12-line
                    # traceback per timed-out test is noise the run is
                    # supposed to be free of.
                    logger.warning(
                        "Worker %s: graceful shutdown raised (%s: %s); "
                        "falling back to the PID kill",
                        self.worker_id,
                        type(exc).__name__,
                        exc,
                    )
                    logger.debug(
                        "Worker %s: graceful-shutdown traceback",
                        self.worker_id,
                        exc_info=True,
                    )
                finally:
                    done.set()

            t = threading.Thread(target=_graceful, daemon=True)
            t.start()
            # Keep sampling the tree while the graceful attempt runs. One
            # snapshot up front is not enough: a timeout that lands during
            # *translation* sees a tree with no dymosim in it, and Dymola
            # then starts dymosim inside this window — from the
            # simulateModel call the abandoned exec thread already issued.
            # That child is only reachable while its parent is alive, which
            # may be for a fraction of this window (measured live,
            # 2026-09-01: two surviving `sh -c ./dymosim` processes).
            # *grace* is the whole budget, walks included: each tick waits
            # only what is left of it, so a slow psutil walk (tens of ms per
            # recursive children() call on Windows, ~20 of them in a 5s
            # Dymola grace) eats into the wait rather than extending the
            # teardown past it.
            deadline = time.monotonic() + grace
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if done.wait(min(self._ORPHAN_POLL_SECONDS, remaining)):
                    break
                self._snapshot_orphan_children()
            self._snapshot_orphan_children()
        self._kill_tracked_pids()

    def _snapshot_orphan_children(self) -> None:
        """Record the tracked processes' current descendants for later."""
        self._orphan_children |= self._live_descendants()

    def _live_descendants(self) -> set[tuple[int, float]]:
        found: set[tuple[int, float]] = set()
        for pid in list(self.pids):
            found |= snapshot_children(pid, name_hint=self.kill_name_hint)
        return found

    def _record_orphans(self, test_key: str, tool_born: float | None) -> None:
        """The tool died by itself while it ran *test_key*: add what it left
        behind to the record :meth:`_kill_tracked_pids` kills.

        The ticks' record misses a child born after the last one — dymosim
        appears and the tool is killed within the same half second. On
        Linux such a child is still found, by the mark of
        :func:`~.common.proc_kill.orphans_in`: parentless, born after this
        worker's tool (*tool_born*), running in this test's own directory.
        A process that fails any part is never touched.

        What remains out of reach: on Linux, a child born after the last
        tick that has left the test's directory; elsewhere, any child born
        after the last tick (:attr:`_CHILD_WATCH_SECONDS`).
        """
        work_dir = getattr(self.config, "work_dir", None)
        if tool_born is None or work_dir is None:
            return
        self._orphan_children |= orphans_in(str(Path(work_dir) / test_key), tool_born)

    def _kill_tracked_pids(self) -> None:
        """Force-kill the process trees attributed to this worker.

        The tree, not the tracked PID alone — the process doing the
        simulating is a child (dymosim under dymola, the compiled model
        binary under omc) and outlives a parent-only kill.
        """
        for pid in list(self.pids):
            kill_process_tree(pid, name_hint=self.kill_name_hint)
        # Orphans were recorded seconds ago, so PID reuse applies to them
        # exactly as it does to a tracked parent — their create times are
        # the guard. The name hint is not: an orphan is the *child*
        # (dymosim, the compiled model binary), which the hint would
        # wrongly exclude.
        for pid, create_time in list(self._orphan_children):
            kill_process_tree(pid, create_time=create_time)
        self.pids = set()
        self._orphan_children = set()

    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        progress: Optional["object"] = None,
    ) -> "TestRunResult":
        """Run *test* with a watchdog. On timeout, hard-kill the worker
        process and return ``TestRunResult(timed_out=True, success=False)``.
        Test-level failures (sim error, missing variable) return a
        ``success=False`` result, not a raise. Worker-level catastrophes
        (subprocess died, pipe broken) MAY raise so the caller restarts.

        ``run_test`` executes on a daemon thread the watchdog abandons on
        expiry; ``self`` is closed and its PIDs killed, then
        ``_evaluate_from_disk`` gets a chance to rescue a simulation that
        finished just before the kill. Either way the worker is dead, so
        the result carries ``worker_killed=True``: the dispatch loop keys
        its benign-restart accounting on ``timed_out or worker_killed``,
        and we killed a *healthy* worker because the test was slow.

        Two more things end the wait. A cancel (:meth:`request_cancel`)
        returns a ``cancelled`` result: after :meth:`cancel_running` and the
        tool's own return when the backend has a soft stop (the worker is
        reused), else after the same kill as a timeout's — with no disk
        rescue, since a cancelled run is not a result whatever it left. And
        the tracked process being gone (looked at every
        :attr:`_CHILD_WATCH_SECONDS`) fails the test and closes the worker,
        which kills the children recorded while it was alive: a tool that
        dies by itself orphans its simulation process, and nothing reaches
        an orphan through a parent that no longer exists. A child born
        after the last look is found by its mark where the platform allows
        (:meth:`_record_orphans`, which also says what stays out of reach).
        """
        result_box: list[TestRunResult | None] = [None]
        exc_box: list[BaseException | None] = [None]
        start_ts = time.monotonic()
        done = threading.Event()
        self.arm(test_key)
        with self._run_lock:
            wake = self._wake
            cancelled_early = self._cancel is not None
        assert wake is not None  # armed just above
        if cancelled_early:
            # Asked to stop before it started: nothing ran, the worker is
            # as it was.
            self.disarm()
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message="Cancelled",
                cancelled=True,
            )

        def _worker_body():
            try:
                result_box[0] = self.run_test(test, test_key, progress=progress)
            except BaseException as e:
                exc_box[0] = e
            finally:
                done.set()
                wake.set()

        t = threading.Thread(
            target=_worker_body,
            daemon=True,
            name=f"{self.exec_thread_prefix}-exec-{self.worker_id}",
        )
        # Read while the tool is certain to be there: what it leaves behind
        # if it dies is told by having been born after it.
        tool_born = created_at(self.pids)
        t.start()
        try:
            return self._watch_test(
                test,
                test_key,
                timeout,
                start_ts,
                t,
                done,
                wake,
                result_box,
                exc_box,
                tool_born,
            )
        finally:
            self.disarm()

    def _watch_test(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        start_ts: float,
        t: threading.Thread,
        done: threading.Event,
        wake: threading.Event,
        result_box: list,
        exc_box: list,
        tool_born: float | None,
    ) -> "TestRunResult":
        """The watchdog's wait, and what each way out of it returns."""

        def _cancelled(worker_killed: bool) -> TestRunResult:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=time.monotonic() - start_ts,
                error_message="Cancelled",
                cancelled=True,
                worker_killed=worker_killed,
            )

        deadline = start_ts + timeout
        gone = False
        while not done.is_set() and self._cancel is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            wake.wait(min(self._CHILD_WATCH_SECONDS, remaining))
            if done.is_set() or self._cancel is not None:
                break
            # Walk first, look second: a tool that dies between the two
            # leaves an empty walk, which must not replace the record of
            # the tick before — that record is all there is of its
            # children.
            children = self._live_descendants()
            if process_gone(self.pids):
                self._orphan_children |= children
                gone = True
                break
            # The tree as it is NOW, not accumulated: a test spawns and
            # reaps compilers for minutes, and only what is alive when the
            # parent dies is worth a kill.
            self._orphan_children = children

        if self._cancel is not None and not done.is_set():
            if self._cancel == "soft" and self._soft_cancel(test_key):
                grace_end = time.monotonic() + self.cancel_grace
                while self._cancel == "soft" and time.monotonic() < grace_end:
                    if done.wait(0.05):
                        break
            if not done.is_set():
                with self._quiet():
                    self.close(grace=1.0)
                    t.join(0.5)
                return _cancelled(worker_killed=True)

        if gone and not done.is_set():
            # Read before the kill: the exit code and the log are the
            # backend's, and close() drops the handle they hang off.
            detail = self._safe_detail()
            elapsed = time.monotonic() - start_ts
            self._record_orphans(test_key, tool_born)
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            with self._quiet():
                self.close(grace=1.0)
                t.join(0.5)
            if disk_result is not None and disk_result.success:
                return disk_result
            # worker_killed stays False: nobody killed it on purpose, so the
            # restart is charged like any other worker-side malfunction.
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="Worker process exited during the test"
                + (f" — {detail}" if detail else ""),
            )

        if not done.is_set():
            with self._quiet():
                self.close(grace=self.timeout_kill_grace)
                t.join(0.5)
            elapsed = time.monotonic() - start_ts
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result is not None and disk_result.success:
                disk_result.worker_killed = True
                return disk_result
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=timeout_message(f"Timed out after {timeout:.0f}s"),
                timed_out=True,
                worker_killed=True,
            )

        if exc_box[0] is not None:
            # Worker exceptions DO count against the restart budget — they
            # signal a real worker-side malfunction (RPC dropped, session
            # wedged), not a slow test, so ``worker_killed`` stays False and
            # the dispatch loop ticks the budget on the next restart. The
            # simulation may still have completed before the interface died,
            # so disk gets its chance here too.
            elapsed = time.monotonic() - start_ts
            if process_gone(self.pids):
                self._record_orphans(test_key, tool_born)
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            with self._quiet():
                self.close(grace=1.0)
            if disk_result is not None and disk_result.success:
                return disk_result
            if self._cancel is not None:
                return _cancelled(worker_killed=True)
            exc = exc_box[0]
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Worker exception: {type(exc).__name__}: {exc}",
            )

        result: TestRunResult = result_box[0]
        if self.is_alive() and process_gone(self.pids):
            # The tool died and run_test still returned (an interface that
            # turns a lost connection into a failed result): close, so the
            # next test restarts the worker instead of failing against a
            # dead process, and the recorded children go with it.
            self._record_orphans(test_key, tool_born)
            self._close_quietly()
        if self._cancel is not None and not result.success:
            # What a stopped simulation reports ("stopped early", a failed
            # record) is the cancel's doing, not a failure of the test.
            return _cancelled(worker_killed=not self.is_alive())
        return result

    def _soft_cancel(self, test_key: str) -> bool:
        try:
            return bool(self.cancel_running(test_key))
        except Exception:
            logger.exception("Worker %s: cancel_running raised", self.worker_id)
            return False

    def export_fmu(self, test: TestModel, output_dir: "Path") -> "Path":
        """Export *test*'s model as an FMU into *output_dir*.

        Backends that declare :attr:`Capability.FMU_EXPORT` on their
        :class:`Backend` MUST override this; the default raises
        :class:`NotImplementedError`. Used by 4.B's cross-backend chain
        (e.g. Dymola export → FMPy simulate as a second baseline).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support FMU export. "
            f"Backend must declare Capability.FMU_EXPORT and override export_fmu()."
        )


# ---------------------------------------------------------------------------
# One worker's dispatch state
# ---------------------------------------------------------------------------


class WorkerRunner(ItemRunner):
    """What a dispatch thread does for each test it takes: bring a dead worker
    back within its restart budget, run the test under the watchdog, keep the
    restart accounting.

    One per worker, living as long as its dispatch thread — a thread of
    ``run_tests``' fixed-list dispatch or of the simulation pool, which is
    what makes a test fail, retry and restart the same way in both.
    ``retired`` is the sentence the worker was given up with; the test in
    hand has already been failed with it, and the thread holding this runner
    takes no more tests.
    """

    backend: "PersistentBackendBase"

    def __init__(self, backend: "PersistentBackendBase", worker: "Worker"):
        super().__init__(backend, worker.worker_id)
        self.worker = worker
        # Whether the previous test left the worker dead for a benign reason
        # (we deliberately hard-killed it after a per-test timeout, or it was
        # disk-rescued post-timeout). Such restarts shouldn't count against
        # the worker's permanent-broken budget — the worker was healthy, the
        # test was slow.
        self._prev_killed_worker = False

    def start(self) -> None:
        self.worker.start_within()
        self.backend.note_versions([self.worker])

    def starting(self) -> tuple[str, float] | None:
        return self.worker.starting()

    def expect(self, test_key: str) -> None:
        super().expect(test_key)
        self.worker.arm(test_key)

    def _stop_test(self, test_key: str) -> bool:
        return self.worker.request_cancel(test_key)

    def stop(self, test_key: str | None) -> None:
        # A (re)start in flight is given up; a test in flight is ended by
        # the kill, not by the backend's soft stop and its grace.
        self.worker.abandon_start()
        if test_key is not None:
            self.worker.request_cancel(test_key, hard=True)

    def close(self) -> None:
        self.worker.close()

    def _run(self, test: TestModel, test_key: str) -> TestRunResult:
        """Run *test* on this worker. Never raises: a worker that cannot be
        restarted, and one that raises, are both a failed result."""
        # Armed before the restart below, so a cancel that arrives while the
        # worker is being brought back is heard when the test would start.
        self.worker.arm(test_key)
        try:
            return self._run_armed(test, test_key)
        finally:
            self.worker.disarm()

    def _run_armed(self, test: TestModel, test_key: str) -> TestRunResult:
        w, backend = self.worker, self.backend
        if not w.is_alive():
            if not self._restart(count_against_budget=not self._prev_killed_worker):
                self.retired = "Worker dead; restart exhausted"
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=0.0,
                    error_message=self.retired,
                )
            self._prev_killed_worker = False  # consumed by restart
        if backend.progress is not None:
            backend.progress.on_start(test_key, worker_id=w.worker_id)
        timeout = float(
            test.timeout if test.timeout is not None else backend.config.timeout
        )
        try:
            tr = w.run_test_with_timeout(
                test,
                test_key,
                timeout,
                progress=backend.progress,
            )
        except Exception as exc:
            # review 2026-07-06 (finding 21): the Worker ABC contract permits
            # raises for worker-level catastrophes. A raise must be recorded —
            # not kill the dispatch thread and silently drop the in-flight
            # test (plus, at --parallel 1, the whole remaining queue). The
            # next test's is_alive()/restart logic handles the possibly-dead
            # worker.
            logger.exception(
                "Worker %s raised while running %s",
                w.worker_id,
                test.model_id,
            )
            self._prev_killed_worker = False
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=0.0,
                error_message=f"worker exception: {exc!r}",
            )
        # Reset the restart counter on a successful test — the worker has
        # demonstrably recovered. Consecutive *real* worker deaths
        # (translation broke / RPC dead) still accumulate to
        # ``max_restarts_per_worker`` and trip restart-exhausted. Benign
        # worker kills (timeout, post-timeout disk-rescue) flow through
        # _prev_killed_worker so they don't poison the counter.
        if tr.success and not tr.worker_killed:
            w._n_restarts = 0
        self._prev_killed_worker = bool(tr.timed_out or tr.worker_killed)
        return tr

    def _restart(self, count_against_budget: bool = True) -> bool:
        """Bring the worker back up after a death.

        ``count_against_budget`` distinguishes two scenarios:

        * **True** (default): worker died because something is genuinely
          wrong with it (translation returned False with no diagnostic
          log; RPC error; start() failure on a previous iteration). The
          counter increments; after ``max_restarts_per_worker`` such
          consecutive failures, we declare the worker permanently dead.
        * **False**: worker died because the dispatch loop hard-killed
          it after a per-test *timeout*. The worker was healthy when
          the test started — it actively translated/simulated for the
          full timeout budget. Restart is just to bring up a fresh
          process; the count of "consecutive worker deaths" should not
          tick up. This avoids a known cascade where 3 slow tests in
          a row exhaust the budget and synthesize ~290 fake failures
          for the rest of the queue.
        """
        w, backend = self.worker, self.backend
        n_restarts = w._n_restarts
        if count_against_budget and n_restarts >= backend.max_restarts_per_worker:
            return False
        if count_against_budget:
            n_restarts += 1
            w._n_restarts = n_restarts
        t0 = time.monotonic()
        # Exactly one retry, after a short pause. A start() that fails is
        # usually transient (licence seat not yet released by the process
        # we just killed); retiring the dispatch thread on the first
        # failure cost the whole remaining queue at --parallel 1. The
        # retry is NOT a second charge against the restart budget — it is
        # the same restart attempt, tried twice.
        backoff = backend.restart_retry_backoff_seconds
        last_exc: Exception | None = None
        for attempt in (1, 2):
            if attempt == 2:
                if not backend.quiet:
                    print(
                        f"  Worker {w.worker_id}: retrying restart in "
                        f"{backoff:.0f}s (one retry, then the worker is retired)",
                        file=sys.stderr,
                    )
                time.sleep(backoff)
                t0 = time.monotonic()
            try:
                w.start_within()
            except Exception as exc:
                last_exc = exc
                if not backend.quiet:
                    print(
                        f"  Worker {w.worker_id}: restart FAILED — {exc}",
                        file=sys.stderr,
                    )
                if w._start_abandoned:
                    # Not a blip: its start is still parked somewhere, and
                    # this object cannot be started again.
                    if not backend.quiet:
                        print(
                            f"  Worker {w.worker_id}: its start never "
                            f"returned — retiring the worker",
                            file=sys.stderr,
                        )
                    return False
                continue
            if not backend.quiet:
                if count_against_budget:
                    print(
                        f"  Worker {w.worker_id}: restarted "
                        f"({time.monotonic() - t0:.1f}s, attempt {n_restarts}/"
                        f"{backend.max_restarts_per_worker})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  Worker {w.worker_id}: restarted post-timeout "
                        f"({time.monotonic() - t0:.1f}s — not counted)",
                        file=sys.stderr,
                    )
            return True
        if not backend.quiet:
            print(
                f"  Worker {w.worker_id}: restart failed twice "
                f"({type(last_exc).__name__}) — retiring the worker",
                file=sys.stderr,
            )
        return False


# ---------------------------------------------------------------------------
# Persistent-worker template
# ---------------------------------------------------------------------------


class PersistentBackendBase(Backend):
    """Template-method class for persistent-worker backends.

    Owns the orchestration shared by Dymola / OpenModelica / Julia persistent
    backends — so adding a new persistent backend is a one-class job, not a
    250-line copy-paste of the dispatch loop.

    Subclass contract:

      - Set ``worker_cls`` to your :class:`Worker` subclass.
      - Set ``backend_label`` (e.g. ``"Dymola"``) — used in headers and
        thread names. Read by both the run-header line and the final
        completion message.
      - Override :meth:`make_worker` if your worker constructor needs
        backend-specific args beyond ``(worker_id, config)``. Use
        :meth:`setup_before_workers` to populate any class state your
        ``make_worker`` reads.
      - Override :meth:`setup_before_workers` if your backend has runtime
        patches that must apply before any worker spawns (Dymola: log
        filter + parallel-startup lock).
      - Override :meth:`preflight` (already declared on ``Backend``)
        to add a cheap dependency probe — see :func:`backends.select_backend`.

    The :meth:`run_tests` template enforces the lifecycle:

        setup_before_workers → _prepare_run (progress + keys + manifest)
            → make_worker × N → start in parallel
            → filter live workers (raise if zero) → dispatch with restart
            → close all workers → finalize progress

    Subclasses generally don't need to touch :meth:`run_tests` itself.
    """

    #: Worker subclass to instantiate per slot. MUST be set by subclasses.
    worker_cls: type["Worker"] = None  # type: ignore[assignment]

    #: Human-readable backend name for headers and thread names.
    backend_label: str = ""

    #: Per-worker restart budget. After a timeout or worker-level exception
    #: the dispatch loop will try to ``start()`` the worker again up to this
    #: many times; on exhaustion the in-flight test gets a synthetic
    #: ``Worker dead; restart exhausted`` failure result.
    max_restarts_per_worker: int = 3

    #: Seconds to wait before the single retry of a failed ``start()``.
    #: A restart fails for transient reasons far more often than permanent
    #: ones — a licence server that hasn't yet released the seat of the
    #: process we just killed, a port still in TIME_WAIT — and at
    #: ``--parallel 1`` a single such blip used to retire the only dispatch
    #: thread and fail-mark the entire remaining queue. One retry after a
    #: short pause covers the transient case without turning a genuinely
    #: broken backend into a long stall.
    restart_retry_backoff_seconds: float = 5.0

    #: What a live worker named for run provenance (:meth:`note_versions`).
    #: On the class so a subclass with its own ``__init__`` chain reads one.
    _worker_versions: dict = {}

    @classmethod
    def persistent_backend_cls(cls):
        return cls  # we ARE the persistent variant; stop recursing

    def setup_before_workers(self) -> None:
        """Hook for runtime patches that must take effect before any worker
        spawns. Default no-op. Dymola uses this to install its log filter
        and narrow the parallel-startup lock; OpenModelica uses it to
        cache its session class; Julia today needs nothing here.
        """
        return None

    def make_worker(self, worker_id: int) -> "Worker":
        """Construct one :class:`Worker`. Default calls
        ``self.worker_cls(worker_id, self.config)``. Backends with extra
        construction args (e.g. ``DymolaConfig`` + interface class) override
        this — typically reading state stashed by
        :meth:`setup_before_workers`.
        """
        if self.worker_cls is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set the `worker_cls` class attr "
                f"or override `make_worker`."
            )
        return self.worker_cls(worker_id, self.config)

    def starting_workers_message(self, n_workers: int) -> str:
        """The "Starting N <backend> worker(s)..." line printed before
        worker startup. Override to add backend-specific guidance — e.g.
        Julia's warmup notice that the first run does several minutes of
        JIT compilation. Default uses :attr:`backend_label`.
        """
        return f"Starting {n_workers} {self.backend_label} worker(s)..."

    def prepare_item_runners(self) -> None:
        self.setup_before_workers()

    def item_runner(self, worker_id: int) -> WorkerRunner:
        return WorkerRunner(self, self.make_worker(worker_id))

    def note_versions(self, live_workers: list["Worker"]) -> dict:
        """Ask a live, idle worker for the tool and library versions and
        keep what it names for :meth:`run_versions`; returns what was new.
        Best-effort — a failed probe leaves the configured backend label as
        the shown version and omits the library line."""
        updates: dict = {}
        version = self._safe_probe_worker_version(live_workers)
        if version:
            updates["tool_version"] = version
        libs = self._safe_probe_library_versions(live_workers)
        if libs:
            updates["library_versions"] = libs
        self._worker_versions = {**self._worker_versions, **updates}
        return updates

    def run_versions(self) -> dict:
        return {**super().run_versions(), **self._worker_versions}

    def _probe_worker_version(self, live_workers: list["Worker"]) -> str | None:
        """Best-effort actual tool version, asked of a live worker.

        The persistent template calls this once workers are up (before
        dispatch) to stamp run provenance. Default returns ``None``; backends
        whose session can name its version (Dymola's ``DymolaVersion()``,
        omc's ``getVersion()``) override this. MUST NOT raise — the template
        wraps it in :meth:`_safe_probe_worker_version`.
        """
        return None

    def _safe_probe_worker_version(self, live_workers: list["Worker"]) -> str | None:
        """Guarded :meth:`_probe_worker_version` — a probe failure degrades to
        ``None`` and never disrupts the run."""
        if not live_workers:
            return None
        try:
            return self._probe_worker_version(live_workers)
        except Exception:
            logger.debug("worker version probe failed", exc_info=True)
            return None

    def _probe_library_versions(self, live_workers: list["Worker"]) -> dict | None:
        """Best-effort loaded-library versions (e.g. ``{"Modelica": "4.1.0"}``)
        asked of a live worker. Default ``None``; Modelica backends override
        (Dymola reads the bundled MSL under ``$DYMOLA``; omc uses
        ``getVersion(Modelica)``). The MSL version is the second drift suspect
        after the tool version itself. MUST NOT raise — wrapped by
        :meth:`_safe_probe_library_versions`."""
        return None

    def _safe_probe_library_versions(self, live_workers: list["Worker"]) -> dict | None:
        """Guarded :meth:`_probe_library_versions`."""
        if not live_workers:
            return None
        try:
            return self._probe_library_versions(live_workers)
        except Exception:
            logger.debug("worker library-version probe failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # The template
    # ------------------------------------------------------------------

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        if not tests:
            return []

        # First, so a failing backend precondition aborts the run before
        # status.json or the manifest are written.
        self.setup_before_workers()

        # Persistent backends usually can't name their version until a worker
        # is live, so tool_version starts None and is patched in after startup
        # via _probe_worker_version (see below).
        setup = self._prepare_run(tests)
        total = setup.total
        manifest = setup.manifest
        test_items = setup.keyed_tests

        n_workers = max(1, self.config.parallel)
        _print_run_header(
            total,
            f"via persistent {self.backend_label} workers",
            n_workers,
            self.config.timeout,
            self.config.work_dir,
            timeout_per_test=True,
            metadata=setup.run_metadata,
            quiet=self.quiet,
        )

        workers = [self.make_worker(i) for i in range(n_workers)]

        start_all = time.monotonic()
        if not self.quiet:
            print(self.starting_workers_message(n_workers), file=sys.stderr)

        live_workers, start_failures = self._start_workers_parallel(workers)
        if not live_workers:
            # review 2026-07-06 (finding 24): close every worker before the
            # raise — start()-failed workers already close themselves in
            # start_within, but close() is idempotent and this guarantees
            # no orphaned simulator process (license seat) survives the abort.
            for w in workers:
                try:
                    w.close()
                except Exception:
                    logger.debug(
                        "Worker %s: close() during startup-failure cleanup raised",
                        w.worker_id,
                        exc_info=True,
                    )
            # Every test fails with the backend's own words: the run is
            # over, and a test left "queued" with the reason printed above
            # — or not printed at all, under ``quiet`` — says nothing.
            message = no_worker_message(self.backend_label, start_failures)
            for test, test_key in test_items:
                manifest.record_result(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        elapsed=0.0,
                        error_message=message,
                    )
                )
                setup.progress.on_finish(
                    test_key, success=False, elapsed=0.0, detail=message
                )
            setup.progress.finalize()
            raise RuntimeError(f"{message}. Try re-running with --batch.")
        if not self.quiet:
            print(
                f"  {len(live_workers)}/{n_workers} workers ready in "
                f"{time.monotonic() - start_all:.1f}s",
                file=sys.stderr,
            )

        # Now that a worker is live, ask it for the real tool version and the
        # loaded library versions (e.g. MSL) and stamp them into the run
        # provenance.
        updates = self.note_versions(live_workers)
        if updates:
            setup.progress.update_metadata(**updates)

        results = self._dispatch_with_restart(live_workers, test_items, total, manifest)

        for w in workers:
            w.close()

        wall = time.monotonic() - start_all
        self._print_run_summary(results, total, wall)

        manifest.results = results
        setup.progress.finalize()
        return [manifest]

    # ------------------------------------------------------------------
    # Internal building blocks
    # ------------------------------------------------------------------

    def _start_workers_parallel(
        self, workers: list["Worker"]
    ) -> tuple[list["Worker"], dict[int, str]]:
        """Spawn every worker concurrently, each start bounded
        (:meth:`Worker.start_within`). Returns the subset that started and,
        by worker id, why each of the others did not; failures are printed
        per worker. Caller decides what to do on zero live workers (the
        template fails every test with the reasons and raises).
        """
        failures: dict[int, str] = {}
        failures_lock = threading.Lock()

        def _start_one(w: "Worker"):
            t0 = time.monotonic()
            try:
                w.start_within()
                dt = time.monotonic() - t0
                if not self.quiet:
                    print(f"  Worker {w.worker_id}: ready ({dt:.1f}s)", file=sys.stderr)
            except Exception as exc:
                with failures_lock:
                    failures[w.worker_id] = str(exc)
                if not self.quiet:
                    print(
                        f"  Worker {w.worker_id}: start FAILED — {exc}",
                        file=sys.stderr,
                    )

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(_start_one, w) for w in workers]
            for f in as_completed(futures):
                f.result()  # propagate unexpected errors; start errors already printed

        return [w for w in workers if w.worker_id not in failures], failures

    def _dispatch_with_restart(
        self,
        live_workers: list["Worker"],
        test_items: list,
        total: int,
        manifest: BatchManifest,
    ) -> list[TestRunResult]:
        """Shared queue + sentinel + per-worker restart dispatch.

        Each test goes once into ``work_queue``; each live worker gets one
        sentinel ``None`` to signal "drain done." Workers that die mid-run
        get up to :attr:`max_restarts_per_worker` restart attempts; on
        exhaustion the in-flight test gets a synthetic failure result.

        *manifest* receives each verdict as it lands — this loop is the one
        place every persistent result (real, restart-exhausted, drained)
        passes through, and a Ctrl-C here would otherwise leave the run's
        outcomes unrecorded.
        """
        work_queue: queue.Queue[tuple | None] = queue.Queue()
        for item in test_items:
            work_queue.put(item)
        for _ in live_workers:
            work_queue.put(None)

        results: list[TestRunResult] = []
        results_lock = threading.Lock()
        completed = [0]

        def _record(test, test_key, tr: TestRunResult) -> None:
            with results_lock:
                results.append(tr)
                completed[0] += 1
                idx = completed[0]
            # Persist before anything else can fail: this is the abort-proof
            # half of the run-verdict record (BatchManifest.record_result).
            manifest.record_result(tr)
            label = f"{test_key} {test.model_id.rsplit('.', 1)[-1]}"
            status = "ok" if tr.success else ("TIMEOUT" if tr.timed_out else "FAIL")
            parts = []
            if getattr(tr, "translation_wall", None) is not None:
                parts.append(f"xlate {tr.translation_wall:.1f}s")
            if getattr(tr, "sim_wall", None) is not None:
                parts.append(f"sim {tr.sim_wall:.1f}s")
            if tr.success and parts:
                detail_str: str | None = ", ".join(parts)
            elif not tr.success:
                # A timeout's message carries the per-test-override hint
                # (types.TIMEOUT_OVERRIDE_HINT) and truncating at 80 would cut
                # exactly the part the user needs, so timeouts print in full.
                detail_str = (tr.error_message or "")[: None if tr.timed_out else 80]
            else:
                detail_str = None
            _print_progress(
                idx,
                total,
                label,
                status,
                elapsed=tr.elapsed,
                detail=detail_str,
                quiet=self.quiet,
            )
            if self.progress is not None:
                self.progress.on_finish(
                    test_key,
                    success=tr.success,
                    elapsed=tr.elapsed,
                    # Same timeout-conditional lift as the progress line
                    # above: the Julia timeout message runs past 120 chars and
                    # the clip landed mid-hint, so the dashboard showed a
                    # truncated "simulation.tim…".
                    detail=None
                    if tr.success
                    else (tr.error_message or "")[: None if tr.timed_out else 120],
                    timed_out=tr.timed_out,
                )

        # review 2026-07-06 (finding 20): dispatch-thread liveness registry.
        # A worker whose restart failed permanently must NOT stay in the
        # dispatch loop — it would tight-loop on the shared queue, instantly
        # fail-marking tests that healthy workers could have run. Its thread
        # exits instead — unless it is the LAST live thread, in which case it
        # drains the queue and fail-marks the remainder so every test gets a
        # recorded result and nothing hangs. (There is no work_queue.join()
        # anywhere — the thread joins below are the only sync point — so an
        # early thread exit cannot deadlock; leftover sentinels are inert.)
        live_threads = [len(live_workers)]
        live_threads_lock = threading.Lock()

        def _fail_dead(test, test_key: str, message: str) -> None:
            _record(
                test,
                test_key,
                TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=0.0,
                    error_message=message,
                ),
            )

        def _drain_remaining(reason: str) -> None:
            while True:
                try:
                    item = work_queue.get_nowait()
                except queue.Empty:
                    return
                if item is not None:
                    test, test_key = item
                    _fail_dead(test, test_key, reason)
                work_queue.task_done()

        def _worker_loop(runner: WorkerRunner):
            try:
                while True:
                    item = work_queue.get()
                    if item is None:
                        work_queue.task_done()
                        return
                    test, test_key = item
                    try:
                        _record(test, test_key, runner.run(test, test_key))
                    finally:
                        work_queue.task_done()
                    if runner.retired:
                        # review 2026-07-06 (finding 20): the item this thread
                        # held is fail-marked (by the runner); exit so the
                        # remaining tests go to live workers.
                        return
            finally:
                # review 2026-07-06 (finding 20): last thread out drains the
                # queue so no test is left unrecorded and no consumer hangs.
                with live_threads_lock:
                    live_threads[0] -= 1
                    is_last = live_threads[0] == 0
                if is_last:
                    _drain_remaining(
                        "Worker dead; restart exhausted (no live workers remaining)"
                    )

        thread_prefix = self.backend_label.lower().replace(" ", "-") or "pworker"
        threads = [
            threading.Thread(
                target=_worker_loop,
                args=(WorkerRunner(self, w),),
                name=f"{thread_prefix}-pworker-{w.worker_id}",
            )
            for w in live_workers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    @property
    def run_summary_phrase(self) -> str:
        return f"Persistent {self.backend_label} run complete"

    @property
    def label(self) -> str:
        return self.backend_label or super().label
