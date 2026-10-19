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


# -- a leaf that one stored number answers ----------------------------------


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


# -- the evaluator scores a summary on a side without the curve -------------

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
    # comparison with a series by its index.
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


# -- every backend records the names it lists -------------------------------


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


# -- no statistics copy ---------------------------------------------------


def _document(**top) -> dict:
    """A reference document whose `diagnostics` hold CPUtime; ``top`` the
    top-level statistics beside its `translation` section."""
    return {
        "model_id": "M",
        "statistics": {"translation": {"a": 1}, **top},
        "diagnostics": [{"name": "CPUtime", "final": 0.5, "min": 0.0, "max": 0.5}],
        "time": [0.0],
        "variables": [],
    }


def test_the_codec_drops_a_copy_of_a_diagnostic_on_read_and_never_writes_it():
    from dstf.storage.baseline_codec import decode_reference, reference_to_json

    ref = decode_reference(_document(CPUtime=0.5))
    assert ref.baseline.statistics == {"translation": {"a": 1}}
    assert reference_to_json(ref)["statistics"] == {"translation": {"a": 1}}


def test_a_soft_check_drops_the_copy_too():
    from dstf.storage.baseline_codec import decode_series

    assert decode_series(_document(CPUtime=0.5), "x").statistics == {
        "translation": {"a": 1}
    }


def test_the_codec_keeps_a_key_that_names_no_diagnostic():
    """``EventCounter`` is no diagnostic of THIS file: kept."""
    from dstf.storage.baseline_codec import decode_reference

    ref = decode_reference(_document(Foo=3.0, EventCounter=2.0))
    assert ref.baseline.statistics == {
        "translation": {"a": 1},
        "Foo": 3.0,
        "EventCounter": 2.0,
    }


def test_the_codec_keeps_a_section_named_like_a_diagnostic():
    from dstf.storage.baseline_codec import decode_reference

    doc = _document()
    doc["statistics"]["CPUtime"] = {"x": 1}
    assert decode_reference(doc).baseline.statistics["CPUtime"] == {"x": 1}


def test_a_file_whose_statistics_were_only_the_copy_is_written_without_them():
    from dstf.storage.baseline_codec import decode_reference, reference_to_json

    doc = _document(CPUtime=0.5)
    del doc["statistics"]["translation"]
    assert "statistics" not in reference_to_json(decode_reference(doc))


def _dymola_run(tmp_path, own=()):
    """The Dymola fixture's result, read as a run: CPUtime and EventCounter
    are float32 diagnostics (0.002 and 3)."""
    from dstf.backends.dymola import backend as dr

    test_dir = tmp_path / "dymola" / "test_0001"
    test_dir.mkdir(parents=True)
    for name in ("dsres.mat", "dslog.txt", "translation_log.txt"):
        shutil.copy(FIXTURES / "results" / "Dymola" / name, test_dir / name)
    cfg = SimpleNamespace(
        backend_setup=[],
        diagnostic_variables=PROJECT,
        work_dir=test_dir.parent,
        backend_path="x",
        timeout=1,
        parallel=1,
    )
    test = make_test_model(
        "Lib.A", variable_patterns=["x"], diagnostic_variables=list(own)
    )
    return dr.DymolaBackend(cfg).read_result(test, "test_0001", None)


def test_a_dymola_run_holds_no_statistics_copy(tmp_path):
    run = _dymola_run(tmp_path)
    assert "CPUtime" not in run.statistics and "EventCounter" not in run.statistics
    assert [(d.name, str(d.values.dtype)) for d in run.diagnostics] == [
        ("CPUtime", "float32"),
        ("EventCounter", "float32"),
    ]


def test_an_accept_over_an_old_file_writes_no_copy(tmp_path):
    from dstf.storage.reference_store import ReferenceStore

    root = tmp_path / "project"
    root.mkdir()
    (root / "testing.json").write_text(
        json.dumps(
            {"source_type": "python", "backend": "Python", "reference_root": "refs"}
        )
    )
    config = Config(config_file=root / "testing.json", work_dir=tmp_path / "work")
    run = _dymola_run(tmp_path)
    test = make_test_model("Lib.A", variable_patterns=["x"])
    store = ReferenceStore(config)
    assert store.store_reference(test, run, provenance={"by": "test"})
    (path,) = config.reference_root.rglob("ref_0001.json")
    old = json.loads(path.read_text(encoding="utf-8"))
    old["statistics"]["CPUtime"] = 0.002  # as 1.1 wrote it
    path.write_text(json.dumps(old), encoding="utf-8")

    assert ReferenceStore(config).store_reference(test, run, provenance={"by": "test"})
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert {
        k for k, v in stored["statistics"].items() if not isinstance(v, dict)
    } == set()
    assert [d["name"] for d in stored["diagnostics"]] == ["CPUtime", "EventCounter"]
    assert stored["sampling"]["precision"] == "single"  # a writer always writes it


# -- the event-count warning and the CPU time read the summaries -------


def _with_events(ev):
    from dstf.storage.baseline import Baseline, ReferenceFile

    return ReferenceFile(
        model_id="M",
        test_id="0001",
        status="active",
        date_added=None,
        last_updated=None,
        sampling=None,
        comparison={},
        baseline=Baseline(
            name="primary",
            time=[],
            variables=[],
            diagnostics=[]
            if ev is None
            else [DiagnosticSummary("EventCounter", final=ev)],
            simulation={},
            # The old copy disagrees: nothing reads it any more.
            statistics={"EventCounter": 99.0},
        ),
    )


def _run_events(ev):
    from dstf.backends.types import TestResult

    values = [] if ev is None else [np.array([0, 1, ev], dtype=np.float32)]
    return TestResult(
        model_id="M",
        success=True,
        variables=[],
        diagnostics=[
            VariableResult(
                index=1, time=np.linspace(0, 1, 3), values=v, name="EventCounter"
            )
            for v in values
        ],
        statistics={"EventCounter": 99.0},
    )


def test_the_event_count_warning_compares_the_two_summaries():
    from dstf.comparison.comparator import _check_structural_changes as check

    got = check(_with_events(19.0), _run_events(20.0))
    assert [(w.field, w.reference_value, w.current_value) for w in got] == [
        ("Event count", "19.0", "20.0")
    ]
    assert check(_with_events(19.0), _run_events(19.0)) == []
    assert check(_with_events(None), _run_events(19.0)) == []
    assert check(_with_events(19.0), _run_events(None)) == []


def test_the_cpu_time_is_the_runs_cpu_time_final():
    from dstf.comparison.comparator import TestComparison
    from dstf.reporting.rows import cpu_time, summary_fields

    t = np.linspace(0, 1, 3)
    cpu = [
        VariableResult(
            index=1, time=t, values=np.array([0.1, 0.05, 0.3]), name="CPUtime"
        )
    ]
    assert cpu_time(cpu) == 0.3
    assert cpu_time([]) is None
    comp = TestComparison(model_id="M", passed=True, variables=[])
    assert summary_fields(comp, diagnostics=cpu)["sim_cpu"] == 0.3


# -- the Statistics rows keep their look -------------------------------------

STATS_MAIN = FIXTURES / "stats_main"


def _without_copy(name: str, tmp_path) -> Path:
    doc = json.loads((STATS_MAIN / f"{name}.json").read_text(encoding="utf-8"))
    for key in ("CPUtime", "EventCounter"):
        del doc["statistics"][key]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["no_sampling", "single"])
@pytest.mark.parametrize("copy", [True, False], ids=["old file", "new file"])
def test_the_rows_and_the_report_read_as_main_read_the_old_file(tmp_path, name, copy):
    """Expected: main's output over the old file (the copy in it), captured
    once from a main archive — ``stats_main/expected.json``. The report's
    Reference cells are its caller's (`_build_template_context`)."""
    from dstf.reporting.context_builder import _build_template_context
    from dstf.reporting.stats_rows import card_text, statistics_rows
    from dstf.storage.baseline_codec import read_reference, shown_digits

    expected = json.loads((STATS_MAIN / "expected.json").read_text())[name]
    run = _dymola_run(tmp_path)
    ref = read_reference(
        STATS_MAIN / f"{name}.json" if copy else _without_copy(name, tmp_path)
    )

    listing = ["CPUtime", "EventCounter"]  # the project's default listing
    ctx = _build_template_context(
        "Lib.A", [], ref, run.statistics, None, result=run, diagnostics=listing
    )
    assert ctx["statistics_sections"] == expected["statistics_sections"]
    assert ctx["key_stats"] == expected["key_stats"]
    rows = statistics_rows(
        run.statistics,
        ref.baseline.statistics,
        listing=listing,
        run_diagnostics=run.diagnostics,
        baseline_diagnostics=ref.baseline.diagnostics,
        baseline_digits=shown_digits(ref.sampling),
    )
    assert [
        [
            r.section,
            r.key,
            r.path,
            r.label,
            r.kind,
            r.changed,
            r.delta,
            card_text(r.kind, r.run, r.digits[0]),
            card_text(r.kind, r.baseline, r.digits[1]),
        ]
        for r in rows
    ] == expected["rows"]


def _report_cells(tmp_path, document: dict, run) -> dict[str, tuple[str, str]]:
    from dstf.reporting.context_builder import _build_template_context
    from dstf.storage.baseline_codec import read_reference

    path = tmp_path / "ref.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    ctx = _build_template_context(
        "M",
        [],
        read_reference(path),
        run.statistics,
        None,
        result=run,
        diagnostics=[d.name for d in run.diagnostics],
    )
    return {
        row["label"]: (row["current"], row["reference"])
        for section in ctx["statistics_sections"]
        for row in section["rows"]
    }


def _summary_document(final: float, **extra) -> dict:
    return {
        "model_id": "M",
        "diagnostics": [{"name": "CPUtime", "final": final, "min": 0.0, "max": final}],
        "time": [0.0],
        "variables": [],
        **extra,
    }


def _cpu_run(values, dtype):
    from dstf.backends.types import TestResult

    v = np.asarray(values, dtype=dtype)
    return TestResult(
        model_id="M",
        success=True,
        variables=[],
        diagnostics=[
            VariableResult(
                index=1, time=np.linspace(0, 1, len(v)), values=v, name="CPUtime"
            )
        ],
        statistics={},
    )


def test_a_file_without_sampling_reads_as_single_precision(tmp_path):
    """Owner, 2026-10-18: a file with no `sampling` was written under the
    float32 default. Its CPUtime reads as 1.1 printed its copy."""
    cells = _report_cells(
        tmp_path,
        _summary_document(0.008599000051617622),
        _cpu_run([0.0, 0.0086], np.float32),
    )
    assert cells["CPUtime"] == ("0.0086 s", "0.008599 s")


def test_a_single_precision_run_and_file_read_at_seven_digits(tmp_path):
    sampling = {
        "setting": "all",
        "source_points": 1,
        "stored_points": 1,
        "precision": "single",
    }
    cells = _report_cells(
        tmp_path,
        _summary_document(0.04324999824166298, sampling=sampling),
        _cpu_run([0.0, 0.04324999824166298], np.float32),
    )
    assert cells["CPUtime"] == ("0.04325 s", "0.04325 s")


def test_a_double_precision_run_and_file_read_at_fifteen_digits(tmp_path):
    sampling = {
        "setting": "all",
        "source_points": 1,
        "stored_points": 1,
        "precision": "double",
    }
    cells = _report_cells(
        tmp_path,
        _summary_document(0.04324999824166298, sampling=sampling),
        _cpu_run([0.0, 0.04324999824166298], np.float64),
    )
    assert cells["CPUtime"] == ("0.043249998241663 s", "0.043249998241663 s")


def test_a_listed_name_neither_a_time_nor_a_count_is_a_value(tmp_path):
    """A height is no time: no unit, and a percentage delta; changed beyond
    the ordinary scalar seed's 1%."""
    from dstf.reporting.stats_rows import card_text, report_text, statistics_rows

    t = np.linspace(0, 1, 3)
    run = [
        VariableResult(index=1, time=t, values=np.array([1.0, 2.0, 0.512]), name="h")
    ]
    base = [DiagnosticSummary("h", final=0.5, min=0.5, max=2.0)]
    (row,) = statistics_rows(
        {}, {}, listing=["h"], run_diagnostics=run, baseline_diagnostics=base
    )
    assert (row.section, row.path, row.kind) == ("simulation", "h", "value")
    assert report_text(row.kind, row.run, row.digits[0]) == "0.512"
    assert card_text(row.kind, row.baseline, row.digits[1]) == "0.500"
    assert (row.delta, row.changed) == ("+2%", True)
    (same,) = statistics_rows(
        {},
        {},
        listing=["h"],
        run_diagnostics=run,
        baseline_diagnostics=[DiagnosticSummary("h", final=0.509)],
    )
    assert (same.delta, same.changed) == ("+0.6%", False)


# -- the card's `+` writes the one spelling ---------------------------


def test_the_cards_plus_on_cpu_time_writes_the_one_spelling_and_scores():
    """Main scored 0.045 against the copy 0.04325; now against the summary."""
    from dstf.authoring.builtin.edit_tree import _quantity_leaf
    from dstf.comparison.tree_spec import parse_metric_tree

    values = np.array([0.0, 0.02, 0.045], dtype=np.float32)
    s = _fake_view({"CPUtime": values})
    assert _quantity_leaf(s, "M", "CPUtime", "and") == {
        "combinator": "warn",
        "children": [
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": "final"},
                "tolerance": 0.1,
                "tolerance_mode": "rel",
            }
        ],
    }
    seed = _quantity_leaf(s, "M", "CPUtime", "or")
    result, vc = _eval(
        parse_metric_tree(seed),
        run_diags=[_vr("CPUtime", values)],
        ref_diags=[DiagnosticSummary("CPUtime", final=0.04325)],
    )
    assert result.passed
    assert (vc.actual_final, vc.reference_final) == (pytest.approx(0.045), 0.04325)


def _fake_view(recorded: dict, stored: dict | None = None, listed=PROJECT):
    """What `_quantity_leaf` reads of a SessionView: the listed names, the
    run's recorded diagnostics, the baseline's summaries."""
    run = SimpleNamespace(
        diagnostics=[
            VariableResult(index=1, time=np.arange(len(v)), values=v, name=n)
            for n, v in recorded.items()
        ]
    )
    return SimpleNamespace(
        diagnostic_names=lambda _m: list(listed),
        baseline_summaries=lambda _m: stored or {},
        project=SimpleNamespace(results={"M": run}),
    )


def test_a_value_rows_plus_with_no_run_seeds_within_1pc_of_the_baseline():
    """The `+` on a listed `h` that no run recorded reads the
    baseline's summary — within 1% of it, the row's `changed` rule."""
    from dstf.authoring.builtin.edit_tree import _quantity_leaf

    s = _fake_view(
        {}, {"h": DiagnosticSummary("h", final=2.0, max=3.0)}, [*PROJECT, "h"]
    )
    assert _quantity_leaf(s, "M", "h", "and") == {
        "metric": "scalar",
        "variable": "h",
        "quantity": {"reduce": "final"},
        "tolerance_mode": "rel",
        "tolerance": 0.01,
    }
    assert _quantity_leaf(s, "M", "h.max", "and")["quantity"] == {"reduce": "max"}


def test_a_statistic_row_keeps_its_quantity_path_seed():
    from dstf.authoring.builtin.edit_tree import _quantity_leaf

    assert _quantity_leaf(
        _fake_view({}), "M", "translation.nonlinear_count", "and"
    ) == {
        "metric": "scalar",
        "quantity": "translation.nonlinear_count",
        "tolerance": 0,
        "tolerance_mode": "abs",
    }


@pytest.mark.parametrize(
    ("into", "wrapped"), [("and", True), ("or", False), ("k-of-n", False)]
)
def test_a_cpu_time_number_warns_only_in_an_and(into, wrapped):
    from dstf.comparison.seeds import seed_variable_number

    seed = seed_variable_number("CPUtime", np.array([0.1, 0.2]), into=into)
    leaf = seed["children"][0] if wrapped else seed
    assert ("combinator" in seed) is wrapped
    assert leaf == {
        "metric": "scalar",
        "variable": "CPUtime",
        "quantity": {"reduce": "final"},
        "tolerance": 0.1,
        "tolerance_mode": "rel",
    }


def test_an_event_count_is_exact_and_bare():
    from dstf.comparison.seeds import seed_variable_number

    assert seed_variable_number("EventCounter", np.array([0.0, 3.0]), into="and") == {
        "metric": "scalar",
        "variable": "EventCounter",
        "quantity": {"reduce": "final"},
        "tolerance": 0,
        "tolerance_mode": "abs",
    }


def test_any_other_name_takes_the_ordinary_scalar_seed_never_a_time():
    """A listed `h` that happens to end on a whole number is no count, and
    a height is no time: the ordinary seed, bare in an `and`."""
    from dstf.comparison.seeds import SeedData, seed_scalar, seed_variable_number

    values = np.array([1.0, 3.0, 2.0])
    seed = seed_variable_number("h", values, into="and")
    assert seed == {
        "metric": "scalar",
        "variable": "h",
        **seed_scalar(SeedData(ref=None, act=(np.arange(3.0), values))),
    }
    assert (seed["min_value"], seed["max_value"]) == (1.8, 2.1)


def test_a_max_seeds_around_the_runs_max():
    from dstf.comparison.seeds import seed_variable_number

    seed = seed_variable_number(
        "h", np.array([1.0, 3.0, 2.0]), into="and", reduce="max"
    )
    assert seed["quantity"] == {"reduce": "max"}
    assert (seed["min_value"], seed["max_value"]) == (2.8, 3.2)


# -- one spelling: a dotted path is a statistic only ------------------------

HINT = ' — a variable\'s number is written {{"variable": "{x}", "quantity": {{"reduce": "{f}"}}}}'


@pytest.mark.parametrize(
    ("quantity", "field"),
    [("CPUtime.final", "final"), ("CPUtime.max", "max"), ("CPUtime", "final")],
    ids=["the picker's 1.1 path", "a max", "the card's 1.1 path"],
)
def test_a_path_naming_a_variable_is_not_found_and_says_the_one_spelling(
    quantity, field
):
    from dstf.comparison.tree_spec import parse_metric_tree

    leaf = parse_metric_tree(
        {"metric": "scalar", "variable": "CPUtime", "quantity": quantity}
    )
    _, vc = _eval(
        leaf,
        run_diags=[_vr("CPUtime", RUN_CPU)],
        ref_diags=[DiagnosticSummary("CPUtime", final=2.0, min=0.0, max=2.0)],
    )
    assert vc.diagnostics["error"] == (
        f"quantity {quantity!r} not found in current run"
        + HINT.format(x="CPUtime", f=field)
    )


def test_a_path_naming_a_baseline_variable_says_it_on_that_side():
    from dstf.comparison.tree_spec import parse_metric_tree

    leaf = parse_metric_tree({"metric": "scalar", "variable": "y", "quantity": "y.min"})
    _, vc = _eval(
        leaf,
        ref_vars=[StoredVariable(index=1, name="y", expression="", values=[1.0] * 11)],
    )
    assert vc.diagnostics["error"] == ("quantity 'y.min' not found in current run")
    _, vc = _eval(leaf, run_vars=[_vr("y", RUN_CPU)])
    assert vc.diagnostics["error"] == (
        "quantity 'y.min' not found in current run" + HINT.format(x="y", f="min")
    )


def test_a_path_naming_nothing_has_no_hint():
    from dstf.comparison.tree_spec import parse_metric_tree

    leaf = parse_metric_tree(
        {"metric": "scalar", "variable": "t", "quantity": "translation.typo"}
    )
    _, vc = _eval(leaf, run_diags=[_vr("CPUtime", RUN_CPU)])
    assert (
        vc.diagnostics["error"]
        == "quantity 'translation.typo' not found in current run"
    )


def test_a_path_is_read_from_the_statistics_alone():
    from dstf.comparison.tree_eval import resolve_scalar_path

    assert resolve_scalar_path("translation.n", {"translation": {"n": 3}}) == 3.0
    assert resolve_scalar_path("CPUtime", {"CPUtime": 0.5}) == 0.5  # a statistic
    assert resolve_scalar_path("CPUtime.final", {}) is None
    assert resolve_scalar_path("CPUtime.final", {"CPUtime": 1.0}) is None


# -- numbers on the card, curves on plots -----------------------------------

CARD_LEAVES = [
    ({"quantity": {"reduce": "final"}}, "CPUtime", True),
    ({"quantity": {"reduce": "max"}}, "CPUtime", True),
    ({"quantity": {"reduce": "final"}}, "y", False),  # unlisted: its plot
    ({"quantity": {"reduce": "mean"}}, "CPUtime", False),  # needs the curve
    ({"quantity": {"reduce": "final"}, "window": {"start": 0.5}}, "CPUtime", False),
    ({"quantity": "translation.n"}, "translation.n", True),  # a statistic
    ({"metric": "nrmse"}, "CPUtime", False),
    ({"metric": "nrmse", "quantity": {"reduce": "final"}}, "CPUtime", False),
]


@pytest.mark.parametrize(("raw", "variable", "card"), CARD_LEAVES)
def test_the_card_rule_reads_the_same_over_a_leaf_and_its_view(raw, variable, card):
    """`on_the_card` and its view twin are one predicate under two inputs."""
    from dstf.comparison.tree_spec import (
        on_the_card,
        on_the_card_view,
        parse_metric_tree,
        spec_to_view,
    )

    leaf = parse_metric_tree({"metric": "scalar", "variable": variable, **raw})
    assert on_the_card(leaf, PROJECT) is card
    assert on_the_card_view(spec_to_view(leaf), PROJECT) is card
    assert on_the_card(leaf, []) is (card and isinstance(raw.get("quantity"), str))


def _card_session(tmp_path, *leaves):
    from dstf.authoring.session import PatchOp
    from test_session import A, _stats_session

    s = _stats_session(tmp_path)
    tree = {
        "combinator": "and",
        "children": [{"metric": "nrmse", "variable": "x"}, *leaves],
    }
    s.stage([PatchOp(A, "add", "/metrics", tree)])
    return s, A


FINAL_CPU = {
    "metric": "scalar",
    "variable": "CPUtime",
    "quantity": {"reduce": "final"},
    "tolerance": 0.5,
    "tolerance_mode": "rel",
}
MAX_CPU = {**FINAL_CPU, "quantity": {"reduce": "max"}}


def test_a_summary_leaf_on_a_listed_name_is_on_the_card_not_on_a_plot(tmp_path):
    s, A = _card_session(tmp_path, FINAL_CPU, MAX_CPU)
    assert [p.subject for p in s.plots(A)] == [("x",)]
    assert [(leaf.variable, path) for leaf, path in s.statistic_leaves(A)] == [
        ("CPUtime", "/metrics/children/1"),
        ("CPUtime", "/metrics/children/2"),
    ]
    rows = {r.path: r for r in s.statistics(A)}
    # Its final on the listed name's own row; its max on a row of its own
    # under Other, valued from the summary.
    assert (rows["CPUtime"].run, rows["CPUtime"].baseline) == (1.12, pytest.approx(0.8))
    assert (rows["CPUtime.max"].section, rows["CPUtime.max"].run) == (
        "other scored",
        1.12,
    )
    assert rows["CPUtime.max"].baseline == pytest.approx(0.8)


def test_the_card_maps_a_summary_leaf_to_its_row(tmp_path):
    from dstf.comparison.tree_spec import card_row, parse_metric_tree

    assert card_row(parse_metric_tree(FINAL_CPU)) == "CPUtime"
    assert card_row(parse_metric_tree(MAX_CPU)) == "CPUtime.max"
    statistic = {"metric": "scalar", "variable": "n", "quantity": "translation.n"}
    assert card_row(parse_metric_tree(statistic)) == "translation.n"
    # The 1.1 path is a statistic's row like any: no longer the name's (S2).
    old = {"metric": "scalar", "variable": "CPUtime", "quantity": "CPUtime.final"}
    assert card_row(parse_metric_tree(old)) == "CPUtime.final"


def test_the_same_leaf_on_an_unlisted_variable_is_on_its_plot(tmp_path):
    s, A = _card_session(tmp_path, {**FINAL_CPU, "variable": "y"})
    assert [p.subject for p in s.plots(A)] == [("x",), ("y",)]
    assert s.statistic_leaves(A) == []


def test_a_curve_criterion_on_a_listed_name_has_its_plot(tmp_path):
    s, A = _card_session(
        tmp_path, FINAL_CPU, {"metric": "nrmse", "variable": "CPUtime"}
    )
    assert [p.subject for p in s.plots(A)] == [("x",), ("CPUtime",)]
    assert [path for _, path in s.statistic_leaves(A)] == ["/metrics/children/1"]


def test_a_cards_max_row_plus_seeds_a_criterion_that_scores(tmp_path):
    """(d): the `+` on the `CPUtime.max` row writes the max in the one
    spelling, and it is scored from the two summaries — never "not found"."""
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import Selection, SessionView

    s, A = _card_session(tmp_path, MAX_CPU)
    view = SessionView(s, Selection(tests=frozenset({A})))
    (op,) = (
        EditTree()
        .act(view, action="add", model_id=A, path="/metrics", quantity="CPUtime.max")
        .ops
    )
    assert op.value == {
        "combinator": "warn",
        "children": [
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": "max"},
                "tolerance": 0.1,
                "tolerance_mode": "rel",
            }
        ],
    }
    s.stage([op])
    vc = s.comparisons[A].variables[-1]
    assert "error" not in (vc.diagnostics or {})
    assert (vc.actual_final, vc.reference_final) == (1.12, pytest.approx(0.8))


def test_the_report_lists_a_listed_names_number_in_its_tree_not_its_plot(tmp_path):
    """(b): a scalar final on CPUtime beside an nrmse on CPUtime: the
    CPUtime plot section's criteria are the nrmse alone; the metric tree
    lists both — as main listed a statistic's criterion."""
    from dstf.reporting.context_builder import _build_template_context
    from dstf.reporting.report_suite import plot_inputs, plot_page_keys
    from dstf.reporting.tree_rows import leaves_of

    nrmse = {"metric": "nrmse", "variable": "CPUtime"}
    s, A = _card_session(tmp_path, FINAL_CPU, nrmse)
    project, comp = s.project, s.comparisons[A]
    test, result = project.test(A), project.results[A]
    listed = s.diagnostic_names(A)
    plots, extra = plot_inputs(test, comp.variables, result, None, None, listed)
    ctx = _build_template_context(
        A,
        list(comp.variables),
        project.store.get_reference(A),
        None,
        None,
        test_model=test,
        result=result,
        metric_tree=comp.metric_tree,
        diagnostics=listed,
    )
    ctx.update(
        plot_page_keys(ctx, plots, extra, comp.metric_tree, None, diagnostics=listed)
    )
    by_subject = {
        tuple(record["subject"]): section
        for record, section in zip(ctx["plots"], ctx["plot_sections"])
    }
    assert list(by_subject) == [("x",), ("CPUtime",)]
    criteria = by_subject[("CPUtime",)]["criteria"]
    assert [(c["leaf"]["path"], c["leaf"]["metric"]) for c in criteria] == [
        ("/metrics/children/2", "nrmse")
    ]
    assert [(n["path"], n["metric"]) for n in leaves_of(ctx["tree_view"])] == [
        ("/metrics/children/0", "nrmse"),
        ("/metrics/children/1", "scalar"),
        ("/metrics/children/2", "nrmse"),
    ]


# -- the add form: every name a variable --------------------------------


def _add(session, A, variable, metric, path="/metrics"):
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import Selection, SessionView

    view = SessionView(session, Selection(tests=frozenset({A}), variable="x"))
    (op,) = (
        EditTree()
        .act(
            view, action="add", model_id=A, path=path, variable=variable, metric=metric
        )
        .ops
    )
    return op.value


def _tree_session(tmp_path, kind="and", own=()):
    from dstf.authoring.session import PatchOp
    from test_session import A, _session_with_entry, _two_var_project

    s = (
        _session_with_entry(tmp_path, {"diagnostic_variables": list(own)})
        if own
        else None
    )
    if s is None:
        from dstf.authoring.session import Session

        cfg, work = _two_var_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
    t = np.asarray([i / 4 for i in range(5)])
    s.project.results[A].diagnostics = [
        VariableResult(index=1, time=t, values=0.4 * t, name="CPUtime"),
        VariableResult(index=2, time=t, values=np.round(4 * t), name="EventCounter"),
        VariableResult(index=3, time=t, values=1.0 + t, name="h"),
    ]
    tree = {"combinator": kind, "children": [{"metric": "nrmse", "variable": "x"}]}
    s.stage([PatchOp(A, "add", "/metrics", tree)])
    return s, A


@pytest.mark.parametrize(("kind", "warned"), [("and", True), ("or", False)])
def test_a_scalar_on_cpu_time_writes_the_one_spelling(tmp_path, kind, warned):
    s, A = _tree_session(tmp_path, kind)
    value = _add(s, A, "CPUtime", "scalar")
    leaf = value["children"][0] if warned else value
    assert ("combinator" in value) is warned
    assert leaf == {
        "metric": "scalar",
        "variable": "CPUtime",
        "quantity": {"reduce": "final"},
        "tolerance": 0.1,
        "tolerance_mode": "rel",
    }


@pytest.mark.parametrize(("kind", "warned"), [("and", True), ("or", False)])
def test_a_curve_criterion_on_cpu_time_warns_only_in_an_and(tmp_path, kind, warned):
    """A time's curve moves with the load as its number does."""
    s, A = _tree_session(tmp_path, kind)
    value = _add(s, A, "CPUtime", "nrmse")
    leaf = value["children"][0] if warned else value
    assert ("combinator" in value) is warned
    assert leaf == {"metric": "nrmse", "variable": "CPUtime"}


def test_a_scalar_on_the_event_count_is_exact_and_bare(tmp_path):
    s, A = _tree_session(tmp_path)
    assert _add(s, A, "EventCounter", "scalar") == {
        "metric": "scalar",
        "variable": "EventCounter",
        "quantity": {"reduce": "final"},
        "tolerance": 0,
        "tolerance_mode": "abs",
    }


def test_a_scalar_on_a_tests_own_listed_name_takes_the_ordinary_seed(tmp_path):
    """A per-test `h`: the ordinary scalar seed over the run's number,
    never a time's warn."""
    from dstf.comparison.seeds import SeedData, seed_scalar

    s, A = _tree_session(tmp_path, own=["h"])
    assert "h" in s.diagnostic_names(A)
    one = (np.array([0.0]), np.array([2.0]))
    assert _add(s, A, "h", "scalar") == {
        "metric": "scalar",
        "variable": "h",
        **seed_scalar(SeedData(ref=None, act=one)),
        "quantity": {"reduce": "final"},
    }


def test_adding_a_card_criterion_leaves_the_plot_cards_shape(tmp_path):
    """A number on the card is on no plot: the plot cards are not rebuilt
    (`TestPage.shape`)."""
    from types import SimpleNamespace

    from dstf.authoring.session import PatchOp
    from dstf.authoring.shell.test_page import TestPage

    s, A = _tree_session(tmp_path)
    page = SimpleNamespace(shell=SimpleNamespace(session=s), model_id=A)
    before = TestPage.shape(page, s.tree_view(A))
    s.stage([PatchOp(A, "add", "/metrics/children/-", _add(s, A, "CPUtime", "scalar"))])
    assert TestPage.shape(page, s.tree_view(A)) == before
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {"metric": "nrmse", "variable": "CPUtime"},
            )
        ]
    )
    assert TestPage.shape(page, s.tree_view(A)) != before


# -- a number on the card, from a run or a baseline ---------------------------------------------------------


def _plus(session, A, quantity):
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import Selection, SessionView

    view = SessionView(session, Selection(tests=frozenset({A})))
    (op,) = (
        EditTree()
        .act(view, action="add", model_id=A, path="/metrics", quantity=quantity)
        .ops
    )
    return op.value


def test_the_cards_plus_with_no_run_writes_the_one_spelling_and_scores_once_one_runs(
    tmp_path,
):
    """A baseline and no run loaded: the CPUtime row's `+` is the name's
    number all the same — decided by the names the test lists, not by
    what a run recorded — and it scores once a run is there."""
    from dstf.authoring.session import PatchOp
    from test_session import A, _stats_session

    s = _stats_session(tmp_path, result=False)
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            )
        ]
    )
    value = _plus(s, A, "CPUtime")
    assert value == {"combinator": "warn", "children": [FINAL_CPU | {"tolerance": 0.1}]}
    ran = _stats_session(tmp_path / "ran")
    ran.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}, value],
                },
            )
        ]
    )
    vc = ran.comparisons[A].variables[-1]
    assert "error" not in (vc.diagnostics or {})
    assert (vc.actual_final, vc.reference_final) == (1.12, pytest.approx(0.8))


def test_a_max_rows_plus_with_no_run_writes_its_max(tmp_path):
    from dstf.authoring.session import PatchOp
    from test_session import A, _stats_session

    s = _stats_session(tmp_path, result=False)
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            )
        ]
    )
    assert _plus(s, A, "CPUtime.max")["children"][0] == MAX_CPU | {"tolerance": 0.1}


def test_a_value_with_no_run_is_seeded_from_the_baselines_summary():
    """No run: the ordinary seed reads the baseline's number — within 1%."""
    from dstf.comparison.seeds import seed_variable_number

    assert seed_variable_number("h", None, into="and", baseline=2.0) == {
        "metric": "scalar",
        "variable": "h",
        "quantity": {"reduce": "final"},
        "tolerance_mode": "rel",
        "tolerance": 0.01,
    }


@pytest.mark.parametrize(("run", "changes"), [(1.0099, False), (1.0101, True)])
def test_a_value_rows_changed_is_exactly_its_plus_failing(run, changes):
    """With the baseline's summary the `+` seeds within 1% of it, the
    `changed` rule: one is true exactly when the other fails."""
    from dstf.comparison.modes import ScalarConfig, ScalarMode
    from dstf.comparison.seeds import seed_variable_number
    from dstf.reporting.stats_rows import statistics_rows

    t = np.linspace(0, 1, 3)
    values = np.array([0.0, 0.5, run])
    (row,) = statistics_rows(
        {},
        {},
        listing=["h"],
        run_diagnostics=[VariableResult(index=1, time=t, values=values, name="h")],
        baseline_diagnostics=[DiagnosticSummary("h", final=1.0)],
    )
    seed = seed_variable_number("h", values, into="and", baseline=1.0)
    assert (seed["tolerance"], seed["tolerance_mode"]) == (0.01, "rel")
    params = {k: v for k, v in seed.items() if k not in ("metric", "variable")}
    got = ScalarMode(ScalarConfig(**params)).compare(
        np.array([0.0]), np.array([1.0]), t, values
    )
    assert row.changed is changes is (not got.passed)


def test_without_a_baseline_summary_a_value_keeps_its_bounds_seed():
    from dstf.comparison.seeds import seed_variable_number

    seed = seed_variable_number("h", np.array([1.0, 2.0]), into="and")
    assert {"min_value", "max_value"} <= set(seed) and "tolerance" not in seed


def _figure_session(tmp_path, with_scalar: bool):
    """A's run and baseline hold CPUtime's curve; its tree an nrmse on
    CPUtime, and — ``with_scalar`` — a scalar final on it too."""
    from dstf.authoring.session import PatchOp, Session
    from dstf.backends.types import TestResult
    from dstf.storage.reference_store import ReferenceStore
    from test_session import PROVENANCE, A, _two_var_project

    cfg, work = _two_var_project(tmp_path)
    config = Config(config_file=cfg, work_dir=work)
    tt = np.asarray([i / 4 for i in range(5)])
    ReferenceStore(config).store_reference(
        make_test_model(A, variable_patterns=["x", "y", "CPUtime"]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name="x"),
                VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
                VariableResult(index=3, time=tt, values=0.8 * tt, name="CPUtime"),
            ],
            diagnostics=[
                VariableResult(index=1, time=tt, values=0.8 * tt, name="CPUtime")
            ],
        ),
        provenance=PROVENANCE,
    )
    s = Session()
    s.open(cfg, work_dir=work)
    res = s.project.results[A]
    res.variables = [
        *res.variables,
        VariableResult(index=3, time=tt, values=0.81 * tt, name="CPUtime"),
    ]
    res.diagnostics = [
        VariableResult(index=1, time=tt, values=0.81 * tt, name="CPUtime")
    ]
    kids = [
        {"metric": "nrmse", "variable": "x"},
        {"metric": "nrmse", "variable": "CPUtime"},
    ]
    if with_scalar:
        kids.append(FINAL_CPU)
    s.stage([PatchOp(A, "add", "/metrics", {"combinator": "and", "children": kids})])
    return s, A


def _cpu_figures(s, A) -> tuple[dict, dict]:
    """The CPUtime plot's figure on the tool and in the report."""
    from dstf.reporting.context_builder import _build_template_context
    from dstf.reporting.report_suite import plot_inputs, plot_page_keys

    tool = s.plot_figure(A, [p.subject for p in s.plots(A)].index(("CPUtime",)))
    project, comp = s.project, s.comparisons[A]
    test, result = project.test(A), project.results[A]
    listed = s.diagnostic_names(A)
    plots, extra = plot_inputs(test, comp.variables, result, None, None, listed)
    ctx = _build_template_context(
        A, list(comp.variables), project.store.get_reference(A), None, None,
        test_model=test, result=result, metric_tree=comp.metric_tree,
        diagnostics=listed,
    )  # fmt: skip
    keys = plot_page_keys(ctx, plots, extra, comp.metric_tree, None, diagnostics=listed)
    at = [tuple(r["subject"]) for r in keys["plots"]].index(("CPUtime",))
    return tool, keys["plot_figures"][at]


def _marks(figure: dict) -> tuple:
    return (
        json.dumps(figure.get("data"), sort_keys=True, default=str),
        json.dumps(
            (figure.get("layout") or {}).get("shapes"), sort_keys=True, default=str
        ),
    )


def test_a_card_criterion_draws_nothing_on_a_plot_on_either_tier(tmp_path):
    """Numbers on the card, curves on plots — a scalar final on CPUtime
    beside its nrmse adds no trace and no shape to the CPUtime plot."""
    alone = _cpu_figures(*_figure_session(tmp_path / "alone", False))
    both = _cpu_figures(*_figure_session(tmp_path / "both", True))
    assert _marks(both[0]) == _marks(alone[0])  # the tool
    assert _marks(both[1]) == _marks(alone[1])  # the report


def test_a_card_criterion_leaves_the_tracked_names_as_they_were(tmp_path):
    """A number on CPUtime keeps CPUtime out of Tracked — the Variables
    drawer lists it under Diagnostics, as on main."""
    from dstf.authoring.session import PatchOp
    from test_session import A, _stats_session

    s = _stats_session(tmp_path)
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            )
        ]
    )
    before = s.tracked(A)
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {"combinator": "warn", "children": [FINAL_CPU]},
            )
        ]
    )
    assert "CPUtime" not in before
    assert s.tracked(A) == before


def test_the_closed_line_names_a_changed_value(tmp_path):
    """A listed `h` at 2.0 against 1.0 is named, as a changed time is."""
    from dstf.authoring.shell.stats_card import line_text
    from dstf.reporting.stats_rows import statistics_rows

    t = np.linspace(0, 1, 3)
    rows = statistics_rows(
        {},
        {},
        listing=["h"],
        run_diagnostics=[
            VariableResult(index=1, time=t, values=np.array([0.0, 1.0, 2.0]), name="h")
        ],
        baseline_diagnostics=[DiagnosticSummary("h", final=1.0)],
    )
    assert line_text(rows, True) == "no count changed · h +100%"


@pytest.mark.parametrize(
    "payload", [{"success": True, "time": [0.0, 1.0]}], ids=["no variables key"]
)
def test_the_json_driver_reads_a_payload_with_no_variables(tmp_path, payload):
    """As main read it — a run with nothing, never a KeyError."""
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
    (test_dir / "result.json").write_text(json.dumps(payload))
    result = pr.PythonBackend(cfg).read_result(make_test_model("T"), "test_0001", None)
    assert (result.success, result.variables, result.diagnostics) == (True, [], [])


def test_a_listed_name_with_non_numeric_values_is_not_recorded(tmp_path):
    """As on main, where a listed name was never read: no diagnostic,
    and the test is not failed for it."""
    result = _json_result(
        tmp_path, {"y": [0, 1], "h": ["a", "b"], "CPUtime": [0.0, 0.5]}, ["h"]
    )
    assert result.success
    assert [d.name for d in result.diagnostics] == ["CPUtime"]


def test_the_dashboards_cpu_time_is_its_recorded_digits():
    """The sidecar's `sim_cpu` (and the sort key) is main's 0.045845,
    not the float32's widening."""
    from dstf.reporting.rows import cpu_time

    t = np.array([0.0, 1.0])
    f32 = VariableResult(
        index=1,
        time=t,
        values=np.array([0.0, 0.045845], dtype=np.float32),
        name="CPUtime",
    )
    assert float(np.float32(0.045845)) != 0.045845
    assert cpu_time([f32]) == 0.045845
    f64 = VariableResult(
        index=1, time=t, values=np.array([0.0, 0.0458449982]), name="CPUtime"
    )
    assert cpu_time([f64]) == 0.0458449982


def test_the_report_routes_a_listed_names_number_through_every_hop(tmp_path):
    """`_build_per_test_args` → `_render_one_test`, as `compare --report`
    renders a page: a scalar on CPUtime is in no plot section (the CPUtime
    plot's criteria are its nrmse alone), and a scalar on EventCounter
    derives no plot."""
    import re

    from dstf.reporting.report_suite import _build_per_test_args, _render_one_test

    ev = {
        "metric": "scalar",
        "variable": "EventCounter",
        "quantity": {"reduce": "final"},
    }
    s, A = _card_session(
        tmp_path, FINAL_CPU, {"metric": "nrmse", "variable": "CPUtime"}, ev
    )
    project, comp = s.project, s.comparisons[A]
    args = _build_per_test_args(
        comp,
        project.results,
        {A: project.test(A)},
        project.store,
        project.config,
        {},
    )
    out = tmp_path / "report"
    _render_one_test(args, out)
    page = out / args["report_id"]
    sidecar = json.loads((page / "comparison_data.json").read_text(encoding="utf-8"))
    assert [tuple(p["subject"]) for p in sidecar["plots"]] == [("x",), ("CPUtime",)]
    html = (page / "interactive.html").read_text(encoding="utf-8")
    sections = re.findall(
        r'id="nodes-(\d+)">(.*?)</div>\s*</div>\s*(?=<|$)', html, re.S
    )
    paths = {
        int(i): re.findall(r'class="node node-leaf" data-path="([^"]+)"', body)
        for i, body in sections
    }
    assert paths[1] == ["/metrics/children/2"]  # the nrmse on CPUtime alone
    assert "/metrics/children/1" not in {p for v in paths.values() for p in v}


# -- one scored tree for the card on both tiers ----------------------------------

FLAT_SCALAR = {
    "mode": "scalar",
    "quantity": {"reduce": "final"},
    "tolerance": 0.1,
    "tolerance_mode": "rel",
}


def _flat_session(tmp_path, scored: bool, override: dict = FLAT_SCALAR):
    """A FLAT entry (no `metrics`) with a watch plot of CPUtime. ``scored``:
    it tracks CPUtime and scores it with a `scalar` ``override`` (its final
    by default) — a card leaf of the implicit tree; else CPUtime is not
    scored at all."""
    from dstf.authoring.session import Session
    from dstf.backends.types import TestResult
    from dstf.storage.reference_store import ReferenceStore
    from test_session import PROVENANCE, A, _two_var_project

    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["plots"] = [{"var": "CPUtime"}]
            if scored:
                entry["variables"] = ["x", "y", "CPUtime"]
                entry["comparison"] = {"variable_overrides": {"CPUtime": override}}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    config = Config(config_file=cfg, work_dir=work)
    tt = np.asarray([i / 4 for i in range(5)])
    ReferenceStore(config).store_reference(
        make_test_model(A, variable_patterns=["x", "y", "CPUtime"]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name="x"),
                VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
                VariableResult(index=3, time=tt, values=0.8 * tt, name="CPUtime"),
            ],
            diagnostics=[
                VariableResult(index=1, time=tt, values=0.8 * tt, name="CPUtime")
            ],
        ),
        provenance=PROVENANCE,
    )
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["variables"]["CPUtime"] = list(0.81 * tt)
    path.write_text(json.dumps(payload), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s, A


def test_a_flat_entrys_card_criterion_draws_nothing_on_its_plot_on_either_tier(
    tmp_path,
):
    """The skip sets of both tiers come from the SCORED tree — for a flat
    entry the implicit one: CPUtime's plot draws its curves and errors and
    none of the scalar's marks (no shape, no trace of a leaf's geometry),
    the same figure on the tool as in the report."""
    tool, report = _cpu_figures(*_flat_session(tmp_path, True))
    for figure in (tool, report):
        assert (figure["layout"].get("shapes") or []) == []
        assert {t["meta"]["kind"] for t in figure["data"]} <= {"act", "ref", "error"}
    assert _marks(tool) == _marks(report)


def test_a_flat_entrys_card_criterion_is_on_the_statistics_card(tmp_path):
    from dstf.authoring.shell.stats_card import scored as scored_rows

    s, A = _flat_session(tmp_path, True)
    leaves = s.statistic_leaves(A)
    assert [(leaf.variable, path) for leaf, path in leaves] == [
        ("CPUtime", "/metrics/children/2")
    ]
    assert s.card_paths(A) == {"/metrics/children/2"}
    reads = scored_rows(leaves, s.tree_view(A))
    assert [c["path"] for c in reads["CPUtime"]] == ["/metrics/children/2"]
    assert reads["CPUtime"][0]["status"] == "pass"


def test_edit_on_plot_is_not_offered_for_a_card_criterion(tmp_path):
    """A number on the card has no marks on a plot, so no handles to drag:
    "Edit on plot" is not offered for it, on the plot of its variable."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.session import Selection, SessionView

    s, A = _card_session(
        tmp_path,
        {"metric": "nrmse", "variable": "CPUtime"},
        MAX_CPU | {"max_value": 9.0},
    )
    view = SessionView(
        s,
        Selection(
            tests=frozenset({A}), variable="CPUtime", leaf_path="/metrics/children/2"
        ),
    )
    said = EditOnPlot().available(view)
    assert said == "/metrics/children/2 is not a criterion on CPUtime"


# -- a value's delta reads its direction ------------------------------------------


def _h_row(base: float, run: float):
    from dstf.reporting.stats_rows import statistics_rows

    t = np.linspace(0, 1, 3)
    (row,) = statistics_rows(
        {},
        {},
        listing=["h"],
        run_diagnostics=[
            VariableResult(index=1, time=t, values=np.array([0.0, 0.0, run]), name="h")
        ],
        baseline_diagnostics=[DiagnosticSummary("h", final=base)],
    )
    return row


@pytest.mark.parametrize(
    ("base", "run", "delta"), [(-2.0, -2.5, "-25%"), (-2.0, -1.5, "+25%")]
)
def test_a_negative_baselines_delta_reads_the_direction_it_moved(base, run, delta):
    """Over a negative baseline the delta is relative to its magnitude: a
    value that went down reads down, on the row and on the closed line."""
    from dstf.authoring.shell.stats_card import line_text

    row = _h_row(base, run)
    assert (row.delta, row.changed) == (delta, True)
    assert line_text([row], True) == f"no count changed · h {delta}"


@pytest.mark.parametrize(
    ("run", "delta"), [(4.6e-05, "-<100%"), (0.0, "-100%")], ids=["moved", "to zero"]
)
def test_a_value_that_is_not_zero_never_reads_minus_100_percent(run, delta):
    """As a number that moved never reads +0%: one that is not zero never
    reads -100% — only a fall to zero does."""
    assert _h_row(0.5, run).delta == delta


# -- the card's `+` on a real Session --------------------------------------------


def _h_session(tmp_path, *, run: float | None, stored: float | None, listed=("h",)):
    """A real Session: A's entry lists ``listed``; its run (``run``: h's
    final, None for no run loaded) and its baseline (``stored``: h's stored
    final, None for no summary of h)."""
    from dstf.authoring.session import PatchOp, Session
    from dstf.backends.types import TestResult
    from dstf.storage.reference_store import ReferenceStore
    from test_session import PROVENANCE, A, _two_var_project

    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["diagnostic_variables"] = list(listed)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    tt = np.asarray([i / 4 for i in range(5)])
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(A, variable_patterns=["x", "y"]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name="x"),
                VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
            ],
            diagnostics=[]
            if stored is None
            else [
                VariableResult(
                    index=0, time=tt, values=np.r_[0.0, 0.1, 0.2, 0.3, stored], name="h"
                )
            ],
        ),
        provenance=PROVENANCE,
    )
    s = Session()
    s.open(cfg, work_dir=work)
    if run is None:
        s.project.results.pop(A, None)
    else:
        s.project.results[A].diagnostics = [
            VariableResult(
                index=0, time=tt, values=np.r_[0.0, 0.1, 0.2, 0.3, run], name="h"
            )
        ]
    s.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            )
        ]
    )
    return s, A


def test_with_no_run_the_plus_reads_the_baseline_summary_through_the_session(tmp_path):
    """No run loaded, the baseline's summary of h at 2.0: the `+` seeds
    within 1% of it — read through `Session.baseline_summaries`."""
    s, A = _h_session(tmp_path, run=None, stored=2.0)
    assert _plus(s, A, "h") == {
        "metric": "scalar",
        "variable": "h",
        "quantity": {"reduce": "final"},
        "tolerance_mode": "rel",
        "tolerance": 0.01,
    }


def test_a_bounds_seeds_tooltip_names_its_bounds(tmp_path):
    """A listed h at 1.5 with no baseline summary is seeded with bounds:
    the `+` says them, as the card's requirement does — never "exact"."""
    from dstf.authoring.builtin.edit_tree import _quantity_leaf, _requirement
    from dstf.authoring.session import Selection, SessionView

    s, A = _h_session(tmp_path, run=1.5, stored=None)
    view = SessionView(s, Selection(tests=frozenset({A})))
    seed = _quantity_leaf(view, A, "h", "and")
    assert (seed["min_value"], seed["max_value"]) == (1.42, 1.58)
    assert _requirement(seed) == "Add a requirement: in [1.42, 1.58]"


H_FINAL = {
    "metric": "scalar",
    "variable": "h",
    "quantity": {"reduce": "final"},
    "tolerance": 0.01,
    "tolerance_mode": "rel",
}


def _report_context(s, A) -> dict:
    """The report's page context over the Session's files: its Statistics
    table, its tree view and its plots, as `generate_comparison_plots`
    builds them."""
    from dstf.reporting.context_builder import _build_template_context
    from dstf.reporting.report_suite import plot_inputs, plot_page_keys

    project, comp = s.project, s.comparisons[A]
    test, result = project.test(A), project.results[A]
    listed = s.diagnostic_names(A)
    plots, extra = plot_inputs(test, comp.variables, result, None, None, listed)
    ctx = _build_template_context(
        A,
        list(comp.variables),
        project.store.get_reference(A),
        None,
        None,
        test_model=test,
        result=result,
        metric_tree=comp.metric_tree,
        diagnostics=listed,
    )
    ctx.update(
        plot_page_keys(ctx, plots, extra, comp.metric_tree, None, diagnostics=listed)
    )
    return ctx


def _report_labels(ctx: dict) -> set[str]:
    return {row["label"] for sec in ctx["statistics_sections"] for row in sec["rows"]}


def test_a_name_the_test_does_not_list_has_no_row_on_either_tier(tmp_path):
    """The run recorded `h` and the baseline summarizes it, but the test
    lists only `x9` (owner, 2026-10-19): no `h` row on the card, none in
    the report's Statistics table — whatever a stored result still holds.
    Its `+` would have put a number on a plot: the rows are of the names
    the test lists, the set a leaf is routed by."""
    s, A = _h_session(tmp_path, run=1.0, stored=1.0, listed=("x9",))
    assert "h" not in s.diagnostic_names(A)
    assert "h" not in {r.path for r in s.statistics(A)}
    assert "h" not in _report_labels(_report_context(s, A))
    listed, A = _h_session(tmp_path / "listed", run=1.0, stored=1.0)
    assert "h" in {r.path for r in listed.statistics(A)}
    assert "h" in _report_labels(_report_context(listed, A))


def test_every_rows_plus_lands_its_criterion_on_the_card(tmp_path):
    """Each row's `+` writes a criterion the card shows (`statistic_leaves`,
    `card_paths`) and no plot is derived for — a listed name's in its one
    spelling, and it scores."""
    from dstf.authoring.session import PatchOp

    s, A = _h_session(tmp_path, run=1.0, stored=1.0)
    rows = [r.path for r in s.statistics(A) if r.kind != "text"]
    assert "h" in rows
    plots = [p.subject for p in s.plots(A)]
    for row in rows:
        value = _plus(s, A, row)
        if row == "h":
            assert (value["variable"], value["quantity"]) == ("h", {"reduce": "final"})
        s.stage([PatchOp(A, "add", "/metrics/children/-", value)])
        path = f"/metrics/children/{len(s.tree(A)['children']) - 1}"
        assert path in [p for _, p in s.statistic_leaves(A)], row
        assert path in s.card_paths(A), row
        assert [p.subject for p in s.plots(A)] == plots, row
    vc = next(v for v in s.comparisons[A].variables if v.name == "h")
    assert "error" not in (vc.diagnostics or {})
    assert (vc.actual_final, vc.reference_final, vc.passed) == (1.0, 1.0, True)


def test_a_criterion_on_a_listed_name_never_run_has_its_row_on_both_tiers(tmp_path):
    """A criterion on a listed name nothing holds yet (a fresh test, a
    listing just added) is routed to the card: its row is there, with no
    values, to see, edit and delete it — and its `+` is that name's number.
    In the report, run against a baseline that holds no `h`, the tree lists
    the criterion and no plot is derived for it."""
    from dstf.authoring.session import PatchOp
    from dstf.authoring.shell.stats_card import scored as scored_rows
    from dstf.reporting.tree_rows import leaves_of

    s, A = _h_session(tmp_path, run=None, stored=None)
    s.stage([PatchOp(A, "add", "/metrics/children/-", H_FINAL)])
    path = "/metrics/children/1"
    (row,) = [r for r in s.statistics(A) if r.path == "h"]
    assert (row.kind, row.run, row.baseline) == ("value", None, None)
    assert [p for _, p in s.statistic_leaves(A)] == [path]
    assert s.card_paths(A) == {path}
    reads = scored_rows(s.statistic_leaves(A), s.tree_view(A))
    assert [(c["path"], c["status"]) for c in reads["h"]] == [(path, "pending")]
    assert [p.subject for p in s.plots(A)] == [("x",)]
    value = _plus(s, A, "h")
    assert (value["variable"], value["quantity"]) == ("h", {"reduce": "final"})

    ran, A = _h_session(tmp_path / "ran", run=1.0, stored=None)
    ran.stage([PatchOp(A, "add", "/metrics/children/-", H_FINAL)])
    ctx = _report_context(ran, A)
    assert [(n["path"], n["metric"]) for n in leaves_of(ctx["tree_view"])] == [
        ("/metrics/children/0", "nrmse"),
        (path, "scalar"),
    ]
    assert [tuple(r["subject"]) for r in ctx["plots"]] == [("x",)]
    assert "h" in _report_labels(ctx)


@pytest.mark.parametrize(
    ("base", "run", "delta"),
    [
        (1.0, 0.004, "-<100%"),
        (1.0, 0.006, "-99%"),
        (1.0, 0.0, "-100%"),
        (1.0, 1e-300, "-<100%"),
        (-1.0, -0.004, "+<100%"),
        (-1.0, -0.006, "+99%"),
        (-1.0, 0.0, "+100%"),
        (-2.0, -1e-6, "+<100%"),
        (-2.0, -4.0, "-100%"),
    ],
)
def test_a_value_near_zero_reads_less_than_100_percent_on_both_sides(base, run, delta):
    """A run within 0.5% of the baseline's magnitude from zero, and not
    zero, reads "<100%" toward zero — down over a positive baseline, up over
    a negative one; only an exact zero reads 100%. Elsewhere the change is
    of the baseline's magnitude: -2 -> -4 moved down by all of it."""
    assert _h_row(base, run).delta == delta


def test_a_flat_entrys_card_leaf_stays_on_the_card_while_its_run_is_in_progress(
    tmp_path,
):
    """The tree the card reads is the one the page is laid out by: while a
    run has taken the result, the comparison it replaced (D151) — never an
    empty one that drops a flat entry's card leaf onto a plot."""
    from dstf.backends.pool import ItemState
    from test_session_queue import drop_item, fake_item

    s, A = _flat_session(tmp_path, scored=True)

    def layout():
        return {
            "statistic_leaves": [path for _, path in s.statistic_leaves(A)],
            "card_paths": sorted(s.card_paths(A)),
            "tracked": s.tracked(A),
            "plots": [p.subject for p in s.plots(A)],
            "rows": [r.path for r in s.statistics(A)],
        }

    idle = layout()
    assert idle["card_paths"] and idle["statistic_leaves"]
    running = fake_item(s, A, ItemState.RUNNING)
    s._item_started(running)
    assert A not in s.project.results
    assert layout() == idle
    drop_item(s, running)


def test_a_flat_entrys_override_on_a_listed_names_max_has_its_own_row(tmp_path):
    """A flat entry's override scoring CPUtime's max is a card leaf of the
    implicit tree, shown on ``CPUtime.max`` — a row no side's statistics
    hold, so the card adds it under "other scored" (S4), read from the
    summaries, as an authored tree's leaf is."""
    s, A = _flat_session(
        tmp_path, scored=True, override={**FLAT_SCALAR, "quantity": {"reduce": "max"}}
    )
    rows = {r.path: r for r in s.statistics(A)}
    assert rows["CPUtime.max"].section == "other scored"
    assert rows["CPUtime.max"].kind == "time"
    assert [path for _, path in s.statistic_leaves(A)] == ["/metrics/children/2"]
    assert s.card_paths(A) == {"/metrics/children/2"}
