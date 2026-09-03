"""Smoke tests for ProgressReporter writing dashboard.html via dashboard_render."""

import json
import threading

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


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Finding 25 — the progress snapshot is taken inside the write lock, so a
# stale snapshot can never overwrite a newer one.
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 25 — progress snapshot taken inside the write lock
# ---------------------------------------------------------------------------


class TestProgressSnapshotUnderLock:
    def test_snapshot_taken_while_write_lock_held(self, tmp_path, monkeypatch):
        from dstf.simulators.progress import ProgressReporter

        pr = ProgressReporter(tmp_path, total=1)
        monkeypatch.setattr(pr, "_render_dashboard", lambda mode: None)
        held: list[bool] = []
        orig = pr._snapshot

        def spy():
            held.append(pr._write_lock.locked())
            return orig()

        monkeypatch.setattr(pr, "_snapshot", spy)
        pr.register("t1", "M.A")
        assert held and all(held)

    def test_written_snapshots_never_regress_under_threads(self, tmp_path, monkeypatch):
        from dstf.simulators.progress import ProgressReporter

        n = 40
        pr = ProgressReporter(tmp_path, total=n)
        monkeypatch.setattr(pr, "_render_dashboard", lambda mode: None)
        written: list[dict] = []
        monkeypatch.setattr(
            pr, "_write_json", lambda snapshot: written.append(snapshot)
        )

        for i in range(n):
            pr.register(f"t{i}", f"M.T{i}")

        def work(i: int):
            pr.on_start(f"t{i}", worker_id=0)
            pr.on_finish(f"t{i}", success=True, elapsed=0.001)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        done = [
            s["counts"]["passed"] + s["counts"]["failed"] + s["counts"]["timed_out"]
            for s in written
        ]
        # With snapshot-in-lock, writes are serialized snapshots — a stale
        # snapshot can never overwrite a newer one.
        assert done == sorted(done)
        assert written[-1]["counts"]["passed"] == n
