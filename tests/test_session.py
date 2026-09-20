"""The Session (two-tier design §4.5, tests per §6 "The Session"): pure
Python against a Python-backend project whose run is on disk."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import make_test_model
from dstf.authoring.capabilities import Artifact, Job, OpenProject, Patch, StoreWrite
from dstf.authoring.session import (
    JobRunningError,
    LockedError,
    Session,
    SessionView,
    StaleSpecError,
)
from dstf.authoring.spec_document import PatchOp
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
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
        posted.get(timeout=10)()  # the rescan's re-merge, run on "the UI loop"
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
        op = session._materialization(D)
        assert op is not None
        assert [leaf["variable"] for leaf in op["value"]["children"]] == [
            vc.name for vc in session.comparisons[D].variables
        ]
        # The flat path's display label for an unnamed result variable —
        # TRANSFORM has 70 tests whose recorded names carry a newline — is
        # not a name the tree path can resolve.
        session.comparisons[D].variables[0].name = "x[1]"
        assert session._materialization(D) is None
        session.comparisons[D].variables[0].name = ""
        assert session._materialization(D) is None

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
        assert s._materialization(A) is not None

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

        with pytest.raises(PatchError, match="no scored variables"):
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
        assert len(session.spec.pending) == n + 1
        assert session.spec.pending[-1].value == 0.2
        session.stage(
            [
                PatchOp(A, "replace", "/metrics/children/0/tolerance", 0.9),
                PatchOp(A, "add", "/comparison", {"tolerance": 0.5}),
            ]
        )
        assert len(session.spec.pending) == n + 3  # a batch is never collapsed
        # The four discriminators, each one op: a different path, a different
        # model, a non-replace op, and then a replace again (collapses).
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
        session.stage([PatchOp(A, "remove", "/comparison/tolerance")])
        assert len(session.spec.pending) == m + 6  # a non-replace never collapses


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
            "tree_view",
            "tree",
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


LAYOUT = [{"kind": "time", "series": [{"var": "x"}, {"var": "z"}]}]


class TestPlots:
    def _open(self, tmp_path) -> Session:
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        return s

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
            for name in LAYOUT[0]["series"]
            for leaf in s.view(A, name["var"]).leaves
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
        assert len(s.plots(A)[0].series) == 2  # and the multi-series branch
        assert rings(view.plot_figure(A, 0))
        assert not rings(view.plot_figure(A, 0, editing=()))

    def test_a_staged_layout_is_what_plots_answers(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert [(p.series, p.missing) for p in s.plots(A)] == [(("x", "z"), ())]
        s.discard()
        assert len(s.plots(A)) == 2

    def test_a_series_the_result_lacks_is_named_missing(self, tmp_path):
        s = self._open(tmp_path)
        s.stage(
            [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "gone"}]}])]
        )
        (plot,) = s.plots(A)
        assert plot.series == ("x",) and plot.missing == ("gone",)

    def test_staging_an_invalid_layout_changes_nothing(self, tmp_path):
        from dstf.config import ConfigError
        from dstf.discovery.patch_apply import PatchError

        s = self._open(tmp_path)
        with pytest.raises((PatchError, ConfigError), match=r"plots\[0\]\.series"):
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
        assert [p.series for p in again.plots(A)] == [("x", "z")]

    def test_plot_figure_of_one_series_is_the_variables_figure(self, tmp_path):
        s = self._open(tmp_path)
        assert s.plot_figure(A, 0) == s.figure(A, "x")

    def test_plot_figure_of_two_series_draws_both(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        fig = s.plot_figure(A, 0)
        ids = [tr["meta"]["id"] for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert ids == ["actual/x", "actual/z"]
        assert fig["config"]["edits"] == {"shapePosition": True}

    def test_plot_figure_keeps_the_leaves_of_a_tracked_series(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        single = s.figure(A, "x")
        multi = s.plot_figure(A, 0)
        leafy = lambda fig: sorted(  # noqa: E731
            tr["meta"].get("id") or tr["meta"]["family"]
            for tr in fig["data"]
            if tr["meta"]["kind"] not in ("act", "ref", "error", "overlay")
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

    def test_a_backend_that_cannot_list_calls_nothing_missing(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        monkeypatch.setattr(
            PythonBackend, "result_names", classmethod(lambda cls, d: None)
        )
        s._reload_results()
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.plots(A)[0].missing == ()

    def test_the_session_view_mirrors_the_three_reads(self, tmp_path):
        from dstf.authoring.session import Selection, SessionView

        s = self._open(tmp_path)
        v = SessionView(s, Selection())
        assert v.variable_names(A) == s.variable_names(A)
        assert v.plots(A) == s.plots(A)
        assert v.plot_figure(A, 0) == s.plot_figure(A, 0)


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
        acts = [tr["meta"]["id"] for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert acts == ["actual/x", "actual/z"]

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
        s.stage(
            [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "gone"}]}])]
        )
        (plot,) = s.plots(A)
        assert plot.series == ("x",) and plot.missing == ("gone",)

    def test_a_negative_plot_index_is_an_index_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(IndexError):
            s.plot_figure(A, -1)
