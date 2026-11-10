"""Every result reader lists a variable in the one spelling of its name
(#28): a file that writes ``T[1, 2]`` (Dymola) and one that writes
``T[1,2]`` (OpenModelica) both list ``T[1,2]``, and either reads its data
when asked for ``T[1,2]``. A file holding both spellings holds one variable
twice, and is not read."""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model as _tm
from dstf.backends.common.live_mat import LiveMatReader
from dstf.backends.common.mat_reader import (
    ResultMatFile,
    list_result_mat_variables,
    read_result_mat,
)
from dstf.backends.common.result_files import (
    json_names,
    json_series,
    mat_names,
    mat_series,
    npz_names,
    npz_series,
)
from dstf.backends.common.result_variables import extract_variables
from mat4_files import write_result_mat

T = np.array([0.0, 0.5, 1.0])
VALUES = np.array([1.0, 2.0, 3.0])
SPELLINGS = ["T[1, 2]", "T[1,2]"]


def _mat(path: Path, raw: str, tool: str) -> Path:
    write_result_mat(
        path, tool=tool, dtype="<f8", time=T, trajectories={raw: VALUES, "x": -VALUES}
    )
    return path


@pytest.mark.parametrize("tool", ["dymola", "openmodelica"])
@pytest.mark.parametrize("raw", SPELLINGS)
class TestAResultMat:
    def test_lists_the_name_in_the_one_spelling(self, tmp_path, raw, tool):
        path = _mat(tmp_path / "r.mat", raw, tool)
        assert list_result_mat_variables(path)[1:] == ["T[1,2]", "x"]
        assert mat_names(path) == ["T[1,2]", "x"]
        assert ResultMatFile(path).names == ("T[1,2]", "x")

    def test_reads_the_variable_by_that_name(self, tmp_path, raw, tool):
        path = _mat(tmp_path / "r.mat", raw, tool)
        data = read_result_mat(path, {"T[1,2]"})
        assert list(data) == ["T[1,2]"]
        assert data["T[1,2]"][1].tolist() == [1.0, 2.0, 3.0]
        assert mat_series(path, ["T[1,2]"])["T[1,2]"][1].tolist() == [1.0, 2.0, 3.0]

    def test_a_tracked_variable_is_extracted_under_that_name(self, tmp_path, raw, tool):
        path = _mat(tmp_path / "r.mat", raw, tool)
        test = _tm("Lib.M", variable_patterns=["T[1,2]"])
        variables, _ = extract_variables(read_result_mat(path), test, [])
        assert [(v.name, v.values.tolist()) for v in variables] == [
            ("T[1,2]", [1.0, 2.0, 3.0])
        ]

    def test_the_live_reader_lists_and_polls_it_by_that_name(self, tmp_path, raw, tool):
        path = _mat(tmp_path / "r.mat", raw, tool)
        reader = LiveMatReader(path)
        snapshot = reader.poll(["T[1,2]"])
        assert reader.names == ("T[1,2]", "x")
        assert snapshot.series["T[1,2]"].values.tolist() == [1.0, 2.0, 3.0]


@pytest.mark.parametrize("tool", ["dymola", "openmodelica"])
def test_a_mat_holding_both_spellings_is_not_read_and_says_why(tmp_path, tool, caplog):
    path = tmp_path / "r.mat"
    write_result_mat(
        path,
        tool=tool,
        dtype="<f8",
        time=T,
        trajectories={"T[1, 2]": VALUES, "T[1,2]": -VALUES},
    )
    with caplog.at_level(logging.ERROR):
        assert list_result_mat_variables(path) is None
        assert read_result_mat(path) is None
    assert "'T[1, 2]' and 'T[1,2]' are the same variable" in caplog.text


def _json(path: Path, **variables) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "success": True,
                "time": T.tolist(),
                "variables": {k: v.tolist() for k, v in variables.items()},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("raw", SPELLINGS)
def test_a_json_result_lists_and_reads_the_one_spelling(tmp_path, raw):
    path = _json(tmp_path / "result.json", **{raw: VALUES, "x": -VALUES})
    assert json_names(path) == ["T[1,2]", "x"]
    assert json_series(path, ["T[1,2]"])["T[1,2]"][1].tolist() == [1.0, 2.0, 3.0]


def test_a_json_result_holding_both_spellings_is_not_read(tmp_path):
    path = _json(tmp_path / "result.json", **{"T[1, 2]": VALUES, "T[1,2]": -VALUES})
    assert json_names(path) is None
    assert json_series(path, ["T[1,2]"]) == {}


def _python_backend(tmp_path):
    from dstf.backends.python.backend import PythonBackend

    cfg = SimpleNamespace(
        backend_path=None,
        source_path=tmp_path,
        work_dir=tmp_path / "work",
        timeout=1,
        diagnostic_variables=[],
    )
    return PythonBackend(cfg)


@pytest.mark.parametrize("raw", SPELLINGS)
def test_a_python_result_is_read_under_the_one_spelling(tmp_path, raw):
    _json(tmp_path / "work" / "test_0001" / "result.json", **{raw: VALUES})
    test = _tm("Lib.M", variable_patterns=["T[1,2]"])
    result = _python_backend(tmp_path).read_result(test, "test_0001", None)
    assert result.success, result.error_message
    assert [(v.name, v.values.tolist()) for v in result.variables] == [
        ("T[1,2]", [1.0, 2.0, 3.0])
    ]


def test_a_python_result_holding_both_spellings_fails_the_test_saying_why(
    tmp_path,
):
    _json(
        tmp_path / "work" / "test_0001" / "result.json",
        **{"T[1, 2]": VALUES, "T[1,2]": -VALUES},
    )
    test = _tm("Lib.M", variable_patterns=["T[1,2]"])
    result = _python_backend(tmp_path).read_result(test, "test_0001", None)
    assert result.success is False
    assert result.error_message == (
        "result.json cannot be read: 'T[1, 2]' and 'T[1,2]' are the same variable"
    )


def _npz(path: Path, *names: str) -> Path:
    arr = np.zeros(3, dtype=[("time", "f8"), *((n, "f8") for n in names)])
    arr["time"] = T
    for i, n in enumerate(names):
        arr[n] = VALUES * (i + 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, result=arr)
    return path


@pytest.mark.parametrize("raw", SPELLINGS)
def test_an_npz_result_lists_and_reads_the_one_spelling(tmp_path, raw):
    path = _npz(tmp_path / "result.npz", raw, "x")
    assert npz_names(path) == ["T[1,2]", "x"]
    assert npz_series(path, ["T[1,2]"])["T[1,2]"][1].tolist() == [1.0, 2.0, 3.0]


def test_an_npz_result_holding_both_spellings_is_not_read(tmp_path):
    path = _npz(tmp_path / "result.npz", "T[1, 2]", "T[1,2]")
    assert npz_names(path) is None
    assert npz_series(path, ["T[1,2]"]) == {}


class TestAnFmuThatSpellsItsNamesDymolasWay:
    """The FMU's model description says ``T[1, 2]``: the test names
    ``T[1,2]``, fmpy is asked for the FMU's own name, and the result is
    read back under the one spelling."""

    DECLARED = ("T[1, 2]", "h")

    def _backend(self, tmp_path, monkeypatch, captured):
        fake = types.ModuleType("fmpy")
        fake.read_model_description = lambda p: SimpleNamespace(
            modelVariables=[SimpleNamespace(name=n) for n in self.DECLARED]
        )
        fake.simulation = SimpleNamespace(
            _get_output_variables=lambda md: [SimpleNamespace(name="T[1, 2]")]
        )

        def sim(**kw):
            captured.update(kw)
            wanted = kw["output"] or ["T[1, 2]"]
            names = ["time", *(n for n in wanted if n in self.DECLARED)]
            arr = np.zeros(3, dtype=[(n, "f8") for n in names])
            arr["time"] = T
            for n in names[1:]:
                arr[n] = VALUES
            return arr

        fake.simulate_fmu = sim
        monkeypatch.setitem(sys.modules, "fmpy", fake)
        from dstf.backends.fmpy.backend import FmpyBackend

        cfg = SimpleNamespace(
            work_dir=tmp_path / "work",
            source_type="fmu",
            backend_name="FMPy",
            parallel=1,
            timeout=60,
            diagnostic_variables=[],
        )
        return FmpyBackend(cfg)

    def _test(self, tmp_path, patterns):
        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"fake fmu")
        return _tm("Model", source_file=fmu, variable_patterns=patterns)

    @pytest.mark.parametrize("patterns", [["T[1,2]"], ["T[1,*]"]])
    def test_fmpy_is_asked_for_the_fmu_s_name_and_the_result_reads_the_one(
        self, tmp_path, monkeypatch, patterns
    ):
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, patterns)
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success, rr.error_message
        assert captured["output"] == ["T[1, 2]"]
        result = backend.read_result(test, "test_0001", rr)
        assert [(v.name, v.values.tolist()) for v in result.variables] == [
            ("T[1,2]", [1.0, 2.0, 3.0])
        ]
        assert backend.result_names(tmp_path / "work" / "test_0001") == ["T[1,2]"]

    def test_with_no_patterns_fmpy_s_default_set_is_recorded_as_is(
        self, tmp_path, monkeypatch
    ):
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, [])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success, rr.error_message
        assert captured["output"] is None
        assert backend.result_names(tmp_path / "work" / "test_0001") == ["T[1,2]"]
