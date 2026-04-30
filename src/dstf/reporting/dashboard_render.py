"""Unified dashboard renderer.

One Jinja template (`dashboard.html`) feeds both live progress
during a run and the post-comparison report. The template renders
gracefully whether comparison data is present (post-run) or absent
(during run); JS-fetch on the client side keeps the page fresh
without full-page reloads.

Live mode is triggered every state change by ProgressReporter.
Final mode is triggered after comparison from cli.cmd_run /
cmd_compare; it strips the JS-fetch poll and adds per-test report
links + post-run columns (worst_nrmse, warnings, translate/sim/total
wall times).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _read_status(work_dir: Path) -> Optional[dict]:
    p = work_dir / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_comparison_sidecar(work_dir: Path, report_dir: str) -> Optional[dict]:
    """Read per-test comparison_data.json if present.

    Per-test reports are at <work_dir>/reports/<report_dir>/comparison_data.json
    (matches generate_comparison_plots layout).
    """
    p = work_dir / "reports" / report_dir / "comparison_data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _enrich_row_from_comparison(row: dict, comp: dict) -> None:
    """Copy post-run fields from a per-test comparison_data.json into a row.

    The sidecar puts row-summary fields under a `summary` block alongside
    the per-variable context. Fall back to top-level for backward compat
    with sidecars written before the summary block was added.
    """
    summary = comp.get("summary", comp)
    for key in (
        "worst_nrmse", "n_vars", "n_vars_passed", "n_warnings",
        "translation_wall", "sim_wall", "total_wall",
        "ref_id", "field_sources",
    ):
        if key in summary:
            row[key] = summary[key]


def build_dashboard_context(work_dir: Path, mode: str) -> dict:
    """Build the Jinja context for dashboard.html.

    mode='live' — auto_refresh=True, post-run fields stay None
    mode='final' — auto_refresh=False, fields enriched from sidecars

    The same template renders both; JS reads `DASHBOARD_MODE` to
    decide whether to start the fetch loop.
    """
    snapshot = _read_status(work_dir) or {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }

    rows = []
    for t in snapshot.get("tests", []):
        row = {
            "test_key": t.get("test_key"),
            "model_id": t.get("model_id"),
            "status_text": t.get("status", "queued"),
            "status_class": t.get("status", "queued").replace("_", "-"),
            "elapsed": t.get("elapsed"),
            "worker_id": t.get("worker_id"),
            "report_dir": t.get("report_dir") or t.get("test_key"),
            "phase": t.get("phase"),
            # Post-run fields default to None; populated below in final mode
            "worst_nrmse": None,
            "n_vars": None,
            "n_vars_passed": None,
            "n_warnings": None,
            "translation_wall": None,
            "sim_wall": None,
            "total_wall": None,
            "ref_id": None,
            "field_sources": {},
        }
        if mode == "final" and row["report_dir"]:
            comp = _read_comparison_sidecar(work_dir, row["report_dir"])
            if comp:
                _enrich_row_from_comparison(row, comp)
        rows.append(row)

    return {
        "mode": mode,
        "auto_refresh": mode == "live",
        "title": "Test progress" if mode == "live" else "Test report",
        "total": snapshot.get("total", 0),
        "elapsed": snapshot.get("elapsed", 0.0),
        "eta_seconds": snapshot.get("eta_seconds"),
        "counts": snapshot.get("counts", {}),
        "tests": rows,
        "updated_at": snapshot.get("updated_at", time.time()),
    }


def _atomic_write(path: Path, text: str) -> None:
    """Atomic file write — Windows file-locking workaround.

    Same retry logic as ProgressReporter._atomic_write — uses unique
    tmp name so concurrent writers can't share the same tmp path.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    last_err: Optional[OSError] = None
    for delay in (0, 0.05, 0.1, 0.2, 0.5):
        if delay:
            time.sleep(delay)
        try:
            tmp.replace(path)
            return
        except OSError as e:
            last_err = e
    try:
        tmp.unlink()
    except OSError:
        pass
    if not (path.parent / path.name).exists():
        raise last_err


def _render(work_dir: Path, mode: str) -> None:
    ctx = build_dashboard_context(work_dir, mode=mode)
    template = _env.get_template("dashboard.html")
    html = template.render(**ctx)
    _atomic_write(work_dir / "dashboard.html", html)


def render_live(work_dir: Path) -> None:
    """Render dashboard.html in live mode (JS-fetch loop active)."""
    _render(work_dir, mode="live")


def render_final(work_dir: Path) -> None:
    """Render dashboard.html in final mode (fetch loop stripped, sidecars merged)."""
    _render(work_dir, mode="final")
