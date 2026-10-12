"""Persistent-worker Julia backend (D78).

Each worker is a long-lived ``julia --project=<dir> run_persistent.jl``
subprocess that reads JSON-per-line test requests from stdin and writes
JSON responses to stdout. Package load (``using ModelingToolkit``,
``OrdinaryDiffEq``, ``JSON3``) happens **once per worker** at startup.
Per-test cost becomes just ``include`` + ``structural_simplify`` + ``solve``.

Why this matters — the batch path (one subprocess per test) pays:
  * Julia startup + `using` cost: 20-40s each time (cached .ji reads).
  * Per-model JIT codegen: 10-60s each time.

Persistent workers reclaim both. Matches the Dymola / OpenModelica
persistent-worker pattern (shared queue + dispatch-thread-per-worker
+ watchdog + up-to-3 worker restarts on catastrophic failure).

A warmup phase runs before any test wall-time starts: the first worker
is kept waiting until its ``ready`` event fires on stdout, signalling
that ``using`` has completed. Only then do we treat the next line as
a test response. This way the per-test timing reported in the dashboard
is just compile/solve of the user model, not the one-time package load
that would have happened regardless of the test set.

This is the default backend; pass ``--batch`` to opt out. There is no
automatic fallback: if Julia's dependencies aren't ready (no Manifest.toml,
failed `using`), ``preflight`` raises RuntimeError and the CLI aborts with
a message naming both fixes — same pattern as Dymola and OpenModelica (see
``backends.select_backend``).
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ...config import Config
from ...discovery.test_registry import TestModel
from ..common.json_driver import resolve_source_file
from ..persistent import STARTUP_TIMEOUT_HINT, PersistentBackendBase, Worker
from ..types import Capability, TestRunResult, timeout_message
from .backend import JuliaBackend, JuliaConfig

logger = logging.getLogger(__name__)

_PERSISTENT_DRIVER_PATH = Path(__file__).resolve().parent / "run_persistent.jl"

# Max seconds to wait for a hard-killed worker to actually exit before we
# stop caring — the handle is dropped either way.
_KILL_EXIT_TIMEOUT = 5.0


def _close_pipes(proc: subprocess.Popen) -> None:
    """Close a finished worker's pipes. Best-effort: the reader threads may
    still be unwinding on the other end."""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream:
            try:
                stream.close()
            except Exception:
                pass


class JuliaWorker(Worker):
    """One long-lived Julia subprocess.

    Owns a subprocess + a pair of reader threads (stdout / stderr) that
    push lines onto internal Queues. ``run_test_with_timeout`` writes a
    request, waits for the response with a timeout, and hard-kills the
    worker if it hangs.
    """

    kill_name_hint = "julia"

    def __init__(self, worker_id: int, config: Config, julia_cfg: JuliaConfig):
        super().__init__(worker_id, config)
        self.julia_cfg = julia_cfg
        self.proc: subprocess.Popen | None = None
        self._stdout_q: queue.Queue[str] = queue.Queue()
        self._stderr_buf: list[str] = []
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    # ---------------------------------------------------------------

    def start(self) -> None:
        """Spawn the subprocess, read the initial 'ready' pulse."""
        cmd = [
            str(self.julia_cfg.julia_binary),
            f"--project={self.julia_cfg.project_dir}",
            "--startup-file=no",
            "--color=no",
            str(_PERSISTENT_DRIVER_PATH),
        ]
        logger.debug("Worker %d cmd: %s", self.worker_id, " ".join(cmd))
        # Fresh queue per process. start() also runs on restart, and the killed
        # process's reader thread may have left lines behind — including its
        # 'ready' pulse, which would satisfy _wait_for_ready before the new
        # process has even loaded. The old reader keeps its own reference to
        # the orphaned queue, so it can't contaminate this one either.
        self._stdout_q = queue.Queue()
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # review 2026-07-06 (finding 75): pin utf-8 + replace — on cp1252
            # Windows a UTF-8 Julia backtrace killed the reader thread and
            # every subsequent test on this worker then timed out.
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
            cwd=str(self.julia_cfg.project_dir),
        )
        self._track_launch(self.proc.pid)
        # MTK + OrdinaryDiffEq: ~15-40 s from the precompiled cache, minutes
        # on a first run.
        self._step = "its package load"

        self._stdout_thread = threading.Thread(
            target=self._reader_loop,
            args=(self.proc.stdout, self._stdout_q),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_drain,
            args=(self.proc.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()

        # Wait for the 'ready' event. This is the worker's warmup —
        # `using` is finishing. Dispatching tests before this point would
        # queue them behind the package load and bill it to the first test.
        bound = self.startup_bound()
        ready = self._wait_for_ready(timeout=bound)
        if not ready:
            # Leave the stderr buffer around so the caller can surface it.
            self.close(grace=0.5)
            raise RuntimeError(
                f"Julia worker {self.worker_id} did not emit ready within "
                f"{bound:g} s {STARTUP_TIMEOUT_HINT}. stderr tail: "
                f"{self._stderr_tail(6)}"
            )
        self._step = ""

    # ---------------------------------------------------------------

    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        progress: object | None = None,
        before: Callable[[], None] | None = None,
    ) -> TestRunResult:
        """Dispatch one test, wait ``timeout`` seconds for the response.

        ``progress`` is accepted for shape compatibility with the other
        backends' workers (Dymola/OM emit ``on_phase`` updates from inside
        the worker). Julia today reports phases via the dispatch loop's
        ``_record`` helper instead, so this argument is currently ignored.
        ``before`` runs first, as the base's does; nothing bounds it here —
        the deadline below starts with the request.

        Overrides the base watchdog: the response arrives on a queue, so a
        deadline poll reads it directly instead of parking a blocking call
        on an exec thread the watchdog would then have to abandon. The kill
        and the restart-accounting flags are the shared ones, and so is the
        cancel (``Worker.request_cancel``): the poll sees it within a tick.
        The driver has no stop short of its process, so a cancel is the
        timeout's kill, uncharged.
        """
        if self.proc is None or self.proc.poll() is not None:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message="Julia worker not running",
            )
        if before is not None:
            before()
        user_file = resolve_source_file(test, self.config, JuliaBackend.source_suffix)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"Julia source not found for {test.model_id}. "
                    f"Ensure the test_spec.json 'source' field resolves to a .jl file."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)
        result_path = test_dir / "result.json"

        request = {
            "user_file": str(user_file.resolve()),
            "stop_time": float(test.stop_time),
            "tolerance": float(test.tolerance),
            "result_path": str(result_path),
            "test_key": test_key,
        }
        wall_start = time.monotonic()
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=f"Julia worker stdin broken: {exc}",
                elapsed=time.monotonic() - wall_start,
            )

        deadline = wall_start + timeout
        while True:
            remaining = deadline - time.monotonic()
            if self._cancel is not None:
                self._kill_tracked_pids()
                self._await_killed_process()
                elapsed = time.monotonic() - wall_start
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=elapsed,
                    sim_wall=elapsed,
                    error_message="Cancelled",
                    cancelled=True,
                    worker_killed=True,
                )
            if remaining <= 0:
                self._kill_tracked_pids()
                self._await_killed_process()
                elapsed = time.monotonic() - wall_start
                # Persist the stderr tail for diagnosis.
                (test_dir / "julia_stderr.txt").write_text(
                    self._stderr_tail(30),
                    encoding="utf-8",
                )
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=elapsed,
                    sim_wall=elapsed,
                    error_message=timeout_message(
                        f"Julia simulation exceeded {timeout}s timeout"
                    ),
                    timed_out=True,
                    # We killed a *healthy* worker because the test was slow;
                    # the dispatch loop's benign-restart accounting keys on
                    # ``timed_out or worker_killed``. Same semantics as the
                    # shared watchdog's timeout branch.
                    worker_killed=True,
                )
            try:
                line = self._stdout_q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                # Subprocess might have crashed; check its liveness.
                if self.proc.poll() is not None:
                    elapsed = time.monotonic() - wall_start
                    (test_dir / "julia_stderr.txt").write_text(
                        self._stderr_tail(30),
                        encoding="utf-8",
                    )
                    return TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        elapsed=elapsed,
                        sim_wall=elapsed,
                        error_message="Julia worker crashed mid-test",
                    )
                continue

            # Skip non-test events (stray 'ready' pulses from "cmd:ready").
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("worker %d: non-JSON stdout: %r", self.worker_id, line)
                continue
            if "event" in resp and "test_key" not in resp:
                continue
            # Response for this test.
            if resp.get("test_key") != test_key:
                logger.debug(
                    "worker %d: test_key mismatch %s != %s; ignoring",
                    self.worker_id,
                    resp.get("test_key"),
                    test_key,
                )
                continue
            elapsed = time.monotonic() - wall_start
            sim_wall = float(resp.get("elapsed", elapsed))
            status = resp.get("status")
            if status == "ok":
                # Persist a minimal stdout artifact for report parity.
                (test_dir / "julia_stdout.txt").write_text(
                    f"// PersistentJuliaBackend worker={self.worker_id}\n"
                    f"test_key={test_key}\nelapsed={sim_wall:.3f}s\n"
                    f"n_vars={resp.get('n_vars', 0)}\n"
                    f"n_time={resp.get('n_time', 0)}\n",
                    encoding="utf-8",
                )
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=True,
                    elapsed=elapsed,
                    sim_wall=sim_wall,
                    statistics={"simulation": {"wall_time": sim_wall}},
                )
            # status == "fail" (or anything else)
            err = resp.get("error", "unknown error")
            (test_dir / "julia_stderr.txt").write_text(
                err + "\n\n--- worker stderr ---\n" + self._stderr_tail(30),
                encoding="utf-8",
            )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                sim_wall=sim_wall,
                error_message=(
                    err.splitlines()[0] if err else "Julia simulation failed"
                ),
            )

    # ---------------------------------------------------------------

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _await_killed_process(self) -> None:
        """Wait out the kill, then drop the handle.

        ``kill_process_tree`` returns before the OS has torn the process
        down, so ``proc.poll()`` can still read alive for a moment after
        it. A worker that reads alive gets the next test dispatched
        straight to it: that test then fails "crashed mid-test" — a
        result carrying no ``worker_killed`` flag, so the restart it
        forces is charged against ``max_restarts_per_worker``. Reaping
        the process here, and detaching it either way, makes
        ``is_alive()`` False before we return so the dispatch loop
        restarts *before* handing us more work.
        """
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        if proc.poll() is None:
            # Belt and braces: kill_process_tree is a no-op whenever psutil
            # is missing, the name guard misses, or the walk hits
            # AccessDenied. We own this Popen — use it.
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        try:
            proc.wait(timeout=_KILL_EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Worker %d: killed julia process %d has not exited after %.0fs",
                self.worker_id,
                proc.pid,
                _KILL_EXIT_TIMEOUT,
            )
            # Only stdin. The reader threads are parked in readline() holding
            # the BufferedReader lock, so closing stdout/stderr here blocks
            # the dispatch thread until the child finally exits — at
            # --parallel 1 that is the whole run hanging on the one process
            # we already failed to kill. The readers close their own ends
            # when the pipe EOFs.
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            return
        _close_pipes(proc)

    def _detach_interface(self) -> object | None:
        proc = self.proc
        self.proc = None
        return proc

    def _shutdown_interface(self, handle: object, grace: float) -> None:
        """Ask the driver to quit, wait for it, then close the pipes.

        A driver that misses *grace* is killed here through the Popen we
        own: the template's PID kill is the only other teardown, and it is
        a no-op whenever psutil is missing or the process name no longer
        matches ``kill_name_hint``.
        """
        proc: subprocess.Popen = handle  # type: ignore[assignment]
        if proc.poll() is None and proc.stdin is not None:
            try:
                proc.stdin.write('{"cmd": "quit"}\n')
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        _close_pipes(proc)

    # ---------------------------------------------------------------

    def _reader_loop(self, stream, q: queue.Queue) -> None:
        # The reader owns this end: it holds the BufferedReader lock inside
        # readline(), so nobody else can safely close it. Closing here on
        # EOF is what lets the teardown paths leave it alone.
        try:
            for line in iter(stream.readline, ""):
                q.put(line.strip())
        except Exception as exc:
            logger.debug("worker %d reader error: %s", self.worker_id, exc)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _stderr_drain(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                line = line.rstrip("\n")
                # Keep a bounded tail for diagnostics.
                self._stderr_buf.append(line)
                if len(self._stderr_buf) > 200:
                    self._stderr_buf = self._stderr_buf[-200:]
        except Exception:
            pass
        finally:
            try:
                stream.close()  # see _reader_loop: the reader owns this end
            except Exception:
                pass

    def _stderr_tail(self, n: int = 20) -> str:
        return "\n".join(self._stderr_buf[-n:])

    def _wait_for_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self._stdout_q.get(timeout=0.5)
            except queue.Empty:
                if self.proc and self.proc.poll() is not None:
                    return False
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "ready":
                return True
        return False


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class PersistentJuliaBackend(PersistentBackendBase, JuliaBackend):
    """Persistent-worker Julia backend.

    Inherits dispatch-loop machinery from :class:`PersistentBackendBase`
    and ``read_result`` + config resolution from the batch
    :class:`JuliaBackend`. Order matters — ``PersistentBackendBase`` first so
    its ``run_tests`` template wins over the batch backend's per-test
    override.
    """

    capabilities = frozenset(
        {
            Capability.PERSISTENT_WORKERS,
            Capability.BATCH_FALLBACK,
            # The worker's: its poll loop honours Worker.request_cancel.
            Capability.CANCEL,
        }
    )

    worker_cls = JuliaWorker
    backend_label = "Julia"

    @classmethod
    def preflight(cls, config) -> None:
        # Cheap probe: does `julia` resolve on PATH? Subsequent failures
        # (toml errors, missing packages, MTK compile failures) still
        # bubble up from worker.start() inside run_tests.
        import shutil

        if not shutil.which("julia"):
            raise RuntimeError(
                "Julia binary not found on PATH. The persistent-worker Julia "
                "backend spawns a long-lived `julia --project=...` subprocess "
                "and needs `julia` available.\n"
                "\n"
                "Install Julia from https://julialang.org/downloads/ "
                "(or use a version manager like `juliaup`)."
            )

    def make_worker(self, worker_id: int) -> JuliaWorker:
        return JuliaWorker(worker_id, self.config, self.julia_config)

    # No _probe_worker_version override: the julia version is a property of
    # the configured binary, not of a live session, so JuliaBackend.
    # describe_tool_version answers it and the shared prologue stamps it
    # before the workers start.

    def starting_workers_message(self, n_workers: int) -> str:
        # First runs hit MTK + OrdinaryDiffEq JIT compile, which is
        # multi-minute. Surfacing this at start time so users don't think
        # the worker is hung. Subsequent runs hit the cache and start in
        # seconds, but the message is still accurate as a worst case.
        return (
            f"Warming up {n_workers} Julia worker(s) — first run may take "
            f"2-3 min while MTK + OrdinaryDiffEq load from cache..."
        )
