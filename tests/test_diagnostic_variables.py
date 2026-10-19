"""``diagnostic_variables``: the names whose summary (final, min, max) every
run records — the project's list in ``testing.json`` and a test entry's own,
which mean the same thing (`diagnostic_names`)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.types import VariableResult
from dstf.config import Config
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import diagnostic_names, merge_tests
from dstf.storage.baseline import DiagnosticSummary, StoredVariable

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


# -- a leaf that one stored number answers (R1) ----------------------------------


def _one_leaf(**leaf):
    from dstf.comparison.tree_spec import parse_metric_tree

    return parse_metric_tree({"metric": "scalar", "variable": "CPUtime", **leaf})


@pytest.mark.parametrize("reduce", ["final", "min", "max"])
def test_a_whole_run_final_min_or_max_reads_a_summary(reduce):
    from dstf.comparison.tree_spec import reads_a_summary

    assert reads_a_summary(_one_leaf(quantity={"reduce": reduce}))


@pytest.mark.parametrize(
    "leaf",
    [
        {"quantity": {"reduce": "mean"}},
        {"quantity": {"reduce": "sample_mean"}},
        {"quantity": {"reduce": "integral"}},
        {"quantity": {"reduce": "final"}, "window": {"start": 0.5}},
        {"quantity": {"reduce": "max"}, "window": {"end": 0.5}},
        {"quantity": "translation.scalar_unknowns"},
        {"metric": "nrmse", "quantity": {"reduce": "final"}},
    ],
    ids=["mean", "sample_mean", "integral", "a start", "an end", "a path", "nrmse"],
)
def test_any_other_criterion_needs_the_curve(leaf):
    from dstf.comparison.tree_spec import reads_a_summary

    assert not reads_a_summary(_one_leaf(**leaf))


# -- the evaluator scores a summary on a side without the curve (R7) -------------

T = np.linspace(0.0, 1.0, 11)
#: CPUtime's run: final 2.0, max 2.0, min 0.0
RUN_CPU = np.linspace(0.0, 2.0, 11)


def _vr(name, values, index=1):
    return VariableResult(
        index=index, time=T, values=np.asarray(values, dtype=float), name=name
    )


def _eval(leaf, *, run_vars=(), run_diags=(), ref_vars=(), ref_diags=()):
    from dstf.comparison.tree_eval import BaselineView, ScalarSources, evaluate_spec

    base = BaselineView(
        name="primary",
        ref_vars_by_name={v.name: v for v in ref_vars},
        shared_ref_time=T,
        statistics={},
        diagnostics=list(ref_diags),
    )
    result = evaluate_spec(
        leaf,
        {v.name: v for v in run_vars},
        {"primary": base},
        0.01,
        scalars=ScalarSources(statistics={}, diagnostics=list(run_diags)),
    )
    return result, result.diagnostics["variable"]


def _final(**leaf):
    return _one_leaf(quantity={"reduce": "final"}, tolerance=0.01, **leaf)


@pytest.mark.parametrize(("ref_final", "passes"), [(2.0, True), (1.0, False)])
def test_both_sides_from_their_summaries(ref_final, passes):
    result, vc = _eval(
        _final(),
        run_diags=[_vr("CPUtime", RUN_CPU)],
        ref_diags=[DiagnosticSummary("CPUtime", final=ref_final, min=0.0, max=9.0)],
    )
    assert result.passed is passes
    assert (vc.actual_final, vc.reference_final) == (2.0, ref_final)
    assert vc.nrmse == pytest.approx(abs(2.0 - ref_final) / ref_final)
    # Never the diagnostic's own number: `build_trajectories` pairs a
    # comparison with a series by its index (review M4).
    assert vc.index == 0


@pytest.mark.parametrize(
    ("reduce", "run", "ref"), [("min", 5.0, 5.0), ("max", 7.0, 9.0)]
)
def test_min_and_max_read_their_own_field_on_both_sides(reduce, run, ref):
    _, vc = _eval(
        _one_leaf(quantity={"reduce": reduce}, tolerance=0.5),
        run_diags=[_vr("CPUtime", RUN_CPU + 5)],
        ref_diags=[DiagnosticSummary("CPUtime", final=0.0, min=5.0, max=9.0)],
    )
    assert (vc.actual_final, vc.reference_final) == (run, ref)


@pytest.mark.parametrize(("ref_final", "passes"), [(2.0, True), (1.0, False)])
def test_a_run_curve_against_a_baseline_summary(ref_final, passes):
    """The run's number is the reduce over its curve, which keeps its index."""
    result, vc = _eval(
        _final(),
        run_vars=[_vr("CPUtime", RUN_CPU, index=4)],
        run_diags=[_vr("CPUtime", RUN_CPU * 0 + 7.0)],
        ref_diags=[DiagnosticSummary("CPUtime", final=ref_final, min=0.0, max=2.0)],
    )
    assert result.passed is passes
    assert (vc.actual_final, vc.reference_final) == (2.0, ref_final)
    assert vc.index == 4


def test_a_run_summary_against_a_baseline_curve():
    _, vc = _eval(
        _final(),
        run_diags=[_vr("CPUtime", RUN_CPU)],
        ref_vars=[
            StoredVariable(
                index=3, name="CPUtime", expression="", values=list(RUN_CPU * 2)
            )
        ],
        ref_diags=[DiagnosticSummary("CPUtime", final=-99.0)],
    )
    assert (vc.actual_final, vc.reference_final, vc.index) == (2.0, 4.0, 0)


def test_declared_bounds_on_a_summary_need_no_baseline():
    result, vc = _eval(
        _one_leaf(quantity={"reduce": "max"}, max_value=1.5),
        run_diags=[_vr("CPUtime", RUN_CPU)],
    )
    assert result.passed is False
    assert vc.actual_final == 2.0
    assert vc.nrmse == pytest.approx(0.5)  # the distance outside the bound


def test_a_run_without_the_name_reads_as_today():
    _, vc = _eval(_final(), ref_diags=[DiagnosticSummary("CPUtime", final=1.0)])
    assert vc.diagnostics["error"] == "variable 'CPUtime' not found in the result"


def test_a_baseline_without_the_name_reads_as_today():
    _, vc = _eval(_final(), run_diags=[_vr("CPUtime", RUN_CPU)])
    assert vc.diagnostics["error"] == "variable 'CPUtime' not found in the baseline"
    assert (vc.actual_final, vc.index) == (2.0, 0)


def test_both_curves_take_todays_path_and_never_read_a_summary():
    result, vc = _eval(
        _final(),
        run_vars=[_vr("CPUtime", RUN_CPU, index=3)],
        run_diags=[_vr("CPUtime", RUN_CPU * 0)],
        ref_vars=[
            StoredVariable(index=3, name="CPUtime", expression="", values=list(RUN_CPU))
        ],
        ref_diags=[DiagnosticSummary("CPUtime", final=-99.0)],
    )
    assert result.passed
    assert (vc.actual_final, vc.reference_final, vc.index) == (2.0, 2.0, 3)


# -- every backend records the names it lists (R4) -------------------------------


def _json_result(tmp_path, payload: dict, own: list[str]):
    from dstf.backends.python import backend as pr

    cfg = SimpleNamespace(
        backend_path=None,
        source_path=tmp_path,
        work_dir=tmp_path / "work",
        timeout=1,
        diagnostic_variables=PROJECT,
    )
    test_dir = tmp_path / "work" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "result.json").write_text(
        json.dumps({"success": True, "time": [0.0, 1.0], "variables": payload})
    )
    test = make_test_model("T", variable_patterns=["y"], diagnostic_variables=own)
    return pr.PythonBackend(cfg).read_result(test, "test_0001", None)


def test_the_json_driver_records_the_listed_names_its_result_holds(tmp_path):
    result = _json_result(
        tmp_path,
        {"y": [0, 1], "h": [3.0, 4.0], "CPUtime": [0.0, 0.5], "v": [9.0, 9.0]},
        ["h"],
    )
    assert [(d.index, d.name, list(d.values)) for d in result.diagnostics] == [
        (1, "CPUtime", [0.0, 0.5]),
        (2, "h", [3.0, 4.0]),
    ]
    assert [v.name for v in result.variables] == ["y"]


def test_the_json_driver_records_none_its_result_lacks(tmp_path):
    result = _json_result(tmp_path, {"y": [0, 1]}, ["h"])
    assert result.diagnostics == []


# -- through accept: the FILE holds the summary, and the curve only when needed --


def test_an_accept_stores_a_summary_and_a_curve_only_once_a_criterion_needs_it(
    tmp_path,
):
    from dstf.backends.python import backend as pr
    from dstf.comparison.tree_spec import parse_metric_tree
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
            {
                "success": True,
                "time": [0.0, 0.5, 1.0],
                "variables": {"y": [0.0, 1.0, 2.0], "h": [3.0, 5.0, 4.0]},
            }
        )
    )
    final_on_h = {"metric": "scalar", "variable": "h", "quantity": {"reduce": "final"}}
    test = make_test_model(
        "Lib.T",
        variable_patterns=["y"],
        diagnostic_variables=["h"],
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [{"metric": "nrmse", "variable": "y"}, final_on_h],
            }
        ),
    )

    def accepted() -> dict:
        store = ReferenceStore(config)
        result = pr.PythonBackend(config).read_result(test, "test_0001", None)
        stored, skipped, _, _ = accept_run_results(
            store, [test], {test.model_id: result}, config.work_dir
        )
        assert (stored, skipped) == (1, [])
        (path,) = config.reference_root.rglob("ref_0001.json")
        return json.loads(path.read_text(encoding="utf-8"))

    first = accepted()
    assert [v["name"] for v in first["variables"]] == ["y"]
    assert [d for d in first["diagnostics"] if d["name"] == "h"] == [
        {"name": "h", "final": 4.0, "min": 3.0, "max": 5.0}
    ]

    test.metric_tree_spec = parse_metric_tree(
        {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "y"},
                final_on_h,
                {"metric": "nrmse", "variable": "h"},
            ],
        }
    )
    second = accepted()
    assert [(v["name"], v["values"]) for v in second["variables"]] == [
        ("y", [0.0, 1.0, 2.0]),
        ("h", [3.0, 5.0, 4.0]),
    ]
    assert [d for d in second["diagnostics"] if d["name"] == "h"] == [
        {"name": "h", "final": 4.0, "min": 3.0, "max": 5.0}
    ]
