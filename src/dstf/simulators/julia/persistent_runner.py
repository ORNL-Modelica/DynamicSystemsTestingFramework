"""Persistent-worker Julia runner (D78).

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

Graceful degradation: if Julia's dependencies aren't ready (no Manifest.
toml, failed `using`), the runner raises RuntimeError and the CLI falls
back to the batch runner with a stderr notice — same pattern as Dymola
and OpenModelica.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

from ...config import Config
from ...discovery.test_registry import TestModel
from ..base import (
    Capability,
    PersistentRunnerBase,
    TestRunResult,
    Worker,
)
from .runner import JuliaConfig, JuliaRunner, _resolve_julia_source

logger = logging.getLogger(__name__)

_PERSISTENT_DRIVER_PATH = Path(__file__).resolve().parent / "run_persistent.jl"

# Max seconds to wait for a worker's startup "ready" pulse. MTK +
# OrdinaryDiffEq precompiled-cache load takes ~15-40s; allow plenty.
_WORKER_READY_TIMEOUT = 240.0

# Grace period on clean close before we kill the worker process.
_CLOSE_GRACE_SECONDS = 5.0


class JuliaWorker(Worker):
    """One long-lived Julia subprocess.

    Owns a subprocess + a pair of reader threads (stdout / stderr) that
    push lines onto internal Queues. ``run_test_with_timeout`` writes a
    request, waits for the response with a timeout, and hard-kills the
    worker if it hangs.
    """

    def __init__(self, worker_id: int, config: Config, julia_cfg: JuliaConfig):
        super().__init__(worker_id, config)
        self.julia_cfg = julia_cfg
        self.proc: subprocess.Popen | None = None
        self._stdout_q: queue.Queue[str] = queue.Queue()
        self._stderr_buf: list[str] = []
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._pid: int | None = None

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
        self._pid = self.proc.pid

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
        ready = self._wait_for_ready(timeout=_WORKER_READY_TIMEOUT)
        if not ready:
            # Leave the stderr buffer around so the caller can surface it.
            self.close(grace=0.5)
            raise RuntimeError(
                f"Julia worker {self.worker_id} did not emit ready within "
                f"{_WORKER_READY_TIMEOUT}s. stderr tail: "
                f"{self._stderr_tail(6)}"
            )

    # ---------------------------------------------------------------

    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        progress: object | None = None,
    ) -> TestRunResult:
        """Dispatch one test, wait ``timeout`` seconds for the response.

        ``progress`` is accepted for shape compatibility with the other
        backends' workers (Dymola/OM emit ``on_phase`` updates from inside
        the worker). Julia today reports phases via the dispatch loop's
        ``_record`` helper instead, so this argument is currently ignored.

        Timed-out workers are hard-killed (the subprocess); the caller's
        restart logic decides whether to respawn.
        """
        if self.proc is None or self.proc.poll() is not None:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message="Julia worker not running",
            )
        user_file = _resolve_julia_source(test, self.config)
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
            if remaining <= 0:
                self._hard_kill()
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
                    error_message=f"Julia simulation exceeded {timeout}s timeout",
                    timed_out=True,
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
                    f"// PersistentJuliaRunner worker={self.worker_id}\n"
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

    def close(self, grace: float = _CLOSE_GRACE_SECONDS) -> None:
        """Graceful close: send ``quit``, wait, hard-kill if needed."""
        if self.proc is None:
            return
        if self.proc.poll() is None and self.proc.stdin is not None:
            try:
                self.proc.stdin.write('{"cmd": "quit"}\n')
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self._hard_kill()
        # Ensure pipes close.
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    # ---------------------------------------------------------------

    def _reader_loop(self, stream, q: queue.Queue) -> None:
        try:
            for line in iter(stream.readline, ""):
                q.put(line.strip())
        except Exception as exc:
            logger.debug("worker %d reader error: %s", self.worker_id, exc)

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

    def _hard_kill(self) -> None:
        if self.proc is None:
            return
        # Try psutil first for clean subprocess tree kill (Julia doesn't
        # spawn children in our driver, but defensive).
        try:
            import psutil

            p = psutil.Process(self.proc.pid)
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            p.kill()
        except Exception:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PersistentJuliaRunner(PersistentRunnerBase, JuliaRunner):
    """Persistent-worker Julia runner.

    Inherits dispatch-loop machinery from :class:`PersistentRunnerBase`
    and ``read_result`` + config resolution from the batch
    :class:`JuliaRunner`. Order matters — ``PersistentRunnerBase`` first so
    its ``run_tests`` template wins over the batch runner's per-test
    override.
    """

    capabilities = frozenset(
        {
            Capability.PERSISTENT_WORKERS,
            Capability.BATCH_FALLBACK,
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
                "runner spawns a long-lived `julia --project=...` subprocess "
                "and needs `julia` available.\n"
                "\n"
                "Install Julia from https://julialang.org/downloads/ "
                "(or use a version manager like `juliaup`)."
            )

    def make_worker(self, worker_id: int) -> JuliaWorker:
        return JuliaWorker(worker_id, self.config, self.julia_config)

    def _probe_worker_version(self, live_workers: list) -> str | None:
        # The version is a property of the configured julia binary, not the
        # running session, so probe the binary directly rather than reaching
        # into a worker's subprocess. Best-effort → None on any failure.
        import subprocess

        try:
            out = subprocess.run(
                [str(self.julia_config.julia_binary), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            banner = (out.stdout or out.stderr or "").strip()
            return banner or None
        except Exception:
            return None

    def starting_workers_message(self, n_workers: int) -> str:
        # First runs hit MTK + OrdinaryDiffEq JIT compile, which is
        # multi-minute. Surfacing this at start time so users don't think
        # the worker is hung. Subsequent runs hit the cache and start in
        # seconds, but the message is still accurate as a worst case.
        return (
            f"Warming up {n_workers} Julia worker(s) — first run may take "
            f"2-3 min while MTK + OrdinaryDiffEq load from cache..."
        )
