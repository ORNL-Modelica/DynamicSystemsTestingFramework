"""The baseline's name that flows to every surface comes from ONE source:
the code of the model id, for a model whose baseline the store reads.

The codes are LITERALS (``sha256sum`` over the model id, outside this
suite). Nothing reads a name from inside a file: an old file's
``"test_id"`` is ignored. The dashboard's own-file check, by path, is
``test_dashboard_render.test_the_comparisons_reference_wins_over_the_sidecars``.
"""

from __future__ import annotations

import json

import numpy as np

from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_one
from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.storage.reference_store import ReferenceStore

PULSE, PULSE_CODE = "TRANSFORM.Blocks.Examples.PulseSine_Test", "5341dec448cc"


def _model(model_id: str, patterns=("x",)) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=len(patterns),
        variable_patterns=list(patterns),
        source="spec",
    )


def _result(model_id: str) -> TestResult:
    t = np.linspace(0.0, 10.0, 11)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=np.sin(t), name="x")],
    )


def _old_file(path, model_id: str, test_id: str) -> None:
    """A baseline converted by name but still holding its old number."""
    t = np.linspace(0.0, 10.0, 11)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model_id": model_id,
                "test_id": test_id,
                "status": "active",
                "simulation": {},
                "time": list(t),
                "variables": [
                    {
                        "index": 1,
                        "name": "x",
                        "expression": "",
                        "values": list(np.sin(t)),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_the_comparisons_code_is_its_models_even_beside_an_old_number(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(
        Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    )
    path = store.ref_dir / f"ref_{PULSE_CODE}.json"
    _old_file(path, PULSE, "0011")

    comp = compare_one(_model(PULSE), _result(PULSE), store)

    assert comp.baseline_path == str(path)


def test_a_simulate_only_test_with_no_baseline_names_no_code(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(
        Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    )
    simulate_only = _model("Lib.SimOnly", patterns=())
    assert simulate_only.is_simulate_only

    comp = compare_one(simulate_only, _result("Lib.SimOnly"), store)

    assert comp.passed
    assert comp.baseline_path is None
