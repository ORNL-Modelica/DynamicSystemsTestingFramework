"""A baseline is found and written by its model's name, ``ref_<code>.json``.

The names are LITERALS (``sha256sum`` over the model id, outside this
suite): a test that recomputed them the way the code does would follow any
change to the rule. Each test reads the folder or the file it is about.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
from contextlib import redirect_stderr, redirect_stdout

import numpy as np
import pytest

from conftest import PROJECT_ROOT, PROVENANCE, PROVENANCES
from dstf.backends.types import TestResult, VariableResult
from dstf.cli.main import main
from dstf.comparison.comparator import compare_one
from dstf.config import Config, ConfigError
from dstf.discovery.test_registry import TestModel
from dstf.reporting.overlay_loader import load_sibling_backend_overlays
from dstf.reporting.status import FAIL, NO_REF, classify, refusal
from dstf.storage.reference_store import ReferenceStore

A, A_FILE = "Lib.A", "ref_6e187be76792.json"
OTHER = "Lib.Other"
#: A real TRANSFORM model whose code is all digits.
HEXE, HEXE_FILE = (
    "TRANSFORM.Media.IdealGases.Examples.HeXeAlternate",
    "ref_121677875640.json",
)
RAMP, RAMP_FILE = "PythonTestingLib.Examples.SimpleRamp", "ref_9bbf24e6f8aa.json"


def _model(model_id: str) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=1,
        variable_patterns=["x"],
        source="spec",
    )


def _result(model_id: str) -> TestResult:
    t = np.linspace(0.0, 10.0, 11)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=np.sin(t), name="x")],
    )


def _config(sample_models_dir, root) -> Config:
    return Config(source_path=sample_models_dir, reference_root=root)


def _hand_written(path, model_id: str, *, status="active", values=None) -> None:
    """A baseline file as a person or another tool might leave one."""
    t = list(np.linspace(0.0, 10.0, 11))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "model_id": model_id,
        "status": status,
        "simulation": {},
        "time": t,
        "variables": [
            {
                "index": 1,
                "name": "x",
                "expression": "",
                "values": values if values is not None else list(np.sin(t)),
            }
        ],
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def test_an_accept_writes_the_models_name_and_nothing_beside_it(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    assert store.store_reference(_model(A), _result(A), provenance=PROVENANCE)
    # No number allocated, no high-water mark: the one file, by its name.
    assert sorted(p.name for p in store.ref_dir.iterdir()) == [A_FILE]
    data = json.loads((store.ref_dir / A_FILE).read_text(encoding="utf-8"))
    assert data["model_id"] == A
    assert "test_id" not in data


def test_an_accept_over_another_models_file_under_the_name_is_refused_untouched(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    squatter = store.ref_dir / A_FILE
    _hand_written(squatter, OTHER)
    before = squatter.read_bytes()

    stored, skipped, _ = store.accept_results(
        [_model(A)], {A: _result(A)}, provenance=PROVENANCES
    )

    assert stored == []
    assert skipped == [
        (
            A,
            "ref_6e187be76792.json holds the baseline of Lib.Other, not of "
            "Lib.A — two model ids share its code, or the file was copied by hand",
        )
    ]
    assert squatter.read_bytes() == before
    assert sorted(p.name for p in store.ref_dir.iterdir()) == [A_FILE]


def test_a_file_of_another_model_under_the_name_refuses_the_test(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / A_FILE, OTHER)

    comp = compare_one(_model(A), _result(A), store)

    assert classify(comp) == FAIL
    assert refusal(comp) == (
        "ref_6e187be76792.json holds the baseline of Lib.Other, not of "
        "Lib.A — two model ids share its code, or the file was copied by hand"
    )


def test_a_baseline_whose_status_is_no_status_is_refused_by_name(
    sample_models_dir, tmp_path
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    path = store.ref_dir / A_FILE
    _hand_written(path, A, status="obsolete")
    before = path.read_bytes()
    said = (
        'ref_6e187be76792.json: status "obsolete" is not a status — '
        "delete the file to remove a baseline"
    )

    comp = compare_one(_model(A), _result(A), store)
    stored, skipped, _ = store.accept_results(
        [_model(A)], {A: _result(A)}, provenance=PROVENANCES
    )

    assert (classify(comp), refusal(comp)) == (FAIL, said)
    assert (stored, skipped) == ([], [(A, said)])
    assert path.read_bytes() == before


def test_a_skipped_baseline_is_read(sample_models_dir, tmp_path):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / A_FILE, A, status="skip")
    assert store.get_reference(A).model_id == A


def test_a_file_named_for_another_code_is_warned_and_left_out(
    sample_models_dir, tmp_path, caplog
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / "ref_000000000000.json", A)

    with caplog.at_level(logging.WARNING):
        listed = store.list_models()

    assert listed == []
    assert "ref_000000000000.json" in caplog.text and A_FILE in caplog.text
    assert classify(compare_one(_model(A), _result(A), store)) == NO_REF


def test_a_misnamed_file_leaves_its_test_without_a_baseline_or_a_page(tmp_path):
    """Over the Python example: SimpleRamp's file renamed to a code that is
    not its own. Its run reads NO_REF, and the report writes no page under
    either name."""
    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    folder = lib / "Resources" / "ReferenceResults" / "Python" / "linux"
    (folder / RAMP_FILE).rename(folder / "ref_000000000000.json")
    work = tmp_path / "work"
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        main(
            [
                "--config",
                str(lib / "Resources" / "ReferenceResults" / "testing.json"),
                "run",
                "--no-browser",
                "--report",
                "--work-dir",
                str(work),
            ]
        )

    verdicts = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    status = {t["model_id"]: t["status"] for t in verdicts["tests"]}
    assert status[RAMP] == "NO_REF"
    pages = {p.name for p in (work / "reports").iterdir() if p.is_dir()}
    assert "ref_9bbf24e6f8aa" not in pages
    assert "ref_000000000000" not in pages


def test_a_file_whose_model_id_is_no_name_is_warned_and_left_out(
    sample_models_dir, tmp_path, caplog
):
    """A file of the folder whose ``model_id`` is a number: no name can be
    computed from it, so it is warned of and listed by nothing — and the
    folder's good baseline is still found."""
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / A_FILE, A)
    (store.ref_dir / "ref_aaaaaaaaaaaa.json").write_text(
        '{"model_id": 5, "status": "active"}', encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING, logger="dstf.storage.ref_index"):
        listed = store.list_models()

    assert listed == [A]
    assert store.reference_file(A) == store.ref_dir / A_FILE
    assert [r.getMessage() for r in caplog.records] == [
        "ref_aaaaaaaaaaaa.json cannot be read (model_id 5 is not a string) — "
        "fix or delete it"
    ]


def test_a_run_over_a_file_whose_model_id_is_no_name_scores_every_test(
    tmp_path, caplog
):
    """Over the Python example, one more file whose ``model_id`` is 5: the
    run scores its three tests against their baselines as before."""
    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    folder = lib / "Resources" / "ReferenceResults" / "Python" / "linux"
    (folder / "ref_aaaaaaaaaaaa.json").write_text(
        '{"model_id": 5, "status": "active"}', encoding="utf-8"
    )
    work = tmp_path / "work"
    with (
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
        caplog.at_level(logging.WARNING, logger="dstf.storage.ref_index"),
    ):
        code = main(
            [
                "--config",
                str(lib / "Resources" / "ReferenceResults" / "testing.json"),
                "run",
                "--no-browser",
                "--work-dir",
                str(work),
            ]
        )

    assert code == 0
    verdicts = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    assert sorted((t["model_id"], t["status"]) for t in verdicts["tests"]) == [
        ("PythonTestingLib.Examples.ConstantCsv", "PASS"),
        ("PythonTestingLib.Examples.PointsCheck", "PASS"),
        (RAMP, "PASS"),
    ]
    assert {r.getMessage() for r in caplog.records} == {
        "ref_aaaaaaaaaaaa.json cannot be read (model_id 5 is not a string) — "
        "fix or delete it"
    }


def test_a_folder_of_numbered_names_is_refused_with_the_way_to_convert_it(
    sample_models_dir, tmp_path
):
    config = _config(sample_models_dir, tmp_path / "refs")
    _hand_written(config.reference_dir / "ref_0001.json", A)
    _hand_written(config.reference_dir / "ref_0002.json", OTHER)

    with pytest.raises(ConfigError) as caught:
        ReferenceStore(config)

    assert str(caught.value) == (
        f"{config.reference_dir} holds baselines under the old numbered names "
        "(ref_0001.json, …) — convert them with scripts/convert_baseline_names.py, "
        "see CHANGELOG"
    )


def test_a_command_over_a_numbered_folder_exits_with_the_sentence(tmp_path):
    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    folder = lib / "Resources" / "ReferenceResults" / "Python" / "linux"
    (folder / RAMP_FILE).rename(folder / "ref_0003.json")
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = main(
            [
                "--config",
                str(lib / "Resources" / "ReferenceResults" / "testing.json"),
                "manifest",
                "show",
            ]
        )
    assert code == 2
    assert "(ref_0003.json) — convert them with scripts/convert_baseline_names.py" in (
        err.getvalue()
    )


def test_a_code_of_digits_alone_is_a_name_not_a_number(sample_models_dir, tmp_path):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / HEXE_FILE, HEXE)
    fresh = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    assert fresh.list_models() == [HEXE]
    assert fresh.reference_file(HEXE) == store.ref_dir / HEXE_FILE


def test_a_sibling_overlay_is_the_file_named_for_the_model_once(
    sample_models_dir, tmp_path
):
    """49 copies of the model's baseline under other names beside the one
    named for it: the overlay is that file's data, and only once."""
    root = tmp_path / "refs"
    sibling = root / "Other" / "linux"
    for i in range(1, 50):
        _hand_written(sibling / f"ref_{i:012x}.json", A)
    _hand_written(sibling / A_FILE, A, values=[7.0] * 11)

    overlays = load_sibling_backend_overlays(_config(sample_models_dir, root), A)

    assert [(ov.name, ov.variables["x"].values) for ov in overlays] == [
        ("Other/linux", [7.0] * 11)
    ]


def test_a_sibling_file_of_another_model_is_no_overlay(sample_models_dir, tmp_path):
    root = tmp_path / "refs"
    _hand_written(root / "Other" / "linux" / A_FILE, OTHER)
    _hand_written(root / "Third" / "linux" / A_FILE, A, values=[3.0] * 11)

    overlays = load_sibling_backend_overlays(_config(sample_models_dir, root), A)

    assert [ov.name for ov in overlays] == ["Third/linux"]
