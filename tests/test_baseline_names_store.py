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
POINTS, POINTS_FILE = "PythonTestingLib.Examples.PointsCheck", "ref_8cb5c68dba3b.json"


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


@pytest.mark.parametrize("status", ["obsolete", "skip"])
def test_a_baseline_whose_status_is_no_status_is_refused_by_name(
    sample_models_dir, tmp_path, status
):
    """``active`` is the one status. ``skip`` was one, read by nothing but
    ``manifest show``'s count, so a skipped baseline was compared all the
    same; a file still marked so is refused, as ``obsolete`` is."""
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    path = store.ref_dir / A_FILE
    _hand_written(path, A, status=status)
    before = path.read_bytes()
    said = (
        f'ref_6e187be76792.json: status "{status}" is not a status — '
        "delete the file to remove a baseline"
    )

    comp = compare_one(_model(A), _result(A), store)
    stored, skipped, _ = store.accept_results(
        [_model(A)], {A: _result(A)}, provenance=PROVENANCES
    )

    assert (classify(comp), refusal(comp)) == (FAIL, said)
    assert (stored, skipped) == ([], [(A, said)])
    assert path.read_bytes() == before


def _conflicted(path) -> None:
    """A's baseline as a merge leaves it: git's conflict markers around it."""
    _hand_written(path, A)
    path.write_bytes(
        b"<<<<<<< HEAD\n" + path.read_bytes() + b"=======\n{}\n>>>>>>> other\n"
    )


def _id_five(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"model_id": 5, "status": "active"}', encoding="utf-8")


def _not_utf8(path) -> None:
    """A's baseline with one byte that is no UTF-8 in a text."""
    _hand_written(path, A)
    path.write_bytes(
        path.read_bytes().replace(b'"expression": ""', b'"expression": "\xff"')
    )


def _body(**changed):
    """A's baseline with parts of its body replaced as a hand edit leaves it."""

    def leave(path) -> None:
        _hand_written(path, A)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update(changed)
        path.write_text(json.dumps(doc), encoding="utf-8")

    return leave


@pytest.mark.parametrize(
    ("leave", "why"),
    [
        (_conflicted, "not JSON at line 1, column 1"),
        (_not_utf8, "not UTF-8 text"),
        (_id_five, "model_id 5 is not a string"),
        (_body(variables="oops", time=None), "variables is not a list"),
        (_body(time="0 to 10"), "time is not a list of numbers"),
        (
            _body(variables=[{"index": 1, "values": [0.0]}]),
            "variable 1 has no name",
        ),
        (_body(variables=[{"index": 1, "name": "x"}]), "variable x has no values"),
        (
            _body(variables=[{"name": "x", "values": [0.0, "1.5", None]}]),
            "variable x: values is not a list of numbers",
        ),
        (
            _body(diagnostics=[{"name": "CPUtime", "final": "fast"}]),
            "diagnostic CPUtime: final, min or max is not a number",
        ),
        (_body(simulation="dassl"), "simulation is not an object"),
    ],
    ids=[
        "conflict-markers",
        "not-utf8",
        "model-id-5",
        "variables-not-a-list",
        "time-not-numbers",
        "variable-no-name",
        "variable-no-values",
        "values-not-numbers",
        "diagnostic-final-text",
        "simulation-text",
    ],
)
def test_an_unreadable_file_under_the_name_refuses_the_test_untouched(
    sample_models_dir, tmp_path, leave, why
):
    """A file at the model's own name that is no readable baseline of it is
    refused like a file of another model: the compare FAILS with the
    sentence, the accept skips the test with it, and the file is kept."""
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    path = store.ref_dir / A_FILE
    leave(path)
    before = path.read_bytes()
    said = f"ref_6e187be76792.json cannot be read ({why}) — fix or delete it"

    comp = compare_one(_model(A), _result(A), store)
    stored, skipped, _ = store.accept_results(
        [_model(A)], {A: _result(A)}, provenance=PROVENANCES
    )

    assert (classify(comp), refusal(comp)) == (FAIL, said)
    assert (stored, skipped) == ([], [(A, said)])
    assert path.read_bytes() == before
    assert sorted(p.name for p in store.ref_dir.iterdir()) == [A_FILE]


def test_a_file_named_for_another_code_is_warned_and_left_out(
    sample_models_dir, tmp_path, caplog
):
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / "ref_000000000000.json", A)

    with caplog.at_level(logging.WARNING):
        listed = store.list_models()

    assert listed == []
    assert [r.getMessage() for r in caplog.records] == [
        f"ref_000000000000.json holds the baseline of {A}, whose file is "
        f"{A_FILE} — rename or delete it"
    ]
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
    # The other two under their own names; SimpleRamp under its test key.
    pages = {p.name for p in (work / "reports").iterdir() if p.is_dir()}
    assert pages == {"ref_f612690ea655", "ref_8cb5c68dba3b", "test_0003"}


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


def test_a_run_over_a_baseline_whose_body_is_broken_fails_that_test_alone(
    tmp_path, caplog
):
    """Over the Python example, PointsCheck's file with ``"variables":
    "oops"`` and ``"time": null`` (measured 2026-11-11: the run exited 1 with
    a traceback from the codec). The run completes: PointsCheck FAILS with
    the sentence that names its file, the other two pass, and an accept
    leaves the file's bytes as they were."""
    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    path = lib / "Resources" / "ReferenceResults" / "Python" / "linux" / POINTS_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(variables="oops", time=None)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    said = "ref_8cb5c68dba3b.json cannot be read (variables is not a list) — fix or delete it"
    work = tmp_path / "work"
    config = str(lib / "Resources" / "ReferenceResults" / "testing.json")

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = main(
            ["--config", config, "run", "--no-browser", "--work-dir", str(work)]
        )
    verdicts = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        accept_code = main(
            [
                "--config",
                config,
                "run",
                "--no-browser",
                "--accept",
                "--work-dir",
                str(tmp_path / "work-accept"),
            ]
        )

    assert code == 1
    assert sorted(
        (t["model_id"], t["status"], t["message"]) for t in verdicts["tests"]
    ) == [
        ("PythonTestingLib.Examples.ConstantCsv", "PASS", None),
        (POINTS, "FAIL", said),
        (RAMP, "PASS", None),
    ]
    assert accept_code == 1
    assert f"Skipped 1 test(s):\n  {POINTS}: {said}\n" in out.getvalue()
    assert path.read_bytes() == before


def test_a_run_over_a_soft_check_whose_body_is_broken_scores_the_test_without_it(
    tmp_path, caplog
):
    """Over the Python example, SimpleRamp scored by a tree, with a soft
    check ``other`` whose file holds ``"variables": "oops"`` (measured
    2026-11-11: the run exited 1 with a traceback from the codec). The run
    completes, every test passes, and the soft check is warned of by its
    file."""
    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    ref_root = lib / "Resources" / "ReferenceResults"
    spec_path = ref_root / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == RAMP]
    entry["metrics"] = {"metric": "nrmse", "variable": "x"}
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    soft = ref_root / "Python" / "linux" / "soft_checks" / "ref_9bbf24e6f8aa"
    soft.mkdir(parents=True)
    (soft / "other.json").write_text(
        '{"time": [0, 1], "variables": "oops"}', encoding="utf-8"
    )
    work = tmp_path / "work"

    with (
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
        caplog.at_level(logging.WARNING, logger="dstf.storage.roles"),
    ):
        code = main(
            [
                "--config",
                str(ref_root / "testing.json"),
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
        (POINTS, "PASS"),
        (RAMP, "PASS"),
    ]
    assert {r.getMessage() for r in caplog.records} == {
        f"Skipping unreadable soft_check {soft / 'other.json'}: variables is not a list"
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
    assert err.getvalue() == (
        f"Config error: {folder} holds baselines under the old numbered names "
        "(ref_0003.json) — convert them with scripts/convert_baseline_names.py, "
        "see CHANGELOG\n"
    )


def test_a_stray_file_name_is_warned_and_left_out_not_called_numbered(
    sample_models_dir, tmp_path, caplog
):
    """Only ``ref_`` and 1 to 11 digits is the old numbered form. A copy
    left beside a baseline, or a code in capitals, is no baseline name: the
    folder is read, the good baseline is found, and each stray file is
    warned of by name and listed as refused."""
    store = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))
    _hand_written(store.ref_dir / A_FILE, A)
    _hand_written(store.ref_dir / "ref_6e187be76792 copy.json", A)
    _hand_written(store.ref_dir / "ref_6E187BE76792.json", A)
    fresh = ReferenceStore(_config(sample_models_dir, tmp_path / "refs"))

    with caplog.at_level(logging.WARNING, logger="dstf.storage.ref_index"):
        listed = fresh.list_models()

    said = {
        name: f"{name} is not a baseline name (ref_ and 12 lowercase hex digits)"
        " — rename or delete it"
        for name in ("ref_6E187BE76792.json", "ref_6e187be76792 copy.json")
    }
    assert listed == [A]
    assert fresh.reference_file(A) == store.ref_dir / A_FILE
    assert [r.getMessage() for r in caplog.records] == list(said.values())
    assert fresh.index.refused() == said


def test_eleven_digits_are_an_old_number(sample_models_dir, tmp_path):
    config = _config(sample_models_dir, tmp_path / "refs")
    _hand_written(config.reference_dir / "ref_12345678901.json", A)

    with pytest.raises(ConfigError) as caught:
        ReferenceStore(config)

    assert str(caught.value) == (
        f"{config.reference_dir} holds baselines under the old numbered names "
        "(ref_12345678901.json) — convert them with "
        "scripts/convert_baseline_names.py, see CHANGELOG"
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
