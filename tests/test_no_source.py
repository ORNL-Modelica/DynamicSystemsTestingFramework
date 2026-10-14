"""A ``test_spec.json`` entry with no ``source``: "no source" is ONE value.

A test of a backend that runs a file (Python, Julia) runs
``<source_path>/<model><suffix>`` when its entry names none
(``resolve_source_file``); FMPy has no such rule — its test runs the
``.fmu`` its entry names, or is refused in one sentence that says so.

"No source" used to be ``Path("")``, whose text is ``"."``: every reader
asking "is there a source?" was told yes, a Python or Julia test ran the
LIBRARY FOLDER (``Cannot load <library dir>``) and an FMPy test handed
``.`` to fmpy as its FMU. It is ``None`` now, written one way.
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from conftest import PROJECT_ROOT, python_example_copy
from dstf.backends.common.json_driver import resolve_source_file
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"


def _drop_source(spec_path: Path, model_id: str) -> None:
    """Remove ``source`` from ``model_id``'s entry of the spec file."""
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in data["tests"]:
        if entry["model"] == model_id:
            del entry["source"]
    spec_path.write_text(json.dumps(data), encoding="utf-8")


def _python_copy_without_source(tmp_path):
    """The Python example, copied, whose ConstantCsv entry names no
    ``source`` while ``<source_path>/<model>.py`` is there (with the data
    file it reads beside it)."""
    config, _ = python_example_copy(tmp_path)
    lib = config.source_path
    shutil.copy(lib / "Examples" / "ConstantCsv.py", lib / f"{CONSTANT_CSV}.py")
    shutil.copytree(lib / "Examples" / "data", lib / "data")
    _drop_source(config.spec_path, CONSTANT_CSV)
    config = Config(config_file=config.config_file, work_dir=tmp_path / "work")
    return config, discover_tests(config)


def _only(tests, model_id):
    match = [t for t in tests if t.model_id == model_id]
    assert len(match) == 1, [t.model_id for t in tests]
    return match[0]


def test_a_python_entry_with_no_source_runs_the_file_named_after_its_model(
    tmp_path,
):
    from dstf.backends.python.backend import PythonBackend

    config, tests = _python_copy_without_source(tmp_path)
    test = _only(tests, CONSTANT_CSV)

    assert test.source_file is None
    assert resolve_source_file(test, config, PythonBackend.source_suffix) == (
        config.source_path / f"{CONSTANT_CSV}.py"
    )


def test_a_python_entry_with_no_source_runs_and_passes(tmp_path):
    """The real Python backend over the copy: the test runs the file named
    after its model and passes — not ``Cannot load <library dir>``."""
    from dstf.backends import dataset_kind_for, get_backend
    from dstf.comparison.comparator import compare_all
    from dstf.pipeline import run_pipeline
    from dstf.reporting.status import PASS, classify
    from dstf.storage.reference_store import ReferenceStore

    config, tests = _python_copy_without_source(tmp_path)
    constant = [_only(tests, CONSTANT_CSV)]
    backend, store = get_backend(config), ReferenceStore(config)
    backend.quiet = True

    outcome = run_pipeline(config, constant, backend=backend, store=store)

    result = outcome.results[CONSTANT_CSV]
    assert result.success, result.error_message
    (comp,) = compare_all(
        constant,
        outcome.results,
        store,
        config.tolerance,
        config.default_points,
        dataset_kind=dataset_kind_for(backend),
    )
    assert classify(comp) == PASS


def test_a_julia_entry_with_no_source_runs_the_file_named_after_its_model(
    tmp_path,
):
    """No julia is started: the path a run would load is the assertion."""
    from dstf.backends.julia.backend import JuliaBackend

    lib = tmp_path / "JuliaMtkTestingLib"
    shutil.copytree(PROJECT_ROOT / "examples" / "julia" / "JuliaMtkTestingLib", lib)
    model_id = "JuliaMtkTestingLib.Examples.SimpleRamp"
    shutil.copy(lib / "Examples" / "SimpleRamp.jl", lib / f"{model_id}.jl")
    refs = lib / "Resources" / "ReferenceResults"
    _drop_source(refs / "test_spec.json", model_id)
    config = Config(config_file=refs / "testing.json", work_dir=tmp_path / "work")
    test = _only(discover_tests(config), model_id)

    assert test.source_file is None
    assert resolve_source_file(test, config, JuliaBackend.source_suffix) == (
        lib / f"{model_id}.jl"
    )


def _fmu_project(tmp_path) -> Config:
    """An FMU project of one entry that names no ``source``, with an
    ``.fmu`` named after its model beside the spec — which FMPy does not
    take: it has no rule that names a file after the model."""
    (tmp_path / "Model.fmu").write_bytes(b"fake fmu")
    (tmp_path / "test_spec.json").write_text(
        json.dumps({"tests": [{"model": "Model", "variables": ["h"]}]}),
        encoding="utf-8",
    )
    (tmp_path / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "fmu",
                "backend": "FMPy",
                "reference_root": ".",
                "test_spec": "./test_spec.json",
            }
        ),
        encoding="utf-8",
    )
    return Config(config_file=tmp_path / "testing.json", work_dir=tmp_path / "work")


def test_an_fmu_entry_with_no_source_is_refused_in_one_sentence(tmp_path, monkeypatch):
    """The entry is named and fmpy is never handed a path: before, it was
    handed ``.`` — the folder, which exists — as the FMU."""
    from dstf.backends.fmpy.backend import FmpyBackend

    config = _fmu_project(tmp_path)
    (test,) = discover_tests(config)
    handed = []
    fake = types.ModuleType("fmpy")
    fake.read_model_description = lambda p: (
        handed.append(p) or SimpleNamespace(modelVariables=[SimpleNamespace(name="h")])
    )
    fake.simulate_fmu = lambda **kw: handed.append(kw.get("filename"))
    monkeypatch.setitem(sys.modules, "fmpy", fake)

    assert test.source_file is None
    run = FmpyBackend(config).run_single_test(test, "test_0001", 1, 1)
    assert handed == []
    assert run.success is False
    assert run.error_message == (
        "Model: its test_spec.json entry names no FMU — an FMPy test runs "
        "the .fmu its 'source' names, a path relative to the spec file."
    )
