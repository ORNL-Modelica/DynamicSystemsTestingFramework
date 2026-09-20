"""Listing and reading ANY variable of a test's result file, through the
backend class (the report never instantiates a backend)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.base import Backend, last_test_keys
from dstf.backends.common.json_driver import JsonDriverBackend
from dstf.backends.common.result_files import (
    json_names,
    json_series,
    npz_names,
    npz_series,
)
from dstf.backends.python.backend import PythonBackend

T = [0.0, 0.5, 1.0]


def _json_result(test_dir: Path, **variables) -> None:
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "result.json").write_text(
        json.dumps({"success": True, "time": T, "variables": variables}),
        encoding="utf-8",
    )


def test_json_names_lists_every_variable_in_file_order(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    assert json_names(tmp_path / "result.json") == ["x", "z"]


def test_json_series_reads_the_named_ones_on_the_shared_time(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    got = json_series(tmp_path / "result.json", ["z", "absent"])
    assert list(got) == ["z"]
    time, values = got["z"]
    assert time.tolist() == T and values.tolist() == [5.0, 5.0, 5.0]


def test_a_missing_or_unreadable_json_is_none_and_empty(tmp_path):
    assert json_names(tmp_path / "result.json") is None
    assert json_series(tmp_path / "result.json", ["x"]) == {}
    (tmp_path / "result.json").write_text("{not json", encoding="utf-8")
    assert json_names(tmp_path / "result.json") is None
    assert json_series(tmp_path / "result.json", ["x"]) == {}


def test_a_series_whose_length_disagrees_with_time_is_left_out(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], short=[1, 2])
    assert list(json_series(tmp_path / "result.json", ["x", "short"])) == ["x"]


def _npz(path: Path) -> None:
    arr = np.zeros(3, dtype=[("time", "f8"), ("h", "f8"), ("v", "f8")])
    arr["time"], arr["h"], arr["v"] = T, [1, 2, 3], [9, 8, 7]
    np.savez_compressed(path, result=arr)


def test_npz_names_leaves_time_out(tmp_path):
    _npz(tmp_path / "result.npz")
    assert npz_names(tmp_path / "result.npz") == ["h", "v"]


def test_npz_series_reads_the_named_columns(tmp_path):
    _npz(tmp_path / "result.npz")
    got = npz_series(tmp_path / "result.npz", ["v", "absent"])
    assert list(got) == ["v"]
    assert got["v"][0].tolist() == T and got["v"][1].tolist() == [9.0, 8.0, 7.0]
    assert npz_series(tmp_path / "result.npz", ["time"]) == {}


def test_a_missing_npz_is_none_and_empty(tmp_path):
    assert npz_names(tmp_path / "result.npz") is None
    assert npz_series(tmp_path / "result.npz", ["h"]) == {}


def test_the_base_backend_keeps_no_readable_file(tmp_path):
    assert Backend.result_names(tmp_path) is None
    assert Backend.result_series(tmp_path, ["x"]) == {}


def test_the_json_driver_backends_read_their_result_json(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    assert PythonBackend.result_names(tmp_path) == ["x", "z"]
    assert list(PythonBackend.result_series(tmp_path, ["z"])) == ["z"]
    assert (
        JsonDriverBackend.result_names.__func__ is PythonBackend.result_names.__func__
    )


def test_fmpy_reads_its_npz(tmp_path):
    pytest.importorskip("fmpy")
    from dstf.backends.fmpy.backend import FmpyBackend

    _npz(tmp_path / FmpyBackend.RESULT_FILENAME)
    assert FmpyBackend.result_names(tmp_path) == ["h", "v"]
    assert FmpyBackend.result_series(tmp_path, ["h"])["h"][1].tolist() == [
        1.0,
        2.0,
        3.0,
    ]


@pytest.mark.parametrize(
    ("module", "cls", "filename"),
    [
        ("dstf.backends.dymola.backend", "DymolaBackend", "dsres.mat"),
        ("dstf.backends.openmodelica.backend", "OpenModelicaBackend", "result_res.mat"),
    ],
)
def test_the_mat_backends_name_their_file_and_use_the_mat_pair(
    module, cls, filename, tmp_path, monkeypatch
):
    import importlib

    from dstf.backends.common import result_files

    backend = getattr(importlib.import_module(module), cls)
    seen: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        result_files, "mat_names", lambda p: seen.append(("names", p)) or ["a"]
    )
    monkeypatch.setattr(
        result_files,
        "mat_series",
        lambda p, names: seen.append(("series", p)) or {},
    )
    assert backend.result_names(tmp_path) == ["a"]
    assert backend.result_series(tmp_path, ["a"]) == {}
    assert seen == [("names", tmp_path / filename), ("series", tmp_path / filename)]


def test_the_persistent_backends_inherit_the_readers():
    from dstf.backends.dymola.backend import DymolaBackend
    from dstf.backends.dymola.persistent_backend import PersistentDymolaBackend

    assert (
        PersistentDymolaBackend.result_names.__func__
        is DymolaBackend.result_names.__func__
    )


def test_mat_series_reads_through_the_mat_reader(tmp_path, monkeypatch):
    from dstf.backends.common import result_files

    calls = []

    def fake_read(path, variable_names=None):
        calls.append((path, variable_names))
        t = np.asarray(T)
        return {"a": (t, t * 2), "time": (t, t)}

    monkeypatch.setattr(result_files, "read_result_mat", fake_read)
    (tmp_path / "r.mat").write_bytes(b"")
    got = result_files.mat_series(tmp_path / "r.mat", ["a", "b"])
    assert calls == [(tmp_path / "r.mat", {"a", "b"})]
    assert list(got) == ["a"] and got["a"][1].tolist() == [0.0, 1.0, 2.0]


def test_mat_names_leaves_time_out_in_either_case(tmp_path, monkeypatch):
    from dstf.backends.common import result_files

    monkeypatch.setattr(
        result_files, "list_result_mat_variables", lambda p: ["Time", "a", "time"]
    )
    (tmp_path / "r.mat").write_bytes(b"")
    assert result_files.mat_names(tmp_path / "r.mat") == ["a"]


def test_mat_pair_on_a_missing_file(tmp_path):
    from dstf.backends.common import result_files

    assert result_files.mat_names(tmp_path / "r.mat") is None
    assert result_files.mat_series(tmp_path / "r.mat", ["a"]) == {}


def test_last_test_keys_maps_model_to_key(tmp_path):
    (tmp_path / "batch_manifest.json").write_text(
        json.dumps(
            {
                "test_0001": {"model_id": "L.A", "ref_id": None},
                "test_0007": {"model_id": "L.B", "ref_id": None},
            }
        ),
        encoding="utf-8",
    )
    assert last_test_keys(tmp_path) == {"L.A": "test_0001", "L.B": "test_0007"}
    assert last_test_keys(tmp_path / "nowhere") == {}
