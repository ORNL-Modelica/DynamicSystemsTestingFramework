"""compare --accept (accept without re-simulating) + the status.json verdict
guard that keeps a partial/stale .mat from becoming a baseline.

2026-07-08: `compare` reads results from disk, but the manifest doesn't
persist per-test success flags, so a test the run recorded as failed/timed_out
can read back as success just because a partial `.mat` survived. That would let
`compare`/`compare --accept` bless garbage. `read_last_results` now overrides
those from status.json.
"""

from __future__ import annotations

import json

from dstf.simulators.base import TestResult, _apply_status_verdicts


def _write_status(work_dir, rows):
    (work_dir / "status.json").write_text(json.dumps({"tests": rows}), encoding="utf-8")


class TestApplyStatusVerdicts:
    def test_failed_status_forces_success_false(self, tmp_path):
        _write_status(
            tmp_path,
            [
                {"model_id": "Lib.A", "status": "failed"},
                {"model_id": "Lib.B", "status": "passed"},
            ],
        )
        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True),
            "Lib.B": TestResult(model_id="Lib.B", success=True),
        }
        _apply_status_verdicts(tmp_path, results)
        assert results["Lib.A"].success is False  # partial .mat overridden
        assert results["Lib.A"].error_message  # reason stamped
        assert results["Lib.B"].success is True  # genuine pass untouched

    def test_timed_out_status_forces_success_false(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.T", "status": "timed_out"}])
        results = {"Lib.T": TestResult(model_id="Lib.T", success=True)}
        _apply_status_verdicts(tmp_path, results)
        assert results["Lib.T"].success is False

    def test_existing_error_message_preserved(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.A", "status": "failed"}])
        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True, error_message="boom")
        }
        _apply_status_verdicts(tmp_path, results)
        assert results["Lib.A"].error_message == "boom"

    def test_missing_status_json_is_noop(self, tmp_path):
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_status_verdicts(tmp_path, results)  # no status.json written
        assert results["Lib.A"].success is True

    def test_unreadable_status_json_is_noop(self, tmp_path):
        (tmp_path / "status.json").write_text("{ not json", encoding="utf-8")
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_status_verdicts(tmp_path, results)
        assert results["Lib.A"].success is True

    def test_model_not_in_results_is_ignored(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.Gone", "status": "failed"}])
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_status_verdicts(tmp_path, results)  # must not KeyError
        assert results["Lib.A"].success is True
