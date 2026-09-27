"""One timing block for every backend (``run_timing``), built where results
are read."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_test_model
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
