"""The persistent-worker lane: the long-lived-process contract and the pool
orchestration that drives it.

:class:`Worker` is one simulator subprocess wrapped as a test-running unit —
it owns the watchdog, the timeout kill and the disk rescue as template
methods, so a backend supplies hooks rather than a copy of the loop.
:class:`PersistentBackendBase` owns the pool: parallel startup, the shared
queue + sentinel dispatch, and per-worker restart accounting.

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
from .base import Backend, _print_progress, _print_run_header
from .common.proc_kill import kill_process_tree, snapshot_children
from .types import TestRunResult, timeout_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract persistent-worker interface
# ---------------------------------------------------------------------------


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
        ``__init__`` → ``start()`` → ``run_test_with_timeout()`` × N
            → ``close()``
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

    # -- template methods ---------------------------------------------

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
        for pid in list(self.pids):
            self._orphan_children |= snapshot_children(
                pid, name_hint=self.kill_name_hint
            )

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
        """
        result_box: list[TestRunResult | None] = [None]
        exc_box: list[BaseException | None] = [None]
        start_ts = time.monotonic()

        def _runner():
            try:
                result_box[0] = self.run_test(test, test_key, progress=progress)
            except BaseException as e:
                exc_box[0] = e

        t = threading.Thread(
            target=_runner,
            daemon=True,
            name=f"{self.exec_thread_prefix}-exec-{self.worker_id}",
        )
        t.start()
        t.join(timeout)

        if t.is_alive():
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
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            with self._quiet():
                self.close(grace=1.0)
            if disk_result is not None and disk_result.success:
                return disk_result
            exc = exc_box[0]
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Worker exception: {type(exc).__name__}: {exc}",
            )

        return result_box[0]  # type: ignore[return-value]

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
        to add a cheap dependency probe — see :meth:`_get_backend` in the CLI.

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
        )

        workers = [self.make_worker(i) for i in range(n_workers)]

        start_all = time.monotonic()
        print(self.starting_workers_message(n_workers), file=sys.stderr)

        live_workers = self._start_workers_parallel(workers)
        if not live_workers:
            # review 2026-07-06 (finding 24): close every worker before the
            # raise — start()-failed workers already close themselves in the
            # startup handler, but close() is idempotent and this guarantees
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
            setup.progress.finalize()
            raise RuntimeError(
                f"All {self.backend_label} persistent workers failed to start. "
                f"See per-worker errors above. Try re-running with --batch."
            )
        print(
            f"  {len(live_workers)}/{n_workers} workers ready in "
            f"{time.monotonic() - start_all:.1f}s",
            file=sys.stderr,
        )

        # Now that a worker is live, ask it for the real tool version and the
        # loaded library versions (e.g. MSL) and stamp them into the run
        # provenance. Best-effort — a failed probe leaves the configured
        # backend label as the shown version and omits the library line.
        updates: dict = {}
        version = self._safe_probe_worker_version(live_workers)
        if version:
            updates["tool_version"] = version
        libs = self._safe_probe_library_versions(live_workers)
        if libs:
            updates["library_versions"] = libs
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

    def _start_workers_parallel(self, workers: list["Worker"]) -> list["Worker"]:
        """Spawn every worker concurrently. Returns the subset that started
        successfully; failures are logged per-worker. Caller decides what
        to do on zero-live-workers (the template raises ``RuntimeError``).
        """
        worker_ready: dict[int, bool] = {}
        ready_lock = threading.Lock()

        def _start_one(w: "Worker"):
            t0 = time.monotonic()
            try:
                w.start()
                dt = time.monotonic() - t0
                with ready_lock:
                    worker_ready[w.worker_id] = True
                print(f"  Worker {w.worker_id}: ready ({dt:.1f}s)", file=sys.stderr)
            except Exception as exc:
                with ready_lock:
                    worker_ready[w.worker_id] = False
                print(
                    f"  Worker {w.worker_id}: start FAILED — {exc}",
                    file=sys.stderr,
                )
                # review 2026-07-06 (finding 24): a worker that failed mid-
                # start may already have spawned its subprocess; close it so
                # the process (and its license seat) doesn't leak. close() is
                # contractually idempotent + safe on never-started workers.
                try:
                    w.close()
                except Exception:
                    logger.debug(
                        "Worker %s: close() after failed start raised",
                        w.worker_id,
                        exc_info=True,
                    )

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(_start_one, w) for w in workers]
            for f in as_completed(futures):
                f.result()  # propagate unexpected errors; start errors already printed

        return [w for w in workers if worker_ready.get(w.worker_id)]

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

        def _try_restart(w: "Worker", count_against_budget: bool = True) -> bool:
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
            n_restarts = w._n_restarts
            if count_against_budget and n_restarts >= self.max_restarts_per_worker:
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
            backoff = self.restart_retry_backoff_seconds
            last_exc: Exception | None = None
            for attempt in (1, 2):
                if attempt == 2:
                    print(
                        f"  Worker {w.worker_id}: retrying restart in "
                        f"{backoff:.0f}s (one retry, then the worker is retired)",
                        file=sys.stderr,
                    )
                    time.sleep(backoff)
                    t0 = time.monotonic()
                try:
                    w.start()
                except Exception as exc:
                    last_exc = exc
                    print(
                        f"  Worker {w.worker_id}: restart FAILED — {exc}",
                        file=sys.stderr,
                    )
                    continue
                if count_against_budget:
                    print(
                        f"  Worker {w.worker_id}: restarted "
                        f"({time.monotonic() - t0:.1f}s, attempt {n_restarts}/"
                        f"{self.max_restarts_per_worker})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  Worker {w.worker_id}: restarted post-timeout "
                        f"({time.monotonic() - t0:.1f}s — not counted)",
                        file=sys.stderr,
                    )
                return True
            print(
                f"  Worker {w.worker_id}: restart failed twice "
                f"({type(last_exc).__name__}) — retiring the worker",
                file=sys.stderr,
            )
            return False

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

        def _worker_loop(w: "Worker"):
            # Track whether the previous test left the worker dead for a
            # benign reason (we deliberately hard-killed it after a per-
            # test timeout, or it was disk-rescued post-timeout). Such
            # restarts shouldn't count against the worker's permanent-
            # broken budget — the worker was healthy, the test was slow.
            prev_killed_worker = False
            try:
                while True:
                    item = work_queue.get()
                    if item is None:
                        work_queue.task_done()
                        return
                    test, test_key = item
                    restart_exhausted = False
                    try:
                        if not w.is_alive():
                            if not _try_restart(
                                w, count_against_budget=not prev_killed_worker
                            ):
                                # review 2026-07-06 (finding 20): fail-mark
                                # ONLY the item this thread already holds;
                                # exit the loop right after the finally so
                                # remaining tests go to live workers.
                                _fail_dead(
                                    test,
                                    test_key,
                                    "Worker dead; restart exhausted",
                                )
                                restart_exhausted = True
                            else:
                                prev_killed_worker = False  # consumed by restart
                        if not restart_exhausted:
                            if self.progress is not None:
                                self.progress.on_start(test_key, worker_id=w.worker_id)
                            timeout = float(
                                test.timeout
                                if test.timeout is not None
                                else self.config.timeout
                            )
                            try:
                                tr = w.run_test_with_timeout(
                                    test,
                                    test_key,
                                    timeout,
                                    progress=self.progress,
                                )
                            except Exception as exc:
                                # review 2026-07-06 (finding 21): the Worker
                                # ABC contract permits raises for worker-level
                                # catastrophes. A raise must be recorded — not
                                # kill the dispatch thread and silently drop
                                # the in-flight test (plus, at --parallel 1,
                                # the whole remaining queue). The next
                                # iteration's is_alive()/restart logic handles
                                # the possibly-dead worker.
                                logger.exception(
                                    "Worker %s raised while running %s",
                                    w.worker_id,
                                    test.model_id,
                                )
                                _record(
                                    test,
                                    test_key,
                                    TestRunResult(
                                        model_id=test.model_id,
                                        test_key=test_key,
                                        success=False,
                                        elapsed=0.0,
                                        error_message=f"worker exception: {exc!r}",
                                    ),
                                )
                                prev_killed_worker = False
                            else:
                                _record(test, test_key, tr)
                                # Reset the restart counter on a successful
                                # test — the worker has demonstrably recovered.
                                # Consecutive *real* worker deaths (translation
                                # broke / RPC dead) still accumulate to
                                # ``max_restarts_per_worker`` and trip restart-
                                # exhausted. Benign worker kills (timeout,
                                # post-timeout disk-rescue) flow through
                                # prev_killed_worker so they don't poison the
                                # counter.
                                if tr.success and not tr.worker_killed:
                                    w._n_restarts = 0
                                prev_killed_worker = bool(
                                    tr.timed_out or tr.worker_killed
                                )
                    finally:
                        work_queue.task_done()
                    if restart_exhausted:
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
                args=(w,),
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
