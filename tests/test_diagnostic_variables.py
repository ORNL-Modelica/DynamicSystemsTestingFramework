"""``diagnostic_variables``: the names whose summary (final, min, max) every
run records — the project's list in ``testing.json`` and a test entry's own,
which mean the same thing (`diagnostic_names`)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_test_model
from dstf.config import Config
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import diagnostic_names, merge_tests

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT = ["CPUtime", "EventCounter"]


# -- which names a test lists ---------------------------------------------------


@pytest.mark.parametrize(
    ("own", "listed"),
    [
        ([], ["CPUtime", "EventCounter"]),
        (["h"], ["CPUtime", "EventCounter", "h"]),
        (["h", "CPUtime", "h"], ["CPUtime", "EventCounter", "h"]),
    ],
    ids=["the project's alone", "the test's after", "each once"],
)
def test_a_test_lists_the_projects_names_then_its_own_each_once(own, listed):
    test = make_test_model("Lib.T", diagnostic_variables=own)
    assert diagnostic_names(test, PROJECT) == listed


def test_a_test_lists_its_own_names_where_the_project_lists_none():
    test = make_test_model("Lib.T", diagnostic_variables=["h", "v"])
    assert diagnostic_names(test, []) == ["h", "v"]


# -- the entry's key -----------------------------------------------------------


def _parsed(entry: dict):
    (test,) = spec_models_from_document(
        {"tests": [{"model": "Lib.T", **entry}]}, base_dir=Path("."), label="spec"
    )
    return test


def test_an_entry_names_its_own():
    assert _parsed({"diagnostic_variables": ["h", "v"]}).diagnostic_variables == [
        "h",
        "v",
    ]


def test_an_entry_without_the_key_names_none():
    assert _parsed({}).diagnostic_variables == []


def test_a_test_found_in_the_source_takes_the_names_from_its_spec_entry():
    scanned = {"Lib.T": make_test_model("Lib.T", source="unit_tests")}
    with_key = {"Lib.T": make_test_model("Lib.T", diagnostic_variables=["h"])}
    without = {"Lib.T": make_test_model("Lib.T")}
    assert merge_tests(scanned, with_key)[0].diagnostic_variables == ["h"]
    assert merge_tests(scanned, without)[0].diagnostic_variables == []


def test_the_tool_keeps_the_key_when_it_saves_two_edits(tmp_path):
    """Read from the FILE, after a patch of two ops: the key is no op's and
    the save leaves it as written."""
    from dstf.authoring.session import PatchOp
    from test_session import LOOSEN, A, _session_with_entry

    session = _session_with_entry(tmp_path, {"diagnostic_variables": ["h", "v"]})
    session.stage(
        [LOOSEN, PatchOp(A, "add", "/metrics/children/0/window", {"start": 0.25})]
    )
    session.save()

    spec = json.loads(session.spec.path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == A]
    assert entry["diagnostic_variables"] == ["h", "v"]
    child = entry["metrics"]["children"][0]
    assert (child["tolerance"], child["window"]) == (0.5, {"start": 0.25})  # both
    (test,) = [t for t in session.project.tests if t.model_id == A]
    assert test.diagnostic_variables == ["h", "v"]


# -- the .mat backends record a test's own names -------------------------------


def test_dymola_records_a_tests_own_name_after_the_projects(tmp_path):
    from dstf.backends.dymola import backend as dr

    test_dir = tmp_path / "test_0001"
    test_dir.mkdir()
    for name in ("dsres.mat", "dslog.txt"):
        shutil.copy(FIXTURES / "results" / "Dymola" / name, test_dir / name)
    cfg = SimpleNamespace(
        backend_setup=[],
        diagnostic_variables=PROJECT,
        work_dir=tmp_path,
        backend_path="x",
        timeout=1,
        parallel=1,
    )
    test = make_test_model("Lib.A", variable_patterns=["x"], diagnostic_variables=["y"])
    result = dr.DymolaBackend(cfg).read_result(test, "test_0001", None)

    assert [(d.index, d.name) for d in result.diagnostics] == [
        (1, "CPUtime"),
        (2, "EventCounter"),
        (3, "y"),
    ]
    assert [v.name for v in result.variables] == ["x"]


def _om_config(tmp_path: Path) -> Config:
    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    return Config(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        backend_name="OpenModelica",
        work_dir=tmp_path / "work",
    )


def test_openmodelica_records_a_tests_own_name(tmp_path):
    from dstf.backends.openmodelica.backend import OpenModelicaBackend

    cfg = _om_config(tmp_path)
    test_dir = cfg.work_dir / "test_0001"
    test_dir.mkdir(parents=True)
    shutil.copy(
        FIXTURES / "results_openmodelica" / "pid_controller_res.mat",
        test_dir / OpenModelicaBackend.RESULT_MAT_FILENAME,
    )
    test = make_test_model("Lib.A", diagnostic_variables=["inertia1.phi"])
    result = OpenModelicaBackend(cfg).read_result(test, "test_0001", None)

    assert [(d.index, d.name) for d in result.diagnostics] == [(1, "inertia1.phi")]
    assert result.variables == []


def test_openmodelica_asks_for_a_tests_own_name_in_its_variable_filter(
    tmp_path, monkeypatch
):
    """The batch run's .mos: the filter keeps the test's own name, after the
    project's."""
    from subprocess import CompletedProcess

    from dstf.backends.openmodelica.backend import OpenModelicaBackend

    omc = tmp_path / "omc"
    omc.write_text("#!/bin/sh\n")
    omc.chmod(0o755)
    cfg = _om_config(tmp_path)
    cfg.backend_path = str(omc)

    def fake_run(cmd, cwd, **kwargs):
        return CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("dstf.backends.openmodelica.backend.subprocess.run", fake_run)
    test = make_test_model("Lib.A", variable_patterns=["x"], diagnostic_variables=["h"])
    OpenModelicaBackend(cfg).run_single_test(
        test, test_key="test_0001", index=1, total=1
    )

    mos = (cfg.work_dir / "test_0001" / "simulate.mos").read_text()
    assert 'variableFilter="^(time|x|CPUtime|EventCounter|h)$"' in mos
