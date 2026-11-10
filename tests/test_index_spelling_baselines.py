"""A stored baseline reads in the one spelling of a name (#28): a file
Dymola's run wrote as ``T[1, 2]`` is the run's ``T[1,2]`` — the old file
needs no rewrite to be scored, and an accept over it loses nothing."""

from __future__ import annotations

import json

from conftest import make_test_model
from dstf.config import Config
from dstf.storage.baseline_codec import decode_reference


def test_a_baseline_s_names_decode_in_the_one_spelling():
    reference = decode_reference(
        {
            "model_id": "Lib.M",
            "time": [0.0, 1.0],
            "variables": [{"index": 1, "name": "T[1, 2]", "values": [1.0, 2.0]}],
            "diagnostics": [{"name": "n[1, 1]", "final": 3, "min": 0, "max": 3}],
            # Old data: a top-level copy of a listed name's final, dropped.
            "statistics": {"n[1, 1]": 3, "CPUtime": 0.5},
            "comparison": {"variable_overrides": {"T[1, 2]": {"tolerance": 0.1}}},
        }
    )
    assert reference.baseline.names() == ["T[1,2]"]
    assert [d.name for d in reference.baseline.diagnostics] == ["n[1,1]"]
    assert reference.baseline.statistics == {"CPUtime": 0.5}
    assert reference.comparison["variable_overrides"] == {"T[1,2]": {"tolerance": 0.1}}


def test_an_accept_over_a_baseline_spelled_dymola_s_way_loses_nothing(tmp_path):
    from dstf.backends.python import backend as pr
    from dstf.storage.accept import accept_run_results
    from dstf.storage.reference_store import ReferenceStore

    root = tmp_path / "project"
    root.mkdir()
    (root / "testing.json").write_text(
        json.dumps(
            {"source_type": "python", "backend": "Python", "reference_root": "refs"}
        )
    )
    config = Config(config_file=root / "testing.json", work_dir=tmp_path / "work")
    test_dir = config.work_dir / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "result.json").write_text(
        json.dumps(
            {"success": True, "time": [0.0, 1.0], "variables": {"T[1,2]": [1.0, 2.0]}}
        )
    )
    test = make_test_model("Lib.T", variable_patterns=["T[1,2]"])

    def accept() -> list[str]:
        store = ReferenceStore(config)
        result = pr.PythonBackend(config).read_result(test, "test_0001", None)
        stored, skipped, warnings, _ = accept_run_results(
            store, [test], {test.model_id: result}, config.work_dir
        )
        assert (stored, skipped) == (1, [])
        return warnings

    assert accept() == []
    (path,) = config.reference_root.rglob("ref_*.json")
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["variables"][0]["name"] = "T[1, 2]"  # as Dymola's run wrote it
    path.write_text(json.dumps(stored), encoding="utf-8")

    assert accept() == []
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert [v["name"] for v in rewritten["variables"]] == ["T[1,2]"]
