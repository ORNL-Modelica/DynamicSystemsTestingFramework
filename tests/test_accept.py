"""`accept_run_results`: `ReferenceStore.accept_results` plus the refusal of a
test whose last run never reached a verdict — the guard that used to live
only in `cmd_compare`, now the one function every accept path calls."""

import json

import numpy as np

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.accept import accept_run_results
from dstf.storage.reference_store import ReferenceStore


def _result(model_id):
    t = np.linspace(0.0, 1.0, 5)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=t * 2, name="x")],
    )


def _env(tmp_path, manifest):
    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib\nend FakeLib;\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    config = Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=work)
    return config, ReferenceStore(config)


def test_a_pending_test_is_refused_with_the_reason_and_the_rest_are_stored(tmp_path):
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.Done",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            },
        },
    )
    tests = [
        make_test_model("FakeLib.Done", variable_patterns=["x"]),
        make_test_model("FakeLib.InFlight", variable_patterns=["x"]),
    ]
    results = {t.model_id: _result(t.model_id) for t in tests}

    stored, skipped = accept_run_results(store, tests, results, config.work_dir)

    assert stored == 1
    assert [m for m, _ in skipped] == ["FakeLib.InFlight"]
    assert "re-run before accepting" in skipped[0][1]
    assert str(config.work_dir) in skipped[0][1]
    assert store.get_reference("FakeLib.Done") is not None
    assert store.get_reference("FakeLib.InFlight") is None


def test_a_pending_test_outside_the_scope_is_not_refused(tmp_path):
    """`compare --accept --filter A` must not fail on B's aborted run: the
    refusal is scoped to the tests being accepted."""
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.Done",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            },
        },
    )
    tests = [make_test_model("FakeLib.Done", variable_patterns=["x"])]
    stored, skipped = accept_run_results(
        store, tests, {"FakeLib.Done": _result("FakeLib.Done")}, config.work_dir
    )
    assert (stored, skipped) == (1, [])


def test_no_manifest_means_no_refusal(tmp_path):
    config, store = _env(tmp_path, {})
    (config.work_dir / "batch_manifest.json").unlink()
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    stored, skipped = accept_run_results(
        store, tests, {"FakeLib.A": _result("FakeLib.A")}, config.work_dir
    )
    assert (stored, skipped) == (1, [])


def test_store_skips_still_come_through(tmp_path):
    config, store = _env(tmp_path, {})
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    stored, skipped = accept_run_results(store, tests, {}, config.work_dir)
    assert stored == 0 and skipped == [("FakeLib.A", "no simulation result")]
