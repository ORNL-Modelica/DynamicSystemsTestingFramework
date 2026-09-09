"""The Session (two-tier design §4.5, tests per §6 "The Session"): pure
Python against a Python-backend project whose run is on disk."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from conftest import make_test_model
from dstf.authoring.session import Session, StaleSpecError
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
