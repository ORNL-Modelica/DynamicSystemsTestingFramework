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

    def test_a_failed_open_keeps_the_live_projects_notices(self, session):
        """The reset sits at the commit point, after the Project is built:
        hoisting it to the top of ``open`` empties a live project's panel on
        an open that never replaces it."""
        from dstf.config import ConfigError

        session.apply(StoreWrite(run=lambda store: "stored 1", models=frozenset({B})))
        config = session.project.config
        (config.work_dir / "status.json").write_text(
            json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []})
        )
        with pytest.raises(ConfigError, match="Nonesuch"):
            session.open(config.config_file, work_dir=config.work_dir)
        assert session.notices == ["stored 1"]
        assert session.project is not None

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
        session.apply(StoreWrite(run=lambda store: None, models=frozenset({B})))
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
