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
    # queued | running | passed | failed | timed_out | cancelled
    status: str = "queued"
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

    def __init__(
        self,
        work_dir: Path,
        total: int,
        rerun_prefix: str | None = None,
        metadata: dict | None = None,
        theme: str = "default",
    ):
        self.work_dir = work_dir
        self.total = total
        # The bundled theme the live page is rendered in (Config.theme).
        self.theme = theme
        # Stashed for inclusion in status.json so the dashboard's rerun-
        # command builder produces an absolute-path command that works
        # from any CWD. Computed by callers via dashboard_render.build_
        # rerun_prefix(config).
        self.rerun_prefix = rerun_prefix
        # Run-level provenance (backend family/configured name/os/version). Serialized into
        # status.json under "metadata" and rendered as a banner by the
        # reporters. None → the "metadata" key stays null (backward compat with
        # snapshots written before this field existed). See run_metadata.py.
        self._metadata = dict(metadata) if metadata else None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._tests: dict[str, TestStatus] = {}
        self._start_time = time.monotonic()
        # Wall-clock anchor (epoch seconds). Sent into status.json so the
        # dashboard JS can compute live "elapsed = now - start_wall" between
        # meta-refresh ticks (otherwise the header elapsed appears frozen at
        # whatever it was when the last test state-change wrote the snapshot).
        self._start_wall = time.time()
        # Set by finalize: the run is over, and nothing writes again.
        self._closed = False

    def update_metadata(self, **fields: object) -> None:
        """Patch run-level metadata after the fact (e.g. a backend that only
        learns its ``tool_version`` once its worker is live) and re-emit the
        snapshot. No-op when no base metadata was supplied — we never fabricate
        a provenance block from a late partial update.
        """
        with self._lock:
            if self._metadata is None:
                return
            self._metadata.update(fields)
        self._write()

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

    def load(self, tests: list[TestStatus]) -> None:
        """Take rows that are already final, whole, with no write per row —
        a queue that drained reports itself in one snapshot. ``finalize``
        writes it."""
        with self._lock:
            self._tests = {t.test_key: t for t in tests}

    def _snapshot(self) -> dict:
        with self._lock:
            tests = [asdict(t) for t in self._tests.values()]
            metadata = dict(self._metadata) if self._metadata else None
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
            "metadata": metadata,
        }

    def _write(self, *, closing: bool = False) -> None:
        # Serialize all file writes — without this, two threads can race on
        # the same tmp filename and `replace` fails on Windows when the file
        # is still open by another thread.
        # review 2026-07-06 (finding 25): snapshot INSIDE the write lock —
        # a snapshot taken outside could be written after a newer one,
        # regressing status.json / the live dashboard.
        with self._write_lock:
            # After finalize, the command that ended the run owns the page:
            # an event from an exec thread the backend abandoned (a timed-out
            # worker) would render it live and unscored again. Checked under
            # the lock, so no write can land after finalize's.
            if self._closed:
                return
            snapshot = self._snapshot()
            self._write_json(snapshot)
            self._render_dashboard()
            self._closed = closing

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

    def _render_dashboard(self) -> None:
        """Defer HTML rendering to dashboard_render.

        ProgressReporter's job is to keep status.json fresh + own atomic
        writes; the page rendering lives in reporting/dashboard_render.py
        so the live and final pages share one template.
        """
        from ..reporting.dashboard_render import render_live

        render_live(self.work_dir, theme=self.theme)

    def finalize(self) -> None:
        """Write the last status.json and render dashboard.html — LIVE.

        The simulations are done; the compare phase is not. A static page
        here is where a watching browser stops, every row reading
        SIMULATED while the verdicts are computed and never loaded. The
        command that ends the run renders the final page: with the
        comparisons it holds (``run``, ``run -i``, ``compare``), or
        without them when it scores nothing (``run --accept``, an aborted
        run, the tool's Rerun). Nothing this reporter hears after it is
        written.
        """
        self._write(closing=True)


def read_status(work_dir: Path) -> dict | None:
    """The last snapshot ``ProgressReporter`` wrote to *work_dir*, or ``None``
    when there is none to read.

    The one reader of ``status.json``: the dashboard renderer, the backends'
    verdict fallback, the per-test banner and the authoring shell's job panel
    all come through here. Never raises — the shell calls it from a UI timer,
    and an unreadable snapshot is a page with no progress rows, not a
    traceback. ``ValueError`` covers both a malformed document
    (``JSONDecodeError``) and bytes that are not UTF-8
    (``UnicodeDecodeError``). One behaviour changed when the three former
    readers folded into this one: a document that is valid JSON but not an
    object (``[]``, ``null``, a bare string) now reads as no snapshot, where
    each of them raised ``AttributeError`` on ``.get``. Every caller
    subscripts the snapshot by name, so a non-object is no more usable than an
    unparseable one.
    """
    p = work_dir / "status.json"
    if not p.exists():
        return None
    try:
        snapshot = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return snapshot if isinstance(snapshot, dict) else None


def recorded_backend(work_dir: Path) -> tuple[str | None, str | None]:
    """``(label, warning)``: the backend the last run in *work_dir* used, per
    ``status.json``'s metadata, and a warning for the caller to show when the
    snapshot predates run provenance.

    ``label`` is ``None`` when the work dir has no snapshot, an unreadable
    one, or one written before provenance was stamped (``metadata: null``).
    ``warning`` is set only when metadata exists but names no backend —
    written before the Backend rename (D123), when the key was "simulator".
    The guard this function exists for (D120) cannot fire then, so
    ``compare`` would fall back to environment auto-detect and could score,
    say, an OpenModelica work dir against the Dymola partition. Reading the
    old key would be a compatibility path; saying so is not.
    """
    snap = read_status(work_dir)
    if snap is None:
        return None, None
    meta = snap.get("metadata") or {}
    label = meta.get("backend_name")
    if isinstance(label, str) and label:
        return label, None
    if meta:
        return None, (
            f"Warning: {work_dir / 'status.json'} predates the Backend rename "
            "and does not record which backend produced it. Pass --backend "
            "explicitly, or re-run instead of comparing."
        )
    return None, None
