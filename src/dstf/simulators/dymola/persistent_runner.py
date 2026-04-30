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
    PersistentRunnerBase,
    TestRunResult,
    Worker,
    _print_progress,
    assign_test_keys,
)
from .log_parser import parse_dslog
from ..common.mat_reader import read_mat_time_extents
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


def _classify_translation_error(log_path: Path) -> str:
    """Best-effort classification of a Dymola translation failure.

    The full ``translation_log.txt`` is preserved as a per-test artifact;
    this helper extracts the most actionable summary line for the
    dashboard / per-test-report ``error_message`` field. Falls back to
    the generic "Translation failed" for unrecognized patterns. Pattern
    list grows organically — when a TRANSFORM run on a new platform
    surfaces a recurring failure category, add a clause here so users
    don't have to crack open the log file just to read the headline.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "Translation failed"
    if "model is too complex for the current license" in text:
        return "Translation failed: license tier too small for this model"
    lower = text.lower()
    if "class not found" in lower or "could not find class" in lower:
        return "Translation failed: missing class / undeclared dependency"
    return "Translation failed"


class DymolaWorker(Worker):
    """One live Dymola process wrapped as a test runner."""

    def __init__(
        self,
        worker_id: int,
        config: Config,
        dymola_config: DymolaConfig,
        dymola_interface_cls,
    ):
        super().__init__(worker_id, config)
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
            # DEBT: on Linux, simulator_path must point at the wrapper script
            # (e.g. /usr/local/bin/dymola), not the bare bin64/dymola binary.
            # The wrapper exports LD_LIBRARY_PATH so Dymola finds its bundled
            # libgit2/Qt6/Qtitan libs. Pointing at bin64/dymola directly fails
            # with confusing "shared library not found" + "Mismatching Dymola
            # version" errors — DymolaInterface only sees an unstartable child
            # process. Surface this as a friendlier check at config-resolution
            # time once we have a Linux-aware simulator-path validator.
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

    def run_test(
        self,
        test: TestModel,
        test_key: str,
        progress=None,
    ) -> TestRunResult:
        """Run one test in this worker. Returns the TestRunResult.

        Splits translateModel + simulateModel into separate calls so we can
        time them independently and emit per-phase progress events.
        """
        test_dir = self.config.work_dir / test_key
        # Fresh dir each time — prevents stale dsres.mat / dslog.txt bleed-through
        if test_dir.exists():
            shutil.rmtree(test_dir)
        test_dir.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        translation_wall: Optional[float] = None
        sim_wall: Optional[float] = None
        try:
            self.dymola.cd(str(test_dir))
            self.dymola.clearlog()

            # Phase 1: translate (may reuse cached translation — nearly free in that case)
            if progress is not None:
                progress.on_phase(test_key, "translating")
            t_trans_start = time.monotonic()
            translation_ok = bool(self.dymola.translateModel(test.model_id))
            translation_wall = time.monotonic() - t_trans_start

            if not translation_ok:
                # Save log, fail fast — skip the simulate call
                try:
                    self.dymola.savelog(str(test_dir / "translation_log.txt"))
                except Exception:
                    pass
                elapsed = time.monotonic() - start
                self._n_tests_run += 1
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=elapsed,
                    error_message=_classify_translation_error(
                        test_dir / "translation_log.txt"
                    ),
                    translation_wall=translation_wall,
                )

            # Phase 2: simulate (uses cached translation)
            if progress is not None:
                progress.on_phase(test_key, "simulating")
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

            t_sim_start = time.monotonic()
            # simulateModel returns bool; actual success/failure determined
            # by on-disk artifacts in _evaluate_from_disk
            _ = self.dymola.simulateModel(test.model_id, **call_kwargs)
            sim_wall = time.monotonic() - t_sim_start

            # Phase 3: finalize (savelog)
            if progress is not None:
                progress.on_phase(test_key, "finalizing")
            self.dymola.savelog(str(test_dir / "translation_log.txt"))
        except Exception as exc:  # DymolaException or connection loss
            elapsed = time.monotonic() - start
            self._n_tests_run += 1
            # Don't call savelog here — the JSON-RPC connection state is
            # indeterminate after Dymola raises mid-call. An attempted
            # savelog can complete partially, leaving the next test's RPC
            # ID mismatched with the prior savelog's response, which
            # poisons the worker for every subsequent test ("Mismatch
            # request/response ID in JSON-RPC call" cascade observed on
            # TRANSFORM/Linux). Translation-log preservation on this path
            # would require either a connection-health probe before the
            # savelog attempt or splitting the RPC client to discard
            # in-flight state on exception — both bigger than this branch
            # warrants. dslog.txt (written by Dymola itself during
            # simulation) is still the available diagnostic.
            msg = f"DymolaInterface error: {exc}"
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
                translation_wall=translation_wall,
                sim_wall=sim_wall,
            )

        elapsed = time.monotonic() - start
        self._n_tests_run += 1
        result = self._evaluate_from_disk(test, test_key, elapsed)
        # Attach phase timings to the result
        result.translation_wall = translation_wall
        result.sim_wall = sim_wall
        return result

    def export_fmu(self, test: TestModel, output_dir: Path) -> Path:
        """Export ``test.model_id`` as an FMU into ``output_dir`` (4.B.2).

        Uses Dymola's ``translateModelFMU`` API. Dymola produces the FMU in
        the current working directory; we cd to ``output_dir`` so it lands
        next to the chain's other artefacts, then return the resolved path.

        Requires the FMI export option in the Dymola license; raises a clear
        message on failure.
        """
        if self.dymola is None:
            raise RuntimeError(
                f"Worker {self.worker_id}: cannot export FMU before start()"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        self.dymola.cd(str(output_dir))
        # Dymola's translateModelFMU returns the FMU file path (without .fmu)
        # on success, or an empty string on failure. fmiVersion="2" is the
        # broadly compatible default; fmiType="all" gives both ME + CS.
        try:
            result = self.dymola.translateModelFMU(
                test.model_id,
                storeResult=False,
                modelName="",
                fmiVersion="2",
                fmiType="all",
                includeSource=False,
                includeImage=0,
            )
        except Exception as exc:
            raise RuntimeError(
                f"DymolaInterface.translateModelFMU failed for "
                f"{test.model_id}: {exc}"
            ) from exc
        if not result:
            raise RuntimeError(
                f"Dymola FMU export returned no path for {test.model_id} "
                f"(check Dymola license includes FMI export, and the model "
                f"translates successfully)"
            )
        # Dymola returns the basename without .fmu — append it.
        fmu_path = output_dir / f"{result}.fmu"
        if not fmu_path.exists():
            # Fallback: scan for any .fmu Dymola may have produced.
            candidates = list(output_dir.glob("*.fmu"))
            if not candidates:
                raise RuntimeError(
                    f"Dymola FMU export claimed success but no .fmu found "
                    f"in {output_dir} (returned: {result!r})"
                )
            fmu_path = candidates[0]
        return fmu_path.resolve()

    def _evaluate_from_disk(
        self,
        test: TestModel,
        test_key: str,
        elapsed: float,
    ) -> TestRunResult:
        """Inspect on-disk artifacts (dsres.mat + logs) and produce a
        TestRunResult. Used both on the normal happy path and as a fallback
        from `run_test_with_timeout` when the in-process flow couldn't
        finish (timeout / worker exception) but the simulation may have
        actually completed and written its mat file.
        """
        test_dir = self.config.work_dir / test_key
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

        translation_failed = False
        translation_log = test_dir / "translation_log.txt"
        if translation_log.exists():
            try:
                tlog = translation_log.read_text(encoding="utf-8", errors="replace")
                if "Translation aborted" in tlog or tlog.strip().endswith("= false"):
                    translation_failed = True
            except OSError:
                pass

        # A simulation truly completed only if BOTH:
        #   1. dsfinal.txt exists (Dymola writes it at end of successful sim)
        #   2. mat file's last time reaches the requested stop_time
        # dsres.mat existence alone is insufficient — Dymola writes it
        # incrementally, so a killed-mid-sim leaves a partial file.
        mat_path = test_dir / "dsres.mat"
        dsfinal_path = test_dir / "dsfinal.txt"
        success = False
        completion_msg: Optional[str] = None
        if not translation_failed and mat_path.exists() and dsfinal_path.exists():
            extents = read_mat_time_extents(mat_path)
            stop_time = float(test.stop_time)
            if extents is not None:
                last_time = extents[1]
                # Allow a tiny tolerance — output_interval may not align perfectly
                tol = max(1e-6, abs(stop_time) * 1e-6)
                if last_time + tol >= stop_time:
                    success = True
                else:
                    completion_msg = (
                        f"Stopped early at T={last_time:.6g} of {stop_time:.6g}"
                    )
            else:
                completion_msg = "dsres.mat unreadable"

        if success:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
                statistics=statistics,
            )

        # Build the most informative failure message we can
        if translation_failed:
            msg = "Translation failed"
        elif not mat_path.exists():
            msg = "No result file produced"
        elif not dsfinal_path.exists():
            msg = "Simulation aborted (no dsfinal.txt)"
        else:
            msg = completion_msg or "Simulation incomplete"

        dslog_path = test_dir / "dslog.txt"
        if dslog_path.exists():
            try:
                log_text = dslog_path.read_text(encoding="utf-8", errors="replace")
                if "ERROR" in log_text or "error" in log_text:
                    lines = log_text.strip().split("\n")
                    msg = msg + " | " + " | ".join(lines[-3:])
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
        progress=None,
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
                result_box[0] = self.run_test(test, test_key, progress=progress)
            except BaseException as e:
                exc_box[0] = e

        t = threading.Thread(target=_runner, daemon=True, name=f"dym-exec-{self.worker_id}")
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Hard-kill Dymola. Inner thread becomes daemon noise. Grace bumped
            # from 1.0s to 5.0s so Dymola has a real chance to flush its open
            # dslog.txt handle before psutil escalates to SIGKILL — diagnostic
            # loss on timeout was a real reported pain point on TRANSFORM/Linux.
            with _suppress_stderr_noise():
                self.close(grace=5.0)
                t.join(0.5)
            elapsed = time.monotonic() - start_ts

            # Even after a watchdog kill, the simulation may have completed
            # and written dsres.mat just before/during the kill. Trust disk.
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result.success:
                return disk_result

            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Timed out after {timeout:.0f}s",
                timed_out=True,
            )

        if exc_box[0] is not None:
            # Worker-level exception (e.g., DymolaInterface dropped its JSON-RPC
            # connection mid-call). Check whether the simulation completed
            # anyway — Dymola often crashes/disconnects after writing the result.
            # Both close() calls are wrapped in the urlopen-noise suppression so
            # stale RPC retries during teardown don't spam the terminal — same
            # pattern as the timeout path above.
            elapsed = time.monotonic() - start_ts
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result.success:
                # Worker connection is still considered broken — restart on next iter
                with _suppress_stderr_noise():
                    self.close(grace=1.0)
                return disk_result

            with _suppress_stderr_noise():
                self.close(grace=1.0)
            exc = exc_box[0]
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Worker exception: {type(exc).__name__}: {exc}",
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

class PersistentDymolaRunner(PersistentRunnerBase, DymolaRunner):
    """Dymola runner using persistent DymolaInterface workers + a queue.

    Inherits the dispatch-loop machinery from :class:`PersistentRunnerBase`
    and the batched :class:`DymolaRunner` for ``read_result`` + config
    extraction. Order matters — ``PersistentRunnerBase`` first so its
    ``run_tests`` template wins over the batch ``.mos`` runner's override.
    """

    worker_cls = DymolaWorker
    backend_label = "Dymola"

    @classmethod
    def preflight(cls, config) -> None:
        # Probes the Dymola Python interface (the wheel/egg shipped under
        # the Dymola install). Cheap — load_dymola_interface caches after
        # the first call. RuntimeError carries an install hint that the
        # CLI surfaces verbatim.
        # Lazy import inside the method so tests can monkeypatch
        # ``interface_loader.load_dymola_interface`` and have the patch
        # take effect here.
        from .interface_loader import load_dymola_interface as _load
        _load(config.dymola_interface_path)

    def setup_before_workers(self) -> None:
        # Cache the DymolaInterface class for make_worker, then apply
        # Dymola-specific runtime patches. These two patches must run
        # before any worker spawns:
        #   * _install_dymola_log_filter — silences DymolaInterface's
        #     own urlopen-spam during forced timeout teardowns.
        #   * _patch_dymola_for_parallel_startup — narrows DymolaInterface's
        #     module-level startup lock so workers can launch concurrently
        #     (~7s × N serialized → all workers in ~7s wall).
        self._di_cls = load_dymola_interface(self.config.dymola_interface_path)
        _install_dymola_log_filter()
        _patch_dymola_for_parallel_startup()

    def make_worker(self, worker_id: int) -> DymolaWorker:
        return DymolaWorker(
            worker_id, self.config, self.dymola_config, self._di_cls,
        )

    def export_fmu(self, test: TestModel, output_dir: Path) -> Path:
        """Spin up a one-shot worker to export a single FMU (4.B.2).

        Heavier than reusing a worker from the pool, but keeps the export
        path independent of the pool's lifecycle (so 4.B's cross-backend
        chain can call this from arbitrary places). Optimization: have the
        chain orchestration reuse an idle worker via a future helper.

        VALIDATION CAVEAT: requires Windows + Dymola + the FMI export
        license option. Cannot be exercised in CI on Linux WSL; tests use
        a mock DymolaInterface.
        """
        di_cls = load_dymola_interface(self.config.dymola_interface_path)
        _patch_dymola_for_parallel_startup()
        worker = DymolaWorker(
            worker_id=-1,
            config=self.config,
            dymola_config=self.dymola_config,
            dymola_interface_cls=di_cls,
        )
        worker.start()
        try:
            return worker.export_fmu(test, output_dir)
        finally:
            try:
                if worker.dymola is not None:
                    worker.dymola.close()
            except Exception:
                pass

    # run_tests is inherited from PersistentRunnerBase.
