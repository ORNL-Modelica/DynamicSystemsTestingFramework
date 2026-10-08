"""A cancelled run is a RECORDED state: never "failed", never a result.

A test whose simulation was stopped on request leaves whatever the simulator
had flushed (Dymola's `stop` file leaves a `dsres.mat` to the reached time
AND a `dsfinal.txt`). Left "pending", its manifest entry falls through to
the `status.json` cross-check, where an earlier run's "failed" row for the
same model is waiting to be applied to it. So the manifest says "cancelled",
that entry is authoritative, and every reader treats it as a test with no
result.
"""

from __future__ import annotations

import json

from conftest import make_test_model, python_example_copy
from dstf.backends import select_backend
from dstf.backends.base import run_verdicts
from dstf.backends.types import TestRunResult
from dstf.storage.batch_manifest import (
    CANCELLED_RUN_STATUS,
    BatchManifest,
    unfinished_run_models,
)


def _manifest(tmp_path, status: str | None = None) -> BatchManifest:
    entry: dict = {"model_id": "Lib.A", "ref_id": None}
    if status is not None:
        entry["last_run_status"] = status
    return BatchManifest(work_dir=tmp_path, manifest={"test_0001": entry})


def _stale_failed_row(work_dir) -> None:
    (work_dir / "status.json").write_text(
        json.dumps({"tests": [{"model_id": "Lib.A", "status": "failed"}]}),
        encoding="utf-8",
    )


def test_a_cancelled_result_is_recorded_cancelled_not_failed(tmp_path):
    manifest = _manifest(tmp_path, "pending")
    manifest.record_result(
        TestRunResult(
            model_id="Lib.A",
            test_key="test_0001",
            success=False,
            error_message="Cancelled",
            cancelled=True,
        )
    )
    on_disk = json.loads((tmp_path / "batch_manifest.json").read_text())
    assert (
        on_disk["test_0001"]["last_run_status"] == CANCELLED_RUN_STATUS == "cancelled"
    )


def test_no_stale_status_row_is_applied_to_a_cancelled_entry(tmp_path):
    _stale_failed_row(tmp_path)
    # The hazard, for contrast: the same entry left pending takes the row.
    assert run_verdicts(tmp_path, [_manifest(tmp_path, "pending")]) == {
        "Lib.A": "failed"
    }
    assert run_verdicts(tmp_path, [_manifest(tmp_path, "cancelled")]) == {}


def test_a_cancelled_entry_blocks_acceptance_like_an_unfinished_one(tmp_path):
    _manifest(tmp_path, "cancelled").save()
    assert unfinished_run_models(tmp_path) == {"Lib.A"}


def test_a_cancelled_test_reads_back_as_no_result(tmp_path):
    """The file the cancelled run left parses — and is not read."""
    config, tests = python_example_copy(tmp_path)
    backend = select_backend(config, persistent=False)
    backend.quiet = True
    ramp = next(t for t in tests if t.model_id.endswith("SimpleRamp"))
    backend.run_tests([ramp])
    assert backend.read_last_results([ramp])[ramp.model_id].success

    manifest = BatchManifest.load(config.work_dir / "batch_manifest.json")
    key = next(iter(manifest.manifest))
    manifest.record_result(
        TestRunResult(
            model_id=ramp.model_id, test_key=key, success=False, cancelled=True
        )
    )

    assert backend.read_last_results([ramp]) == {}


def test_a_cancelled_run_in_hand_yields_no_result(tmp_path):
    """`read_results` with the cancelled run result itself (the queue's case):
    not the failed result a run that broke gets."""
    config, _ = python_example_copy(tmp_path)
    backend = select_backend(config, persistent=False)
    backend.quiet = True
    test = make_test_model("Lib.A")
    manifest, keyed = backend.key_tests([test])
    rr = TestRunResult(
        model_id="Lib.A", test_key=keyed[0][1], success=False, cancelled=True
    )
    manifest.record_result(rr)
    manifest.results = [rr]

    assert backend.read_results([manifest], [test]) == {}
    assert backend.result_of_run(test, keyed[0][1], rr) is None
