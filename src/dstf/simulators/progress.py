"""Backend-agnostic live progress reporter.

Writes `status.json` and `dashboard.html` to the work directory on every
state change so a user can watch progress live while tests run.

Event contract:
    register(test_key, model_id)  — before any work starts
    on_start(test_key, worker_id) — test/batch begins
    on_finish(test_key, success, elapsed, detail, timed_out)

Thread-safe. Writes are atomic (temp-file + replace).
"""

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TestStatus:
    test_key: str
    model_id: str
    status: str = "queued"  # queued | running | passed | failed | timed_out
    started_at: float | None = None  # time.monotonic — for accurate elapsed computation
    started_wall: float | None = (
        None  # time.time (epoch) — for JS to compute live "running for Ns"
    )
    elapsed: float | None = None
    detail: str | None = None
    worker_id: int | None = None
    report_dir: str | None = (
        None  # e.g., "ref_0042" or "test_0005" — matches generate_report_suite naming
    )
    phase: str | None = (
        None  # "translating" / "simulating" / ... while status == "running"
    )
    # Per-field provenance, plumbed from TestModel.field_sources via register().
    # Surfaced in the dashboard's Resolution column.
    field_sources: dict = field(default_factory=dict)


class ProgressReporter:
    """Thread-safe live progress tracker.

    Writes status.json + dashboard.html to work_dir on every state change.
    """

    def __init__(self, work_dir: Path, total: int, rerun_prefix: str | None = None):
        self.work_dir = work_dir
        self.total = total
        # Stashed for inclusion in status.json so the dashboard's rerun-
        # command builder produces an absolute-path command that works
        # from any CWD. Computed by callers via dashboard_render.build_
        # rerun_prefix(config).
        self.rerun_prefix = rerun_prefix
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._tests: dict[str, TestStatus] = {}
        self._start_time = time.monotonic()
        # Wall-clock anchor (epoch seconds). Sent into status.json so the
        # dashboard JS can compute live "elapsed = now - start_wall" between
        # meta-refresh ticks (otherwise the header elapsed appears frozen at
        # whatever it was when the last test state-change wrote the snapshot).
        self._start_wall = time.time()

    def register(
        self,
        test_key: str,
        model_id: str,
        report_dir: str | None = None,
        field_sources: dict | None = None,
    ) -> None:
        with self._lock:
            self._tests[test_key] = TestStatus(
                test_key=test_key,
                model_id=model_id,
                report_dir=report_dir,
                field_sources=field_sources or {},
            )
        self._write()

    def on_start(self, test_key: str, worker_id: int | None = None) -> None:
        with self._lock:
            ts = self._tests.get(test_key)
            if ts is not None:
                ts.status = "running"
                ts.started_at = time.monotonic()
                ts.started_wall = time.time()
                ts.worker_id = worker_id
                ts.phase = None
        self._write()

    def on_phase(self, test_key: str, phase: str) -> None:
        with self._lock:
            ts = self._tests.get(test_key)
            if ts is not None and ts.status == "running":
                ts.phase = phase
        self._write()

    def on_finish(
        self,
        test_key: str,
        success: bool,
        elapsed: float,
        detail: str | None = None,
        timed_out: bool = False,
    ) -> None:
        with self._lock:
            ts = self._tests.get(test_key)
            if ts is not None:
                if timed_out:
                    ts.status = "timed_out"
                elif success:
                    ts.status = "passed"
                else:
                    ts.status = "failed"
                ts.elapsed = elapsed
                ts.detail = detail
        self._write()

    def _snapshot(self) -> dict:
        with self._lock:
            tests = [asdict(t) for t in self._tests.values()]
        counts = {"queued": 0, "running": 0, "passed": 0, "failed": 0, "timed_out": 0}
        for t in tests:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        elapsed = time.monotonic() - self._start_time
        done = counts["passed"] + counts["failed"] + counts["timed_out"]
        eta = None
        if done > 0 and done < self.total:
            rate = done / elapsed if elapsed > 0 else 0
            remaining = self.total - done
            eta = remaining / rate if rate > 0 else None
        return {
            "total": self.total,
            "elapsed": elapsed,
            "eta_seconds": eta,
            "counts": counts,
            "tests": tests,
            "rerun_prefix": self.rerun_prefix,
            "start_wall": self._start_wall,
            "updated_at": time.time(),
        }

    def _write(self) -> None:
        # Serialize all file writes — without this, two threads can race on
        # the same tmp filename and `replace` fails on Windows when the file
        # is still open by another thread.
        snapshot = self._snapshot()
        with self._write_lock:
            self._write_json(snapshot)
            self._render_dashboard(mode="live")

    def _atomic_write(self, path: Path, text: str) -> None:
        # Unique tmp name per write so concurrent writers never share a tmp
        # file even if a higher-level lock is bypassed.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(text, encoding="utf-8")
        # Retry replace on Windows — antivirus, search indexer, browser
        # previews, and Explorer can briefly lock either the tmp or the
        # target file and cause WinError 5/32. Short backoff usually clears it.
        last_err: OSError | None = None
        for delay in (0, 0.05, 0.1, 0.2, 0.5):
            if delay:
                time.sleep(delay)
            try:
                tmp.replace(path)
                return
            except OSError as e:
                last_err = e
        # Give up: clean up tmp and silently drop this update so progress
        # writes never crash the run. Worst case the dashboard misses one tick.
        try:
            tmp.unlink()
        except OSError:
            pass
        # Re-raise on the very first write so a misconfiguration is visible,
        # but suppress later transient failures.
        if not (path.parent / path.name).exists():
            raise last_err

    def _write_json(self, snapshot: dict) -> None:
        path = self.work_dir / "status.json"
        self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))

    def _render_dashboard(self, mode: str) -> None:
        """Defer HTML rendering to dashboard_render.

        ProgressReporter's job is to keep status.json fresh + own atomic
        writes; the page rendering lives in reporting/dashboard_render.py
        so the live and final pages share one template.
        """
        from ..reporting.dashboard_render import render_final, render_live

        if mode == "live":
            render_live(self.work_dir)
        else:
            render_final(self.work_dir)

    def finalize(self) -> None:
        """Write a final status.json + render dashboard.html in final mode.

        Final mode strips the JS-fetch loop bootstrap so the page becomes
        a static report. Comparison-data sidecars (per-test
        comparison_data.json) are merged in by dashboard_render.render_final
        when present — typically populated later by --report.
        """
        with self._write_lock:
            snapshot = self._snapshot()
            self._write_json(snapshot)
            self._render_dashboard(mode="final")
