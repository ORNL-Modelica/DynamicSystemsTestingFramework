"""Smoke tests for ProgressReporter writing dashboard.html via dashboard_render."""

from pathlib import Path

from dstf.simulators.progress import ProgressReporter


def test_register_writes_status_and_dashboard(tmp_path):
    pr = ProgressReporter(tmp_path, total=2)
    pr.register("test_0001", "Lib.A")
    pr.register("test_0002", "Lib.B")
    assert (tmp_path / "status.json").exists()
    assert (tmp_path / "dashboard.html").exists()
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "Lib.A" in html
    assert "Lib.B" in html
    assert "DASHBOARD_MODE = 'live'" in html


def test_finalize_strips_live_mode(tmp_path):
    pr = ProgressReporter(tmp_path, total=1)
    pr.register("test_0001", "Lib.A")
    pr.on_start("test_0001", worker_id=0)
    pr.on_finish("test_0001", success=True, elapsed=1.0)
    pr.finalize()
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "DASHBOARD_MODE = 'final'" in html
