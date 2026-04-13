"""Persistent-worker Dymola runner using the Python interface.

Unlike the default batched runner (which launches Dymola per batch and drives
it via generated `.mos` scripts), this runner keeps N long-lived
`DymolaInterface` processes alive for the whole run. Each worker loads the
library once and then pulls tests off a shared queue.

Benefits:
  - Library-load overhead paid once per worker, never again
  - Per-test granularity: start/finish events fire as each test transitions,
    no "batch jumps together" visibility gap
  - Natural work-stealing: whichever worker finishes first grabs the next test
  - Per-test timeout isolation: a hung test kills one worker (we restart it)
    while the others keep running

Gated behind the `--persistent` CLI flag; the default batched runner is
preserved untouched.
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ...config import Config
from ...discovery.test_registry import TestModel
from ..base import (
    BatchManifest,
    TestRunResult,
    _print_progress,
    assign_test_keys,
)
from .log_parser import parse_dslog
from .runner import DymolaRunner, DymolaConfig
from .interface_loader import load_dymola_interface

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stderr noise suppression
# ---------------------------------------------------------------------------

_parallel_startup_patched = False


def _patch_dymola_for_parallel_startup() -> None:
    """Narrow Dymola's broad startup lock so workers can launch in parallel.

    `dymola.dymola_interface_internal.dymola_lock` is held for the entire
    `DymolaInterface.__init__`, which includes `_check_dymola` — an HTTP
    ping retry loop that waits for the just-spawned Dymola to come up
    (~7s per worker). With this lock in place, N workers all serialize on
    startup, so 10 workers take 10×7 = 70s instead of ~7s.

    The genuinely shared state is just port selection (otherwise two workers
    could pick the same random port and collide). We narrow the critical
    section to just `_find_available_port` and replace the broad lock with
    a no-op so the slow per-worker waits overlap.
    """
    global _parallel_startup_patched
    if _parallel_startup_patched:
        return
    try:
        import dymola.dymola_interface_internal as _di  # type: ignore[import-not-found]
    except ImportError:
        return

    _port_lock = threading.Lock()
    _orig_find_port = _di.DymolaInterfaceInternal._find_available_port

    def _safe_find_port(self):
        with _port_lock:
            return _orig_find_port(self)

    _di.DymolaInterfaceInternal._find_available_port = _safe_find_port  # type: ignore[method-assign]

    class _NoLock:
        def acquire(self, blocking=True, timeout=-1):
            return True

        def release(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    _di.dymola_lock = _NoLock()  # type: ignore[attr-defined]
    _parallel_startup_patched = True


_NOISE_PATTERNS = (
    "WinError 10061",
    "WinError 10054",
    "urlopen error",
)

_suppress_until = 0.0  # monotonic deadline
_dymola_logger_patched = False


def _install_dymola_log_filter() -> None:
    """Monkey-patch Dymola's logger so noise from its internal HTTP retries
    (WinError 10061/10054 / urlopen error) can be muted during our
    suppression window.

    Dymola's `DymolaLogger._PrintMessage` uses `print(msg)` — so messages
    go to stdout, not stderr. Patching the logger itself is cleaner than
    filtering stdout globally.
    """
    global _dymola_logger_patched
    if _dymola_logger_patched:
        return
    try:
        from dymola.dymola_interface_internal import DymolaLogger  # type: ignore[import-not-found]
    except ImportError:
        return  # interface not loaded yet; will be retried on next call
    orig = DymolaLogger._PrintMessage

    def _filtered_print(level, msg):
        text = str(msg)
        if time.monotonic() < _suppress_until and any(p in text for p in _NOISE_PATTERNS):
            return
        orig(level, msg)

    DymolaLogger._PrintMessage = staticmethod(_filtered_print)  # type: ignore[method-assign]
    _dymola_logger_patched = True


class _suppress_stderr_noise:
    """Mute known-noisy Dymola log lines for a short grace period.

    Extends any currently-active window rather than overwriting it, so
    overlapping timeouts from multiple workers still benefit.
    """

    def __init__(self, grace: float = 12.0):
        self.grace = grace

    def __enter__(self):
        global _suppress_until
        _install_dymola_log_filter()
        deadline = time.monotonic() + self.grace
        if deadline > _suppress_until:
            _suppress_until = deadline
        return self

    def __exit__(self, exc_type, exc, tb):
        return False  # don't suppress exceptions — noise window expires naturally


class DymolaWorker:
    """One live Dymola process wrapped as a test runner."""

    def __init__(
        self,
        worker_id: int,
        config: Config,
        dymola_config: DymolaConfig,
        dymola_interface_cls,
    ):
        self.worker_id = worker_id
        self.config = config
        self.dymola_config = dymola_config
        self._DI = dymola_interface_cls
        self.dymola = None  # the DymolaInterface instance
        self.pids: set[int] = set()  # PIDs attributed to this worker's Dymola
        self._n_tests_run = 0
        self._n_restarts = 0

    def start(self) -> None:
        """Launch Dymola and apply startup: settings + library loads.

        Launches run fully in parallel — PID attribution is read directly
        from DymolaInterface's internal subprocess handle, so we never need
        to serialize around a process-set snapshot.
        """
        kwargs: dict = {"showwindow": bool(self.dymola_config.show_ide)}
        if self.config.simulator_path:
            kwargs["dymolapath"] = str(self.config.simulator_path)

        self.dymola = self._DI(**kwargs)
        # DymolaInterfaceInternal stores its subprocess as `_dymola_process`
        # (a subprocess.Popen). Fall back to an empty set if the attribute
        # ever moves — kill-by-pid just becomes best-effort in that case.
        proc = getattr(self.dymola, "_dymola_process", None)
        self.pids = {proc.pid} if proc is not None and getattr(proc, "pid", None) else set()

        # Establish working directory
        self.dymola.cd(str(self.config.work_dir))

        # Load dependencies
        for dep_path in self.config.dependencies or []:
            dep_pkg = Path(dep_path).resolve() / "package.mo"
            ok = self.dymola.openModel(str(dep_pkg))
            if not ok:
                raise RuntimeError(
                    f"Worker {self.worker_id}: failed to load dependency "
                    f"{dep_pkg} — {self._last_error()}"
                )

        # Load main library
        main_pkg = self.config.library_dir / "package.mo"
        ok = self.dymola.openModel(str(main_pkg))
        if not ok:
            raise RuntimeError(
                f"Worker {self.worker_id}: failed to load library {main_pkg} "
                f"— {self._last_error()}"
            )

        # Framework settings (mirror startup.mos). ExecuteCommand returns the
        # value of the command (e.g., `true` for an assignment), not a
        # success/failure indicator — we only detect a real failure by the
        # Dymola error log.
        self._exec_setting("OutputCPUtime := true;")
        self._exec_setting("Advanced.UI.TranslationInCommandLog := true;")

        # User setup commands — warn on failure, don't abort
        for cmd in self.dymola_config.simulator_setup or []:
            cmd = cmd.strip()
            if not cmd.endswith(";"):
                cmd += ";"
            self._exec_setting(cmd)

    def run_test(self, test: TestModel, test_key: str) -> TestRunResult:
        """Run one test in this worker. Returns the TestRunResult."""
        test_dir = self.config.work_dir / test_key
        # Fresh dir each time — prevents stale dsres.mat / dslog.txt bleed-through
        if test_dir.exists():
            shutil.rmtree(test_dir)
        test_dir.mkdir(parents=True, exist_ok=True)

        short_name = test.model_id.rsplit(".", 1)[-1]
        label = f"{test_key} {short_name}"

        start = time.monotonic()
        translation_failed = False
        sim_ok = False
        try:
            self.dymola.cd(str(test_dir))
            self.dymola.clearlog()

            # Arguments — mirror _generate_test_mos
            call_kwargs: dict = {
                "method": test.method,
                "tolerance": test.tolerance,
                "resultFile": "dsres",
            }
            if test.stop_time != 1.0:
                call_kwargs["stopTime"] = test.stop_time
            if test.number_of_intervals is not None:
                call_kwargs["numberOfIntervals"] = test.number_of_intervals
            elif test.output_interval is not None:
                call_kwargs["outputInterval"] = test.output_interval

            sim_ok = bool(self.dymola.simulateModel(test.model_id, **call_kwargs))
            # Persist translation log so post-hoc analysis matches batch mode
            self.dymola.savelog(str(test_dir / "translation_log.txt"))
        except Exception as exc:  # DymolaException or connection loss
            elapsed = time.monotonic() - start
            self._n_tests_run += 1
            msg = f"DymolaInterface error: {exc}"
            _print_progress(
                self.worker_id + 1, 0, label, "FAIL", elapsed=elapsed, detail=msg[:80],
            )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
            )

        elapsed = time.monotonic() - start
        self._n_tests_run += 1

        # Evaluate against disk exactly like the batch path
        statistics = parse_dslog(test_dir / "dslog.txt")
        translation_stats = parse_dslog(test_dir / "translation_log.txt")
        if translation_stats:
            if statistics is None:
                statistics = translation_stats
            else:
                for k, v in translation_stats.items():
                    if k not in statistics:
                        statistics[k] = v
                    elif isinstance(v, dict) and isinstance(statistics[k], dict):
                        statistics[k].update(v)
                    else:
                        statistics[k] = v

        translation_log = test_dir / "translation_log.txt"
        if translation_log.exists():
            try:
                tlog = translation_log.read_text(encoding="utf-8", errors="replace")
                if "Translation aborted" in tlog or tlog.strip().endswith("= false"):
                    translation_failed = True
            except OSError:
                pass

        mat_path = test_dir / "dsres.mat"
        if mat_path.exists() and not translation_failed:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
                statistics=statistics,
            )

        msg = "Translation failed" if translation_failed else "No result file produced"
        dslog_path = test_dir / "dslog.txt"
        if dslog_path.exists():
            try:
                log_text = dslog_path.read_text(encoding="utf-8", errors="replace")
                if "ERROR" in log_text or "error" in log_text:
                    lines = log_text.strip().split("\n")
                    msg = " | ".join(lines[-3:])
            except OSError:
                pass

        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=msg,
            statistics=statistics,
        )

    def close(self, grace: float = 5.0) -> None:
        """Terminate the Dymola subprocess. Tries graceful close first, then
        hard-kills tracked PIDs via psutil if close hangs or fails.
        Safe to call multiple times; idempotent.
        """
        d = self.dymola
        self.dymola = None
        if d is not None:
            done = threading.Event()
            def _try():
                try:
                    d.close()
                except Exception:
                    pass
                finally:
                    done.set()
            t = threading.Thread(target=_try, daemon=True)
            t.start()
            done.wait(grace)
        self._kill_tracked_pids()

    def _kill_tracked_pids(self) -> None:
        """Force-kill any still-running Dymola process we attributed to this worker."""
        if not self.pids:
            return
        import psutil
        for pid in list(self.pids):
            try:
                p = psutil.Process(pid)
                if "dymola" in (p.name() or "").lower():
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.pids = set()

    def is_alive(self) -> bool:
        return self.dymola is not None

    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
    ) -> TestRunResult:
        """Run the test with a watchdog. On timeout, hard-kill Dymola.

        After a timeout or worker-level exception, `self.dymola` is None and
        the worker must be restarted before further tests are dispatched.
        """
        result_box: list[Optional[TestRunResult]] = [None]
        exc_box: list[Optional[BaseException]] = [None]
        start_ts = time.monotonic()

        def _runner():
            try:
                result_box[0] = self.run_test(test, test_key)
            except BaseException as e:
                exc_box[0] = e

        t = threading.Thread(target=_runner, daemon=True, name=f"dym-exec-{self.worker_id}")
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Timed out — hard-kill Dymola so the inner thread unblocks.
            # We intentionally don't wait long for the inner thread: it's a
            # daemon and will die on process exit. Dymola's Python interface
            # retries internal HTTP calls for several seconds after the
            # process is killed, emitting "WinError 10061" / "urlopen error"
            # on stderr — muting stderr briefly swallows that spam.
            with _suppress_stderr_noise():
                self.close(grace=1.0)
                t.join(0.5)
            elapsed = time.monotonic() - start_ts
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Timed out after {timeout:.0f}s",
                timed_out=True,
            )

        if exc_box[0] is not None:
            # Unexpected exception — assume the Dymola connection is broken
            self.close(grace=2.0)
            elapsed = time.monotonic() - start_ts
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Worker exception: {exc_box[0]}",
            )

        return result_box[0]  # type: ignore[return-value]

    # -----------------------------------------------------------------
    def _exec_setting(self, cmd: str) -> None:
        """Run a Modelica settings command. ExecuteCommand returns the value
        of the command (e.g., `true` for an assignment), which we can't use
        as a success indicator. Only log a warning if the Dymola error log
        picks up an issue.
        """
        assert self.dymola is not None
        try:
            self.dymola.ExecuteCommand(cmd)
        except Exception as exc:  # pragma: no cover — diagnostic path
            logger.warning(
                "Worker %s: setting '%s' raised: %s", self.worker_id, cmd, exc,
            )
            return
        err = self._last_error()
        if err and ("Error" in err or "error:" in err.lower()):
            logger.warning(
                "Worker %s: setting '%s' reported: %s",
                self.worker_id, cmd, err.strip()[:200],
            )

    def _last_error(self) -> str:
        try:
            return str(self.dymola.getLastErrorLog()) if self.dymola else ""
        except Exception:
            return ""


# ---------------------------------------------------------------------------

class PersistentDymolaRunner(DymolaRunner):
    """Dymola runner using persistent DymolaInterface workers + a queue.

    Subclasses the batched DymolaRunner so it inherits `read_result` and config
    extraction; only overrides `run_tests`.
    """

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        if not tests:
            return []

        # Import lazily so the dependency only matters in --persistent mode.
        di_cls = load_dymola_interface(self.config.dymola_interface_path)
        # Patch Dymola's logger so timeout-noise suppression takes effect
        # from the first timeout, and narrow its module-level startup lock
        # so workers can launch in parallel (instead of ~7s × N serialized).
        _install_dymola_log_filter()
        _patch_dymola_for_parallel_startup()

        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        total = len(tests)

        from ..progress import ProgressReporter
        self.progress = ProgressReporter(self.config.work_dir, total)

        # Persistent keys — same behavior as batched runner
        manifest_map, test_items = assign_test_keys(self.config.work_dir, tests)
        for test, test_key in test_items:
            report_dir = self.ref_id_map.get(test.model_id) or test_key
            self.progress.register(test_key, test.model_id, report_dir=report_dir)

        manifest = BatchManifest(
            batch_id=0,
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        manifest.save()

        n_workers = max(1, self.config.parallel)
        print(
            f"Running {total} tests via persistent workers "
            f"(parallel={n_workers}, timeout={self.config.timeout}s/test)",
            file=sys.stderr,
        )
        dashboard = self.config.work_dir / "dashboard.html"
        print(f"Live progress: {dashboard.resolve().as_uri()}", file=sys.stderr)

        # Start workers concurrently — each pays the library-load cost once.
        workers: list[DymolaWorker] = [
            DymolaWorker(i, self.config, self.dymola_config, di_cls)
            for i in range(n_workers)
        ]

        start_all = time.monotonic()
        print(f"Starting {n_workers} Dymola worker(s)...", file=sys.stderr)

        worker_ready: dict[int, bool] = {}
        ready_lock = threading.Lock()

        def _start_one(w: DymolaWorker):
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
                print(f"  Worker {w.worker_id}: start FAILED — {exc}", file=sys.stderr)

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_start_one, w) for w in workers]
            for f in as_completed(futures):
                f.result()  # propagate unexpected errors; start errors already printed

        live_workers = [w for w in workers if worker_ready.get(w.worker_id)]
        if not live_workers:
            print("All workers failed to start. Aborting.", file=sys.stderr)
            if self.progress is not None:
                self.progress.finalize()
            return [manifest]
        print(
            f"  {len(live_workers)}/{n_workers} workers ready in "
            f"{time.monotonic() - start_all:.1f}s",
            file=sys.stderr,
        )

        # Dispatch: shared queue, one thread per live worker.
        # Workers that die mid-run get one restart attempt per test.
        MAX_RESTARTS_PER_WORKER = 3
        di_cls_local = di_cls

        work_queue: queue.Queue[Optional[tuple]] = queue.Queue()
        for item in test_items:
            work_queue.put(item)
        for _ in live_workers:
            work_queue.put(None)  # sentinel per worker

        results: list[TestRunResult] = []
        results_lock = threading.Lock()
        completed = [0]

        def _record(test, test_key, tr: TestRunResult) -> None:
            with results_lock:
                results.append(tr)
                completed[0] += 1
                idx = completed[0]
            label = f"{test_key} {test.model_id.rsplit('.', 1)[-1]}"
            status = "ok" if tr.success else ("TIMEOUT" if tr.timed_out else "FAIL")
            _print_progress(
                idx, total, label, status,
                elapsed=tr.elapsed,
                detail=None if tr.success else (tr.error_message or "")[:80],
            )
            if self.progress is not None:
                self.progress.on_finish(
                    test_key, success=tr.success, elapsed=tr.elapsed,
                    detail=None if tr.success else (tr.error_message or "")[:120],
                    timed_out=tr.timed_out,
                )

        def _try_restart(w: DymolaWorker) -> bool:
            if w._n_restarts >= MAX_RESTARTS_PER_WORKER:
                return False
            w._n_restarts += 1
            t0 = time.monotonic()
            try:
                w.start()
                print(
                    f"  Worker {w.worker_id}: restarted "
                    f"({time.monotonic() - t0:.1f}s, attempt {w._n_restarts}/"
                    f"{MAX_RESTARTS_PER_WORKER})",
                    file=sys.stderr,
                )
                return True
            except Exception as exc:
                print(
                    f"  Worker {w.worker_id}: restart FAILED — {exc}",
                    file=sys.stderr,
                )
                return False

        def _worker_loop(w: DymolaWorker):
            while True:
                item = work_queue.get()
                if item is None:
                    work_queue.task_done()
                    return
                test, test_key = item
                try:
                    # Ensure worker is alive; restart if needed
                    if not w.is_alive():
                        if not _try_restart(w):
                            _record(test, test_key, TestRunResult(
                                model_id=test.model_id, test_key=test_key,
                                success=False, elapsed=0.0,
                                error_message="Worker dead; restart exhausted",
                            ))
                            continue

                    if self.progress is not None:
                        self.progress.on_start(test_key, worker_id=w.worker_id)

                    timeout = float(test.timeout if test.timeout is not None
                                    else self.config.timeout)
                    tr = w.run_test_with_timeout(test, test_key, timeout)
                    _record(test, test_key, tr)
                    # After a timeout or worker exception, is_alive() is False;
                    # restart happens lazily on the next iteration.
                finally:
                    work_queue.task_done()

        threads = [
            threading.Thread(target=_worker_loop, args=(w,), name=f"dym-pworker-{w.worker_id}")
            for w in live_workers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Tear down workers (library unload + process exit)
        for w in workers:
            w.close()

        wall = time.monotonic() - start_all
        total_work = sum(r.elapsed for r in results)
        speedup = (total_work / wall) if wall > 0 else 0.0
        n_ok = sum(1 for r in results if r.success)
        n_timeout = sum(1 for r in results if r.timed_out)
        n_fail = total - n_ok - n_timeout
        print(file=sys.stderr)
        print(
            f"Persistent run complete: {n_ok} ok, {n_fail} failed, {n_timeout} timed out "
            f"({wall:.0f}s wall, {total_work:.0f}s total work, {speedup:.1f}x parallel speedup)",
            file=sys.stderr,
        )

        manifest.results = results
        if self.progress is not None:
            self.progress.finalize()
        return [manifest]
