"""Content pins for the Julia driver scripts + the worker's pipe plumbing.

The ``.jl`` drivers cannot be executed here (no julia binary on this
machine), so these tests read the shipped scripts and pin the decisions
textually; the worker test drives ``Popen`` through a fake. Nothing here
needs julia, which is why it lives outside the julia-gated
``tests/test_julia_backend.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JULIA_DRIVER_DIR = PROJECT_ROOT / "src" / "dstf" / "backends" / "julia"


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Findings 4/27/75 — Julia driver scripts. The .jl changes cannot be executed
# here (no julia binary on this machine), so the driver-content tests pin the
# decisions textually and a regression at least trips something.
# =========================================================================


# ---------------------------------------------------------------------------
# Findings 4/27/75 — Julia driver scripts (content pins; julia not runnable
# on this machine, so the decisions are at least pinned textually)
# ---------------------------------------------------------------------------


class TestJuliaDriverContent:
    def test_both_drivers_check_retcode_and_stop_time(self):
        run_test = (JULIA_DRIVER_DIR / "run_test.jl").read_text(encoding="utf-8")
        run_persistent = (JULIA_DRIVER_DIR / "run_persistent.jl").read_text(
            encoding="utf-8"
        )
        for text in (run_test, run_persistent):
            assert "successful_retcode" in text, "finding 4: retcode unchecked"
            assert "sol.t[end]" in text, "finding 4: stop-time reach unchecked"
            assert "retcode" in text

    def test_persistent_driver_poisons_build_mtk_system_before_include(self):
        """finding 27 (completed post-audit): the sentinel must be rebound
        BEFORE every include — an isdefined check alone only caught the
        first-ever bad file; after any good test had defined the function,
        a later bad file silently reused the previous test's model."""
        run_persistent = (JULIA_DRIVER_DIR / "run_persistent.jl").read_text(
            encoding="utf-8"
        )
        assert "stale-definition guard" in run_persistent
        # rindex: the phrase also appears in the header comment; the code
        # call is the last occurrence.
        include_pos = run_persistent.rindex("include(user_file)")
        poison_pos = run_persistent.index("stale-definition guard")
        assert poison_pos < include_pos, (
            "finding 27: the poison sentinel must be installed before "
            "include(user_file), not after"
        )

    def test_batch_driver_failure_json_uses_manual_escaper(self):
        run_test = (JULIA_DRIVER_DIR / "run_test.jl").read_text(encoding="utf-8")
        assert "Base.repr" not in run_test, (
            "finding 75: Julia repr escaping produces invalid JSON (\\$)"
        )
        assert "json_escape" in run_test


class TestJuliaPersistentPipeEncoding:
    def test_worker_popen_uses_utf8_replace(self, tmp_path, monkeypatch):
        """Finding 75: on cp1252 Windows a UTF-8 Julia backtrace kills the
        reader thread unless the pipes decode utf-8 with errors=replace."""
        from dstf.backends.julia import persistent_backend as jpr

        captured: dict = {}

        def fake_popen(*args, **kwargs):
            captured.update(kwargs)
            raise OSError("stop here — kwargs captured")

        monkeypatch.setattr(jpr.subprocess, "Popen", fake_popen)
        julia_cfg = SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path)
        worker = jpr.JuliaWorker(0, SimpleNamespace(work_dir=tmp_path), julia_cfg)
        with pytest.raises(OSError):
            worker.start()
        assert captured.get("encoding") == "utf-8"
        assert captured.get("errors") == "replace"
