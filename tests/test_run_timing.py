"""One timing block for every backend (``run_timing``), built where results
are read, and kept with the result across processes (``run_stats.json``)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dstf
from conftest import make_test_model, python_example_copy
from dstf.backends.base import Backend
from dstf.backends.types import TestResult, TestRunResult
from dstf.config import Config
from dstf.storage.batch_manifest import BatchManifest


def _rr(**kw) -> TestRunResult:
    base = {"model_id": "Lib.A", "test_key": "test_0001", "success": True}
    base.update(kw)
    return TestRunResult(**base)


def _config(tmp_path: Path) -> Config:
    lib = tmp_path / "Lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "package.mo").write_text("package Lib end Lib;")
    return Config(
        source_path=lib,
        reference_root=tmp_path / "refs",
        backend_name="Dymola",
        work_dir=tmp_path / "work",
    )


def _manifest(tmp_path: Path, rr: TestRunResult) -> BatchManifest:
    m = BatchManifest(
        work_dir=tmp_path / "work",
        manifest={rr.test_key: {"model_id": rr.model_id, "ref_id": None}},
    )
    m.results = [rr]
    return m


def _modelica_backend(name: str, tmp_path: Path) -> Backend:
    from dstf.backends.dymola.backend import DymolaBackend
    from dstf.backends.openmodelica.backend import OpenModelicaBackend

    cls = {"Dymola": DymolaBackend, "OpenModelica": OpenModelicaBackend}[name]
    backend = cls.__new__(cls)
    Backend.__init__(backend, _config(tmp_path))
    return backend


@pytest.mark.parametrize("name", ["Dymola", "OpenModelica"])
@pytest.mark.parametrize(
    "walls, expected",
    [
        (
            {"translation_wall": 3.333, "sim_wall": 5.555},
            {
                "translation_wall": 3.33,
                "sim_wall": 5.55,
                "other_wall": 1.12,
                "total_wall": 10.0,
            },
        ),
        ({}, None),
    ],
)
def test_the_modelica_backends_numbers_are_unchanged(tmp_path, name, walls, expected):
    """The block Dymola's and OpenModelica's ``read_result`` built before it
    moved to ``read_results``: the same numbers, for a result whose .mat is
    missing, and none for a run that measured no wall (Dymola batch)."""
    backend = _modelica_backend(name, tmp_path)
    rr = _rr(elapsed=10.004, **walls)
    out = backend.read_results([_manifest(tmp_path, rr)], [make_test_model("Lib.A")])
    stats = out["Lib.A"].statistics or {}
    assert stats.get("timing") == expected


class _PassThroughBackend(Backend):
    """A backend whose ``read_result`` passes the run's statistics through
    untouched, as FMPy and the JSON-driver backends (Julia, Python) do."""

    def run_single_test(self, test, test_key, index, total):  # pragma: no cover
        raise AssertionError

    def read_result(self, test, test_key, run_result):
        return TestResult(
            model_id=test.model_id,
            success=True,
            statistics=run_result.statistics if run_result else None,
        )


def _read(tmp_path, backend, rr):
    out = backend.read_results([_manifest(tmp_path, rr)], [make_test_model("Lib.A")])
    return out["Lib.A"]


def test_a_sim_wall_backend_gets_a_timing_block(tmp_path):
    rr = _rr(elapsed=1.5, sim_wall=1.25, statistics={"simulation": {"steps": 3}})
    res = _read(tmp_path, _PassThroughBackend(_config(tmp_path)), rr)
    assert res.statistics["timing"] == {"sim_wall": 1.25, "total_wall": 1.5}
    assert res.statistics["simulation"] == {"steps": 3}


def test_a_failed_run_keeps_its_walls(tmp_path):
    rr = _rr(success=False, elapsed=2.0, translation_wall=1.5)
    res = _read(tmp_path, _PassThroughBackend(_config(tmp_path)), rr)
    assert res.success is False
    assert res.statistics["timing"] == {"translation_wall": 1.5, "total_wall": 2.0}


def test_the_tools_own_timing_keys_survive(tmp_path):
    """OpenModelica's omc-reported ``timing`` keys are updated, not replaced."""
    tool = {"frontend": 0.25, "simulation": 0.5, "total": 1.9}
    rr = _rr(
        elapsed=2.0,
        translation_wall=1.0,
        sim_wall=0.5,
        statistics={"timing": dict(tool)},
    )
    res = _read(tmp_path, _PassThroughBackend(_config(tmp_path)), rr)
    assert res.statistics["timing"] == {
        **tool,
        "translation_wall": 1.0,
        "sim_wall": 0.5,
        "other_wall": 0.5,
        "total_wall": 2.0,
    }


@pytest.mark.parametrize("name", ["Dymola", "OpenModelica"])
def test_the_run_results_own_statistics_are_not_written_through(tmp_path, name):
    tool = {"frontend": 0.25}
    rr = _rr(
        elapsed=2.0, translation_wall=1.0, sim_wall=0.5, statistics={"timing": tool}
    )
    _read(tmp_path, _modelica_backend(name, tmp_path), rr)
    assert rr.statistics == {"timing": {"frontend": 0.25}}


def test_no_wall_no_block(tmp_path):
    rr = _rr(elapsed=4.0, statistics={"simulation": {"steps": 3}})
    res = _read(tmp_path, _PassThroughBackend(_config(tmp_path)), rr)
    assert "timing" not in res.statistics


# ---------------------------------------------------------------------------
# The run's statistics are kept beside its result: <test_dir>/run_stats.json.
# Real runs of the Python example.
# ---------------------------------------------------------------------------

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
SIMPLE_RAMP = "PythonTestingLib.Examples.SimpleRamp"


@pytest.fixture
def ran(tmp_path):
    """``(config, tests, backend, store, results)`` after one full run."""
    from dstf.backends import get_backend
    from dstf.pipeline import run_pipeline
    from dstf.storage.reference_store import ReferenceStore

    config, tests = python_example_copy(tmp_path)
    backend = get_backend(config)
    backend.quiet = True
    store = ReferenceStore(config)
    outcome = run_pipeline(config, tests, backend=backend, store=store)
    return config, tests, backend, store, outcome.results


def _keys(config) -> dict[str, str]:
    manifest = json.loads((config.work_dir / "batch_manifest.json").read_text())
    return {e["model_id"]: k for k, e in manifest.items()}


def _kept(config, key) -> dict:
    return json.loads((config.work_dir / key / "run_stats.json").read_text())


def test_a_run_keeps_each_tests_statistics_beside_its_result(ran):
    config, tests, _, _, results = ran
    keys = _keys(config)
    assert set(keys) == {t.model_id for t in tests}
    for model_id, key in keys.items():
        data = _kept(config, key)
        assert set(data) == {
            "model_id",
            "translation_wall",
            "sim_wall",
            "elapsed",
            "statistics",
            "run_metadata",
        }
        assert data["model_id"] == model_id
        # The run that produced the result, as its reporter held it: what an
        # accept writes as the baseline's provenance.
        assert data["run_metadata"]["backend"] == "Python"
        assert data["run_metadata"]["generated_at"] > 0
        assert data["translation_wall"] is None  # written as null
        assert data["sim_wall"] > 0 and data["elapsed"] >= data["sim_wall"]
        assert data["statistics"]["simulation"]["wall_time"] == data["sim_wall"]
        assert "timing" in results[model_id].statistics


def test_a_new_process_reads_back_the_same_timing(ran):
    config, _, _, _, results = ran
    script = f"""
import json, sys
import dstf
assert dstf.__file__ == {dstf.__file__!r}, dstf.__file__
from dstf.backends import get_backend
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests
config = Config(config_file={str(config.config_file)!r}, work_dir={str(config.work_dir)!r})
backend = get_backend(config)
backend.quiet = True
got = backend.read_last_results(discover_tests(config))
print(json.dumps({{m: r.statistics for m, r in got.items()}}))
"""
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    reloaded = json.loads(out.stdout.strip().splitlines()[-1])
    assert reloaded and set(reloaded) == set(results)
    for model_id, stats in reloaded.items():
        assert stats["timing"] == results[model_id].statistics["timing"]
        assert stats["simulation"] == results[model_id].statistics["simulation"]


def test_a_rerun_rewrites_its_tests_file_and_keeps_the_others(ran):
    from dstf.pipeline import run_pipeline

    config, tests, backend, store, _ = ran
    keys = _keys(config)
    sentinel = {
        "translation_wall": None,
        "sim_wall": 99.0,
        "elapsed": 99.5,
        "statistics": None,
    }
    for model_id, key in keys.items():
        (config.work_dir / key / "run_stats.json").write_text(
            json.dumps({**sentinel, "model_id": model_id})
        )
    ramp = [t for t in tests if t.model_id == SIMPLE_RAMP]
    outcome = run_pipeline(
        config, ramp, backend=backend, store=store, merge=True, candidates=tests
    )
    for model_id, key in keys.items():
        data = _kept(config, key)
        timing = outcome.results[model_id].statistics["timing"]
        if model_id == SIMPLE_RAMP:
            assert data["sim_wall"] != 99.0 and timing["sim_wall"] != 99.0
        else:
            assert data == {**sentinel, "model_id": model_id}
            # A merged-in test's timing is the one its own run kept.
            assert timing == {"sim_wall": 99.0, "total_wall": 99.5}


def test_a_test_dir_without_the_file_reads_back_without_timing(ran, capsys):
    """A run from before the file existed: no timing, and not a word."""
    config, tests, backend, _, _ = ran
    for key in _keys(config).values():
        (config.work_dir / key / "run_stats.json").unlink()
    capsys.readouterr()
    got = backend.read_last_results(tests)
    assert got and all("timing" not in (r.statistics or {}) for r in got.values())
    assert all(r.success for r in got.values())
    assert "WARN" not in capsys.readouterr().err


def test_a_corrupt_file_reads_back_without_timing_and_one_warning(ran, capsys):
    config, tests, backend, _, _ = ran
    key = _keys(config)[CONSTANT_CSV]
    (config.work_dir / key / "run_stats.json").write_text("{not json")
    capsys.readouterr()
    got = backend.read_last_results(tests)
    assert got[CONSTANT_CSV].success
    assert "timing" not in (got[CONSTANT_CSV].statistics or {})
    assert "timing" in got[SIMPLE_RAMP].statistics
    warns = [ln for ln in capsys.readouterr().err.splitlines() if "WARN" in ln]
    assert len(warns) == 1 and "run_stats.json" in warns[0]


def test_a_wall_that_is_not_a_number_reads_back_as_none(tmp_path):
    """The file is data: a JSON ``true`` is an ``int`` to Python, and must
    not read back as a 1.0 s wall."""
    from dstf.storage.run_stats import read_run_stats

    (tmp_path / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "translation_wall": True,
                "sim_wall": "2.0",
                "elapsed": False,
                "statistics": None,
            }
        )
    )
    assert read_run_stats(tmp_path, "Lib.A") == {
        "translation_wall": None,
        "sim_wall": None,
        "elapsed": 0.0,
        "statistics": None,
        "run_metadata": None,
    }


def test_another_models_file_is_ignored_with_one_warning(ran, capsys):
    config, tests, backend, _, _ = ran
    keys = _keys(config)
    src = config.work_dir / keys[SIMPLE_RAMP] / "run_stats.json"
    dst = config.work_dir / keys[CONSTANT_CSV] / "run_stats.json"
    dst.write_text(src.read_text())
    capsys.readouterr()
    got = backend.read_last_results(tests)
    assert "timing" not in (got[CONSTANT_CSV].statistics or {})
    warns = [ln for ln in capsys.readouterr().err.splitlines() if "WARN" in ln]
    assert len(warns) == 1 and SIMPLE_RAMP in warns[0]


def test_a_test_outside_the_read_reads_no_file(ran, capsys):
    """Only a test that reaches the read opens its file: one not handed to
    ``read_results`` (out of scope, or not found in discovery) does not."""
    config, tests, backend, _, _ = ran
    (config.work_dir / _keys(config)[CONSTANT_CSV] / "run_stats.json").write_text(
        "{not json"
    )
    capsys.readouterr()
    got = backend.read_last_results([t for t in tests if t.model_id != CONSTANT_CSV])
    assert got[CONSTANT_CSV].error_message == "Test model not found in discovery"
    assert "WARN" not in capsys.readouterr().err


def test_the_manifests_verdict_stands_over_the_kept_file(ran):
    """The kept run result is rebuilt as a success; the verdict is the
    manifest's, applied after the read — a failed run stays failed."""
    config, tests, backend, _, _ = ran
    path = config.work_dir / "batch_manifest.json"
    data = json.loads(path.read_text())
    for entry in data.values():
        if entry["model_id"] == SIMPLE_RAMP:
            entry["last_run_status"] = "failed"
    path.write_text(json.dumps(data))
    got = backend.read_last_results(tests)
    assert got[SIMPLE_RAMP].success is False
    assert "timing" in got[SIMPLE_RAMP].statistics
    assert got[CONSTANT_CSV].success is True


def test_a_failed_run_keeps_its_file(tmp_path):
    (tmp_path / "work").mkdir()
    m = BatchManifest(
        work_dir=tmp_path / "work",
        manifest={"test_0001": {"model_id": "Lib.A", "ref_id": None}},
    )
    m.record_result(_rr(success=False, elapsed=2.0, translation_wall=1.5), None)
    data = json.loads((tmp_path / "work" / "test_0001" / "run_stats.json").read_text())
    assert data["translation_wall"] == 1.5 and data["elapsed"] == 2.0
    assert data["sim_wall"] is None


def test_record_result_writes_the_runs_metadata_beside_the_result(tmp_path):
    """The metadata of the run that produced the result, in the FILE beside
    it, and read back as written: what an accept writes as provenance."""
    from dstf.storage.run_stats import read_run_stats

    (tmp_path / "work").mkdir()
    m = BatchManifest(
        work_dir=tmp_path / "work",
        manifest={"test_0001": {"model_id": "Lib.A", "ref_id": None}},
    )
    meta = {"backend": "Python", "tool_version": "Python 3.12.1", "generated_at": 5.0}
    m.record_result(_rr(elapsed=1.0), meta)
    test_dir = tmp_path / "work" / "test_0001"
    data = json.loads((test_dir / "run_stats.json").read_text())
    assert data["run_metadata"] == meta
    assert read_run_stats(test_dir, "Lib.A")["run_metadata"] == meta


def test_statistics_json_cannot_hold_do_not_cost_the_verdict(tmp_path):
    import numpy as np

    (tmp_path / "work").mkdir()
    m = BatchManifest(
        work_dir=tmp_path / "work",
        manifest={"test_0001": {"model_id": "Lib.A", "ref_id": None}},
    )
    m.record_result(
        _rr(
            elapsed=1.0,
            sim_wall=1.0,
            statistics={"x": np.float32(1.5), "p": Path("/a")},
        ),
        None,
    )
    assert m.manifest["test_0001"]["last_run_status"] == "passed"
    data = json.loads((tmp_path / "work" / "test_0001" / "run_stats.json").read_text())
    assert data["sim_wall"] == 1.0


def test_a_file_that_cannot_be_written_does_not_cost_the_verdict(tmp_path, capsys):
    """``record_result`` is the abort-proof verdict record: the file is
    written after the manifest, and its failure is one warning line."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "test_0001").write_text("a file where the test dir should be")
    m = BatchManifest(
        work_dir=work,
        manifest={"test_0001": {"model_id": "Lib.A", "ref_id": None}},
    )
    m.record_result(_rr(elapsed=1.0, sim_wall=1.0), None)
    saved = json.loads((work / "batch_manifest.json").read_text())
    assert saved["test_0001"]["last_run_status"] == "passed"
    warns = [ln for ln in capsys.readouterr().err.splitlines() if "WARN" in ln]
    assert len(warns) == 1 and "run_stats.json" in warns[0]


def test_review_accepting_added_variables_keeps_the_runs_statistics(
    tmp_path, monkeypatch
):
    """``run -i``: ``[v]`` adds a variable, ``[a]`` re-reads the result with
    no run result in hand — the stored baseline keeps the run's timing."""
    from dstf.backends import dataset_kind_for, get_backend
    from dstf.cli.review import _interactive_review
    from dstf.comparison.comparator import compare_all
    from dstf.pipeline import run_pipeline
    from dstf.storage.reference_store import ReferenceStore

    config, tests = python_example_copy(tmp_path, baselines=False)
    ramp = [t for t in tests if t.model_id == SIMPLE_RAMP]
    backend, store = get_backend(config), ReferenceStore(config)
    backend.quiet = True
    outcome = run_pipeline(config, ramp, backend=backend, store=store)
    comparisons = compare_all(
        outcome.scope_tests,
        outcome.results,
        store,
        config.tolerance,
        config.default_points,
        dataset_kind=dataset_kind_for(backend),
    )
    answers = iter(["v", "x", "a"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    _interactive_review(
        outcome.scope_tests, outcome.results, comparisons, store, config
    )
    stored = store.get_reference(SIMPLE_RAMP)
    assert stored is not None
    kept = _kept(config, _keys(config)[SIMPLE_RAMP])
    assert stored.baseline.statistics["timing"] == {
        "sim_wall": round(kept["sim_wall"], 2),
        "total_wall": round(kept["elapsed"], 2),
    }
