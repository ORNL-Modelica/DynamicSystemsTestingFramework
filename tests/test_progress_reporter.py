"""Smoke tests for ProgressReporter writing dashboard.html via dashboard_render."""

import json

from dstf.simulators.progress import ProgressReporter


def _status(work_dir):
    return json.loads((work_dir / "status.json").read_text(encoding="utf-8"))


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


def test_metadata_absent_by_default(tmp_path):
    """No metadata passed → status.json carries metadata=None (backward compat)."""
    pr = ProgressReporter(tmp_path, total=1)
    pr.register("test_0001", "Lib.A")
    assert _status(tmp_path).get("metadata") is None


def test_metadata_flows_into_status_json(tmp_path):
    meta = {"backend": "Dymola", "simulator": "Dymola 2026x", "os": "linux"}
    pr = ProgressReporter(tmp_path, total=1, metadata=meta)
    pr.register("test_0001", "Lib.A")
    got = _status(tmp_path)["metadata"]
    assert got["backend"] == "Dymola"
    assert got["simulator"] == "Dymola 2026x"
    assert got["os"] == "linux"


def test_update_metadata_sets_late_tool_version(tmp_path):
    """Backends learn their true version only after the worker starts —
    update_metadata patches the already-written snapshot."""
    meta = {"backend": "Dymola", "simulator": "Dymola 2026x", "tool_version": None}
    pr = ProgressReporter(tmp_path, total=1, metadata=meta)
    pr.register("test_0001", "Lib.A")
    assert _status(tmp_path)["metadata"]["tool_version"] is None

    pr.update_metadata(tool_version="Dymola 2026x (build 6.1)")
    assert _status(tmp_path)["metadata"]["tool_version"] == "Dymola 2026x (build 6.1)"
    # existing keys preserved
    assert _status(tmp_path)["metadata"]["backend"] == "Dymola"


def test_update_metadata_noop_without_base_metadata(tmp_path):
    """update_metadata on a reporter that never got metadata must not crash
    and must not fabricate a metadata block."""
    pr = ProgressReporter(tmp_path, total=1)
    pr.register("test_0001", "Lib.A")
    pr.update_metadata(tool_version="x")  # should be a safe no-op
    assert _status(tmp_path).get("metadata") is None
