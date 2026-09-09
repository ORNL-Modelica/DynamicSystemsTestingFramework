"""The Session (two-tier design §4.5, tests per §6 "The Session"): pure
Python against a Python-backend project whose run is on disk."""

from __future__ import annotations

import json
import threading
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


def _snapshot(work: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(p.relative_to(work)), p.read_bytes())
        for p in work.rglob("*")
        if p.is_file()
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

    def test_the_recorded_backend_wins_over_the_config(self, tmp_path):
        cfg, work = _project(tmp_path)
        (work / "status.json").write_text(
            json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []})
        )
        from dstf.config import ConfigError

        with pytest.raises(ConfigError, match="Nonesuch"):
            Session().open(cfg, work_dir=work)


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

    def test_a_glob_spec_rescores_without_staging(self, session):
        """Conventions #7: a leaf per pattern would score a `*` sentinel and
        flip G to FAIL; rescore scores it as loaded and stages nothing."""
        assert classify(session.rescore(G)) == PASS
        assert session.spec.pending == []
        assert "metrics" not in session.spec.entry(G)

    def test_a_missing_literal_rescores_without_staging(self, session):
        """A tracked name the result never resolved would become a sentinel
        leaf; the Session stages materialization only when the tree scores
        exactly what the flat path scored."""
        entry = dict(session.spec.entry(D))
        entry["variables"] = ["x", "ghost"]
        assert session._materializable(D, entry) is False
        assert session._materializable(D, session.spec.entry(D)) is True

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


class TestView:
    def test_a_scored_variable_carries_its_leaves(self, session):
        view = session.view(A, "x")
        assert view.ref_time is not None and len(view.leaves) == 1
        assert view.leaves[0].path.startswith("/metrics")
        fig = session.figure(A, "x")
        assert {"data", "layout"} <= fig.keys() and len(fig["data"]) >= 2

    def test_no_baseline_is_the_empty_state_with_the_trajectory(self, session):
        view = session.view(B, "x")
        assert view.act_time is not None and view.ref_time is None and view.leaves == ()

    def test_no_result_is_the_empty_state_without_a_trajectory(self, session):
        view = session.view(C, "x")
        assert view.act_time is None and view.leaves == ()
        assert "layout" in session.figure(C, "x")


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
        session.apply(OpenProject(config_path=other_cfg), work_dir=other_work)
        assert (
            session.project is not old
            and session.project.config.spec_path == other_cfg.parent / "test_spec.json"
        )

    def test_none_and_sequences(self, session):
        session.apply(None)
        session.apply([Patch(ops=[LOOSEN]), None])
        assert LOOSEN in session.spec.pending


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
            "comparisons",
            "artifacts",
            "rescore",
            "view",
            "figure",
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
