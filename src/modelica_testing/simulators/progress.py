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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>Test progress — {total} tests</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 1.5em; background: #fafafa; color: #333; }}
h1 {{ margin-bottom: 0.2em; font-size: 1.4em; }}
.updated {{ font-size: 0.8em; color: #888; margin-bottom: 1em; }}
.counters {{ display: flex; gap: 0.6em; flex-wrap: wrap; margin-bottom: 1em; }}
.counter {{ background: white; border: 1px solid #ddd; border-radius: 4px; padding: 0.4em 0.8em; min-width: 90px; }}
.counter .label {{ font-size: 0.7em; color: #888; text-transform: uppercase; }}
.counter .value {{ font-size: 1.3em; font-weight: 600; }}
.bar {{ height: 14px; background: #eee; border-radius: 7px; overflow: hidden; margin-bottom: 1em; display: flex; }}
.bar span {{ display: block; height: 100%; }}
.bar .b-passed {{ background: #4CAF50; }}
.bar .b-failed {{ background: #f44336; }}
.bar .b-timed_out {{ background: #9C27B0; }}
.bar .b-running {{ background: #2196F3; }}
table {{ border-collapse: collapse; width: 100%; background: white; font-size: 0.85em; }}
th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: left; }}
th {{ background: #f5f5f5; position: sticky; top: 0; }}
tr.queued td {{ color: #888; }}
tr.running td {{ background: #e3f2fd; font-weight: 500; }}
tr.passed td {{ background: #f1f8e9; }}
tr.failed td {{ background: #ffebee; }}
tr.timed_out td {{ background: #f3e5f5; }}
.status {{ font-weight: 600; font-size: 0.75em; text-transform: uppercase; }}
.status.queued {{ color: #888; }}
.status.running {{ color: #1976D2; }}
.status.passed {{ color: #2E7D32; }}
.status.failed {{ color: #C62828; }}
.status.timed_out {{ color: #6A1B9A; }}
td a {{ color: #1976D2; text-decoration: none; }}
td a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Test progress</h1>
<div class="updated">Auto-refreshing every 2s · updated {updated_str} · elapsed {elapsed:.0f}s{eta_str} · <a href="reports/index.html">overall report</a> (if generated using flag --report)</div>
<div class="bar">{bar_html}</div>
<div class="counters">{counters_html}</div>
<table>
<tr><th>Test</th><th>Model</th><th>Status</th><th>Worker</th><th>Elapsed</th><th>Detail</th></tr>
{rows_html}
</table>
</body>
</html>
"""


@dataclass
class TestStatus:
    test_key: str
    model_id: str
    status: str = "queued"  # queued | running | passed | failed | timed_out
    started_at: Optional[float] = None
    elapsed: Optional[float] = None
    detail: Optional[str] = None
    worker_id: Optional[int] = None
    report_dir: Optional[str] = None  # e.g., "ref_0042" or "test_0005" — matches generate_report_suite naming
    phase: Optional[str] = None  # "translating" / "simulating" / ... while status == "running"


class ProgressReporter:
    """Thread-safe live progress tracker.

    Writes status.json + dashboard.html to work_dir on every state change.
    """

    def __init__(self, work_dir: Path, total: int):
        self.work_dir = work_dir
        self.total = total
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._tests: dict[str, TestStatus] = {}
        self._start_time = time.monotonic()

    def register(self, test_key: str, model_id: str, report_dir: Optional[str] = None) -> None:
        with self._lock:
            self._tests[test_key] = TestStatus(
                test_key=test_key, model_id=model_id, report_dir=report_dir,
            )
        self._write()

    def on_start(self, test_key: str, worker_id: Optional[int] = None) -> None:
        with self._lock:
            ts = self._tests.get(test_key)
            if ts is not None:
                ts.status = "running"
                ts.started_at = time.monotonic()
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
        detail: Optional[str] = None,
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
            "updated_at": time.time(),
        }

    def _write(self) -> None:
        # Serialize all file writes — without this, two threads can race on
        # the same tmp filename and `replace` fails on Windows when the file
        # is still open by another thread.
        snapshot = self._snapshot()
        with self._write_lock:
            self._write_json(snapshot)
            self._write_html(snapshot)

    def _atomic_write(self, path: Path, text: str) -> None:
        # Unique tmp name per write so concurrent writers never share a tmp
        # file even if a higher-level lock is bypassed.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(text, encoding="utf-8")
        # Retry replace on Windows — antivirus, search indexer, browser
        # previews, and Explorer can briefly lock either the tmp or the
        # target file and cause WinError 5/32. Short backoff usually clears it.
        last_err: Optional[OSError] = None
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

    def _write_html(self, snapshot: dict) -> None:
        import html as html_mod
        counts = snapshot["counts"]
        total = snapshot["total"]

        # Progress bar segments (percentages)
        def pct(n):
            return (n / total * 100) if total else 0
        bar_html = (
            f'<span class="b-passed" style="width:{pct(counts["passed"]):.2f}%"></span>'
            f'<span class="b-failed" style="width:{pct(counts["failed"]):.2f}%"></span>'
            f'<span class="b-timed_out" style="width:{pct(counts["timed_out"]):.2f}%"></span>'
            f'<span class="b-running" style="width:{pct(counts["running"]):.2f}%"></span>'
        )

        counters_html = "".join(
            f'<div class="counter"><div class="label">{k}</div>'
            f'<div class="value">{v}</div></div>'
            for k, v in counts.items()
        )
        counters_html += (
            f'<div class="counter"><div class="label">Total</div>'
            f'<div class="value">{total}</div></div>'
        )

        rows = []
        for t in snapshot["tests"]:
            elapsed_str = f"{t['elapsed']:.1f}s" if t["elapsed"] is not None else ""
            worker_str = f"W{t['worker_id']}" if t["worker_id"] is not None else ""
            detail = html_mod.escape(t["detail"] or "")
            test_key = html_mod.escape(t["test_key"])
            model_id = html_mod.escape(t["model_id"])
            # Test directory link (always exists during/after run)
            test_link = f'<a href="{test_key}/">{test_key}</a>'
            # Per-test report link (matches generate_report_suite naming);
            # 404s harmlessly if --report wasn't used or hasn't finished yet
            report_dir = t.get("report_dir") or t["test_key"]
            model_link = f'<a href="reports/{html_mod.escape(report_dir)}/interactive.html">{model_id}</a>'
            status_label = t["status"]
            if t["status"] == "running" and t.get("phase"):
                status_label = f'{t["status"]} ({t["phase"]})'
            rows.append(
                f'<tr class="{t["status"]}">'
                f'<td>{test_link}</td>'
                f'<td>{model_link}</td>'
                f'<td><span class="status {t["status"]}">{status_label}</span></td>'
                f'<td>{worker_str}</td>'
                f'<td>{elapsed_str}</td>'
                f'<td>{detail}</td>'
                f'</tr>'
            )

        eta_str = ""
        if snapshot["eta_seconds"] is not None:
            eta_str = f" · ETA {snapshot['eta_seconds']:.0f}s"
        updated_str = time.strftime("%H:%M:%S", time.localtime(snapshot["updated_at"]))

        html_out = _DASHBOARD_TEMPLATE.format(
            total=total,
            updated_str=updated_str,
            elapsed=snapshot["elapsed"],
            eta_str=eta_str,
            bar_html=bar_html,
            counters_html=counters_html,
            rows_html="\n".join(rows),
        )

        path = self.work_dir / "dashboard.html"
        self._atomic_write(path, html_out)

    def finalize(self) -> None:
        """Write a final snapshot, then strip the auto-refresh meta tag."""
        with self._write_lock:
            snapshot = self._snapshot()
            self._write_json(snapshot)
            self._write_html(snapshot)
            path = self.work_dir / "dashboard.html"
            html_out = path.read_text(encoding="utf-8").replace(
                '<meta http-equiv="refresh" content="2">', ""
            )
            self._atomic_write(path, html_out)
