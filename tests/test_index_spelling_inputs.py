"""Every place a person or a file hands the program a variable name takes
it in the one spelling (#28): ``a[1, 1]`` typed in the spec, a patch,
testing.json, the review prompt or a companion's record is ``a[1,1]`` from
then on — and a file the program writes back holds that spelling."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

from conftest import PROVENANCE
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config, ConfigError
from dstf.discovery.spec_parser import spec_document_from_bytes
from dstf.storage.reference_store import ReferenceStore


def _decoded(entry: dict) -> dict:
    raw = json.dumps({"tests": [entry]}).encode()
    return spec_document_from_bytes(raw, "test_spec.json")["tests"][0]


def test_the_spec_decoder_spells_every_name_of_an_entry_one_way():
    entry = _decoded(
        {
            "model": "Lib.M",
            "variables": ["pipe.T[1, 1]", "pipe.T[2, *]"],
            "diagnostic_variables": ["a.n[1, 2]"],
            "comparison": {
                "variable_overrides": {"pipe.T[1, 1]": {"mode": "tube"}, "x": {}}
            },
            "metrics": {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "pipe.T[1, 1]"},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "scalar",
                                "variable": "a.n[1, 2]",
                                "quantity": "statistics.a[1, 2]",
                            }
                        ],
                    },
                ],
            },
            "plots": [{"var": "pipe.T[1, 1]", "context": ["pipe.T[1, 2]"]}],
        }
    )
    assert entry["variables"] == ["pipe.T[1,1]", "pipe.T[2,*]"]
    assert entry["diagnostic_variables"] == ["a.n[1,2]"]
    assert list(entry["comparison"]["variable_overrides"]) == ["pipe.T[1,1]", "x"]
    first, warn = entry["metrics"]["children"]
    assert first["variable"] == "pipe.T[1,1]"
    (scalar,) = warn["children"]
    assert scalar["variable"] == "a.n[1,2]"
    # A scalar's quantity is a path into the statistics, not a name.
    assert scalar["quantity"] == "statistics.a[1, 2]"
    assert entry["plots"] == [{"var": "pipe.T[1,1]", "context": ["pipe.T[1,2]"]}]


def test_two_overrides_that_are_one_variable_are_refused_naming_both():
    with pytest.raises(ConfigError) as caught:
        _decoded(
            {
                "model": "Lib.M",
                "comparison": {
                    "variable_overrides": {"a[1, 1]": {}, "a[1,1]": {"tolerance": 1}}
                },
            }
        )
    assert str(caught.value) == (
        "test_spec.json: tests[Lib.M].comparison.variable_overrides: "
        "'a[1, 1]' and 'a[1,1]' are the same variable"
    )


def test_a_patch_lands_in_the_file_in_the_one_spelling(tmp_path, sample_models_dir):
    """Values and pointer tokens alike; the file is read back after a
    patch of several ops (one replace through a pointer typed the other
    way than the file was)."""
    from dstf.discovery.spec_update import apply_validated_patches

    spec = tmp_path / "test_spec.json"
    spec.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "model": "Lib.M",
                        "variables": ["d[1, 2]"],
                        "comparison": {
                            "variable_overrides": {"d[1, 2]": {"tolerance": 0.1}}
                        },
                    }
                ]
            }
        )
    )
    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    ops = [
        {
            "op": "add",
            "path": "/comparison/variable_overrides/a[1, 1]",
            "value": {"mode": "tube", "tube_rel": 0.02},
        },
        {
            "op": "replace",
            "path": "/comparison/variable_overrides/d[1, 2]/tolerance",
            "value": 0.05,
        },
        {
            "op": "add",
            "path": "/plots",
            "value": [{"var": "b[2, 2]", "context": ["c[3, 3]"]}],
        },
    ]
    apply_validated_patches(spec, [("Lib.M", ops)], config)
    (entry,) = json.loads(spec.read_text())["tests"]
    assert entry == {
        "model": "Lib.M",
        "variables": ["d[1,2]"],
        "comparison": {
            "variable_overrides": {
                "d[1,2]": {"tolerance": 0.05},
                "a[1,1]": {"mode": "tube", "tube_rel": 0.02},
            }
        },
        "plots": [{"var": "b[2,2]", "context": ["c[3,3]"]}],
    }


def test_testing_json_s_diagnostic_variables_are_spelled_one_way(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "backend": "Python",
                "reference_root": "refs",
                "diagnostic_variables": ["CPUtime", "a.n[1, 2]"],
            }
        )
    )
    config = Config(config_file=root / "testing.json", work_dir=tmp_path / "work")
    assert config.diagnostic_variables == ["CPUtime", "a.n[1,2]"]


def test_the_review_prompt_splits_on_commas_outside_an_index(
    tmp_path, monkeypatch, capsys
):
    """``a[1, 1], b`` is two patterns, not three; both written to the spec
    in the one spelling."""
    from dstf.cli.review import _interactive_review
    from dstf.comparison.types import TestComparison

    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("package Lib\nend Lib;\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    config = Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=work)
    answers = iter(["v", "a[1, 1], b", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = TestResult(model_id="Lib.A", success=True)
    _interactive_review(
        [],
        {"Lib.A": result},
        [TestComparison("Lib.A", passed=True)],
        ReferenceStore(config),
        config,
    )
    out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
    assert "Added 2 pattern(s) to test_spec.json" in out
    (entry,) = json.loads(config.spec_path.read_text())["tests"]
    assert entry["variables"] == ["a[1,1]", "b"]


def _store_holding(tmp_path, sample_models_dir, name: str):
    from dstf.discovery.test_registry import TestModel

    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    store = ReferenceStore(config)
    test = TestModel(
        model_id="Lib.Test1",
        source_file=None,
        n_vars=0,
        variable_patterns=[name],
        source="spec",
    )
    time = np.linspace(0, 1, 5)
    result = TestResult(
        model_id=test.model_id,
        success=True,
        variables=[VariableResult(index=1, time=time, values=time, name=name)],
    )
    store.store_reference(test, result, provenance=PROVENANCE)
    return store, test


def test_a_companion_s_recorded_mapping_reads_in_the_one_spelling(
    tmp_path, sample_models_dir
):
    store, test = _store_holding(tmp_path, sample_models_dir, "a[1,1]")
    rig = tmp_path / "rig.csv"
    rig.write_text('time,"b[2, 2]"\n0,0\n1,1\n')
    store.add_companion(
        test.model_id, "rig", path=rig, variables={"a[1, 1]": "b[2, 2]"}
    )
    assert store.get_companions(test.model_id)["rig"].variables == {"a[1,1]": "b[2,2]"}


def test_a_csv_companion_s_headers_are_spelled_one_way(tmp_path):
    from dstf.reporting.overlay_loader import companion_variable_names

    rig = tmp_path / "rig.csv"
    rig.write_text('time,"b[2, 2]",c\n0,0,1\n1,1,2\n')
    assert companion_variable_names(rig, "csv") == ["b[2,2]", "c"]


def test_companion_add_takes_a_map_typed_either_way_and_records_one(
    tmp_path, sample_models_dir
):
    from dstf.cli import main

    store, test = _store_holding(tmp_path, sample_models_dir, "a[1,1]")
    rig = tmp_path / "rig.csv"
    rig.write_text('time,"b[2,2]"\n0,0\n1,1\n')
    rc = main(
        [
            "companion",
            "add",
            test.model_id,
            "rig",
            str(rig),
            "--map",
            "a[1, 1]=b[2, 2]",
            "--source-path",
            str(store.config.source_path),
            "--reference-root",
            str(store.config.reference_root),
            "--backend",
            str(store.config.backend_name),
        ]
    )
    assert rc == 0
    (meta,) = [
        p
        for p in store.config.reference_root.rglob("rig.json")
        if p.parent.name != "soft_checks"
    ]
    assert json.loads(meta.read_text())["variables"] == {"a[1,1]": "b[2,2]"}
