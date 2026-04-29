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
import os
import queue
import signal
import subprocess
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
    Capability,
    TestRunResult,
    assign_test_keys,
)
from .runner import JuliaConfig, JuliaRunner, _resolve_julia_source

logger = logging.getLogger(__name__)

_PERSISTENT_DRIVER_PATH = (
    Path(__file__).resolve().parent / "run_persistent.jl"
)

# Max seconds to wait for a worker's startup "ready" pulse. MTK +
# OrdinaryDiffEq precompiled-cache load takes ~15-40s; allow plenty.
_WORKER_READY_TIMEOUT = 240.0

# Grace period on clean close before we kill the worker process.
_CLOSE_GRACE_SECONDS = 5.0


class JuliaWorker:
    """One long-lived Julia subprocess.

    Owns a subprocess + a pair of reader threads (stdout / stderr) that
    push lines onto internal Queues. ``run_test_with_timeout`` writes a
    request, waits for the response with a timeout, and hard-kills the
    worker if it hangs.
    """

    def __init__(self, worker_id: int, config: Config, julia_cfg: JuliaConfig):
        self.worker_id = worker_id
        self.config = config
        self.julia_cfg = julia_cfg
        self.proc: Optional[subprocess.Popen] = None
        self._stdout_q: queue.Queue[str] = queue.Queue()
        self._stderr_buf: list[str] = []
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._pid: Optional[int] = None

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
        self, test: TestModel, test_key: str, timeout: float,
    ) -> TestRunResult:
        """Dispatch one test, wait ``timeout`` seconds for the response.

        Timed-out workers are hard-killed (the subprocess); the caller's
        restart logic decides whether to respawn.
        """
        if self.proc is None or self.proc.poll() is not None:
            return TestRunResult(
                model_id=test.model_id, test_key=test_key, success=False,
                error_message="Julia worker not running",
            )
        user_file = _resolve_julia_source(test, self.config)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id, test_key=test_key, success=False,
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
                model_id=test.model_id, test_key=test_key, success=False,
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
                    self._stderr_tail(30), encoding="utf-8",
                )
                return TestRunResult(
                    model_id=test.model_id, test_key=test_key, success=False,
                    elapsed=elapsed, sim_wall=elapsed,
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
                        self._stderr_tail(30), encoding="utf-8",
                    )
                    return TestRunResult(
                        model_id=test.model_id, test_key=test_key, success=False,
                        elapsed=elapsed, sim_wall=elapsed,
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
                    self.worker_id, resp.get("test_key"), test_key,
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
                    model_id=test.model_id, test_key=test_key, success=True,
                    elapsed=elapsed, sim_wall=sim_wall,
                    statistics={"simulation": {"wall_time": sim_wall}},
                )
            # status == "fail" (or anything else)
            err = resp.get("error", "unknown error")
            (test_dir / "julia_stderr.txt").write_text(
                err + "\n\n--- worker stderr ---\n" + self._stderr_tail(30),
                encoding="utf-8",
            )
            return TestRunResult(
                model_id=test.model_id, test_key=test_key, success=False,
                elapsed=elapsed, sim_wall=sim_wall,
                error_message=(err.splitlines()[0] if err else "Julia simulation failed"),
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

class PersistentJuliaRunner(JuliaRunner):
    """Persistent-worker Julia runner. Subclasses :class:`JuliaRunner` so
    it inherits ``read_result`` + config resolution; only ``run_tests``
    changes."""

    capabilities = frozenset({
        Capability.PERSISTENT_WORKERS,
        Capability.BATCH_FALLBACK,
    })

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        if not tests:
            return []

        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        total = len(tests)

        from ..progress import ProgressReporter
        self.progress = ProgressReporter(self.config.work_dir, total)

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
            f"Running {total} tests via persistent Julia workers "
            f"(parallel={n_workers}, timeout={self.config.timeout}s/test)",
            file=sys.stderr,
        )
        dashboard = self.config.work_dir / "dashboard.html"
        print(f"Live progress: {dashboard.resolve().as_uri()}", file=sys.stderr)

        workers = [
            JuliaWorker(i, self.config, self.julia_config)
            for i in range(n_workers)
        ]

        start_all = time.monotonic()
        print(
            f"Warming up {n_workers} Julia worker(s) — first run may take "
            f"2-3 min while MTK + OrdinaryDiffEq load from cache...",
            file=sys.stderr,
        )

        worker_ready: dict[int, bool] = {}
        ready_lock = threading.Lock()

        def _start_one(w: JuliaWorker):
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

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_start_one, w) for w in workers]
            for f in as_completed(futures):
                f.result()

        live_workers = [w for w in workers if worker_ready.get(w.worker_id)]
        if not live_workers:
            if self.progress is not None:
                self.progress.finalize()
            raise RuntimeError(
                "All Julia persistent workers failed to start. "
                "Try re-running with --batch to fall back to per-test subprocess."
            )
        print(
            f"  {len(live_workers)}/{n_workers} workers ready in "
            f"{time.monotonic() - start_all:.1f}s",
            file=sys.stderr,
        )

        MAX_RESTARTS_PER_WORKER = 3

        work_queue: queue.Queue = queue.Queue()
        for item in test_items:
            work_queue.put(item)
        for _ in live_workers:
            work_queue.put(None)  # sentinel

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
                idx, total, label, status, elapsed=tr.elapsed,
                detail=(tr.error_message[:80] if not tr.success and tr.error_message else None),
            )
            if self.progress:
                self.progress.on_finish(
                    test_key, success=tr.success, elapsed=tr.elapsed,
                    detail=(tr.error_message[:80] if not tr.success and tr.error_message else None),
                    timed_out=tr.timed_out,
                )

        def dispatch_loop(worker: JuliaWorker):
            restarts = 0
            while True:
                item = work_queue.get()
                if item is None:
                    break
                test, test_key = item
                if not worker.is_alive():
                    if restarts >= MAX_RESTARTS_PER_WORKER:
                        tr = TestRunResult(
                            model_id=test.model_id, test_key=test_key, success=False,
                            error_message=f"Worker {worker.worker_id} exceeded max restarts",
                        )
                        _record(test, test_key, tr)
                        continue
                    restarts += 1
                    print(
                        f"  Worker {worker.worker_id}: restarting ({restarts}/"
                        f"{MAX_RESTARTS_PER_WORKER})",
                        file=sys.stderr,
                    )
                    try:
                        worker.start()
                    except Exception as exc:
                        tr = TestRunResult(
                            model_id=test.model_id, test_key=test_key, success=False,
                            error_message=f"Worker restart failed: {exc}",
                        )
                        _record(test, test_key, tr)
                        continue

                if self.progress:
                    self.progress.on_start(test_key)
                    self.progress.on_phase(test_key, "simulating")

                timeout = float(
                    test.timeout if test.timeout is not None else self.config.timeout
                )
                tr = worker.run_test_with_timeout(test, test_key, timeout)
                _record(test, test_key, tr)

        threads = [
            threading.Thread(target=dispatch_loop, args=(w,), daemon=True)
            for w in live_workers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for w in workers:
            w.close()

        elapsed_total = time.monotonic() - start_all
        n_ok = sum(1 for r in results if r.success)
        n_to = sum(1 for r in results if r.timed_out)
        print(
            f"\nSimulations complete: {n_ok} ok, {total - n_ok - n_to} failed, "
            f"{n_to} timed out ({elapsed_total:.0f}s elapsed)",
            file=sys.stderr,
        )
        if self.progress is not None:
            self.progress.finalize()

        return [manifest]


def _print_progress(
    idx: int, total: int, label: str, status: str,
    elapsed: float = 0.0, detail: Optional[str] = None,
) -> None:
    """Compact per-test progress line shared with the batch runner's style."""
    bar_width = 30
    frac = idx / max(1, total)
    filled = int(frac * bar_width)
    bar = "=" * filled + " " * (bar_width - filled)
    pct = int(frac * 100)
    extra = ""
    if elapsed:
        extra = f" ({elapsed:.1f}s)"
    if detail:
        extra += f" — {detail}"
    sys.stderr.write(
        f"\r  [{bar}] {idx}/{total} ({pct}%) {label} → {status}{extra}\n"
    )
    sys.stderr.flush()
