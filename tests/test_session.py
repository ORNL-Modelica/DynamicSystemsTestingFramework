"""The Session (two-tier design §4.5, tests per §6 "The Session"): pure
Python against a Python-backend project whose run is on disk."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import make_test_model
from dstf.authoring.capabilities import Artifact, Job, OpenProject, Patch, StoreWrite
from dstf.authoring.session import (
    NOTHING_SCORED,
    JobRunningError,
    LockedError,
    Session,
    SessionView,
    StaleSpecError,
)
from dstf.authoring.spec_document import PatchOp, SpecDocument
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config, ConfigError
from dstf.discovery.patch_apply import PatchError
from dstf.discovery.plots import PlotExtra, parse_plots
from dstf.discovery.spec_update import implicit_tree_op, materialize_implicit_metrics
from dstf.discovery.test_registry import TestModel
from dstf.reporting.status import FAIL, NO_REF, PASS, SIM_FAIL, classify
from dstf.storage.reference_store import ReferenceStore

A, B, C, D, G = "FakeLib.A", "FakeLib.B", "FakeLib.C", "FakeLib.D", "FakeLib.G"


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """testing.json + work dir. A: baseline x=t, result x=1.25t — nrmse 0.153,
    FAIL at the config tolerance 0.01. B: no baseline (NO_REF). C: no result
    (SIM_FAIL). D: baseline x=t, result x=1.001t — nrmse 6e-4, PASS at 0.01
    and FAIL at the 1e-4 default, so a scorer that drops config.tolerance is
    caught. G: like D but tracked as ["*"] — a glob spec whose verdict is
    PASS as loaded, so a `*` sentinel leaf would flip it (Conventions #7)."""
    lib = tmp_path / "FakeLib"
    (lib / "Examples").mkdir(parents=True)
    for short in ("A", "B", "C", "D", "G"):
        (lib / "Examples" / f"{short}.py").write_text(
            "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
        )
    refs = lib / "refs"
    refs.mkdir()
    (refs / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "source_path": "..",
                "library_name": "FakeLib",
                "test_spec": "./test_spec.json",
                "reference_root": ".",
                "backend": "Python",
                "backends": {"Python": ["python3", "python"]},
                "tolerance": 0.01,
            }
        ),
        encoding="utf-8",
    )
    entries = [
        {
            "model": m,
            "source": f"../Examples/{m.rsplit('.', 1)[-1]}.py",
            "variables": ["*"] if m == G else ["x"],
            "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
        }
        for m in (A, B, C, D, G)
    ]
    (refs / "test_spec.json").write_text(
        json.dumps({"tests": entries}, indent=2) + "\n", encoding="utf-8"
    )

    work = tmp_path / "work"
    work.mkdir()
    t = [i / 4 for i in range(5)]
    manifest = {
        "test_0001": {"model_id": A, "ref_id": None, "last_run_status": "passed"},
        "test_0002": {"model_id": B, "ref_id": None, "last_run_status": "passed"},
        "test_0003": {"model_id": C, "ref_id": None, "last_run_status": "pending"},
        "test_0004": {"model_id": D, "ref_id": None, "last_run_status": "passed"},
        "test_0005": {"model_id": G, "ref_id": None, "last_run_status": "passed"},
    }
    (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for key, values in (
        ("test_0001", [v * 1.25 for v in t]),
        ("test_0002", t),
        ("test_0004", [v * 1.001 for v in t]),
        ("test_0005", [v * 1.001 for v in t]),
    ):
        (work / key).mkdir()
        (work / key / "result.json").write_text(
            json.dumps({"success": True, "time": t, "variables": {"x": values}}),
            encoding="utf-8",
        )
    (work / "status.json").write_text(
        json.dumps({"metadata": {"backend_name": "Python"}, "tests": []}),
        encoding="utf-8",
    )

    config = Config(config_file=refs / "testing.json", work_dir=work)
    tt = np.asarray(t)
    store = ReferenceStore(config)
    for m in (A, D, G):
        store.store_reference(
            make_test_model(m, variable_patterns=["x"]),
            TestResult(
                model_id=m,
                success=True,
                variables=[VariableResult(index=1, time=tt, values=tt, name="x")],
            ),
        )
    return refs / "testing.json", work


def _modelica_project(tmp_path: Path) -> Path:
    """test_discovery._write_lib's Modelica library, opened on the Python
    backend so `select_backend` succeeds without a simulator. The scan is
    what these tests are about; nothing is simulated."""
    from test_discovery import _write_lib

    lib = tmp_path / "MyLib"
    lib.mkdir()
    _write_lib(lib)
    cfg_path = lib / "testing.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.update({"backend": "Python", "backends": {"Python": ["python3", "python"]}})
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg_path


def _bump_stop_time(tmp_path: Path) -> tuple[Path, TestModel]:
    """Raise Foo.mo's StopTime by one; returns the file and the model as
    scanned BEFORE the edit (model_id MyLib.Foo, stop_time 100.0)."""
    from dstf.discovery.test_registry import scan_source_tests

    lib = tmp_path / "MyLib"
    before = scan_source_tests(Config(config_file=lib / "testing.json"))["MyLib.Foo"]
    mo = lib / "Foo.mo"
    text = mo.read_text(encoding="utf-8")
    assert f"StopTime={before.stop_time:g}" in text
    mo.write_text(
        text.replace(
            f"StopTime={before.stop_time:g}", f"StopTime={before.stop_time + 1:g}"
        ),
        encoding="utf-8",
    )
    return mo, before


def _add_bar(tmp_path: Path) -> None:
    """A second model in the Modelica library `_modelica_project` writes."""
    (tmp_path / "MyLib" / "Bar.mo").write_text(
        "within MyLib;\nmodel Bar\n  Real y;\n  UnitTests unitTests(n=1, x={y});\n"
        "equation\n  y = time;\n  annotation(experiment(StopTime=1));\nend Bar;\n",
        encoding="utf-8",
    )


def _snapshot(work: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(p.relative_to(work)), p.read_bytes())
        for p in work.rglob("*")
        if p.is_file()
    )


def _two_var_project(tmp_path: Path, ref_models=(A,)) -> tuple[Path, Path]:
    """`_project` with A and B tracking ["x", "y"], results for both, and a
    two-variable baseline for each model in ``ref_models`` (A only by
    default, so B stays NO_REF)."""
    cfg, work = _project(tmp_path)
    t = [i / 4 for i in range(5)]
    for key in ("test_0001", "test_0002"):
        (work / key / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {"x": [v * 1.25 for v in t], "y": [2 * v for v in t]},
                }
            ),
            encoding="utf-8",
        )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] in (A, B):
            entry["variables"] = ["x", "y"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    config = Config(config_file=cfg, work_dir=work)
    tt = np.asarray(t)
    store = ReferenceStore(config)
    for m in ref_models:
        store.store_reference(
            make_test_model(m, variable_patterns=["x", "y"]),
            TestResult(
                model_id=m,
                success=True,
                variables=[
                    VariableResult(index=1, time=tt, values=tt, name="x"),
                    VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
                ],
            ),
        )
    return cfg, work


#: The untracked variables of A's result `_page_project` draws as watch plots.
PAGE_WATCH = ("w1", "w2", "w3", "w4", "w5")


def _page_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project`, scored by an explicit tree, with five WATCH plots
    over `PAGE_WATCH`, extra variables of A's result file: seven plots, a
    page taller than the viewport, so the cards below it are the ones
    render-on-scroll has to reach.

    A's tree is `and[tube · x, range · x, range · y]` (the tube is
    `test_handles.TREE`'s band leaf, its control points inside the trace's
    span): the tube and the range on y FAIL, the range on x passes, so the
    closed tree line has a tally and two jumps, and the test's verdict is
    FAIL. The tube is the leaf with rings; the range on x is the leaf whose
    bound LINES are on the same plot, which is what a refused drag needs.
    """
    # test_handles imports this module: its TREE is read at call time.
    from test_handles import TREE

    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for k, name in enumerate(PAGE_WATCH, start=3):
        payload["variables"][name] = [k * v for v in payload["time"]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] != A:
            continue
        entry["plots"] = [{"var": name} for name in PAGE_WATCH]
        entry["metrics"] = {
            "combinator": "and",
            "children": [
                copy.deepcopy(TREE["children"][0]),
                {
                    "metric": "range",
                    "variable": "x",
                    "min_value": -1.0,
                    "max_value": 2.0,
                },
                {
                    "metric": "range",
                    "variable": "y",
                    "min_value": -1.0,
                    "max_value": 1.0,
                },
            ],
        }
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


#: A model in a sub-package of FakeLib: the one id `_two_package_project` adds.
CASES_X = "FakeLib.Cases.X"


def _two_package_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_project` with a second package: `FakeLib.Cases.X` (tracks x, no
    result, so SIM_FAIL). The project lists its tests by model id, so X
    stands between C and D; a grouped list sorts by package first, so X
    comes last under its own label. Every other id shares FakeLib, which is
    why only this fixture tells the two orders apart: grouped reads A, B, C,
    D, G, Cases.X; flat reads A, B, C, Cases.X, D, G."""
    cfg, work = _project(tmp_path)
    (cfg.parent.parent / "Examples" / "X.py").write_text(
        "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
    )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"].append(
        {
            "model": CASES_X,
            "source": "../Examples/X.py",
            "variables": ["x"],
            "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
        }
    )
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


#: The expression that used to cost its variable its identity: the text a
#: Modelica ``x={cat(1, a, b)}`` writes beside ``unitTests.x[1]``. No result
#: file ever carried it as a NAME — the comparison relabelled the variable
#: ``x[1]`` for scoring and display, which is what left ~70 TRANSFORM tests
#: with no editable tree. It is a label now; the name is the result file's.
AWKWARD_EXPRESSION = "cat(1, a, b)[1]"

#: A UnitTests variable's result-file name, and the expression beside it.
UT1, UT2 = "unitTests.x[1]", "unitTests.x[2]"
UT1_EXPR, UT2_EXPR = AWKWARD_EXPRESSION, "cat(1, a, b)[2]"


def with_expressions(session: Session, model_id: str, expressions: dict[str, str]):
    """Stamp ``expression`` on the loaded result's variables, keyed by name.

    FakeLib is a Python-backend project: its ``result.json`` carries names and
    values, and the backend has no carrier for an expression — only a Modelica
    UnitTests component writes one. A test that needs a UnitTests-shaped
    variable sets it here, after ``open``. Nothing is renamed."""
    for var in session.project.results[model_id].variables:
        if var.name in expressions:
            var.expression = expressions[var.name]


def _unit_tests_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with A's two variables named the way the result file
    of a UnitTests test names them — the shape the expressions ride beside."""
    cfg, work = _two_var_project(tmp_path, ref_models=())
    t = [i / 4 for i in range(5)]
    (work / "test_0001" / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "time": t,
                "variables": {UT1: [v * 1.25 for v in t], UT2: [2 * v for v in t]},
            }
        ),
        encoding="utf-8",
    )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = [UT1, UT2]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    tt = np.asarray(t)
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(A, variable_patterns=[UT1, UT2]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name=UT1),
                VariableResult(index=2, time=tt, values=2.1 * tt, name=UT2),
            ],
        ),
    )
    return cfg, work


def _awkward_expression_project(tmp_path: Path, *, with_baseline: bool = True):
    """`_two_var_project` with A's first variable named the way a UnitTests
    result file names it, `AWKWARD_EXPRESSION` the text the model wrote for
    it — the shape of the ~70 TRANSFORM tests this plan is for. The
    expression is stamped with `with_expressions` once a Session is open (a
    Python backend carries none). Without a baseline for A, the fixture's
    one-variable baseline is all there is and the result's names are what
    the page has to go on."""
    cfg, work = _two_var_project(tmp_path, ref_models=(A,) if with_baseline else ())
    t = [i / 4 for i in range(5)]
    (work / "test_0001" / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "time": t,
                "variables": {UT1: t, "y": [2 * v for v in t]},
            }
        ),
        encoding="utf-8",
    )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = [UT1, "y"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    if with_baseline:
        tt = np.asarray(t)
        ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
            make_test_model(A, variable_patterns=[UT1, "y"]),
            TestResult(
                model_id=A,
                success=True,
                variables=[
                    VariableResult(index=1, time=tt, values=tt, name=UT1),
                    VariableResult(index=2, time=tt, values=2 * tt, name="y"),
                ],
            ),
        )
    return cfg, work


def _base_kinds(fig: dict) -> list[tuple[str, str]]:
    """The act / ref / error traces a figure draws, as sorted (kind, id).
    Leaves are not base traces; overlays are, but no fixture in this module
    has one, so they are not collected here."""
    return sorted(
        (tr["meta"].get("kind"), tr["meta"].get("id"))
        for tr in fig["data"]
        if tr.get("meta") and tr["meta"].get("kind") in ("act", "ref", "error")
    )


@pytest.fixture
def session(tmp_path) -> Session:
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


# The materialized leaf for A is {"metric": "nrmse", "variable": "x"} — no
# tolerance key (the entry has no variable_overrides), so the op is an add.
LOOSEN = PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)


class TestOpen:
    def test_loaded_verdicts_match_the_cli_classifier(self, session):
        assert classify(session.comparisons[A]) == FAIL
        assert classify(session.comparisons[B]) == NO_REF
        assert classify(session.comparisons[C]) == SIM_FAIL
        assert (
            classify(session.comparisons[D]) == PASS
        )  # config tolerance 0.01, not the 1e-4 default
        assert classify(session.comparisons[G]) == PASS
        assert session.project.config.backend_name == "Python"
        assert [t.model_id for t in session.project.tests] == [A, B, C, D, G]
        assert session.spec.pending == []

    def test_open_writes_nothing_in_the_work_dir(self, tmp_path):
        cfg, work = _project(tmp_path)
        before = _snapshot(work)
        Session().open(cfg, work_dir=work)
        assert _snapshot(work) == before

    def test_open_warms_the_overlay_index_off_the_calling_thread(
        self, tmp_path, monkeypatch
    ):
        """Brief §2.2: the first click paid 3 s for the sibling-backend
        index. It is built once, right after open, on its own thread."""
        from dstf.reporting import overlay_loader as ol

        warmed_on = []
        real_warm = ol.warm_sibling_backend_index

        def spy(config):
            warmed_on.append(threading.current_thread().name)
            real_warm(config)

        monkeypatch.setattr(ol, "warm_sibling_backend_index", spy)
        cfg, work = _project(tmp_path)
        ol._sibling_backend_index.cache_clear()
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.overlay_index_ready.wait(5.0)
        assert warmed_on == ["dstf-overlay-index"]
        before = ol._sibling_backend_index.cache_info()
        s.view(A, "x")
        after = ol._sibling_backend_index.cache_info()
        assert before.misses == 1
        assert after.misses == 1 and after.hits == before.hits + 1

    def test_the_index_warms_alongside_the_results_read_not_after_it(
        self, tmp_path, monkeypatch
    ):
        """The warm thread reads the sibling reference trees; the results read
        reads this backend's. Neither waits on the other, so the warm belongs
        at the commit point, where the project is live and the reload has not
        started — over TRANSFORM the reload is 15.6 s of head start.

        The reload here blocks until the warm thread is observed running, so a
        start moved back after the reload deadlocks this test rather than
        quietly costing the first click its overlays.
        """
        from dstf.reporting import overlay_loader as ol

        warming = threading.Event()
        monkeypatch.setattr(
            ol, "warm_sibling_backend_index", lambda config: warming.set()
        )
        real_reload = Session._reload_results

        def reload_waiting_for_the_warm(self):
            assert warming.wait(5.0), "the overlay index was not warming yet"
            return real_reload(self)

        monkeypatch.setattr(Session, "_reload_results", reload_waiting_for_the_warm)
        cfg, work = _project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.overlay_index_ready.wait(5.0)

    def test_a_failed_warm_still_marks_the_index_ready(
        self, tmp_path, monkeypatch, caplog
    ):
        """A sibling tree that raises (a PermissionError from iterdir) is
        logged, and the Event is set anyway — a waiter never hangs on it."""
        from dstf.reporting import overlay_loader as ol

        def broken(config):
            raise PermissionError("sibling tree unreadable")

        monkeypatch.setattr(ol, "warm_sibling_backend_index", broken)
        cfg, work = _project(tmp_path)
        s = Session()
        with caplog.at_level("ERROR", logger="dstf.authoring.session"):
            s.open(cfg, work_dir=work)
            assert s.overlay_index_ready.wait(5.0)
        assert "warming the sibling-backend overlay index failed" in caplog.text

    def test_the_recorded_backend_wins_over_the_config(self, tmp_path):
        cfg, work = _project(tmp_path)
        (work / "status.json").write_text(
            json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []})
        )
        from dstf.config import ConfigError

        with pytest.raises(ConfigError, match="Nonesuch"):
            Session().open(cfg, work_dir=work)

    def test_a_backend_that_cannot_be_built_opens_as_a_config_error(
        self, tmp_path, monkeypatch
    ):
        """A missing executable raises ``RuntimeError`` out of the backend's
        constructor. ``open`` builds through ``select_backend``, so it reaches
        `dstf author` as the ConfigError the CLI prints and exits 2 on, not a
        traceback."""
        import shutil

        cfg, work = _project(tmp_path)
        testing = json.loads(cfg.read_text(encoding="utf-8"))
        testing["backends"] = {"Python": [str(tmp_path / "no-such-python")]}
        cfg.write_text(json.dumps(testing), encoding="utf-8")
        # The map's one path does not exist; without this neither does the
        # PATH fallback, in the config and in the backend alike.
        monkeypatch.setattr(shutil, "which", lambda *a, **k: None)

        from dstf.config import ConfigError

        with pytest.raises(ConfigError, match="Python binary not found"):
            Session().open(cfg, work_dir=work)

    def test_a_snapshot_predating_the_backend_rename_notices_its_warning(
        self, tmp_path
    ):
        """``metadata`` with no ``backend_name`` (written when the key was
        "simulator") — ``recorded_backend``'s warning is the project's first
        notice, so the job panel shows it."""
        cfg, work = _project(tmp_path)
        (work / "status.json").write_text(
            json.dumps({"metadata": {"simulator_name": "Python"}, "tests": []})
        )
        session = Session()
        session.open(cfg, work_dir=work)
        assert any("predates the Backend rename" in n for n in session.notices)
        assert not hasattr(session, "warnings")

    def test_the_projects_backend_is_quiet_and_open_narrates_nothing(
        self, tmp_path, capsys
    ):
        """A UI process has no console to narrate to. Not the `session`
        fixture: it opens before capsys starts capturing, so the assertion
        would pass against nothing."""
        cfg, work = _project(tmp_path)
        session = Session()
        session.open(cfg, work_dir=work)
        assert session.project.backend.quiet is True
        # Both streams: the run header and the progress bar go to stderr, the
        # "Reading N result files..." and "Read phase:" lines to stdout.
        assert capsys.readouterr() == ("", "")

    def test_read_last_results_reports_each_result_with_its_run_verdict_applied(
        self, tmp_path
    ):
        """The tool scores each result as it lands (Session._reload_results);
        a test whose run failed must arrive already forced to success=False,
        or it is scored green for the seconds before the batch verdict."""
        cfg, work = _project(tmp_path)
        # Record A's last run as failed in the manifest _project wrote. The
        # file IS the test-key map (BatchManifest.save writes `.manifest`
        # at the top level, src/dstf/storage/batch_manifest.py:69); A's
        # result.json still reads as a success.
        path = work / "batch_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.values():
            if entry["model_id"] == A:
                entry["last_run_status"] = "failed"
        path.write_text(json.dumps(data), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        seen = []
        results = s.project.backend.read_last_results(
            s.project.tests,
            on_result=lambda model_id, result: seen.append((model_id, result.success)),
        )
        assert sorted(seen) == sorted((m, r.success) for m, r in results.items())
        assert dict(seen)[A] is False  # the verdict, before the callback
        assert dict(seen)[D] is True

    def test_open_reports_its_phases_and_every_scored_test(self, tmp_path):
        """Brief §3.8: the page shows "scanning… N found", fills rows as
        results are scored, and a test opens as soon as it is scored."""
        cfg, work = _project(tmp_path)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.on("results", lambda e: events.append({"event": "results", **e}))
        s.open(cfg, work_dir=work)
        phases = [e["phase"] for e in events if e["event"] == "loading"]
        assert (
            phases[0] == "scan" and phases[-1] == "done"
        )  # "scanning…" fires even for a source that scans nothing
        scored = [e["model"] for e in events if e.get("phase") == "results"]
        # Every model the manifest names: C has no result.json and the reader
        # returns it as a failed result, which is read and announced too.
        assert set(scored) == {A, B, C, D, G}
        # Each results event names a model already scored.
        for e in events:
            if e.get("phase") == "results":
                assert e["done"] <= e["total"]
        # ONE batch results event, after the per-test events (Conventions #7).
        kinds = [e["event"] for e in events]
        assert kinds.count("results") == 1
        assert kinds.index("results") > max(
            i for i, e in enumerate(events) if e.get("phase") == "results"
        )
        assert events[-1]["phase"] == "done" and events[-1]["seconds"] >= 0

    def test_a_test_is_scored_when_its_loading_event_fires(self, tmp_path):
        cfg, work = _project(tmp_path)
        s = Session()
        verdicts = {}
        s.on(
            "loading",
            lambda e: (
                verdicts.update({e["model"]: s.comparisons[e["model"]].passed})
                if e["phase"] == "results"
                else None
            ),
        )
        s.open(cfg, work_dir=work)
        assert verdicts[A] is False and verdicts[D] is True

    def test_a_test_added_since_the_last_run_is_announced_and_done_reaches_total(
        self, tmp_path
    ):
        """FakeLib.E is in the spec and has no manifest entry: it is scored
        after the per-result loop (SIM_FAIL, no result). It must be
        announced like the others — a page filling rows per event has no
        row for it otherwise — and the last announcement is done == total."""
        cfg, work = _project(tmp_path)
        e_id = "FakeLib.E"
        (cfg.parent.parent / "Examples" / "E.py").write_text(
            "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
        )
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": e_id,
                "source": "../Examples/E.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=work)
        announced = [e for e in events if e["phase"] == "results"]
        assert {e["model"] for e in announced} == {A, B, C, D, G, e_id}
        assert [e["done"] for e in announced] == list(range(1, 7))
        assert announced[-1]["done"] == announced[-1]["total"] == 6
        assert classify(s.comparisons[e_id]) == SIM_FAIL

    def test_one_models_scoring_failure_leaves_the_results_complete_and_is_raised(
        self, session, monkeypatch
    ):
        """A scorer that raises for one model (a malformed reference, a
        scorer bug) must not abort the read: every result is stored, every
        model is announced, the batch event fires, the failed model's stale
        verdict is dropped rather than shown, and the failure is raised once
        — `_finish_job` records it as the job's error; `open` propagates it."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        events = []
        session.on("loading", events.append)
        session.on("results", lambda e: events.append({"event": "results", **e}))
        with pytest.raises(RuntimeError) as exc_info:
            session._reload_results()
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert D in str(exc_info.value)
        assert {A, B, C, D, G} <= set(session.project.results)
        assert D not in session.comparisons
        assert classify(session.comparisons[A]) == FAIL
        announced = [e for e in events if e.get("phase") == "results"]
        assert announced[-1]["done"] == announced[-1]["total"] == 5
        assert events[-1]["event"] == "results"

    def test_a_failed_models_announcement_carries_the_error_flag(
        self, session, monkeypatch
    ):
        """A per-event row filler reads `comparisons[model]` for the announced
        model; a model whose scoring raised has none there, and its event
        must say so: `error=True` on that announcement, `False` on every
        other (docs/ideas.md 2026-09-16, second entry)."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        events = []
        session.on("loading", events.append)
        with pytest.raises(RuntimeError):
            session._reload_results()
        flags = {e["model"]: e["error"] for e in events if e.get("phase") == "results"}
        assert flags == {A: False, B: False, C: False, D: True, G: False}

    def test_open_emits_done_and_starts_the_rescan_when_one_models_scoring_raised(
        self, tmp_path, monkeypatch
    ):
        """After a partial scoring failure the project is open and its
        results complete (the test above); a shell waiting for `done` — or
        for `rescan_done` — must not wait forever. The failure still reaches
        the caller, after `done`."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        cfg, work = _project(tmp_path)
        s = Session()
        events = []
        s.on("loading", events.append)
        with pytest.raises(RuntimeError, match="scoring FakeLib.D failed"):
            s.open(cfg, work_dir=work)
        assert events[-1]["phase"] == "done"
        assert s.rescan_done.wait(5)  # a Python project scans nothing: set at once
        assert s.project is not None and {A, B, C, D, G} <= set(s.project.results)
        assert D not in s.comparisons

    def test_a_model_under_two_manifest_keys_is_one_failure(self, session, monkeypatch):
        """`failures` counts models, not `_score` calls: a manifest listing one
        model under two keys (hand-edited; the tool never writes one) read
        "2 of 5 models" for one failing model (docs/ideas.md 2026-09-16)."""
        import dstf.authoring.session as session_mod

        manifest = session.project.config.work_dir / "batch_manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["test_0009"] = dict(data["test_0004"])  # D, a second time
        manifest.write_text(json.dumps(data), encoding="utf-8")
        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        with pytest.raises(RuntimeError, match=r"\(1 of 5 models\)"):
            session._reload_results()

    def test_a_cached_open_parses_nothing_and_rescans_in_the_background(
        self, tmp_path, monkeypatch
    ):
        from dstf.discovery import scan_cache, test_registry

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)  # helper: _write_lib + a Python backend entry
        Session().open(cfg, work_dir=tmp_path / "work")  # cold: writes the cache
        assert list(scan_cache.cache_dir().glob("*.json"))

        calls = []
        real = test_registry.scan_source_tests

        def spy(config, progress=None):
            calls.append(threading.current_thread().name)
            return real(config, progress=progress)

        monkeypatch.setattr("dstf.authoring.session.scan_source_tests", spy)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=tmp_path / "work")
        assert (
            threading.current_thread().name not in calls
        )  # nothing parsed on the opening thread (the rescan thread may already have run)
        assert any(e["phase"] == "scan" and e.get("cached") for e in events)
        assert s.rescan_done.wait(10.0)
        assert calls == ["dstf-rescan"]
        phases = [e["phase"] for e in events]
        assert phases.index("done") < phases.index(
            "rescan"
        )  # `done` is emitted before the thread starts
        assert events[-1]["phase"] == "rescan" and events[-1]["changed"] is False

    def test_a_source_edit_reaches_the_project_through_the_rescan(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        mo, before = _bump_stop_time(tmp_path)
        s = Session()
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        s.open(cfg, work_dir=tmp_path / "work")
        assert (
            s.project.test(before.model_id).stop_time == before.stop_time
        )  # from the cache
        assert s.rescan_done.wait(10.0)
        assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        assert rescored[-1] == {before.model_id}  # only the changed model was rescored

    def test_a_model_added_since_the_cache_reads_its_result_before_it_is_scored(
        self, tmp_path, monkeypatch
    ):
        """The cache names Foo only; `dstf run` has since simulated Foo and
        Bar. The stale open reads Bar as "not found in discovery"; the
        rescan must re-read it with its TestModel before rescoring, or Bar
        scores SIM_FAIL where a cold open says NO_REF."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        work = tmp_path / "work"
        Session().open(cfg, work_dir=work)  # the cache names Foo only
        _add_bar(tmp_path)
        work.mkdir(exist_ok=True)
        manifest = {
            "test_0001": {
                "model_id": "MyLib.Foo",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "MyLib.Bar",
                "ref_id": None,
                "last_run_status": "passed",
            },
        }
        (work / "batch_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        for key in manifest:
            (work / key).mkdir()
            (work / key / "result.json").write_text(
                json.dumps({"success": True, "time": [0, 1], "variables": {}}),
                encoding="utf-8",
            )
        stale = Session()
        stale.open(cfg, work_dir=work)
        assert stale.rescan_done.wait(10.0)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cold"))
        cold = Session()
        cold.open(cfg, work_dir=work)
        assert classify(stale.comparisons["MyLib.Bar"]) == classify(
            cold.comparisons["MyLib.Bar"]
        )
        assert stale.project.results["MyLib.Bar"].success is True

    def test_the_rescan_stores_only_the_models_it_re_read(self, tmp_path, monkeypatch):
        """`read_results` reports EVERY manifest entry, and a model outside
        the re-read's own list comes back as "not found in discovery". Only
        the changed models may be stored: without that filter an unchanged
        Bar's result flips to a failed read on every rescan, and its next
        rescore says SIM_FAIL where the open said NO_REF."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        _add_bar(tmp_path)
        work = tmp_path / "work"
        Session().open(cfg, work_dir=work)  # the cache names Foo and Bar
        work.mkdir(exist_ok=True)
        manifest = {
            "test_0001": {
                "model_id": "MyLib.Foo",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "MyLib.Bar",
                "ref_id": None,
                "last_run_status": "passed",
            },
        }
        (work / "batch_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        for key in manifest:
            (work / key).mkdir()
            (work / key / "result.json").write_text(
                json.dumps({"success": True, "time": [0, 1], "variables": {}}),
                encoding="utf-8",
            )
        _mo, before = _bump_stop_time(tmp_path)  # Foo changed, Bar not
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.rescan_done.wait(10.0)
        assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        assert s.project.results["MyLib.Bar"].success is True
        assert classify(s.rescore("MyLib.Bar")) == NO_REF

    def test_a_rescans_read_that_raises_leaves_the_session_as_it_was(
        self, tmp_path, monkeypatch
    ):
        """The rescan re-reads a changed model with its new TestModel. The
        read happened after the tests were swapped and the gone models'
        comparisons dropped, so one that raised (the disk went away) left a
        project whose tests were the new scan's and whose results and
        comparisons were the old one's. Read first: a raise changes
        nothing."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        _mo, before = _bump_stop_time(tmp_path)
        posted: list = []
        s = Session(post=posted.append)
        s.open(cfg, work_dir=tmp_path / "work")
        for fn in list(posted):  # the open's own hooks; the rescan may be among them
            if fn.__name__ != "apply":
                posted.remove(fn)
                fn()
        deadline = time.monotonic() + 10.0
        while not any(fn.__name__ == "apply" for fn in posted):
            assert time.monotonic() < deadline, "the rescan posted nothing"
            time.sleep(0.02)
        apply = next(fn for fn in posted if fn.__name__ == "apply")
        project = s.project
        tests, scanned = list(project.tests), dict(project.scanned)
        comparisons, results = dict(s.comparisons), dict(project.results)

        def gone(*_args, **_kwargs):
            raise OSError("the work dir went away")

        monkeypatch.setattr(project.backend, "read_last_results", gone)
        with pytest.raises(OSError, match="went away"):
            apply()
        assert project.tests == tests
        assert project.test(before.model_id).stop_time == before.stop_time
        assert project.scanned == scanned
        assert s.comparisons == comparisons and project.results == results
        assert s.rescan_done.is_set()  # a waiter is never left hanging

    def test_only_the_changed_model_of_two_is_rescored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        _add_bar(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        _mo, before = _bump_stop_time(tmp_path)
        s = Session()
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        s.open(cfg, work_dir=tmp_path / "work")
        assert {"MyLib.Foo", "MyLib.Bar"} <= set(s.comparisons)
        assert s.rescan_done.wait(10.0)
        assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        assert rescored[-1] == {before.model_id}

    def test_done_precedes_rescan_even_when_the_rescan_wins_the_race(
        self, tmp_path, monkeypatch
    ):
        """The worst schedule: the rescan thread finishes inside `start()`.
        `done` is emitted before the thread starts, so it still comes first."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        real_start = threading.Thread.start

        def start(self):
            if self.name == "dstf-rescan":
                self.run()
            else:
                real_start(self)

        monkeypatch.setattr(threading.Thread, "start", start)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=tmp_path / "work")
        assert s.rescan_done.is_set()
        phases = [e["phase"] for e in events]
        assert phases.index("done") < phases.index("rescan")

    def test_a_locked_model_is_left_to_its_job_by_the_rescan(
        self, tmp_path, monkeypatch
    ):
        import queue

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        _mo, before = _bump_stop_time(tmp_path)
        posted: queue.Queue = queue.Queue()
        s = Session(post=posted.put)
        s.open(cfg, work_dir=tmp_path / "work")
        comp = s.comparisons[before.model_id]
        release = threading.Event()
        s.apply(
            Job(
                run=lambda ctx: release.wait(10),
                label="rerun",
                models=frozenset({before.model_id}),
            )
        )
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        # The rescan's re-merge, run on "the UI loop" — and the open's other
        # post, the sibling index ready (D141), in whichever order they came.
        for _ in range(2):
            posted.get(timeout=10)()
        assert s.rescan_done.is_set()
        assert s.comparisons[before.model_id] is comp  # not rescored under the lock
        assert all(before.model_id not in r for r in rescored)
        release.set()
        posted.get(timeout=10)()  # the job's completion
        assert s.running is None

    def test_a_late_thread_from_a_previous_open_does_not_mark_this_one_ready(
        self, tmp_path, monkeypatch
    ):
        """Each open owns its Events; a rescan or index thread still running
        from the previous open sets ITS events, never the new open's."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        s = Session()
        s.open(cfg, work_dir=tmp_path / "work")
        first_rescan, first_index = s.rescan_done, s.overlay_index_ready
        s.open(cfg, work_dir=tmp_path / "work")  # allowed: no job, no pending edits
        assert s.rescan_done is not first_rescan
        assert s.overlay_index_ready is not first_index
        assert s.rescan_done.wait(10.0) and s.overlay_index_ready.wait(5.0)

    def test_open_writes_nothing_in_the_work_dir_even_when_it_caches(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        Session().open(cfg, work_dir=work)
        assert _snapshot(work) == []

    def test_a_cold_open_that_cannot_cache_still_opens(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "dstf").write_text("not a directory", encoding="utf-8")
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        cfg = _modelica_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=tmp_path / "work")
        assert "MyLib.Foo" in s.comparisons
        assert s.rescan_done.is_set()  # a cold open has nothing to rescan

    def test_a_rescan_that_cannot_cache_still_re_merges_and_finishes(
        self, tmp_path, monkeypatch
    ):
        """The cache was written by a cold open and is readable; the rescan
        finds a change and cannot write it back. The re-merge and the
        `rescan` event must still happen and `rescan_done` must be set."""
        import os
        import stat

        cache = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        from dstf.discovery.scan_cache import cache_dir

        scan_dir = cache_dir()
        assert list(scan_dir.glob("*.json"))
        _mo, before = _bump_stop_time(tmp_path)
        scan_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            if os.access(scan_dir, os.W_OK):
                pytest.skip("this user can write a read-only directory (root)")
            s = Session()
            events = []
            s.on("loading", events.append)
            s.open(cfg, work_dir=tmp_path / "work")
            assert s.rescan_done.wait(10.0)
            assert events[-1]["phase"] == "rescan" and events[-1]["changed"] is True
            assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        finally:
            scan_dir.chmod(stat.S_IRWXU)


class TestRescoreAndStage:
    def test_rescore_on_a_flat_test_stages_the_materialization_spec_update_would(
        self, session
    ):
        entry = session.spec.entry(A)
        assert "metrics" not in entry
        session.rescore(A)
        assert session.spec.pending == [
            PatchOp(A, "add", "/metrics", implicit_tree_op(entry)["value"])
        ]
        assert (
            session.spec.pending[0].raw()
            == materialize_implicit_metrics(entry, [LOOSEN.raw()])[0]
        )
        # And again is a no-op: the tree is there now.
        session.rescore(A)
        assert len(session.spec.pending) == 1

    def test_rescore_verdict_equals_the_loaded_one_when_nothing_changed(self, session):
        assert classify(session.rescore(A)) == FAIL
        assert (
            classify(session.rescore(D)) == PASS
        )  # the tree path keeps config.tolerance too

    def test_a_pattern_tracked_spec_materializes_its_resolved_names(self, session):
        """Owner ruling (D126): the leaves are what the pattern resolved
        today; the pattern stays in `variables` and keeps tracking. The
        verdict is what it was."""
        assert classify(session.rescore(G)) == PASS
        entry = session.spec.entry(G)
        assert entry["variables"] == ["*"]
        assert [c["variable"] for c in entry["metrics"]["children"]] == ["x"]
        assert session.comparisons[G].warnings == []

    def test_a_variable_that_matches_later_is_tracked_but_not_scored_and_says_so(
        self, session
    ):
        """The consequence of D126 made visible: after materialization a
        new match has no leaf. It is stored and plotted, not judged — and
        the comparison carries the warning naming it, on both tiers."""
        session.rescore(G)
        work = session.project.config.work_dir
        t = [i / 4 for i in range(5)]
        (work / "test_0005" / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {"x": [v * 1.001 for v in t], "y": t},
                }
            ),
            encoding="utf-8",
        )
        session._reload_results()
        comp = session.comparisons[G]
        assert classify(comp) == PASS  # y is not judged
        (warning,) = comp.warnings
        assert warning.field == "Tracked variables without a criterion"
        assert warning.current_value == "y" and "1 scored" in warning.reference_value
        # Cover it and the warning goes.
        session.stage(
            [
                PatchOp(
                    G,
                    "add",
                    "/metrics/children/-",
                    {"metric": "nrmse", "variable": "y"},
                )
            ]
        )
        assert session.comparisons[G].warnings == []

    def test_materialization_builds_leaves_from_what_the_flat_path_scored(
        self, session
    ):
        """The report's tree: one leaf per scored comparison, in that order,
        whatever the spec's own variable list says (for a test tracked by its
        .mo annotation the spec lists none). An unnamed comparison cannot
        become a leaf, so nothing is staged for it."""
        op, why = session._materialization(D)
        assert op is not None and why is None
        assert [leaf["variable"] for leaf in op["value"]["children"]] == [
            vc.name for vc in session.comparisons[D].variables
        ]
        # A leaf must name a variable the loaded result carries: a name that
        # is not one of them — or no name at all — resolves to nothing on the
        # tree path, so nothing is staged.
        session.comparisons[D].variables[0].name = "x[1]"
        assert session._materialization(D)[0] is None
        session.comparisons[D].variables[0].name = ""
        assert session._materialization(D)[0] is None

    def test_a_unit_tests_test_materializes_and_the_tree_scores_what_the_flat_did(
        self, tmp_path
    ):
        """The ~70 TRANSFORM tests this plan is for: their variables are the
        UnitTests component's, expressions and all. The names are the result
        file's on both paths, so the materialized tree scores the same
        numbers the flat path did — and the expression rides on either."""
        from dataclasses import asdict

        cfg, work = _unit_tests_project(tmp_path)
        session = Session()
        session.open(cfg, work_dir=work)
        with_expressions(session, A, {UT1: UT1_EXPR, UT2: UT2_EXPR})
        # Score the flat path AFTER the expressions are on the result: the
        # comparisons `open` built were made without them, so reading those
        # asserted "rides on either" on the tree alone (the Task 3 review).
        flat = session._score(A)
        assert [vc.expression for vc in flat.variables] == [UT1_EXPR, UT2_EXPR]

        def numbers(comp):
            keep = (
                "name",
                "passed",
                "nrmse",
                "rmse",
                "max_abs_error",
                "max_abs_error_time",
                "tolerance_used",
                "mode",
            )
            return [{k: asdict(vc)[k] for k in keep} for vc in comp.variables]

        flat_status, flat_numbers = classify(flat), numbers(flat)
        op, _why = session._materialization(A)
        assert op is not None
        assert [leaf["variable"] for leaf in op["value"]["children"]] == [UT1, UT2]

        tree = session.rescore(A)  # stages the op, scores by the tree
        assert session.spec.entry(A)["metrics"]["children"][0]["variable"] == UT1
        assert classify(tree) == flat_status
        assert numbers(tree) == flat_numbers
        assert [vc.expression for vc in tree.variables] == [UT1_EXPR, UT2_EXPR]

    def test_a_model_with_no_spec_entry_gets_one_from_the_materialization(
        self, session
    ):
        """TRANSFORM's shape: 313 of 331 tests have no spec entry at all. The
        op targets the model; staging creates the entry with the tree and
        the verdict is what it was."""
        from dstf.authoring.spec_document import SpecDocument

        doc = session.spec.effective()
        doc["tests"] = [e for e in doc["tests"] if e["model"] != D]
        session.spec = SpecDocument(session.spec.path, doc, session.spec.content_hash)
        assert session.spec.entry(D) is None
        session.rescore(D)
        entry = session.spec.entry(D)
        assert entry is not None and [
            c["variable"] for c in entry["metrics"]["children"]
        ] == ["x"]
        assert classify(session.comparisons[D]) == PASS
        assert session.export()["patches"][0]["model"] == D

    def test_a_reference_json_override_rescores_without_staging(self, tmp_path):
        """The flat path merges the baseline's own comparison.variable_overrides
        (the tier below the spec's); a synthesized tree ignores them. A's
        baseline carrying tolerance 0.5 makes A PASS as loaded — a look must
        not stage the tree that would fail it."""
        cfg, work = _project(tmp_path)
        config = Config(config_file=cfg, work_dir=work)
        t = np.asarray([i / 4 for i in range(5)])
        ReferenceStore(config).store_reference(
            make_test_model(
                A, variable_patterns=["x"], variable_overrides={"x": {"tolerance": 0.5}}
            ),
            TestResult(
                model_id=A,
                success=True,
                variables=[VariableResult(index=1, time=t, values=t, name="x")],
            ),
        )
        s = Session()
        s.open(cfg, work_dir=work)
        assert classify(s.comparisons[A]) == PASS
        assert classify(s.rescore(A)) == PASS
        assert s.spec.pending == []
        # A spec override for x shadows the baseline's: the tree scores the
        # same thing again, so materialization is back on the table.
        s.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/comparison",
                    {"variable_overrides": {"x": {"tolerance": 0.5}}},
                )
            ]
        )
        assert s._materialization(A)[0] is not None

    def test_an_unknown_mode_rescores_without_staging(self, tmp_path):
        """The flat path fails a variable whose override names an unknown
        mode; the synthesizer would fall back to nrmse and PASS it."""
        cfg, work = _project(tmp_path)
        doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
        entry = next(e for e in doc["tests"] if e["model"] == D)
        entry["comparison"] = {
            "variable_overrides": {"x": {"mode": "nrmsee", "tolerance": 0.01}}
        }
        (cfg.parent / "test_spec.json").write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
        s = Session()
        s.open(cfg, work_dir=work)
        assert classify(s.comparisons[D]) == FAIL
        assert classify(s.rescore(D)) == FAIL
        assert s.spec.pending == []
        # And the CLI's materialization refuses the same entry outright.
        from dstf.discovery.patch_apply import PatchError
        from dstf.discovery.spec_update import implicit_tree_op

        with pytest.raises(PatchError, match="nrmsee"):
            implicit_tree_op(s.spec.entry(D))

    def test_a_string_quantity_rescores_without_staging(self, tmp_path):
        """The flat path refuses a stats-path quantity in an override ("needs
        a metrics tree"); a tree would score it — FAIL to PASS on a look."""
        cfg, work = _project(tmp_path)
        doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
        entry = next(e for e in doc["tests"] if e["model"] == D)
        entry["comparison"] = {
            "variable_overrides": {
                "x": {"mode": "scalar", "quantity": "CPUtime.final", "max_value": 100}
            }
        }
        (cfg.parent / "test_spec.json").write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
        s = Session()
        s.open(cfg, work_dir=work)
        assert classify(s.comparisons[D]) == FAIL
        assert classify(s.rescore(D)) == FAIL
        assert s.spec.pending == []

    def test_stage_materializes_like_rescore_and_refuses_an_unscored_test(
        self, session
    ):
        """One rule on both paths: a per-leaf op on B (NO_REF, nothing scored)
        cannot get a tree from the entry's list behind rescore's back."""
        from dstf.discovery.patch_apply import PatchError

        with pytest.raises(PatchError, match=f"{B}: {NOTHING_SCORED}$"):
            session.stage([PatchOp(B, "add", "/metrics/children/0/tolerance", 0.5)])
        assert session.spec.pending == []
        session.stage([LOOSEN])
        assert [c["variable"] for c in session.spec.pending[0].value["children"]] == [
            vc.name for vc in session.comparisons[A].variables
        ]

    def test_open_emits_spec_before_results(self, tmp_path):
        cfg, work = _project(tmp_path)
        s = Session()
        seen = []
        s.on("spec", lambda e: seen.append("spec"))
        s.on("results", lambda e: seen.append("results"))
        s.open(cfg, work_dir=work)
        assert seen == ["spec", "results"]

    def test_reload_drops_a_model_the_spec_no_longer_names(self, session):
        p = session.spec.path
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["tests"] = [e for e in doc["tests"] if e["model"] != D]
        p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        session.reload()
        assert D not in session.comparisons
        assert [t.model_id for t in session.project.tests] == [A, B, C, G]

    def test_view_of_an_unknown_model_is_an_error_like_rescore(self, session):
        with pytest.raises(KeyError):
            session.view("FakeLib.Nope", "x")

    def test_rescore_reflects_a_staged_op(self, session):
        session.stage([LOOSEN])
        assert classify(session.comparisons[A]) == PASS
        assert classify(session.rescore(A)) == PASS
        assert [op.path for op in session.spec.pending] == ["/metrics", LOOSEN.path]

    def test_a_bad_op_leaves_the_session_unchanged(self, session):
        from dstf.discovery.patch_apply import PatchError

        with pytest.raises(PatchError):
            session.stage([PatchOp(A, "replace", "/simulation/stop_time", 3)])
        assert session.spec.pending == [] and classify(session.comparisons[A]) == FAIL

    def test_stage_emits_spec_then_results(self, session):
        seen = []
        session.on("spec", lambda e: seen.append(("spec", e["models"])))
        session.on("results", lambda e: seen.append(("results", e["models"])))
        session.stage([LOOSEN])
        assert seen == [("spec", {A}), ("results", {A})]

    def test_no_result_and_no_baseline_rescore_to_the_cli_states(self, session):
        assert classify(session.rescore(B)) == NO_REF
        assert classify(session.rescore(C)) == SIM_FAIL

    def test_consecutive_replaces_of_one_path_collapse(self, session):
        """Edit criteria stages on every control change; the drawer and the
        export must read "leaf -> new leaf", not a keystroke log."""
        session.stage([LOOSEN])
        n = len(session.spec.pending)
        for tol in (0.4, 0.3, 0.2):
            session.stage([PatchOp(A, "replace", "/metrics/children/0/tolerance", tol)])
        # LOOSEN is an add, and a replace after an add of its path is that
        # add with the new value: the three replaces collapse INTO it.
        assert len(session.spec.pending) == n
        assert session.spec.pending[-1] == PatchOp(
            A, "add", "/metrics/children/0/tolerance", 0.2
        )
        session.stage(
            [
                PatchOp(A, "replace", "/metrics/children/0/tolerance", 0.9),
                PatchOp(A, "add", "/comparison", {"tolerance": 0.5}),
            ]
        )
        assert len(session.spec.pending) == n + 2  # a batch is never collapsed
        # The discriminators, each one op: a different path, a different
        # model, and an add then a remove of one path (never collapsed).
        m = len(session.spec.pending)
        session.stage(
            [PatchOp(A, "replace", "/comparison/tolerance", 0.6)]
        )  # last is the add
        session.stage([PatchOp(A, "replace", "/metrics/children/0/tolerance", 0.1)])
        assert len(session.spec.pending) == m + 2  # a different path never collapses
        session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.3})])
        session.stage([PatchOp(D, "replace", "/comparison/tolerance", 0.2)])
        session.stage([PatchOp(A, "replace", "/comparison/tolerance", 0.7)])
        assert len(session.spec.pending) == m + 5  # a different model never collapses
        session.stage([PatchOp(A, "add", "/comparison/tolerance", 0.8)])
        assert len(session.spec.pending) == m + 6  # an add never collapses
        session.stage([PatchOp(A, "remove", "/comparison/tolerance")])
        # A remove after an ADD stays two ops: the add may have replaced a
        # member that existed, and dropping both would leave that member.
        assert len(session.spec.pending) == m + 7

    def test_tree_or_reason_is_the_one_why(self, session):
        """The tree, or why there is none — one short line each, true of the
        test it is said of: nothing scored (B: no baseline; C: no result), a
        simulate-only test, a last run the loaded result no longer matches
        (a scored name it does not carry), and every other refusal of
        `_materialization` (an unnamed comparison)."""
        from dstf.authoring.session import NO_TREE, SIMULATE_ONLY, STALE

        tree, why = session.tree_or_reason(A)
        assert tree == session.tree(A) and tree is not None and why is None
        assert session.tree_or_reason(B) == (None, NOTHING_SCORED)
        assert session.tree_or_reason(C) == (None, NOTHING_SCORED)
        assert NOTHING_SCORED == "nothing is scored"
        session.project.test(B).simulate_only = True
        assert session.tree_or_reason(B) == (None, SIMULATE_ONLY)
        assert SIMULATE_ONLY == "simulate-only: nothing is scored"
        assert session.tree_or_reason(D)[1] is None
        session.comparisons[D].variables[0].name = "x[1]"
        assert session.tree_or_reason(D) == (None, STALE)
        assert STALE == "its last run no longer holds what it scored — rerun it"
        with pytest.raises(PatchError, match=f"{D}: {STALE}$"):
            session.stage([PatchOp(D, "add", "/metrics/children/0/tolerance", 0.5)])
        session.comparisons[D].variables[0].name = ""
        assert session.tree_or_reason(D) == (None, NO_TREE)
        assert all(
            len(why) <= 110 for why in (NOTHING_SCORED, SIMULATE_ONLY, STALE, NO_TREE)
        )
        with pytest.raises(KeyError):
            session.tree_or_reason("FakeLib.Examples.Nope")

    def test_the_baseline_is_read_once_for_the_tree_and_its_reason(self, session):
        """`tree_or_reason` asks `_materialization` once: the tree and the
        reason come from one reading of the baseline JSON."""
        calls = []
        read = session.project.store.get_reference
        session.project.store.get_reference = lambda m: calls.append(m) or read(m)
        session.tree_or_reason(D)
        assert calls == [D]

    def test_stage_on_a_model_not_in_the_project_is_a_patch_error(self, session):
        """A per-leaf op on a model the project does not have is refused as
        a patch, never a KeyError out of the reason's lookup."""
        with pytest.raises(PatchError, match="^FakeLib.Nope: "):
            session.stage(
                [PatchOp("FakeLib.Nope", "add", "/metrics/children/0/tolerance", 1)]
            )
        assert session.spec.pending == []

    def test_a_null_metrics_is_no_tree(self, tmp_path):
        """`"metrics": null` is no tree: spec_parser scores the flat path, so
        the Session materializes that tree — it did not, and `tree` was None
        beside a tree view while `stage` descended into the null (and the
        validator refused any op on the entry: a null is no tree to parse)."""
        from dstf.discovery.spec_update import needs_materialization

        cfg, work = _project(tmp_path)
        doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
        next(e for e in doc["tests"] if e["model"] == A)["metrics"] = None
        (cfg.parent / "test_spec.json").write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.tree_view(A) is not None
        assert needs_materialization(s.spec.entry(A), [LOOSEN.raw()])
        assert [c["variable"] for c in s.tree(A)["children"]] == ["x"]
        assert s.tree_or_reason(A)[1] is None
        s.stage([PatchOp(A, "add", "/comparison", {"tolerance": 0.01})])
        s.stage([LOOSEN])
        assert [op.path for op in s.spec.pending] == [
            "/comparison",
            "/metrics",
            LOOSEN.path,
        ]
        assert s.spec.pending[1].materializes
        assert s.spec.entry(A)["metrics"]["children"][0]["tolerance"] == 0.5


class TestAFlatTestScoresWhatItTracks:
    def test_an_extra_in_the_result_is_neither_scored_nor_materialized(self, tmp_path):
        """D tracks ["x"] with no tree; its result also holds w (a re-read
        for a criterion since discarded leaves one). The verdict and the
        tree a per-leaf op materializes are x's alone."""
        cfg, work = _project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        x = s.project.results[D].variables[0]
        s.project.results[D].variables.append(
            VariableResult(index=2, time=x.time, values=x.values * 7, name="w")
        )
        # A flat-path rescore (an op no tree needs), then a per-leaf one
        # that materializes the tree from what that scored.
        s.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.01})])
        assert [vc.name for vc in s.comparisons[D].variables] == ["x"]
        assert classify(s.comparisons[D]) == PASS
        s.stage([PatchOp(D, "add", "/metrics/children/0/tolerance", 0.01)])
        tree = s.spec.pending[1]
        assert tree.path == "/metrics"
        assert [c["variable"] for c in tree.value["children"]] == ["x"]


class TestView:
    def test_a_scored_variable_carries_its_leaves(self, session):
        view = session.view(A, "x")
        assert view.ref_time is not None and len(view.leaves) == 1
        assert view.leaves[0].path.startswith("/metrics")
        fig = session.figure(A, "x")
        assert {"data", "layout"} <= fig.keys() and len(fig["data"]) >= 2

    def test_the_tools_figure_rings_every_marker_handle(self, session):
        tree = {
            "combinator": "and",
            "children": [
                {
                    "metric": "tube",
                    "variable": "x",
                    "tube_width_mode": "band",
                    "tube_points": [
                        {"time": 0.5, "upper": 0.1, "lower": 0.2},
                        {"time": 0.9, "upper": 0.1, "lower": 0.1},
                    ],
                },
                {
                    "metric": "range",
                    "variable": "x",
                    "min_value": -0.5,
                    "max_value": 2.0,
                },
                {
                    "metric": "points",
                    "variable": "x",
                    "points": [
                        {"time": 0.25, "tolerance": 0.05},
                        {"time": 0.75, "value": 0.75, "tolerance": 0.05},
                    ],
                },
                {
                    "metric": "scalar",
                    "variable": "x",
                    "quantity": {"reduce": "max"},
                    "min_value": 0.0,
                    "max_value": 2.0,
                },
                {
                    "metric": "tube",
                    "variable": "x",
                    "tube_width_mode": "rel",
                    "tube_points": [{"time": 0.5, "upper": 0.1, "lower": 0.1}],
                },
                {
                    "metric": "tube",
                    "variable": "x",
                    "tube_width_mode": "abs",
                    "tube_points": [{"time": 0.0, "upper": 1.5, "lower": -0.5}],
                },
            ],
        }
        session.stage([PatchOp(D, "add", "/metrics", tree)])
        fig = session.figure(D, "x")
        assert fig["config"]["edits"] == {"shapePosition": True}
        rings = {s["name"] for s in fig["layout"]["shapes"] if s["type"] == "circle"}
        c = "/metrics/children"
        assert rings == {
            f"{c}/0/tube/upper/0",
            f"{c}/0/tube/lower/0",
            f"{c}/0/tube/upper/1",
            f"{c}/0/tube/lower/1",
            f"{c}/2/points/target/0",
            f"{c}/2/points/target/1",
            f"{c}/4/tube/upper/0",
            f"{c}/4/tube/lower/0",
            f"{c}/5/tube/upper/0",
            f"{c}/5/tube/lower/0",
        }
        assert not any("/points/box/" in name for name in rings)
        # Real modes over several leaves: every ring is drawn after every other
        # shape (the later shape takes a drag).
        shapes = fig["layout"]["shapes"]
        assert all(s["type"] == "circle" for s in shapes[-len(rings) :])

    def test_editing_names_the_leaves_that_get_rings(self, session):
        import copy

        from test_handles import TREE

        session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])

        def rings(fig):
            return {s["name"] for s in fig["layout"]["shapes"] if s["type"] == "circle"}

        every = rings(session.figure(D, "x"))
        leaf = "/metrics/children/0"
        some = rings(session.figure(D, "x", editing=(leaf,)))
        assert some and some < every and all(n.startswith(leaf + "/") for n in some)
        none = session.figure(D, "x", editing=())
        assert not rings(none)
        assert (
            none["config"] == session.figure(D, "x")["config"]
        )  # react, never newPlot

    def test_editing_refuses_a_str(self, session):
        """One path, not a one-tuple: a str's membership is by substring, so
        it must not reach the ring test. The refusal is the figure layer's."""
        import copy

        from test_handles import TREE

        session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
        with pytest.raises(TypeError, match="not a str"):
            session.figure(D, "x", editing="/metrics/children/0")

    def test_no_baseline_is_the_empty_state_with_the_trajectory(self, session):
        view = session.view(B, "x")
        assert view.act_time is not None and view.ref_time is None and view.leaves == ()

    def test_no_result_is_the_empty_state_without_a_trajectory(self, session):
        view = session.view(C, "x")
        assert view.act_time is None and view.leaves == ()
        assert "layout" in session.figure(C, "x")

    def test_tree_view_is_the_reports_tree(self, session):
        view = session.tree_view(A)
        assert view["kind"] == "combinator" and view["path"] == "/metrics"
        (leaf,) = view["children"]
        assert leaf["path"] == "/metrics/children/0" and leaf["variable"] == "x"
        assert leaf["passed"] is False and leaf["score_display"]
        assert session.tree_view(B) is None  # NO_REF: nothing compared

    def test_tree_is_the_entry_or_the_materialization(self, session):
        assert [c["variable"] for c in session.tree(A)["children"]] == [
            "x"
        ]  # would materialize
        session.stage([LOOSEN])
        assert session.tree(A)["children"][0]["tolerance"] == 0.5  # the entry's now
        assert session.tree(B) is None
        # A copy: editing it in place must not reach the document.
        session.tree(A)["children"][0]["tolerance"] = 99.0
        assert session.tree(A)["children"][0]["tolerance"] == 0.5
        with pytest.raises(KeyError):
            session.tree("FakeLib.Nope")


class TestSaveDiscardExport:
    def test_save_applies_exactly_the_pending_ops_and_clears_them(self, session):
        session.stage([LOOSEN])
        expected = session.spec.effective()
        session.save()
        on_disk = json.loads(session.spec.path.read_text(encoding="utf-8"))
        assert on_disk == expected
        assert session.spec.pending == [] and not session.spec.on_disk_changed()
        assert on_disk["tests"][0]["metrics"]["children"][0]["tolerance"] == 0.5
        assert classify(session.comparisons[A]) == PASS
        assert b"\r\n" not in session.spec.path.read_bytes()

    def test_save_refuses_on_a_changed_hash(self, session):
        session.stage([LOOSEN])
        p = session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        before = p.read_bytes()
        with pytest.raises(StaleSpecError, match=str(p)):
            session.save()
        assert p.read_bytes() == before and len(session.spec.pending) == 2

    def test_reload_after_a_refused_save(self, session):
        session.stage([LOOSEN])
        p = session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        session.reload()
        assert session.spec.pending == [] and not session.spec.on_disk_changed()
        assert classify(session.comparisons[A]) == FAIL

    def test_discard_restores_the_loaded_verdicts(self, session):
        session.stage([LOOSEN])
        assert classify(session.comparisons[A]) == PASS
        session.discard()
        assert session.spec.pending == [] and classify(session.comparisons[A]) == FAIL
        assert "metrics" not in session.spec.entry(A)

    def test_export_is_the_multi_model_envelope(self, session):
        session.stage([LOOSEN, PatchOp(B, "add", "/comparison", {"tolerance": 0.2})])
        env = session.export()
        assert [g["model"] for g in env["patches"]] == [A, B]
        assert env["patches"][0]["patch"][-1] == LOOSEN.raw()
        assert "model" not in env


def _started(session):
    """Capture the RunningJob from the started event — reading
    `session.running` after apply() races a job that completes at once."""
    jobs = []
    session.on(
        "job", lambda e: jobs.append(e["job"]) if e["state"] == "started" else None
    )
    return jobs


class _Gate:
    """A job that blocks until released, so the lock set can be observed."""

    def __init__(self):
        self.release = threading.Event()
        self.seen = None

    def __call__(self, ctx):
        self.seen = ctx
        assert self.release.wait(5)


class TestOutcomes:
    """Each Outcome kind reaches its effect and no other."""

    def test_patch_stages_and_touches_nothing_else(self, session, tmp_path):
        before = session.spec.path.read_bytes()
        session.apply(Patch(ops=[LOOSEN]))
        assert [op.path for op in session.spec.pending] == ["/metrics", LOOSEN.path]
        assert session.spec.path.read_bytes() == before and session.running is None
        assert session.project.store.get_reference(B) is None

    def test_store_write_runs_against_the_store_and_reloads_results(self, session):
        calls = []
        seen = []
        session.on("results", lambda e: seen.append(e["models"]))
        session.apply(
            StoreWrite(run=lambda store: calls.append(store), models=frozenset({B}))
        )
        assert calls == [session.project.store]
        assert session.spec.pending == [] and session.running is None
        assert seen and seen[-1] == {A, B, C, D, G}

    def test_a_store_write_that_reports_a_line_notices_it_after_the_reload(
        self, session
    ):
        order = []
        session.on("results", lambda e: order.append("results"))
        session.on("job", lambda e: order.append(e["state"]))
        events = []
        session.on(
            "job", lambda e: events.append(e) if e["state"] == "written" else None
        )
        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        assert session.notices == ["stored 1"]
        assert [e["state"] for e in events] == ["written"]
        assert events[0]["message"] == "stored 1"
        assert order == ["results", "written"]  # the reload first

    def test_a_store_write_that_reports_nothing_notices_nothing(self, session):
        states = []
        session.on("job", lambda e: states.append(e["state"]))
        session.apply(StoreWrite(run=lambda store: None, models=frozenset({B})))
        assert session.notices == [] and states == []

    def test_close_empties_the_notices(self, session):
        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        assert session.notices == ["stored 1"]
        session.close()
        assert session.notices == []

    def test_open_empties_the_notices(self, session):
        """A notice names a project's models; an OpenProject swap calls open,
        never close, so without this it would show in the next project's
        panel."""
        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        assert session.notices == ["stored 1"]
        config = session.project.config
        session.open(config.config_file, work_dir=config.work_dir)
        assert session.notices == []

    def test_open_empties_the_artifacts(self, session, tmp_path):
        """An artifact's links point into this project's work dir; like a
        notice, a swap must not leave them under the next project."""
        session.apply(Artifact(paths=[Path("x.png")], label="PNG"))
        other_cfg, other_work = _project(tmp_path / "other")
        session.open(other_cfg, work_dir=other_work)
        assert session.artifacts == [] and SessionView(session).artifacts == ()

    def test_a_failed_open_keeps_the_live_projects_notices(self, session):
        """The reset sits at the commit point, after the Project is built:
        hoisting it to the top of ``open`` empties a live project's panel on
        an open that never replaces it."""
        from dstf.config import ConfigError

        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        art = Artifact(paths=[Path("x.png")], label="PNG")
        session.apply(art)
        config = session.project.config
        (config.work_dir / "status.json").write_text(
            json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []})
        )
        with pytest.raises(ConfigError, match="Nonesuch"):
            session.open(config.config_file, work_dir=config.work_dir)
        assert session.notices == ["stored 1"] and session.artifacts == [art]
        assert session.project is not None

    def test_after_reload_gets_the_rescored_comparisons(self, session):
        """A job that wrote a run finishes its artifacts with the verdicts
        (a rerun's dashboard): `after_reload` is called once the reload has
        rescored, with the project and the Session's comparisons."""
        seen: list[tuple] = []
        jobs = _started(session)
        session.apply(
            Job(
                run=lambda ctx: None,
                label="rerun",
                models=frozenset({A}),
                after_reload=lambda project, comps: seen.append((project, comps)),
            )
        )
        assert jobs[0].done.wait(5) and jobs[0].reloaded
        assert session.wait_background(5)  # the hook runs off the UI loop
        ((project, comps),) = seen
        assert project is session.project
        assert comps == session.comparisons and comps is not session.comparisons

    def test_a_raising_after_reload_does_not_fail_a_good_reload(self, session, caplog):
        def boom(project, comps):
            raise OSError("disk full")

        jobs = _started(session)
        session.apply(
            Job(
                run=lambda ctx: None,
                label="rerun",
                models=frozenset({A}),
                after_reload=boom,
            )
        )
        assert jobs[0].done.wait(5)
        assert jobs[0].error is None and jobs[0].reloaded
        assert session.wait_background(5)
        assert "after_reload" in caplog.text

    def test_no_after_reload_for_a_failed_job(self, session):
        seen: list[object] = []

        def fail(ctx):
            raise RuntimeError("the run broke")

        jobs = _started(session)
        session.apply(
            Job(
                run=fail,
                label="rerun",
                models=frozenset({A}),
                after_reload=lambda p, c: seen.append(p),
            )
        )
        assert jobs[0].done.wait(5) and jobs[0].error is not None
        assert session.wait_background(5)
        assert seen == []

    def test_open_drops_the_last_job(self, session):
        """The outcome line is project-scoped like a notice: a swap calls
        open, never close, so LibA's `finished:` would otherwise sit in the
        next project's panel."""
        jobs = _started(session)
        session.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        assert jobs[0].done.wait(5)
        assert session.last_job is jobs[0]
        config = session.project.config
        session.open(config.config_file, work_dir=config.work_dir)
        assert session.last_job is None

    def test_a_failed_open_keeps_the_live_projects_last_job(self, session):
        """The reset sits at the commit point with the notices': an open that
        never replaces the project must not blank its outcome line."""
        from dstf.config import ConfigError

        jobs = _started(session)
        session.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        assert jobs[0].done.wait(5)
        config = session.project.config
        (config.work_dir / "status.json").write_text(
            json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []})
        )
        with pytest.raises(ConfigError, match="Nonesuch"):
            session.open(config.config_file, work_dir=config.work_dir)
        assert session.last_job is jobs[0]

    def test_job_runs_on_a_thread_locks_its_models_and_reloads_on_completion(
        self, session
    ):
        gate = _Gate()
        events = []
        session.on("job", lambda e: events.append(e["state"]))
        work = session.project.config.work_dir

        def fix_a_then_wait(ctx):
            # What a rerun does: A's result on disk changes. Reloading must see it.
            t = [i / 4 for i in range(5)]
            (work / "test_0001" / "result.json").write_text(
                json.dumps({"success": True, "time": t, "variables": {"x": t}}),
                encoding="utf-8",
            )
            gate(ctx)

        session.apply(Job(run=fix_a_then_wait, label="rerun", models=frozenset({A})))
        assert session.running is not None and session.running.models == {A}
        assert events == ["started"]
        with pytest.raises(LockedError, match="rerun is running"):
            session.stage([LOOSEN])
        with pytest.raises(LockedError):
            session.rescore(A)
        with pytest.raises(LockedError):
            session.apply(StoreWrite(run=lambda s: None, models=frozenset({A, B})))
        session.stage(
            [PatchOp(B, "add", "/comparison", {"tolerance": 0.2})]
        )  # B is free
        with pytest.raises(JobRunningError):
            session.apply(Job(run=gate, label="second", models=frozenset({B})))
        job = session.running
        gate.release.set()
        assert job.done.wait(5)
        assert session.running is None and job.error is None
        assert gate.seen.models == {A} and gate.seen.project is session.project
        assert events == ["started", "finished"]
        assert (
            classify(session.comparisons[A]) == PASS
        )  # the reload read the rewritten result

    def test_job_failure_is_recorded_and_unlocks(self, session):
        jobs = _started(session)

        def boom(ctx):
            raise RuntimeError("driver died")

        session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        (job,) = jobs
        assert job.done.wait(5)
        assert session.running is None and "driver died" in str(job.error)
        session.stage([LOOSEN])  # unlocked again

    def test_job_without_reload_leaves_results_alone(self, session):
        jobs = _started(session)
        seen = []
        session.on("results", lambda e: seen.append(e["models"]))
        session.apply(
            Job(
                run=lambda ctx: None,
                label="export",
                models=frozenset(),
                reloads_results=False,
            )
        )
        (job,) = jobs
        assert job.done.wait(5)
        assert seen == [] and session.running is None

    def test_open_and_close_refuse_while_a_job_runs(self, session, tmp_path):
        gate = _Gate()
        jobs = _started(session)
        session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        with pytest.raises(JobRunningError):
            session.close()
        with pytest.raises(JobRunningError):
            session.apply(OpenProject(config_path=session.project.config.config_file))
        gate.release.set()
        assert jobs[0].done.wait(5)

    def test_completion_goes_through_post(self, tmp_path):
        cfg, work = _project(tmp_path)
        posted = []
        s = Session(post=posted.append)
        s.open(cfg, work_dir=work)
        # The open's own post first: the sibling index ready (D141).
        assert s.overlay_index_ready.wait(5.0)
        for _ in range(50):
            if posted:
                break
            threading.Event().wait(0.05)
        posted.pop(0)()
        s.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        deadline = threading.Event()
        for _ in range(50):
            if posted:
                break
            deadline.wait(0.1)
        assert (
            len(posted) == 1 and s.running is not None
        )  # not finished until the loop runs it
        posted[0]()
        assert s.running is None

    def test_artifact_is_recorded_and_nothing_reloads(self, session):
        seen = []
        session.on("results", lambda e: seen.append(e))
        art = Artifact(paths=[Path("x.png")], label="PNG")
        session.apply(art)
        assert session.artifacts == [art] and seen == []

    def test_open_project_swaps_the_project(self, session, tmp_path):
        other_cfg, other_work = _project(tmp_path / "other")
        old = session.project
        session.apply(OpenProject(config_path=other_cfg, work_dir=other_work))
        assert (
            session.project is not old
            and session.project.config.spec_path == other_cfg.parent / "test_spec.json"
        )

    def test_open_and_close_refuse_while_edits_are_pending(self, session, tmp_path):
        from dstf.authoring.session import PendingEditsError

        session.stage([LOOSEN])
        with pytest.raises(PendingEditsError, match="2 staged"):
            session.close()
        other_cfg, other_work = _project(tmp_path / "other")
        with pytest.raises(PendingEditsError):
            session.apply(OpenProject(config_path=other_cfg, work_dir=other_work))
        session.discard()
        session.close()
        assert session.project is None and session.artifacts == []

    def test_none_and_sequences(self, session):
        session.apply(None)
        session.apply([Patch(ops=[LOOSEN]), None])
        assert LOOSEN in session.spec.pending


class TestLockAndCompletion:
    """The lock has no side door; completion cannot wedge the Session."""

    def test_rescore_of_a_locked_model_is_refused_before_any_staging(self, session):
        """G is not materializable, so rescore's OWN guard is what refuses
        (the earlier test hit stage's guard through materialization)."""
        gate = _Gate()
        jobs = _started(session)
        session.apply(Job(run=gate, label="rerun", models=frozenset({G})))
        with pytest.raises(LockedError, match="rerun is running"):
            session.rescore(G)
        assert SessionView(session).is_locked(G) is True
        gate.release.set()
        assert jobs[0].done.wait(5)

    def test_a_running_job_records_when_it_started(self, session):
        """The job panel ignores a status.json older than this, so the
        previous run's snapshot is never shown as this job's progress."""
        gate = _Gate()
        jobs = _started(session)
        session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        assert abs(session.running.started_wall - time.time()) < 5.0
        gate.release.set()
        assert jobs[0].done.wait(5)

    def test_a_reload_during_a_job_keeps_the_locked_model_as_it_was(self, session):
        """What the run pipeline does at start: clear the model's directory.
        A StoreWrite on a free model reloads results; the locked model must
        keep its in-memory result and verdict until the job's own reload."""
        gate = _Gate()
        jobs = _started(session)
        work = session.project.config.work_dir
        before = session.comparisons[A]
        session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        (work / "test_0001" / "result.json").unlink()
        events = []
        session.on("loading", events.append)
        session.apply(StoreWrite(run=lambda store: None, models=frozenset({B})))
        # A is locked: to_read is B, C, D, G — four announcements, total 4.
        assert [e["total"] for e in events if e.get("phase") == "results"] == [4] * 4
        assert (
            session.comparisons[A] is before
            and classify(session.comparisons[A]) == FAIL
        )
        assert session.project.results[A].success
        gate.release.set()
        assert jobs[0].done.wait(5)
        assert (
            classify(session.comparisons[A]) == SIM_FAIL
        )  # now the job's reload sees the disk

    def test_a_post_that_raises_still_finishes_the_job(self, tmp_path):
        cfg, work = _project(tmp_path)

        def dead_loop(fn):
            raise RuntimeError("loop closed")

        s = Session(post=dead_loop)
        s.open(cfg, work_dir=work)
        jobs = _started(s)
        s.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        assert jobs[0].done.wait(5)
        assert s.running is None and "loop closed" in str(jobs[0].error)

    def test_a_listener_that_raises_still_marks_the_job_done(self, session):
        events = []
        session.on("job", lambda e: events.append(e["state"]))

        def boom(e):
            if e["state"] != "started":
                raise RuntimeError("element deleted")

        session.on("job", boom)
        jobs = _started(session)
        session.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        assert jobs[0].done.wait(5)
        assert session.running is None and events[-1] == "finished"

    def test_a_failed_job_emits_failed_and_skips_the_reload(self, session):
        events = []
        seen = []
        session.on("job", lambda e: events.append(e["state"]))
        session.on("results", lambda e: seen.append(e["models"]))
        jobs = _started(session)

        def boom(ctx):
            raise RuntimeError("driver died")

        session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        assert jobs[0].done.wait(5)
        assert events == ["started", "failed"] and seen == []

    def test_a_store_write_that_raises_leaves_the_session_unchanged(self, session):
        before = dict(session.comparisons)

        def boom(store):
            raise OSError("disk full")

        with pytest.raises(OSError):
            session.apply(StoreWrite(run=boom, models=frozenset({B})))
        assert session.comparisons == before and session.running is None
        assert session.spec.pending == []

    def test_a_patch_rescores_only_its_models(self, session):
        seen = []
        session.on("results", lambda e: seen.append(e["models"]))
        untouched = session.comparisons[B]
        session.apply(Patch(ops=[LOOSEN]))
        assert seen == [{A}] and session.comparisons[B] is untouched


class TestReadCache:
    # `compare_one` reads the reference too (comparator.py:569), on every
    # rescore, so the spy is on the overlay load: `_reads_for` is the only
    # caller of `load_overlays` in the Session.
    @staticmethod
    def _spy_overlays(monkeypatch):
        from dstf.reporting import overlay_loader as ol

        loads = []
        real = ol.load_overlays
        monkeypatch.setattr(
            ol,
            "load_overlays",
            lambda store, m, config=None: (
                loads.append(m) or real(store, m, config=config)
            ),
        )
        return loads

    def test_a_second_view_of_a_model_reads_nothing_from_disk(
        self, session, monkeypatch
    ):
        loads = self._spy_overlays(monkeypatch)
        session.view(A, "x")
        session.view(A, "x")
        assert loads == [A]

    def test_a_second_figure_decimates_only_the_leaves(self, session, monkeypatch):
        from dstf.figures import view as view_mod

        calls = []
        real = view_mod.lttb
        monkeypatch.setattr(
            view_mod, "lttb", lambda t, v, n: calls.append(len(t)) or real(t, v, n)
        )
        session.figure(A, "x")
        first = len(calls)
        session.figure(A, "x")
        # The base — actual, reference, three error series (FakeLib has no
        # overlays) — is decimated on the first call only; a second call
        # decimates the leaves' Series primitives and nothing else. No
        # bundled metric emits a Series (nrmse emits no geometry; tube is a
        # Band plus Markers; Bands go through decimate.py's own `lttb`,
        # which this spy does not see), so `leaf_series` is 0 here and the
        # guard is "5, then 0"; per-call leaf decimation is guarded by
        # `test_the_tools_figure_rings_every_marker_handle`.
        from dstf.comparison.geometry import Series

        leaf_series = sum(
            isinstance(p, Series)
            for leaf in session.view(A, "x").leaves
            for p in leaf.geometry
        )
        assert first == 5 + leaf_series
        assert len(calls) - first == leaf_series

    def test_a_results_reload_drops_the_cache(self, session, monkeypatch):
        loads = self._spy_overlays(monkeypatch)
        session.view(A, "x")
        session._reload_results()
        session.view(A, "x")
        assert loads == [A, A]

    def test_a_store_write_drops_the_cache(self, session, monkeypatch):
        loads = self._spy_overlays(monkeypatch)
        session.view(D, "x")
        session.apply(StoreWrite(run=lambda store: "", models=frozenset({D})))
        session.view(D, "x")
        assert loads == [D, D]

    def test_a_staged_edit_keeps_the_cache_and_changes_the_leaves(self, session):
        session.figure(A, "x")
        session.stage([LOOSEN])
        fig = session.figure(A, "x")
        leaf = next(tr for tr in fig["data"] if tr["meta"].get("kind") == "act")
        assert leaf  # the base traces are still drawn
        assert session.comparisons[A].passed is True  # the loosened verdict

    # A second Session over the same project, opened fresh, is the oracle:
    # what it draws after the same staged op is what the cached one must
    # draw. `_base_kinds` compares base traces only — the leaves are
    # decimated per call and are not what the cache holds.
    @staticmethod
    def _pair(cfg, work):
        s1, s2 = Session(), Session()
        s1.open(cfg, work_dir=work)
        s2.open(cfg, work_dir=work)
        return s1, s2

    def test_a_staged_edit_that_stops_scoring_a_variable_drops_its_base(self, tmp_path):
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        assert [v.name for v in cached.comparisons[A].variables] == ["x", "y"]
        cached.figure(A, "y")  # y scored: act + ref + errors cached
        op = PatchOp(A, "remove", "/metrics/children/1")
        cached.stage([op])
        fresh.stage([op])
        assert [v.name for v in cached.comparisons[A].variables] == ["x"]
        assert _base_kinds(cached.figure(A, "y")) == _base_kinds(fresh.figure(A, "y"))

    def test_a_staged_edit_that_starts_scoring_a_variable_rebuilds_its_base(
        self, tmp_path
    ):
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        remove = PatchOp(A, "remove", "/metrics/children/1")
        cached.stage([remove])
        fresh.stage([remove])
        cached.figure(A, "y")  # y unscored: an empty base cached
        add = PatchOp(
            A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "y"}
        )
        cached.stage([add])
        fresh.stage([add])
        assert [v.name for v in cached.comparisons[A].variables] == ["x", "y"]
        assert _base_kinds(cached.figure(A, "y")) == _base_kinds(fresh.figure(A, "y"))

    def test_a_baseline_free_leaf_on_another_variable_moves_an_unscored_one(
        self, tmp_path
    ):
        """B has no baseline: with nothing scored, x is drawn through the
        no-baseline path (act only). A tree scoring ONLY y (a baseline-free
        range) makes the scored list non-empty and x — still unscored —
        becomes the empty state. A key on "is x scored" does not move; the
        key on the whole scored list does."""
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        assert not cached.comparisons[B].evaluated
        cached.figure(B, "x")
        op = PatchOp(
            B,
            "add",
            "/metrics",
            {
                "combinator": "and",
                "children": [
                    {
                        "metric": "range",
                        "variable": "y",
                        "min_value": -10,
                        "max_value": 10,
                    }
                ],
            },
        )
        cached.stage([op])
        fresh.stage([op])
        assert cached.comparisons[B].evaluated
        assert _base_kinds(cached.figure(B, "x")) == _base_kinds(fresh.figure(B, "x"))

    def test_figure_of_an_unknown_model_is_a_key_error_that_caches_nothing(
        self, session
    ):
        with pytest.raises(KeyError):
            session.figure("FakeLib.Nope", "x")
        assert "FakeLib.Nope" not in session._reads

    def test_a_model_the_spec_drops_loses_its_cached_read(self, session):
        session.figure(D, "x")
        p = session.spec.path
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["tests"] = [e for e in doc["tests"] if e["model"] != D]
        p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        session.reload()
        assert D not in session._reads
        with pytest.raises(KeyError):
            session.figure(D, "x")

    def test_the_cached_figure_is_the_one_shot_figure_under_decimation(self, tmp_path):
        """The cache must draw exactly what one uncached ``variable_figure``
        of the view would, before and after a staged edit moves a handle —
        with a budget small enough that every trace is decimated, leaves
        whose geometry is decimated, and a companion overlay. Caching the
        leaves with the base, or letting the leaves-only view's zero
        "act"/"ref" counts win the merge, both fail here."""
        from dstf.figures.plotly import variable_figure

        cfg, work = _project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        csv = tmp_path / "companion.csv"
        csv.write_text(
            "time,x\n" + "".join(f"{i / 40},{0.9 * i / 40}\n" for i in range(41)),
            encoding="utf-8",
        )
        s.project.store.add_companion(A, "c1", csv)
        s._reload_results()
        s.project.config.max_embedded_samples = 3
        tree = {
            "combinator": "and",
            "children": [
                {
                    "metric": "tube",
                    "variable": "x",
                    "tube_width_mode": "band",
                    "tube_points": [
                        {"time": 0.5, "upper": 0.1, "lower": 0.2},
                        {"time": 0.9, "upper": 0.1, "lower": 0.1},
                    ],
                },
                {
                    "metric": "points",
                    "variable": "x",
                    "points": [
                        {"time": 0.25, "tolerance": 0.05},
                        {"time": 0.75, "value": 0.75, "tolerance": 0.05},
                    ],
                },
                {
                    "metric": "range",
                    "variable": "x",
                    "min_value": -0.5,
                    "max_value": 2.0,
                },
            ],
        }

        def one_shot() -> str:
            fig = variable_figure(s.view(A, "x"), 3, editable=True)
            return json.dumps(fig, sort_keys=True, default=str)

        s.stage([PatchOp(A, "add", "/metrics", tree)])
        first = json.dumps(s.figure(A, "x"), sort_keys=True, default=str)
        assert "c1" in first  # the companion overlay is drawn
        assert first == one_shot()
        s.stage([PatchOp(A, "replace", "/metrics/children/0/tube_points/0/upper", 0.3)])
        second = json.dumps(s.figure(A, "x"), sort_keys=True, default=str)
        assert second == one_shot()
        assert second != first

    def test_close_drops_every_cached_read(self, session):
        session.view(A, "x")
        session.close()
        assert session._reads == {}


class TestSessionView:
    def test_exposes_readers_and_no_writer(self, session):
        view = SessionView(session)
        public = {n for n in dir(view) if not n.startswith("_")}
        assert public == {
            "project",
            "spec_path",
            "pending",
            "selection",
            "running",
            "last_job",
            "comparisons",
            "artifacts",
            "notices",
            "rescore",
            "view",
            "figure",
            "plot_figure",
            "plots",
            "variable_names",
            # `labels` is a reader like the rest: a capability's caption
            # ("Add a criterion for pipe.T[1]") reads what the page's titles
            # read, and nothing is keyed by it (Task 4).
            "labels",
            # both readings: the add form's rows show the plain label with
            # the muted name beside it, and say the name once
            "variable_texts",
            "tree_view",
            "tree",
            # the tree or why there is none, in the one text every surface
            # says it with
            "tree_or_reason",
            "is_locked",
        }
        assert not public & {
            "stage",
            "save",
            "discard",
            "apply",
            "open",
            "close",
            "reload",
            "select",
        }

    def test_the_project_view_reaches_no_store_writer(self, session):
        """§4.6: enforcement is structural. The view's project is a frozen
        projection; the store's writers exist only behind StoreWrite.run and
        JobContext, which the Session hands out."""
        import dataclasses

        pv = SessionView(session).project
        assert not hasattr(pv, "store") and not hasattr(pv, "backend")
        assert isinstance(pv.tests, tuple) and pv.tests[0].model_id == A
        with pytest.raises(dataclasses.FrozenInstanceError):
            pv.tests = ()
        with pytest.raises(TypeError):
            pv.results[A] = None
        assert pv.reference(A) is not None and pv.reference(B) is None
        assert pv.baseline_names(A) == ("primary",)
        assert pv.baseline_names(B) == ()
        t = [i / 4 for i in range(5)]
        session.project.store.add_soft_check(
            A, "om_cross", t, [{"index": 1, "name": "x", "values": t}]
        )
        assert pv.baseline_names(A) == ("primary", "om_cross")
        assert (
            pv.config.backend_name == "Python"
            and pv.dataset_kind is session.project.dataset_kind
        )

    def test_readers_reflect_the_session(self, session):
        view = SessionView(session)
        session.stage([LOOSEN])
        assert view.pending == tuple(session.spec.pending)
        assert view.comparisons[A] is session.comparisons[A]
        assert view.is_locked(A) is False
        with pytest.raises(TypeError):
            view.comparisons[A] = None  # a read-only mapping
        assert view.notices == ()
        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        assert view.notices == ("stored 1",)


def _plots_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with an UNTRACKED variable `z` in A's result file."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    flat = [7.0 for _ in payload["time"]]
    # `a0` sits BEFORE x in the file: file order is not tracked-first order.
    payload["variables"] = {"a0": flat, **payload["variables"], "z": flat}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg, work


#: What `_drawer_project` adds to A's result after x and y: a name of every
#: group the Variables drawer draws — top level, two families (one behind a
#: two-dimensional index), a diagnostic.
DRAWER_EXTRA = [
    "a0",
    "z",
    "pipe.T[1]",
    "pipe.T[2]",
    "pipe.T[3]",
    "port[2, 1].C[1]",
    "port[2, 1].C[2]",
    "port[2, 1].C[3]",
    "CPUtime",
]


def _drawer_project(tmp_path: Path, extra: int = 0) -> tuple[Path, Path]:
    """`_two_var_project` with A's result carrying, after x and y, the
    untracked `DRAWER_EXTRA`, each a distinct ramp so a plot shows them
    apart, and ``extra`` more untracked `v0`, `v1`, ... for the drawer's cap.
    A's tree is the implicit one: x and y scored, one nrmse each."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    t = payload["time"]
    names = DRAWER_EXTRA + [f"v{i}" for i in range(extra)]
    for k, name in enumerate(names, start=3):
        payload["variables"][name] = [k * v for v in t]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg, work


#: Two tracked names as long as a real library's (TRANSFORM's), which
#: FakeLib's `x` and `y` are not: a card's header row and a drawer row
#: that fitted every short name overflowed on these at 900 px.
LONG = ("heater.flowModel.states[1].T", "condenser.heatTransfer.Q_flow_total")
#: An UNTRACKED name of the same length in `_long_names_project`'s result:
#: what a watch plot is made of.
LONG_WATCH = "evaporator.flowModel.states[3].h_flow"


def _long_names_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_project` with A tracking the two `LONG` names, both with a
    baseline (one nrmse each; the first fails, so its card has a pill and
    an error select), drawn as two plots, the first with the second as its
    context; the result also carries the untracked `LONG_WATCH`."""
    cfg, work = _project(tmp_path)
    t = [i / 4 for i in range(5)]
    first, second = LONG
    (work / "test_0001" / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "time": t,
                "variables": {
                    first: [v * 1.25 for v in t],
                    second: [2 * v for v in t],
                    LONG_WATCH: [3 * v for v in t],
                },
            }
        ),
        encoding="utf-8",
    )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = [first, second]
            entry["plots"] = [{"var": first, "context": [second]}]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    tt = np.asarray(t)
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(A, variable_patterns=[first, second]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name=first),
                VariableResult(index=2, time=tt, values=2 * tt, name=second),
            ],
        ),
    )
    return cfg, work


#: A context series on x's plot: z, an untracked variable of A's result.
LAYOUT = [{"var": "x", "context": ["z"]}]


class TestPlots:
    def _open(self, tmp_path) -> Session:
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        return s

    def test_tracked_is_what_was_scored_then_the_rest_of_the_result(self, tmp_path):
        """The page's default plots: the scored names in their order, then
        the rest of the result's — never the file's, where `a0` sits before
        `x`. The result's names are the union's second half because a test
        keeps its variables while an edit is staged: a tree scoring x alone
        must not take y's card, its `+` and every way back to it."""
        s = self._open(tmp_path)
        assert s.tracked(A) == ["x", "y"]
        assert s.tracked(B) == ["x", "y"]  # nothing scored: the result's names
        assert s.tracked(C) == []
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
        assert [vc.name for vc in s.comparisons[A].variables] == ["x"]
        assert s.tracked(A) == ["x", "y"]
        # the plots come from the tree: y's last criterion went, so did its plot
        assert [p.series for p in s.plots(A)] == [("x",)]

    def test_labels_read_the_results_expressions(self, tmp_path):
        """What the page READS for each variable: the expression the model
        wrote, else the name. A name with no entry is its own label, and
        nothing is keyed by any of it."""
        cfg, work = _unit_tests_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.labels(A) == {UT1: UT1, UT2: UT2}  # no expression: the names
        with_expressions(s, A, {UT1: UT1_EXPR})
        assert s.labels(A) == {UT1: UT1_EXPR, UT2: UT2}
        assert s.labels(C) == {}  # no result

    def test_labels_are_the_text_the_page_reads_not_the_plain_label(self, tmp_path):
        """`labels` is the TEXT: a capability's caption, a criteria row and
        a pill carry no muted name, so the one that is ambiguous says its
        own. Only a title beside a muted name reads `variable_texts`'
        plain `label` — and `test_test_page` is not the only thing that
        may pin this."""
        cfg, work = _unit_tests_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        with_expressions(s, A, {UT1: "a.Q", UT2: "a.Q"})
        assert s.labels(A) == {UT1: f"a.Q ({UT1})", UT2: f"a.Q ({UT2})"}
        scope = s.variable_texts(A)
        assert [scope[n].label for n in (UT1, UT2)] == ["a.Q", "a.Q"]
        assert [scope[n].text for n in (UT1, UT2)] == [
            f"a.Q ({UT1})",
            f"a.Q ({UT2})",
        ]

    def test_the_label_scope_holds_a_plot_only_series(self, tmp_path):
        """The scope is every name the page can SHOW, plot-only ones
        included: the result-file variable `z` is what makes a tracked
        variable whose EXPRESSION is `z` read its name beside it. Read
        from the result's variables alone, both said `z`."""
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        with_expressions(s, A, {"x": "z"})
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.labels(A)["x"] == "z (x)"
        assert s.labels(A)["z"] == "z"  # its label IS its name: nothing ambiguous

    def test_a_labelled_variable_with_no_baseline_still_reads_the_label(self, tmp_path):
        """Nothing is scored without a reference, so the variable's view is
        built from the NO-BASELINE trajectory instead. That is the only
        place a brand-new test names its variables, and reading it as the
        bare `unitTests.x[1]` told the reader nothing."""
        cfg, work = _two_var_project(tmp_path)
        t = [i / 4 for i in range(5)]
        (work / "test_0002" / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {UT1: t, "y": [2 * v for v in t]},
                }
            ),
            encoding="utf-8",
        )
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == B:
                entry["variables"] = [UT1, "y"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        with_expressions(s, B, {UT1: AWKWARD_EXPRESSION})
        assert s.project.store.get_reference(B) is None  # the regime
        assert s.comparisons[B].variables == []
        view = s.view(B, UT1)
        assert (view.name, view.label) == (UT1, AWKWARD_EXPRESSION)
        assert s.view(B, "y").label == "y"  # no expression: its name
        actual = next(
            tr for tr in s.figure(B, UT1)["data"] if tr["meta"]["id"] == "actual"
        )
        assert actual["meta"]["label"] == AWKWARD_EXPRESSION

    def test_a_labelled_variables_figure_reads_the_label(self, tmp_path):
        """The figure the tool draws is the report's, so the label reaches
        it the same way: through the trajectory the view is built from."""
        cfg, work = _unit_tests_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        with_expressions(s, A, {UT1: UT1_EXPR})
        assert s.view(A, UT1).label == UT1_EXPR
        assert s.view(A, UT2).label == UT2  # no expression: its name
        figure = s.figure(A, UT1)
        actual = next(tr for tr in figure["data"] if tr["meta"]["id"] == "actual")
        assert actual["name"] == "Actual"  # the picker's key, unchanged
        assert actual["meta"]["label"] == UT1_EXPR

    def test_a_failed_simulations_partial_result_is_not_tracked(self, tmp_path):
        """A backend that stopped mid-run leaves what it had in the file.
        Listing those variables gave the page a card each for a run that
        produced nothing the test asked for (the final review); the page
        says "no plots: the run failed" instead."""
        s = self._open(tmp_path)
        assert s.tracked(B) == ["x", "y"]
        tt = np.asarray([i / 4 for i in range(5)])
        # The Python driver's reader drops the variables of a failed run;
        # a reader that keeps what the run wrote — a .mat file truncated
        # mid-simulation — hands this in.
        s.project.results[B] = TestResult(
            model_id=B,
            success=False,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name="x"),
                VariableResult(index=2, time=tt, values=2 * tt, name="y"),
            ],
        )
        assert s.tracked(B) == []
        assert s.plots(B) == ()

    def test_variable_names_lists_the_file_tracked_first(self, tmp_path):
        s = self._open(tmp_path)
        assert s.variable_names(A) == ["x", "y", "a0", "z"]

    def test_variable_names_of_a_test_with_no_result_is_empty(self, tmp_path):
        s = self._open(tmp_path)
        assert s.variable_names(C) == []

    def test_variable_names_of_an_unknown_model_is_a_key_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(KeyError):
            s.variable_names("FakeLib.Nope")

    def test_the_default_layout_is_one_plot_per_scored_variable(self, tmp_path):
        s = self._open(tmp_path)
        assert [(p.index, p.series, p.missing) for p in s.plots(A)] == [
            (0, ("x",), ()),
            (1, ("y",), ()),
        ]

    def test_plots_follow_the_tree_on_the_page_project(self, tmp_path):
        """A's tree is `and[tube·x, range·x, range·y]`: plots [x, y], then
        the key's watch plots in their order."""
        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        assert [(p.subject, p.watch) for p in s.plots(A)] == [
            (("x",), False),
            (("y",), False),
            *(((w,), True) for w in PAGE_WATCH),
        ]
        assert all(p.missing == () for p in s.plots(A))

    def test_nothing_compared_is_one_plot_per_tracked_name(self, tmp_path):
        s = self._open(tmp_path)
        assert s.comparisons[B].variables == []
        assert [(p.subject, p.watch) for p in s.plots(B)] == [
            (("x",), False),
            (("y",), False),
        ]

    def test_unit_tests_variables_never_fold_whatever_their_expressions(self, tmp_path):
        """O3: `unitTests.x[i]` is its own test quantity — one plot each, in
        tree order, even where the expressions read as one array."""
        cfg, work = _unit_tests_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "range", "variable": UT2, "min_value": -9.0},
                {"metric": "range", "variable": UT1, "min_value": -9.0},
            ],
        }
        s.stage([PatchOp(A, "add", "/metrics", tree)])
        with_expressions(s, A, {UT1: "pipe.T[1]", UT2: "pipe.T[2]"})
        assert [p.subject for p in s.plots(A)] == [(UT2,), (UT1,)]

    def test_an_array_tracked_by_its_names_folds_on_any_scale(self, tmp_path):
        """O3: any other array folds by its result-file names, with no
        scale check — here nothing is scored (B has no baseline), and the
        tracked array is still one plot."""
        cfg, work = _two_var_project(tmp_path)
        t = [i / 4 for i in range(5)]
        (work / "test_0002" / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {"p[1]": t, "p[2]": [1e5 * v for v in t]},
                }
            ),
            encoding="utf-8",
        )
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == B:
                entry["variables"] = ["p[1]", "p[2]"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        assert [p.subject for p in s.plots(B)] == [("p[1]", "p[2]")]

    def test_a_no_baseline_test_with_a_tree_draws_the_trees_plots(self, tmp_path):
        """A NO_REF test's `metrics` tree names what will be scored: its
        plots are the tree's, in its order — not one per tracked name."""
        s = self._open(tmp_path)
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "y"},
                {"metric": "nrmse", "variable": "x"},
            ],
        }
        s.stage([PatchOp(B, "add", "/metrics", tree)])
        assert [p.subject for p in s.plots(B)] == [("y",), ("x",)]

    def test_context_and_a_watch_plot_are_staged_extras(self, tmp_path):
        from dstf.authoring.layout import add_context, layout_op, new_watch

        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        extras = add_context(s.layout(A), s.plots(A)[0], ["y"])
        extras = new_watch(extras, "a.untracked.name")
        s.stage([layout_op(A, s.spec.entry(A), extras)])
        assert [(p.subject, p.context, p.watch) for p in s.plots(A)] == [
            (("x",), ("y",), False),
            (("y",), (), False),
            *(((w,), (), True) for w in PAGE_WATCH),
            (("a.untracked.name",), (), True),
        ]
        assert s.plots(A)[-1].missing == ("a.untracked.name",)
        assert s.layout(A) == (
            *(PlotExtra(w) for w in PAGE_WATCH),
            PlotExtra("x", ("y",)),
            PlotExtra("a.untracked.name"),
        )
        assert [op.path for op in s.spec.pending] == ["/plots"]

    def test_removing_a_variables_last_criterion_removes_its_plot(self, tmp_path):
        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "remove", "/metrics/children/2")])
        assert [p.subject for p in s.plots(A)] == [("x",), *((w,) for w in PAGE_WATCH)]

    def test_with_an_extra_the_plot_stays_as_a_watch_plot(self, tmp_path):
        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "replace", "/plots", [{"var": "y", "context": ["x"]}])])
        s.stage([PatchOp(A, "remove", "/metrics/children/2")])
        assert [(p.subject, p.context, p.watch) for p in s.plots(A)] == [
            (("x",), (), False),
            (("y",), ("x",), True),
        ]

    def test_painting_the_plots_never_loads_the_baseline(self, tmp_path, monkeypatch):
        """The plots walk the tree SPEC, never the materialized tree: that
        reads the baseline, and the page paints its cards before it."""
        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        monkeypatch.setattr(
            s, "tree", lambda *a, **k: pytest.fail("plots materialized the tree")
        )
        assert len(s.plots(A)) == 7

    def test_view_of_an_untracked_variable_reads_the_result_file(self, tmp_path):
        s = self._open(tmp_path)
        view = s.view(A, "z")
        assert view.act_values.tolist() == [7.0] * 5
        assert view.ref_time is None and view.leaves == ()

    def test_view_of_a_name_the_file_lacks_is_the_empty_state(self, tmp_path):
        s = self._open(tmp_path)
        view = s.view(A, "nowhere")
        assert view.act_time is None and view.leaves == ()

    def test_a_multi_series_plot_is_drawn_over_the_cached_bases(
        self, tmp_path, monkeypatch
    ):
        session = self._open(tmp_path)
        session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        first = session.plot_figure(A, 0)
        import dstf.reporting.context_builder as cb

        calls = []
        real = cb.build_trajectories
        monkeypatch.setattr(
            cb, "build_trajectories", lambda *a, **k: calls.append(1) or real(*a, **k)
        )
        again = session.plot_figure(A, 0)
        assert again == first
        assert calls == []  # the second draw assembles nothing

    def test_a_second_multi_series_figure_decimates_only_the_leaves(
        self, tmp_path, monkeypatch
    ):
        """`TestReadCache.test_a_second_figure_decimates_only_the_leaves`,
        over a plot: the base of EVERY series is decimated on the first draw
        only, and the multi branch draws WITHOUT a budget — or `plot_figure`
        would decimate each cached base a second time."""
        from dstf.comparison.geometry import Series
        from dstf.figures import view as view_mod

        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        s.plot_figure(A, 0)
        # As in the single-series test, the bundled metrics emit no Series
        # (A's materialized leaf is an nrmse, z has no leaves), so this is 0
        # here; it is the per-call leaf decimation the assertion allows.
        leaf_series = sum(
            isinstance(p, Series)
            for name in ("x", "z")
            for leaf in s.view(A, name).leaves
            for p in leaf.geometry
        )
        calls = []
        real = view_mod.lttb
        monkeypatch.setattr(
            view_mod, "lttb", lambda t, v, n: calls.append(len(t)) or real(t, v, n)
        )
        s.plot_figure(A, 0)
        assert len(calls) == leaf_series

    def test_a_multi_series_plot_takes_editing(self, tmp_path):
        import copy

        from test_handles import TREE

        session = self._open(tmp_path)
        session.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(TREE))])
        every = session.plot_figure(A, 0)
        assert [s for s in every["layout"]["shapes"] if s.get("type") == "circle"]
        fig = session.plot_figure(A, 0, editing=())
        assert not [s for s in fig["layout"]["shapes"] if s.get("type") == "circle"]
        # `editing` moves the rings and NOTHING else: same traces, and the
        # same config (react, never newPlot) — as on the single figure.
        assert fig["config"] == every["config"] and fig["data"] == every["data"]

    def test_editing_survives_every_forward_to_the_figure(self, tmp_path):
        """`editing` is handed on through three hops that could silently drop
        it: `SessionView.figure`, `SessionView.plot_figure`, and the
        single-series branch of `Session.plot_figure` (which IS `figure`)."""
        import copy

        from dstf.authoring.session import Selection, SessionView
        from test_handles import TREE

        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(TREE))])
        view = SessionView(s, Selection())

        def rings(fig):
            return [sh for sh in fig["layout"]["shapes"] if sh.get("type") == "circle"]

        assert s.plots(A)[0].series == ("x",)  # the default layout: one series
        assert rings(view.figure(A, "x"))
        assert rings(view.plot_figure(A, 0))
        assert rings(s.plot_figure(A, 0))
        assert not rings(view.figure(A, "x", editing=()))
        assert not rings(view.plot_figure(A, 0, editing=()))
        assert not rings(s.plot_figure(A, 0, editing=()))

        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.plots(A)[0].series == ("x", "z")  # and the branch with context
        assert rings(view.plot_figure(A, 0))
        assert not rings(view.plot_figure(A, 0, editing=()))

    def test_a_staged_layout_is_what_plots_answers(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert [(p.series, p.missing) for p in s.plots(A)] == [
            (("x", "z"), ()),
            (("y",), ()),
        ]
        s.discard()
        assert len(s.plots(A)) == 2

    def test_layout_is_the_effective_plots_key(self, tmp_path):
        """The extras a gesture edits are what the spec SAYS: no key is no
        extras, and a name the result lacks stays in them."""
        s = self._open(tmp_path)
        assert s.layout(A) == () and s.layout(B) == ()
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.layout(A) == parse_plots(LAYOUT)
        s.discard()
        raw = [{"var": "x", "context": ["gone"]}]
        s.stage([PatchOp(A, "add", "/plots", raw)])
        assert s.layout(A) == parse_plots(raw)

    def test_a_series_the_result_lacks_is_named_missing(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", [{"var": "x", "context": ["gone"]}])])
        plot = s.plots(A)[0]
        assert plot.series == ("x", "gone") and plot.missing == ("gone",)

    def test_staging_an_invalid_layout_changes_nothing(self, tmp_path):
        from dstf.config import ConfigError
        from dstf.discovery.patch_apply import PatchError

        s = self._open(tmp_path)
        with pytest.raises((PatchError, ConfigError), match=r"plots\[0\]: unknown key"):
            s.stage([PatchOp(A, "add", "/plots", [{"series": []}])])
        assert s.spec.pending == [] or tuple(s.spec.pending) == ()
        assert len(s.plots(A)) == 2

    def test_a_layout_edit_does_not_materialize_a_metrics_tree(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert [op.path for op in s.spec.pending] == ["/plots"]

    def test_saving_writes_the_key_and_a_reopen_reads_it(self, tmp_path):
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        s.save()
        entry = next(
            e
            for e in json.loads((cfg.parent / "test_spec.json").read_text())["tests"]
            if e["model"] == A
        )
        assert entry["plots"] == LAYOUT
        again = Session()
        again.open(cfg, work_dir=work)
        assert [p.series for p in again.plots(A)] == [("x", "z"), ("y",)]

    def test_plot_figure_of_one_series_is_the_variables_figure(self, tmp_path):
        s = self._open(tmp_path)
        assert s.plot_figure(A, 0) == s.figure(A, "x")

    def test_plot_figure_draws_context_beside_its_subject(self, tmp_path):
        """x alone is `figure`'s drawing, error traces and all; its context
        z is one more trace on the same axis."""
        s = self._open(tmp_path)
        alone = s.plot_figure(A, 0)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        fig = s.plot_figure(A, 0)
        ids = [
            tr["meta"]["id"]
            for tr in fig["data"]
            if tr["meta"]["kind"] in ("act", "context")
        ]
        assert ids == ["actual", "context/z"]
        assert fig["data"][: len(alone["data"])] == alone["data"]
        assert fig["config"]["edits"] == {"shapePosition": True}

    def test_a_context_variable_scored_elsewhere_signs_no_leaf_here(self, tmp_path):
        """y is scored on its own plot; as context on x's it is a line."""
        cfg, work = _page_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "replace", "/plots", [{"var": "x", "context": ["y"]}])])
        fig = s.plot_figure(A, 0)
        leaves = {tr["meta"]["leaf"] for tr in fig["data"] if "leaf" in tr["meta"]}
        assert leaves and "/metrics/children/2" not in leaves

    def test_plot_figure_keeps_the_leaves_of_a_tracked_series(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        single = s.figure(A, "x")
        multi = s.plot_figure(A, 0)
        leafy = lambda fig: sorted(  # noqa: E731
            tr["meta"].get("id") or tr["meta"]["family"]
            for tr in fig["data"]
            if tr["meta"]["kind"] not in ("act", "ref", "error", "overlay", "context")
        )
        assert leafy(multi) == leafy(single)

    def test_plot_figure_out_of_range_is_an_index_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(IndexError):
            s.plot_figure(A, 5)

    def test_the_file_read_is_cached_and_dropped_with_the_results(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        calls = []
        real = PythonBackend.result_series.__func__

        def counting(cls, test_dir, names):
            calls.append(tuple(names))
            return real(cls, test_dir, names)

        monkeypatch.setattr(PythonBackend, "result_series", classmethod(counting))
        s.view(A, "z")
        s.view(A, "z")
        assert calls == [("z",)]
        s._reload_results()  # what TestReadCache's reload test calls
        s.view(A, "z")
        assert calls == [("z",), ("z",)]

    def test_a_backend_that_cannot_list_draws_what_the_result_carries(
        self, tmp_path, monkeypatch
    ):
        """No listing: what the result in memory carries is all the plots
        can draw (`plots_of_test`, the report's rule too) — the tracked x is
        there, the file-only z is named missing rather than drawn blank."""
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        monkeypatch.setattr(
            PythonBackend, "result_names", classmethod(lambda cls, d: None)
        )
        s._reload_results()
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.plots(A)[0].missing == ("z",)

    def test_a_locked_model_whose_names_were_never_read_marks_nothing_missing(
        self, tmp_path
    ):
        """While a job holds the model, the file is not listed — and a
        listing never read is not a listing of nothing: the file-only z is
        not named missing (it is, once the names are read)."""
        from dstf.authoring.session import RunningJob

        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        s._reads.pop(A, None)
        s.running = RunningJob(label="rerun", models=frozenset({A}))
        assert [p.missing for p in s.plots(A)] == [(), ()]
        assert not s._reads_for(A).names_read
        s.running = None
        assert [p.missing for p in s.plots(A)] == [(), ()]
        assert s._reads_for(A).names_read

    def test_the_session_view_mirrors_the_three_reads(self, tmp_path):
        from dstf.authoring.session import Selection, SessionView

        s = self._open(tmp_path)
        v = SessionView(s, Selection())
        assert v.variable_names(A) == s.variable_names(A)
        assert v.plots(A) == s.plots(A)
        assert v.plot_figure(A, 0) == s.plot_figure(A, 0)


def _ordered_project(tmp_path: Path, names: list[str]) -> tuple[Path, Path]:
    """`_two_var_project` with A and B tracking ``names``, listed in that
    order in the spec and in both results."""
    cfg, work = _two_var_project(tmp_path, ref_models=())
    t = [i / 4 for i in range(5)]
    for key in ("test_0001", "test_0002"):
        (work / key / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {
                        n: [(k + 1) * v for v in t] for k, n in enumerate(names)
                    },
                }
            ),
            encoding="utf-8",
        )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] in (A, B):
            entry["variables"] = list(names)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


class TestPlotsInTheSpecsOrder:
    """With no tree the plots are the tracked names, in the order the spec
    wrote them — never sorted (`resolve_variable_patterns`)."""

    def test_unit_tests_elements_come_in_index_order(self, tmp_path):
        names = [f"unitTests.x[{i}]" for i in range(1, 13)]
        cfg, work = _ordered_project(tmp_path, names)
        s = Session()
        s.open(cfg, work_dir=work)
        assert [p.subject for p in s.plots(B)] == [(n,) for n in names]

    def test_names_come_as_the_spec_wrote_them(self, tmp_path):
        names = ["heater.T", "condenser.Q"]
        cfg, work = _ordered_project(tmp_path, names)
        s = Session()
        s.open(cfg, work_dir=work)
        assert [p.subject for p in s.plots(B)] == [(n,) for n in names]


class TestPlotsUnderALock:
    """The file reads behave as `view` and `figure` always have for a model a
    job holds: from memory, never from the directory the job is rewriting."""

    def _locked(self, tmp_path):
        from dstf.authoring.session import RunningJob

        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.running = RunningJob(label="rerun", models=frozenset({A}))
        return s, work

    def test_a_locked_models_result_file_is_not_opened_and_nothing_is_cached(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.python.backend import PythonBackend

        s, _ = self._locked(tmp_path)

        def boom(cls, *a):
            raise AssertionError("a locked model's work dir was read")

        monkeypatch.setattr(PythonBackend, "result_names", classmethod(boom))
        monkeypatch.setattr(PythonBackend, "result_series", classmethod(boom))
        assert s.variable_names(A) == ["x", "y"]
        assert s.view(A, "z").act_time is None
        assert s._reads[A].names is None and s._reads[A].series == {}
        s.running = None
        monkeypatch.undo()
        assert s.variable_names(A) == ["x", "y", "a0", "z"]
        assert s.view(A, "z").act_values.tolist() == [7.0] * 5

    def test_what_was_read_before_the_lock_is_still_served(self, tmp_path):
        from dstf.authoring.session import RunningJob

        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.view(A, "z").act_values.tolist() == [7.0] * 5
        assert s.variable_names(A) == ["x", "y", "a0", "z"]
        s.running = RunningJob(label="rerun", models=frozenset({A}))
        (work / "test_0001" / "result.json").write_text("{half", encoding="utf-8")
        assert s.view(A, "z").act_values.tolist() == [7.0] * 5
        assert s.variable_names(A) == ["x", "y", "a0", "z"]

    def _locked_with_layout(self, tmp_path):
        """A staged multi-series layout BEFORE the lock (a staged edit
        rescores, which a locked model refuses), then the lock."""
        from dstf.authoring.session import RunningJob

        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        s.running = RunningJob(label="rerun", models=frozenset({A}))
        return s

    def test_a_multi_series_base_read_under_the_lock_is_not_cached(self, tmp_path):
        """`_file_series` answers None under a lock and caches nothing — "the
        job's reload answers it". The base built from that None must not be
        cached either: a job that ends without dropping `_reads` (a successful
        `Job(reloads_results=False)`) would leave z's EMPTY base behind."""
        s = self._locked_with_layout(tmp_path)
        s.plot_figure(A, 0)
        s.running = None  # the job ended without dropping `_reads`
        fig = s.plot_figure(A, 0)
        drawn = [
            (tr["meta"]["id"], len(tr["y"]))
            for tr in fig["data"]
            if tr["meta"]["kind"] in ("act", "context")
        ]
        assert drawn == [("actual", 5), ("context/z", 5)]

    def test_a_single_series_base_read_under_the_lock_is_not_cached(self, tmp_path):
        s = self._locked_with_layout(tmp_path)
        s.figure(A, "z")
        s.running = None
        fig = s.figure(A, "z")
        assert [
            tr["meta"]["id"] for tr in fig["data"] if tr["meta"]["kind"] == "act"
        ] == ["actual"]

    def test_a_failed_job_drops_what_was_read_of_its_models(self, tmp_path):
        """No reload follows a failed job, and its half-run rewrote the files."""
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.view(A, "z")
        s.view(D, "x")
        gate = _Gate()
        jobs = _started(s)

        def run(ctx):
            gate(ctx)
            raise RuntimeError("the run died")

        s.apply(Job(run=run, label="rerun", models=frozenset({A})))
        gate.release.set()
        assert jobs[0].done.wait(5)
        assert jobs[0].error is not None
        assert A not in s._reads and D in s._reads


class TestPlotsMisses:
    def _open(self, tmp_path) -> Session:
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        return s

    def test_a_miss_is_read_once(self, tmp_path, monkeypatch):
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        calls = []
        real = PythonBackend.result_series.__func__

        def counting(cls, test_dir, names):
            calls.append(tuple(names))
            return real(cls, test_dir, names)

        monkeypatch.setattr(PythonBackend, "result_series", classmethod(counting))
        assert s.view(A, "nowhere").act_time is None
        assert s.view(A, "nowhere").act_time is None
        assert calls == [("nowhere",)]
        s._reload_results()
        s.view(A, "nowhere")
        assert calls == [("nowhere",), ("nowhere",)]

    def test_an_empty_but_listable_file_still_names_a_series_missing(
        self, tmp_path, monkeypatch
    ):
        """`[]` is "the file lists nothing"; only `None` is "cannot list"."""
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        monkeypatch.setattr(PythonBackend, "result_names", classmethod(lambda c, d: []))
        s._reload_results()
        s.stage([PatchOp(A, "add", "/plots", [{"var": "x", "context": ["gone"]}])])
        plot = s.plots(A)[0]
        assert plot.series == ("x", "gone") and plot.missing == ("gone",)

    def test_a_negative_plot_index_is_an_index_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(IndexError):
            s.plot_figure(A, -1)


def test_plot_figure_passes_the_pages_visibility(tmp_path):
    """Legend b: the page's Visibility reaches both figure reads, on the
    Session and through SessionView, with no new public name."""
    from dstf.figures.plotly import Visibility

    cfg, work = _page_project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    range_x = "/metrics/children/1"
    hide = Visibility(hidden=frozenset({range_x}))
    for reader in (s, SessionView(s)):
        # Plot 0 draws x alone (the one-series branch), then with y beside it.
        for extras in ([], [{"var": "x", "context": ["y"]}]):
            s.discard()
            if extras:
                s.stage([PatchOp(A, "replace", "/plots", extras)])
            fig = reader.plot_figure(A, 0, editing=(), visibility=hide)
            mine = [
                t for t in fig["data"] if (t.get("meta") or {}).get("leaf") == range_x
            ]
            assert mine and all(t["visible"] is False for t in mine), extras
        s.discard()
        fig = reader.figure(A, "x", visibility=Visibility(error="error/signed"))
        shown = [
            t["meta"]["id"]
            for t in fig["data"]
            if t["meta"].get("kind") == "error" and t["visible"] is True
        ]
        assert shown == ["error/signed"]
        assert fig["layout"]["yaxis3"]["visible"] is True
        assert fig["layout"]["yaxis4"]["visible"] is False


L1 = [{"var": "x", "context": ["z"]}]
L2 = [{"var": "x", "context": ["z", "a0"]}]


def _session_with_plots(tmp_path: Path, plots: list) -> Session:
    """`_project` with A's entry holding ``plots`` on disk before `open`."""
    cfg, work = _project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["plots"] = plots
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s


class TestStageCollapses:
    """Every layout gesture stages the whole `/plots` list, so a run of
    gestures on one path must read as one pending op, never five."""

    def test_a_replace_after_an_add_of_one_path_is_one_add(self, session):
        session.stage([PatchOp(A, "add", "/plots", L1)])
        session.stage([PatchOp(A, "replace", "/plots", L2)])
        assert session.spec.pending == [PatchOp(A, "add", "/plots", L2)]
        assert session.spec.entry(A)["plots"] == L2

    def test_a_remove_after_a_replace_is_one_remove(self, tmp_path):
        s = _session_with_plots(tmp_path, L1)
        s.stage([PatchOp(A, "replace", "/plots", L2)])
        s.stage([PatchOp(A, "remove", "/plots")])
        assert s.spec.pending == [PatchOp(A, "remove", "/plots")]
        assert "plots" not in s.spec.entry(A)

    def test_a_remove_after_an_add_is_not_collapsed(self, tmp_path):
        """The add acts as a replace of the L1 on disk: dropping both ops
        would leave L1 in place instead of removing it."""
        s = _session_with_plots(tmp_path, L1)
        s.stage([PatchOp(A, "add", "/plots", L2)])
        s.stage([PatchOp(A, "remove", "/plots")])
        assert len(s.spec.pending) == 2
        assert "plots" not in s.spec.entry(A)

    def test_ops_on_other_paths_or_models_do_not_collapse(self, session):
        session.stage([PatchOp(A, "add", "/plots", L1)])
        session.stage([PatchOp(B, "add", "/plots", L1)])
        assert len(session.spec.pending) == 2
        # A's replace of its own /plots follows B's add: another model's op.
        session.stage([PatchOp(A, "replace", "/plots", L2)])
        assert len(session.spec.pending) == 3

    def test_a_refused_stage_keeps_the_op_a_collapse_would_have_replaced(
        self, tmp_path
    ):
        """The collapse pops the last pending op from a COPY: a stage the
        Session refuses must leave the live op where it was."""
        s = _session_with_plots(tmp_path, L1)
        s.stage([PatchOp(A, "replace", "/plots", L2)])
        before = list(s.spec.pending)
        with pytest.raises(ConfigError):
            s.stage([PatchOp(A, "replace", "/plots", [{"var": "x", "kind": "nope"}])])
        assert s.spec.pending == before

    def test_a_batch_is_not_collapsed_through_its_last_op(self, session):
        session.stage([PatchOp(A, "add", "/plots", L1)])
        session.stage(
            [
                PatchOp(A, "add", "/comparison/tolerance", 0.5),
                PatchOp(A, "replace", "/plots", L2),
            ]
        )
        assert len(session.spec.pending) == 3

    def test_a_replace_at_an_append_path_is_not_collapsed(self, session):
        """``/-`` names no member a replace could land on: the add appended
        one, and the replace is refused rather than folded into the add."""
        session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/metrics/children/-",
                    {"metric": "nrmse", "variable": "x"},
                )
            ]
        )
        with pytest.raises(PatchError):
            session.stage(
                [
                    PatchOp(
                        A,
                        "replace",
                        "/metrics/children/-",
                        {"metric": "nrmse", "variable": "x"},
                    )
                ]
            )


class TestTheMaterializationIsMarked:
    """The drawer's preview diffs against the tree as scoring sees it: the
    op `stage` prepends to write a flat entry's tree out says so."""

    def test_the_prepended_tree_is_marked_and_the_edit_is_not(self, session):
        session.stage([LOOSEN])
        tree, edit = session.spec.pending
        assert (tree.op, tree.path) == ("add", "/metrics") and tree.materializes
        assert edit == LOOSEN and not edit.materializes

    def test_an_edit_that_lands_on_the_tree_unmarks_it(self, session):
        """Convention 8 folds a replace of ``/metrics`` onto the op that
        added it: the value is no longer the scored tree."""
        session.stage([LOOSEN])
        tree = session.spec.pending[0]
        session.spec = SpecDocument(
            session.spec.path, session.spec.loaded, session.spec.content_hash, [tree]
        )
        other = copy.deepcopy(tree.value)
        other["children"][0]["tolerance"] = 0.5
        session.stage([PatchOp(A, "replace", "/metrics", other)])
        (folded,) = session.spec.pending
        assert (folded.op, folded.value) == ("add", other)
        assert not folded.materializes

    def test_a_tree_the_edit_supplies_is_not_marked(self, session):
        tree = session.tree(A)
        session.stage([PatchOp(A, "add", "/metrics", tree)])
        assert not session.spec.pending[0].materializes

    def test_a_tree_built_from_edited_overrides_is_not_marked(self, session):
        """The tree is built from the overrides as EDITED: it is not what
        the saved entry scores, and the drawer shows it added."""
        session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/comparison",
                    {"variable_overrides": {"x": {"tolerance": 0.5}}},
                )
            ]
        )
        session.stage(
            [
                PatchOp(
                    A, "add", "/metrics/children/0/window", {"start": 0.0, "end": 1.0}
                )
            ]
        )
        assert [op.materializes for op in session.spec.pending] == [False] * 3
        assert session.spec.preview_base(A) == session.spec.saved_entry(A)

    def test_an_earlier_edit_the_tree_does_not_read_keeps_the_mark(self, session):
        session.stage([PatchOp(A, "add", "/comparison", {"tolerance": 0.5})])
        session.stage([LOOSEN])
        assert [op.materializes for op in session.spec.pending] == [
            False,
            True,
            False,
        ]

    def test_rescores_materialization_is_marked_and_previews_as_nothing(self, session):
        from dstf.authoring.spec_preview import entry_lines

        session.rescore(A)
        (tree,) = session.spec.pending
        assert tree.materializes
        assert entry_lines(session.spec.preview_base(A), session.spec.entry(A)) == []
        session.stage([LOOSEN])
        lines = entry_lines(session.spec.preview_base(A), session.spec.entry(A))
        assert [line.text.strip() for line in lines if line.kind == "add"] == [
            '"variable": "x",',
            '"tolerance": 0.5',
        ]

    def test_a_capabilitys_patch_cannot_claim_a_mark(self, session):
        """Only the Session's own materialization is the scored tree: a
        Patch that says its op is one is staged unmarked."""
        tree = session.tree(A)
        session.apply(
            Patch(ops=[PatchOp(A, "add", "/metrics", tree, materializes=True)])
        )
        assert not session.spec.pending[0].materializes

    def test_rescore_after_an_overrides_edit_is_not_marked(self, session):
        """`rescore`'s tree, built from overrides a pending op edited, is not
        what the saved entry scores: unmarked, as `stage`'s."""
        session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/comparison",
                    {"variable_overrides": {"x": {"tolerance": 0.5}}},
                )
            ]
        )
        session.rescore(A)
        assert [op.materializes for op in session.spec.pending] == [False, False]
        assert session.spec.preview_base(A) == session.spec.saved_entry(A)

    def test_an_override_whose_value_moved_unmarks_the_tree(self, tmp_path):
        """The overrides are compared by value, not by their keys: x's saved
        tolerance moved, so the tree built from it is not the saved one."""
        s = _session_with_entry(
            tmp_path, {"comparison": {"variable_overrides": {"x": {"tolerance": 0.3}}}}
        )
        s.stage(
            [PatchOp(A, "replace", "/comparison/variable_overrides/x/tolerance", 0.5)]
        )
        s.stage(
            [
                PatchOp(
                    A, "add", "/metrics/children/0/window", {"start": 0.0, "end": 1.0}
                )
            ]
        )
        assert [op.materializes for op in s.spec.pending] == [False, False, False]

    def test_a_saved_tree_removed_is_not_what_the_saved_entry_scores(self, tmp_path):
        """The saved entry scores its OWN tree: with it removed, the tree
        `rescore` builds from the overrides is not that one, even though the
        overrides are as saved — unmarked, it diffs as a change."""
        from dstf.authoring.spec_preview import entry_lines

        authored = {
            "combinator": "and",
            "children": [{"metric": "nrmse", "variable": "x", "tolerance": 0.3}],
        }
        s = _session_with_entry(tmp_path, {"metrics": authored})
        s.stage([PatchOp(A, "remove", "/metrics")])
        s.rescore(A)
        assert not s.spec.pending[-1].materializes
        assert entry_lines(s.spec.preview_base(A), s.spec.entry(A)) != []


def _session_with_entry(tmp_path: Path, extra: dict) -> Session:
    """`_project` with ``extra`` merged into A's entry on disk before `open`."""
    cfg, work = _project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry.update(extra)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s


class TestAStagedValueIsTheSessions:
    """`stage` keeps a copy of each op's value: the caller's dict is its own."""

    def test_a_caller_editing_its_dict_after_staging_changes_nothing(self, session):
        value = {
            "combinator": "and",
            "children": [{"metric": "nrmse", "variable": "x", "tolerance": 0.5}],
        }
        session.stage([PatchOp(A, "add", "/metrics", value)])
        value["children"][0]["tolerance"] = 99.0  # the caller keeps editing
        assert session.spec.pending[0].value["children"][0]["tolerance"] == 0.5
        (patch,) = session.export()["patches"]
        assert patch["patch"][0]["value"]["children"][0]["tolerance"] == 0.5
        session.save()
        saved = session.spec.saved_entry(A)
        assert saved is not None
        assert saved["metrics"]["children"][0]["tolerance"] == 0.5


class TestPerTestDiscardAndSavedVerdict:
    """The Test page's strip: one test's edits discarded, and the verdict
    the test has as SAVED beside the one its edits give it."""

    TIGHTEN_D = PatchOp(D, "add", "/comparison", {"tolerance": 1e-5})
    LOOSEN_A = PatchOp(A, "add", "/comparison", {"tolerance": 1.0})

    def test_discard_of_one_model_keeps_the_others_ops(self, session):
        session.stage([self.LOOSEN_A])
        session.stage([self.TIGHTEN_D])
        assert classify(session.comparisons[A]) == PASS
        assert classify(session.comparisons[D]) == FAIL
        session.discard(models={A})
        assert session.spec.pending == [self.TIGHTEN_D]
        assert classify(session.comparisons[A]) == FAIL
        assert classify(session.comparisons[D]) == FAIL  # still the edited one
        session.discard()
        assert session.spec.pending == []
        assert classify(session.comparisons[D]) == PASS

    def test_discard_of_one_model_is_refused_while_a_job_runs(self, session):
        from dstf.authoring.session import RunningJob

        session.stage([self.LOOSEN_A])
        session.running = RunningJob(label="Rerun", models=frozenset({B}))
        with pytest.raises(JobRunningError):
            session.discard(models={A})
        assert session.spec.pending == [self.LOOSEN_A]

    def test_discard_of_a_model_with_nothing_pending_says_nothing(self, session):
        """No op of D is pending: the discard changes nothing, and says so
        by emitting nothing — no page repaints for it."""
        session.stage([self.LOOSEN_A])
        events: list[dict] = []
        session.on("spec", events.append)
        session.on("results", events.append)
        spec = session.spec
        session.discard(models={D})
        assert events == []
        assert session.spec is spec

    def test_saved_comparisons_merge_the_saved_spec_once(self, session, monkeypatch):
        """The drawer asks for every edited test's saved verdict at once:
        the loaded spec is merged ONCE for all of them, and each verdict is
        the one `saved_comparison` gives."""
        session.stage([self.LOOSEN_A])
        session.stage([self.TIGHTEN_D])
        merges: list[int] = []
        real = session._merge

        def counting(*args, **kwargs):
            merges.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(session, "_merge", counting)
        saved = session.saved_comparisons({A, D, B})
        assert len(merges) == 1
        assert set(saved) == {A, D, B}
        assert classify(saved[A]) == FAIL and classify(saved[D]) == PASS
        assert saved[B] is session.comparisons[B]  # nothing pending for B
        merges.clear()
        assert session.saved_comparisons({B}) == {B: session.comparisons[B]}
        assert merges == []  # nothing pending: nothing merged
        assert classify(session.saved_comparison(A)) == FAIL

    def test_saved_comparison_with_nothing_pending_is_the_comparison(self, session):
        assert session.saved_comparison(A) is session.comparisons[A]

    def test_saved_comparison_is_the_verdict_as_saved(self, session):
        session.stage([self.LOOSEN_A])
        comparisons = dict(session.comparisons)
        tests = list(session.project.tests)
        loaded = copy.deepcopy(session.spec.loaded)
        assert classify(session.comparisons[A]) == PASS
        saved = session.saved_comparison(A)
        assert session.spec.loaded == loaded
        assert classify(saved) == FAIL
        assert session.comparisons == comparisons  # nothing written
        assert all(a is b for a, b in zip(session.project.tests, tests))
        assert len(session.project.tests) == len(tests)
        assert classify(session.comparisons[A]) == PASS

    def test_saved_comparison_of_a_new_entry_is_none(self, tmp_path):
        cfg, work = _project(tmp_path)
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        # D drops out of the spec: it is scanned by no recognizer (Python
        # source), so its entry is the only thing that makes it a test.
        spec["tests"] = [e for e in spec["tests"] if e["model"] != D]
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.project.test(D) is None
        s.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.01})])
        assert s.saved_comparison(D) is None


class TestACriterionOnAFileVariable:
    """The owner's O2: staging a criterion on a variable the loaded result
    lacks re-reads the model when its result FILE holds the variable (a
    backend that records everything), so it scores at once; a name the
    file lacks is left for a rerun, and nothing is re-read."""

    def _leaf(self, variable: str) -> PatchOp:
        return PatchOp(
            D,
            "add",
            "/metrics/children/-",
            {"metric": "range", "variable": variable, "min_value": 0, "max_value": 1},
        )

    def test_a_variable_the_file_has_is_re_read_and_scored(self, tmp_path):
        cfg, work = _project(tmp_path)
        path = work / "test_0004" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["w"] = [0.5 for _ in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        assert [v.name for v in s.project.results[D].variables] == ["x"]
        s.stage([self._leaf("w")])
        assert [(v.index, v.name) for v in s.project.results[D].variables] == [
            (1, "x"),
            (2, "w"),
        ]
        leaf = s.comparisons[D].metric_tree.children[-1]
        assert leaf.passed, leaf.diagnostics
        assert classify(s.comparisons[D]) == PASS
        # The saved spec (no tree) scores what D tracks, not the re-read w.
        assert classify(s.saved_comparison(D)) == PASS
        s.discard()
        assert [v.name for v in s.project.results[D].variables] == ["x", "w"]
        assert [vc.name for vc in s.comparisons[D].variables] == ["x"]
        assert classify(s.comparisons[D]) == PASS

    def test_a_variable_the_file_lacks_is_not_re_read(self, session, monkeypatch):
        before = session.project.results[D]
        reads: list = []
        real = session.project.backend.read_last_results
        monkeypatch.setattr(
            session.project.backend,
            "read_last_results",
            lambda tests, **kw: (reads.append(tests), real(tests, **kw))[1],
        )
        session.stage([self._leaf("z")])
        assert reads == []
        assert session.project.results[D] is before
        leaf = session.comparisons[D].metric_tree.children[-1]
        assert not leaf.passed

    @staticmethod
    def _with_w(tmp_path, *, tree: bool) -> tuple[Path, Path]:
        """`_project` whose D result FILE holds ``w`` beside ``x``, and —
        with ``tree`` — whose SAVED D entry is a tree scoring ``x``."""
        cfg, work = _project(tmp_path)
        if tree:
            spec_path = cfg.parent / "test_spec.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            for entry in spec["tests"]:
                if entry["model"] == D:
                    entry["metrics"] = {
                        "combinator": "and",
                        "children": [{"metric": "nrmse", "variable": "x"}],
                    }
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
        path = work / "test_0004" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["w"] = [0.5 for _ in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        return cfg, work

    def test_a_discarded_criterions_variable_is_not_tracked_after(self, tmp_path):
        """The re-read w outlives its criterion in the result: a saved tree
        does not then warn of it as a tracked variable without a criterion,
        and the Variables drawer's Tracked group does not hold it."""
        cfg, work = self._with_w(tmp_path, tree=True)
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.comparisons[D].warnings == []
        s.stage([self._leaf("w")])
        assert [v.name for v in s.project.results[D].variables] == ["x", "w"]
        assert s.comparisons[D].warnings == []
        s.discard()
        assert [v.name for v in s.project.results[D].variables] == ["x", "w"]
        assert s.comparisons[D].warnings == []
        assert s.tracked(D) == ["x"]
        assert "w" in s.variable_names(D)  # still the file's

    def test_the_saved_comparison_under_a_pending_criterion_does_not_warn(
        self, tmp_path
    ):
        cfg, work = self._with_w(tmp_path, tree=True)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([self._leaf("w")])
        assert s.saved_comparison(D).warnings == []

    def test_a_flat_discard_leaves_the_plots_as_they_were(self, tmp_path):
        cfg, work = self._with_w(tmp_path, tree=False)
        s = Session()
        s.open(cfg, work_dir=work)
        before = [list(p.series) for p in s.plots(D)]
        s.stage([self._leaf("w")])
        s.discard()
        assert [list(p.series) for p in s.plots(D)] == before
        assert s.tracked(D) == ["x"]

    def test_a_re_read_that_raises_leaves_the_result_and_the_reads(
        self, tmp_path, monkeypatch
    ):
        """The re-read is atomic: a read that raises drops nothing — the
        loaded result and the model's reads stand."""
        cfg, work = self._with_w(tmp_path, tree=False)
        s = Session()
        s.open(cfg, work_dir=work)
        s.variable_names(D)  # the model's reads, the file listed
        before, reads = s.project.results[D], s._reads[D]

        def boom(tests, **kw):
            raise OSError("the disk went away")

        monkeypatch.setattr(s.project.backend, "read_last_results", boom)
        with pytest.raises(OSError, match="went away"):
            s.stage([self._leaf("w")])
        assert s.project.results[D] is before
        assert s._reads[D] is reads

    def test_a_stage_whose_re_read_raises_leaves_the_session_as_it_was(
        self, tmp_path, monkeypatch
    ):
        """`stage`'s promise — on any error the Session is unchanged — holds
        across the re-read: it reads before the spec and the tests are
        replaced, so nothing is pending, the tests and the comparison are
        the ones before, and no event said otherwise."""
        cfg, work = self._with_w(tmp_path, tree=False)
        s = Session()
        s.open(cfg, work_dir=work)
        spec, tests, comp = s.spec, s.project.tests, s.comparisons[D]
        events: list[dict] = []
        for kind in ("spec", "results"):
            s.on(kind, events.append)

        def boom(tests, **kw):
            raise OSError("the disk went away")

        monkeypatch.setattr(s.project.backend, "read_last_results", boom)
        with pytest.raises(OSError, match="went away"):
            s.stage([self._leaf("w")])
        assert s.spec is spec and s.spec.pending == []
        assert s.project.tests is tests
        assert s.comparisons[D] is comp
        assert events == []

    def test_a_re_read_keeps_the_files_listing(self, tmp_path, monkeypatch):
        """The file did not change: the re-read keeps its listing, and the
        next `variable_names` does not list it again."""
        cfg, work = self._with_w(tmp_path, tree=False)
        s = Session()
        s.open(cfg, work_dir=work)
        backend = type(s.project.backend)
        real = backend.result_names
        listed: list = []

        def counting(test_dir):
            listed.append(test_dir)
            return real(test_dir)

        monkeypatch.setattr(backend, "result_names", staticmethod(counting))
        assert "w" in s.variable_names(D)
        assert len(listed) == 1
        s.stage([self._leaf("w")])
        assert [v.name for v in s.project.results[D].variables] == ["x", "w"]
        assert "w" in s.variable_names(D)
        assert len(listed) == 1, listed


# -- the Statistics rows (U3) -----------------------------------------------------

#: Dymola-shaped statistics: the baseline was stored before the parser always
#: wrote a list's summaries (an empty `nonlinear`, no `nonlinear_count`).
STATS_RUN = {
    "translation": {"nonlinear": [1, 1, 1], "nonlinear_count": 3},
    "simulation": {"state_events": 2},
    "timing": {"total_wall": 4.02},
    "CPUtime": 1.12,
}
STATS_BASE = {
    "translation": {"nonlinear": []},
    "simulation": {"state_events": 2},
    "timing": {"total_wall": 3.91},
    "CPUtime": 0.80,
}


def _stats_session(tmp_path: Path, *, result: bool = True) -> Session:
    """`_two_var_project` with A's baseline carrying `STATS_BASE` and its
    in-memory result `STATS_RUN` (FakeLib records only walls)."""
    cfg, work = _two_var_project(tmp_path)
    config = Config(config_file=cfg, work_dir=work)
    tt = np.asarray([i / 4 for i in range(5)])
    ReferenceStore(config).store_reference(
        make_test_model(A, variable_patterns=["x", "y"]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=1, time=tt, values=tt, name="x"),
                VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
            ],
            statistics=copy.deepcopy(STATS_BASE),
            diagnostics=[
                VariableResult(index=0, time=tt, values=0.80 * tt, name="CPUtime")
            ],
        ),
    )
    s = Session()
    s.open(cfg, work_dir=work)
    if result:
        res = s.project.results[A]
        res.statistics = copy.deepcopy(STATS_RUN)
        res.diagnostics = [
            VariableResult(index=0, time=tt, values=1.12 * tt, name="CPUtime")
        ]
    else:
        s.project.results.pop(A, None)
    return s


class TestTheStatisticsRows:
    def test_an_unknown_models_statistic_leaves_are_a_key_error(self, tmp_path):
        """As for `statistics` and `tree`: a model the project does not
        have is a caller's error, not a test with no criteria."""
        s = _stats_session(tmp_path)
        with pytest.raises(KeyError):
            s.statistic_leaves("No.Such.Model")
        assert s.statistic_leaves(A) == []

    def test_the_run_beside_the_baseline_in_the_reports_order(self, tmp_path):
        s = _stats_session(tmp_path)
        rows = s.statistics(A)
        assert [r.path for r in rows] == [
            "translation.nonlinear",
            "translation.nonlinear_count",
            # the list's other summaries: 0 on the baseline's empty list,
            # nothing on a run that wrote only the count
            "translation.nonlinear_max",
            "translation.nonlinear_total",
            "CPUtime",
            "simulation.state_events",
            "timing.total_wall",
        ]
        by = {r.path: r for r in rows}
        assert by["translation.nonlinear_max"].baseline == 0
        assert by["translation.nonlinear_max"].run is None
        assert by["translation.nonlinear_count"].baseline == 0
        assert by["translation.nonlinear_count"].delta == "0→3"
        assert by["translation.nonlinear_count"].changed
        assert by["CPUtime"].changed and by["CPUtime"].delta == "+40%"
        assert not by["timing.total_wall"].changed

    def test_a_staged_leaf_on_an_unknown_quantity_gets_its_own_row(self, tmp_path):
        s = _stats_session(tmp_path)
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "scalar", "quantity": "translation.typo", "tolerance": 0},
                {
                    "metric": "scalar",
                    "variable": "CPUtime",
                    "quantity": "CPUtime.final",
                    "tolerance": 0.1,
                    "tolerance_mode": "rel",
                },
            ],
        }
        s.stage([PatchOp(A, "add", "/metrics", tree)])
        rows = s.statistics(A)
        assert (rows[-1].path, rows[-1].section) == ("translation.typo", "other scored")
        assert rows[-1].run is None and rows[-1].baseline is None
        # `CPUtime.final` is the `CPUtime` row (S2): no second one.
        assert [r.path for r in rows].count("CPUtime") == 1
        assert "CPUtime.final" not in [r.path for r in rows]

    def test_an_implicit_tree_is_never_materialized_for_them(self, tmp_path):
        """The extra rows come from the ENTRY's own tree: an implicit tree
        holds no statistics leaf, and materializing one reads the baseline
        (Corrections B4)."""
        s = _stats_session(tmp_path)

        def refuse(model_id):
            raise AssertionError("materialized")

        s._materialization = refuse  # type: ignore[method-assign]
        assert s.statistics(A)

    def test_no_result_reads_the_baseline_rows_alone(self, tmp_path):
        s = _stats_session(tmp_path, result=False)
        rows = s.statistics(A)
        assert rows and all(r.run is None and not r.changed for r in rows)
        assert s.statistics(B) == []
        with pytest.raises(KeyError):
            s.statistics("FakeLib.Nope")

    def test_the_cached_reference_is_never_written(self, tmp_path):
        s = _stats_session(tmp_path)
        before = copy.deepcopy(s._reads_for(A).ref)
        s.statistics(A)
        s.statistics(A)
        assert s._reads_for(A).ref == before

    def test_the_rows_take_each_diagnostics_own_samples(self, tmp_path):
        s = _stats_session(tmp_path)
        tt = np.asarray([i / 4 for i in range(5)])
        res = s.project.results[A]
        res.statistics = {**res.statistics, "Steps": 4.0, "Wall": 2.5}
        res.diagnostics = [
            VariableResult(index=0, time=tt, values=4 * tt, name="Steps"),  # whole
            VariableResult(
                index=0, time=tt, values=2.5 * tt, name="Wall"
            ),  # fractional
        ]
        by = {r.path: r for r in s.statistics(A)}
        assert by["Wall"].kind == "time"
        assert by["Steps"].kind == "count"

    def test_a_diagnostic_max_leaf_gets_its_own_row_from_both_sides(self, tmp_path):
        s = _stats_session(tmp_path)
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {
                    "metric": "scalar",
                    "variable": "CPUtime",
                    "quantity": "CPUtime.max",
                    "tolerance": 0.1,
                    "tolerance_mode": "rel",
                },
            ],
        }
        s.stage([PatchOp(A, "add", "/metrics", tree)])
        row = s.statistics(A)[-1]
        assert (row.path, row.section) == ("CPUtime.max", "other scored")
        assert row.run == pytest.approx(1.12) and row.baseline == pytest.approx(0.80)

    def test_a_statistics_leaf_is_no_tracked_variable(self, tmp_path):
        """A statistics leaf scores a stored number, not a variable: its
        parsed `variable` (its path, or a diagnostic's name) is never one of
        the test's tracked variables, which the add form's picker lists
        (Corrections B5)."""
        s = _stats_session(tmp_path)
        s.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/metrics",
                    {
                        "combinator": "and",
                        "children": [
                            {"metric": "nrmse", "variable": "x"},
                            {
                                "metric": "scalar",
                                "quantity": "translation.nonlinear_count",
                                "tolerance": 0,
                            },
                            {
                                "metric": "scalar",
                                "variable": "CPUtime",
                                "quantity": "CPUtime.final",
                                "tolerance": 0.1,
                                "tolerance_mode": "rel",
                            },
                        ],
                    },
                )
            ]
        )
        scored = [vc.name for vc in s.comparisons[A].variables]
        assert "translation.nonlinear_count" in scored  # what B5 is about
        assert s.tracked(A) == ["x", "y"]


#: Dymola-shaped statistics at TRANSFORM's lengths (the keys `log_parser`
#: writes), for the Statistics card: the baseline was stored before the
#: parser wrote a list's zero summaries (an empty `nonlinear`, no `_count`),
#: and the run's model grew three nonlinear systems. CPU time moved
#: 0.80 -> 1.12 s (+40%); the total wall by 3%, inside the 10% a time's
#: requirement allows.
DYMOLA_BASE = {
    "translation": {
        "scalar_unknowns": 8715,
        "scalar_equations": 8715,
        "continuous_time_states": 120,
        "numerical_jacobians": 0,
        "nonlinear": [],
        "init_homotopy_nonlinear_after_manipulation": [31, 31],
        "init_homotopy_nonlinear_after_manipulation_count": 2,
        "init_homotopy_nonlinear_after_manipulation_total": 62,
        "init_homotopy_nonlinear_after_manipulation_max": 31,
        "state_names": ["pipe_nParallel.pipe.flowModel.firstOrder_dps_K[1].y"],
    },
    "simulation": {
        "cpu_time_integration": 0.201,
        "result_points": 102,
        "accepted_steps": 25,
        "state_events": 0,
    },
    "timing": {
        "translation_wall": 2.1,
        "sim_wall": 1.2,
        "other_wall": 0.61,
        "total_wall": 3.91,
    },
    "CPUtime": 0.80,
    "EventCounter": 0,
}
DYMOLA_RUN = copy.deepcopy(DYMOLA_BASE)
DYMOLA_RUN["translation"].update(
    {
        "nonlinear": [3, 5, 1],
        "nonlinear_count": 3,
        "nonlinear_total": 9,
        "nonlinear_max": 5,
    }
)
DYMOLA_RUN["timing"]["total_wall"] = 4.02
DYMOLA_RUN["CPUtime"] = 1.12


def _stats_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with A's run and baseline carrying `DYMOLA_RUN` /
    `DYMOLA_BASE` ON DISK — the run's in `run_stats.json` (what a reload
    reads; FakeLib's fixture never runs), the baseline's through the store —
    so a served `dstf author` reads them too. B's run carries `DYMOLA_RUN`
    and B has no baseline (NO_REF: nothing is scored)."""
    cfg, work = _two_var_project(tmp_path, ref_models=())
    for key, model in (("test_0001", A), ("test_0002", B)):
        (work / key / "run_stats.json").write_text(
            json.dumps(
                {
                    "model_id": model,
                    "translation_wall": None,
                    "sim_wall": None,
                    "elapsed": 0.0,
                    "statistics": DYMOLA_RUN,
                }
            ),
            encoding="utf-8",
        )
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
            statistics=copy.deepcopy(DYMOLA_BASE),
        ),
    )
    return cfg, work
