"""Tests for the unified dashboard renderer."""

import json
from pathlib import Path

import pytest

from dstf.reporting.dashboard_render import (
    render_live,
    render_final,
    build_dashboard_context,
)


def _write_status_json(work_dir: Path, snapshot: dict) -> None:
    (work_dir / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")


def test_build_context_live_only(tmp_path):
    """Live snapshot (no comparison data yet): rows have status/elapsed
    populated; NRMSE/warnings columns are None; auto_refresh=True."""
    snapshot = {
        "total": 2,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 1, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [
            {"test_key": "test_0001", "model_id": "Lib.A",
             "status": "passed", "elapsed": 2.0, "worker_id": 0,
             "report_dir": "test_0001"},
            {"test_key": "test_0002", "model_id": "Lib.B",
             "status": "running", "elapsed": None, "worker_id": 1,
             "report_dir": "test_0002"},
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="live")
    assert ctx["mode"] == "live"
    assert ctx["auto_refresh"] is True
    assert len(ctx["tests"]) == 2
    row_a = next(r for r in ctx["tests"] if r["model_id"] == "Lib.A")
    assert row_a["status_text"] == "passed"
    assert row_a["worst_nrmse"] is None
    assert row_a["n_vars"] is None


def test_build_context_final_with_comparisons(tmp_path):
    """Final snapshot: status.json + comparison_data.json sidecars yield
    a row with both live + post-run fields populated."""
    snapshot = {
        "total": 1, "elapsed": 5.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "passed", "elapsed": 2.0,
                   "worker_id": 0, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(json.dumps({
        "model_id": "Lib.A",
        "worst_nrmse": 1.2e-5,
        "n_vars": 3,
        "n_vars_passed": 3,
        "n_warnings": 0,
        "translation_wall": 0.5,
        "sim_wall": 1.5,
        "total_wall": 2.0,
    }))
    ctx = build_dashboard_context(tmp_path, mode="final")
    assert ctx["mode"] == "final"
    assert ctx["auto_refresh"] is False
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 1.2e-5
    assert row["n_vars"] == 3
    assert row["translation_wall"] == 0.5


def test_render_live_writes_dashboard_html(tmp_path):
    """render_live writes dashboard.html and it contains the auto-refresh
    JS-fetch hook, not <meta http-equiv='refresh'>."""
    snapshot = {
        "total": 1, "elapsed": 0.0, "eta_seconds": None,
        "counts": {"queued": 1, "running": 0, "passed": 0,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "queued", "elapsed": None,
                   "worker_id": None, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "<title>" in out
    assert "Lib.A" in out
    assert 'http-equiv="refresh"' not in out
    assert "DASHBOARD_MODE" in out  # JS fetch hook bootstrap


def test_render_final_strips_refresh(tmp_path):
    snapshot = {
        "total": 1, "elapsed": 0.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "passed", "elapsed": 1.0,
                   "worker_id": 0, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "DASHBOARD_MODE = 'final'" in out
