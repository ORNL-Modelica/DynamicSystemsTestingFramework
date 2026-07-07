"""Regression tests for the 2026-07-06 review — final CLI/config/manifest batch.

Decisions encoded here (see CODE_REVIEW_2026-07-06.md, findings 30, 58-67,
71, 72, plus two leftovers):

* Finding 58 — the ``-i``/``--rerun`` category string is validated at
  argparse time (exit 2 with the valid-categories message), never after a
  multi-hour simulation run.
* Finding 59 — ``--rerun`` with an empty matching set is SUCCESS (exit 0,
  "Nothing to rerun"), and the merged report is still produced. A generic
  empty ``--filter`` stays exit 1.
* Finding 60 — ``export --output`` accepts a string path (coerced to Path).
* Finding 30 — the ``--accept`` branch exits nonzero when any simulation
  failed or fewer baselines were stored than attempted (naming the skips),
  and accepts ONLY the tests simulated in THIS run, never the merged
  ``--rerun``/``--merge`` manifest scope.
* Finding 61 — ``--accept`` + ``-i`` is a hard argparse error.
* Finding 62 — interactive review exits 1 iff any reviewed-or-skipped test
  remains failing/sim-failed and was not accepted; else 0 (quit included).
* Finding 63 — ``--filter`` matching uses ``fnmatch.fnmatchcase`` so the
  same filter selects the same tests on Windows and Linux.
* Finding 64 — ``--filter`` splits on commas NOT inside ``[]`` character
  classes, so ``Test[A,B]*`` survives as one pattern.
* Finding 65 — ``--package`` matches the package itself or children across
  a dot boundary (no more ``MyLib.Fluid`` ⊃ ``MyLib.FluidExperimental``).
* Finding 66 — ``batch_manifest.json`` is written atomically; a corrupt
  manifest is quarantined to ``*.corrupt`` with a warning instead of
  crashing every subsequent run/compare at startup.
* Finding 67 — "Python" is a recognized simulator backend prefix, so
  ``"simulator": "Python 3.12"`` resolves.
* Finding 71 — overlays on nobaseline trajectories are decimated under the
  same embedded-HTML budget as everything else.
* Finding 72 — the exported JSON-Schema wires the per-mode ``$defs`` into
  the leaf spec via if/then, with a lenient posture (unknown keys valid).
* Leftover A — ``ConfigError`` exits CLI commands with one clean line and
  exit code 2, not a traceback.
* Leftover B — ``store_reference`` rejects baselines whose trajectory
  contains NaN/Inf (naming model + variable); ``accept_results`` skips the
  poisoned test with a warning and keeps accepting the rest.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dstf.cli import (
    _filter_tests,
    _interactive_review,
    _resolve_filter_patterns,
    build_arg_parser,
    cmd_run,
    main,
)
from dstf.config import SIMULATOR_BACKENDS, Config, _detect_backend, detect_os
from dstf.discovery.test_registry import TestModel
from dstf.simulators.base import BatchManifest, TestResult, VariableResult
from dstf.storage.reference_store import ReferenceStore

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mk_test(model_id: str, **kw) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path(""),
        source_package=model_id.split(".")[0],
        short_name=model_id.rsplit(".", 1)[-1],
        n_vars=1,
        variable_patterns=["x"],
        source="test_spec",
        **kw,
    )


def _mk_result(
    model_id: str,
    values=None,
    success: bool = True,
) -> TestResult:
    t = np.linspace(0.0, 1.0, 11)
    if not success:
        return TestResult(model_id=model_id, success=False, error_message="boom")
    v = np.asarray(values if values is not None else np.sin(t), dtype=float)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=v, name="x")],
    )


class _FakeRunner:
    """Stands in for a SimulatorRunner in cmd_run — no simulator involved."""

    artifact_files: tuple = ()

    def __init__(self, work_dir, results, prior_results=None, manifest_map=None):
        self.work_dir = Path(work_dir)
        self.results = results
        self.prior_results = prior_results or {}
        self.manifest_map = manifest_map
        self.ref_id_map: dict = {}
        self.ran_tests: list | None = None

    def run_tests(self, tests):
        self.ran_tests = list(tests)
        mm = self.manifest_map or {
            f"test_{i:04d}": {"model_id": t.model_id, "ref_id": None}
            for i, t in enumerate(tests, 1)
        }
        return [BatchManifest(batch_id=0, work_dir=self.work_dir, manifest=mm)]

    def read_results(self, manifests, scope_tests):
        return {
            t.model_id: self.results[t.model_id]
            for t in scope_tests
            if t.model_id in self.results
        }

    def read_last_results(self, tests):
        return dict(self.prior_results)


@pytest.fixture
def run_env(tmp_config_dir, tmp_path, monkeypatch):
    """Wire cmd_run to fakes: no simulator, no browser, tmp work dir."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(
        "dstf.reporting.plot_comparison.open_in_browser", lambda p: None
    )

    def make(tests, results, prior_results=None, manifest_map=None):
        runner = _FakeRunner(work_dir, results, prior_results, manifest_map)
        monkeypatch.setattr("dstf.cli.discover_tests", lambda config: list(tests))
        monkeypatch.setattr(
            "dstf.cli._get_runner", lambda config, persistent=False: runner
        )
        return runner

    return SimpleNamespace(config_dir=tmp_config_dir, work_dir=work_dir, make=make)


def _run_args(env, *extra) -> argparse.Namespace:
    parser = build_arg_parser()
    return parser.parse_args(
        ["--config", str(env.config_dir), "run", "--work-dir", str(env.work_dir)]
        + list(extra)
    )


def _stored_model_ids(env) -> set[str]:
    ref_dir = (
        env.config_dir / "Resources" / "ReferenceResults" / "Dymola" / detect_os()
    )
    if not ref_dir.exists():
        return set()
    return {
        json.loads(f.read_text(encoding="utf-8"))["model_id"]
        for f in ref_dir.glob("ref_*.json")
    }


# ---------------------------------------------------------------------------
# Finding 58 — review filter validated at parse time (exit 2, fast)
# ---------------------------------------------------------------------------


class TestReviewFilterValidatedAtParseTime:
    def test_interactive_typo_aborts_at_parse_time(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # any fallout stays out of the repo
        with pytest.raises(SystemExit) as exc:
            main(["run", "-i", "sim_failed"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "Invalid review filter" in err
        assert "sim-failed" in err  # the valid-categories hint

    def test_rerun_typo_aborts_at_parse_time(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["run", "--rerun", "bogus"])
        assert exc.value.code == 2
        assert "Invalid review filter" in capsys.readouterr().err

    def test_valid_filters_still_parse(self):
        parser = build_arg_parser()
        args = parser.parse_args(["run", "-i", "failed,warnings"])
        assert args.interactive == "failed,warnings"
        args = parser.parse_args(["run", "-i"])
        assert args.interactive == "all"
        args = parser.parse_args(["run", "--rerun"])
        assert args.rerun == "failed"


# ---------------------------------------------------------------------------
# Finding 61 — --accept and -i are mutually exclusive
# ---------------------------------------------------------------------------


def test_accept_and_interactive_are_mutually_exclusive(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["run", "-i", "--accept"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Finding 59 — empty --rerun set is success; empty --filter stays an error
# ---------------------------------------------------------------------------


class TestRerunEmptySetIsSuccess:
    def test_nothing_to_rerun_exits_zero(self, run_env, capsys):
        t1 = _mk_test("Lib.T1")
        run_env.make(
            [t1], results={}, prior_results={"Lib.T1": _mk_result("Lib.T1")}
        )
        rc = cmd_run(_run_args(run_env, "--rerun", "failed"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Nothing to rerun" in out
        assert "failed" in out

    def test_empty_rerun_still_produces_merged_report(
        self, run_env, capsys, monkeypatch
    ):
        called = {}

        def fake_suite(comparisons, results, tests, store, config):
            called["comparisons"] = list(comparisons)
            return 1  # per-test failures must NOT flip the empty-rerun success

        monkeypatch.setattr("dstf.cli._generate_report_suite", fake_suite)
        t1 = _mk_test("Lib.T1")
        run_env.make(
            [t1], results={}, prior_results={"Lib.T1": _mk_result("Lib.T1")}
        )
        rc = cmd_run(_run_args(run_env, "--rerun", "failed", "--report"))
        assert rc == 0
        assert called, "merged report suite was not generated"
        assert (run_env.work_dir / "dashboard.html").exists()

    def test_plain_empty_filter_still_exits_one(self, run_env, capsys):
        t1 = _mk_test("Lib.T1")
        run_env.make([t1], results={})
        rc = cmd_run(_run_args(run_env, "--filter", "Nope.*"))
        assert rc == 1
        assert "No tests matched the filter." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Finding 30 — --accept exit code + this-run-only scope
# ---------------------------------------------------------------------------


class TestAcceptExitCodeAndScope:
    def test_partial_accept_exits_nonzero_and_names_skips(self, run_env, capsys):
        t1, t2 = _mk_test("Lib.T1"), _mk_test("Lib.T2")
        run_env.make(
            [t1, t2],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T2": _mk_result("Lib.T2", success=False),
            },
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Accepted 1" in out
        assert "Lib.T2" in out
        assert "simulation failed" in out
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_full_accept_exits_zero(self, run_env, capsys):
        t1 = _mk_test("Lib.T1")
        run_env.make([t1], results={"Lib.T1": _mk_result("Lib.T1")})
        rc = cmd_run(_run_args(run_env, "--accept"))
        assert rc == 0
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_accept_with_merge_stores_only_this_runs_tests(self, run_env, capsys):
        """--merge expands the report scope, but stale on-disk results for
        out-of-scope tests must never become baselines."""
        t1, t2 = _mk_test("Lib.T1"), _mk_test("Lib.T2")
        run_env.make(
            [t1, t2],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T2": _mk_result("Lib.T2"),  # stale on-disk result
            },
            manifest_map={
                "test_0001": {"model_id": "Lib.T1", "ref_id": None},
                "test_0002": {"model_id": "Lib.T2", "ref_id": None},
            },
        )
        rc = cmd_run(_run_args(run_env, "--filter", "Lib.T1", "--merge", "--accept"))
        assert rc == 0
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_accept_skips_poisoned_baseline_and_continues(self, run_env, capsys):
        bad_values = [0.0, 1.0, float("nan")] + [2.0] * 8
        t1, t3 = _mk_test("Lib.T1"), _mk_test("Lib.T3")
        run_env.make(
            [t1, t3],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T3": _mk_result("Lib.T3", values=bad_values),
            },
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Lib.T3" in out
        assert "non-finite" in out
        assert _stored_model_ids(run_env) == {"Lib.T1"}


# ---------------------------------------------------------------------------
# Leftover B — store_reference rejects non-finite trajectories
# ---------------------------------------------------------------------------


def _mk_store(sample_models_dir, tmp_path) -> ReferenceStore:
    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    return ReferenceStore(config)


class TestStoreReferenceRejectsNonFinite:
    @pytest.mark.parametrize("poison", [float("nan"), float("inf"), float("-inf")])
    def test_poisoned_values_rejected_naming_model_and_variable(
        self, sample_models_dir, tmp_path, poison
    ):
        store = _mk_store(sample_models_dir, tmp_path)
        values = [0.0, 1.0, poison] + [2.0] * 8
        with pytest.raises(ValueError) as exc:
            store.store_reference(_mk_test("Lib.Bad"), _mk_result("Lib.Bad", values))
        msg = str(exc.value)
        assert "Lib.Bad" in msg
        assert "x" in msg
        assert "non-finite" in msg
        assert not list((tmp_path / "refs").rglob("ref_*.json"))

    def test_poisoned_time_vector_rejected(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        result = _mk_result("Lib.Bad")
        result.variables[0].time = np.array([0.0, float("nan"), 2.0])
        result.variables[0].values = np.array([0.0, 1.0, 2.0])
        with pytest.raises(ValueError, match="Lib.Bad"):
            store.store_reference(_mk_test("Lib.Bad"), result)

    def test_finite_trajectory_still_stores(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        assert store.store_reference(_mk_test("Lib.Good"), _mk_result("Lib.Good"))

    def test_accept_results_skips_poisoned_and_continues(
        self, sample_models_dir, tmp_path, caplog
    ):
        store = _mk_store(sample_models_dir, tmp_path)
        good, bad = _mk_test("Lib.Good"), _mk_test("Lib.Bad")
        results = {
            "Lib.Good": _mk_result("Lib.Good"),
            "Lib.Bad": _mk_result("Lib.Bad", [float("nan")] * 11),
        }
        with caplog.at_level(logging.WARNING):
            stored, skipped = store.accept_results([bad, good], results)
        assert stored == 1
        assert [m for m, _ in skipped] == ["Lib.Bad"]
        assert "non-finite" in skipped[0][1]
        assert store.get_reference("Lib.Good") is not None

    def test_accept_results_reports_sim_failures(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        t1, t2 = _mk_test("Lib.T1"), _mk_test("Lib.T2")
        results = {
            "Lib.T1": _mk_result("Lib.T1"),
            "Lib.T2": _mk_result("Lib.T2", success=False),
        }
        stored, skipped = store.accept_results([t1, t2], results)
        assert stored == 1
        assert skipped == [("Lib.T2", "simulation failed")]

    def test_accept_results_simulate_only_not_counted_as_skip(
        self, sample_models_dir, tmp_path
    ):
        store = _mk_store(sample_models_dir, tmp_path)
        sim_only = _mk_test("Lib.SimOnly", simulate_only=True)
        result = TestResult(model_id="Lib.SimOnly", success=True)
        stored, skipped = store.accept_results([sim_only], {"Lib.SimOnly": result})
        assert stored == 0
        assert skipped == []


# ---------------------------------------------------------------------------
# Finding 62 — interactive review exit code
# ---------------------------------------------------------------------------


@dataclass
class _FakeComp:
    model_id: str = "Lib.T"
    passed: bool = True
    sim_success: bool = True
    has_reference: bool = True
    warnings: list = field(default_factory=list)
    error_message: str | None = None
    variables: list = field(default_factory=list)
    test_id: int | None = None
    metric_tree: object = None


class _AcceptingStore:
    def store_reference(self, test, result):
        return True


def _feed_inputs(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


class TestInteractiveReviewExitCode:
    def test_skipping_all_failing_tests_exits_one(self, monkeypatch):
        _feed_inputs(monkeypatch, ["s"])
        rc = _interactive_review(
            [], {}, [_FakeComp("Lib.A", passed=False)], _AcceptingStore()
        )
        assert rc == 1

    def test_quit_on_green_suite_exits_zero(self, monkeypatch):
        _feed_inputs(monkeypatch, ["q"])
        comps = [_FakeComp("Lib.A"), _FakeComp("Lib.B")]
        rc = _interactive_review([], {}, comps, _AcceptingStore())
        assert rc == 0

    def test_completed_green_review_exits_zero(self, monkeypatch):
        _feed_inputs(monkeypatch, ["s", "s"])
        comps = [_FakeComp("Lib.A"), _FakeComp("Lib.B")]
        rc = _interactive_review([], {}, comps, _AcceptingStore())
        assert rc == 0

    def test_sim_failed_auto_skip_exits_one(self):
        comps = [_FakeComp("Lib.A", passed=False, sim_success=False)]
        rc = _interactive_review([], {}, comps, _AcceptingStore())
        assert rc == 1

    def test_accepting_the_failure_resolves_it(self, monkeypatch):
        test = _mk_test("Lib.A")
        result = _mk_result("Lib.A")
        _feed_inputs(monkeypatch, ["a"])
        rc = _interactive_review(
            [test], {"Lib.A": result}, [_FakeComp("Lib.A", passed=False)],
            _AcceptingStore(),
        )
        assert rc == 0

    def test_quit_after_skipping_a_failure_exits_one(self, monkeypatch):
        _feed_inputs(monkeypatch, ["s", "q"])
        comps = [_FakeComp("Lib.A", passed=False), _FakeComp("Lib.B", passed=False)]
        rc = _interactive_review([], {}, comps, _AcceptingStore())
        assert rc == 1

    def test_store_valueerror_does_not_crash_review(self, monkeypatch):
        """Leftover B interplay: a poisoned result raises in store_reference;
        the review loop must report it and continue, not traceback."""

        class _PoisonStore:
            def store_reference(self, test, result):
                raise ValueError("refusing to store baseline: non-finite values")

        test = _mk_test("Lib.A")
        result = _mk_result("Lib.A")
        _feed_inputs(monkeypatch, ["a"])
        rc = _interactive_review(
            [test], {"Lib.A": result}, [_FakeComp("Lib.A", passed=False)],
            _PoisonStore(),
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# Finding 63 — case-sensitive filter matching on every OS
# ---------------------------------------------------------------------------


def test_filter_matching_is_case_sensitive_even_if_fnmatch_is_not(monkeypatch):
    """Simulate Windows' case-insensitive fnmatch.fnmatch; _filter_tests must
    use fnmatchcase so both OS partitions select identical test sets."""
    import fnmatch as fnmatch_mod

    monkeypatch.setattr(
        fnmatch_mod,
        "fnmatch",
        lambda name, pat: fnmatch_mod.fnmatchcase(name.lower(), pat.lower()),
    )
    tests = [_mk_test("Lib.Foo")]
    assert _filter_tests(tests, pattern="lib.foo") == []
    assert len(_filter_tests(tests, pattern="Lib.Foo")) == 1


# ---------------------------------------------------------------------------
# Finding 64 — commas inside [] character classes are not separators
# ---------------------------------------------------------------------------


class TestFilterCommaSplit:
    def test_character_class_comma_survives(self):
        assert _resolve_filter_patterns("Test[A,B]*,Other.*") == [
            "Test[A,B]*",
            "Other.*",
        ]

    def test_plain_comma_split_unchanged(self):
        assert _resolve_filter_patterns("Foo.A, Foo.B") == ["Foo.A", "Foo.B"]

    def test_unclosed_bracket_is_one_pattern(self):
        assert _resolve_filter_patterns("Test[A,B") == ["Test[A,B"]

    def test_filter_tests_with_character_class(self):
        tests = [
            _mk_test("Lib.TestA1"),
            _mk_test("Lib.TestC1"),
            _mk_test("Other.X"),
        ]
        got = {
            t.model_id
            for t in _filter_tests(tests, pattern="Lib.Test[A,B]*,Other.*")
        }
        assert got == {"Lib.TestA1", "Other.X"}


# ---------------------------------------------------------------------------
# Finding 65 — --package respects the dot boundary
# ---------------------------------------------------------------------------


def test_package_filter_respects_dot_boundary():
    tests = [
        _mk_test("MyLib.Fluid"),
        _mk_test("MyLib.Fluid.PipeTest"),
        _mk_test("MyLib.FluidExperimental.Test"),
    ]
    got = {t.model_id for t in _filter_tests(tests, package="MyLib.Fluid")}
    assert got == {"MyLib.Fluid", "MyLib.Fluid.PipeTest"}


# ---------------------------------------------------------------------------
# Finding 66 — BatchManifest atomic save + corrupt-load quarantine
# ---------------------------------------------------------------------------


class TestBatchManifestRobustness:
    def test_corrupt_manifest_is_quarantined_not_fatal(self, tmp_path, caplog):
        p = tmp_path / "batch_manifest.json"
        p.write_text("{ definitely not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            bm = BatchManifest.load(p)
        assert bm.manifest == {}
        assert not p.exists()
        corrupt = tmp_path / "batch_manifest.json.corrupt"
        assert corrupt.exists()
        assert corrupt.read_text(encoding="utf-8").startswith("{ definitely")
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_wrong_shape_manifest_is_quarantined(self, tmp_path):
        p = tmp_path / "batch_manifest.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        bm = BatchManifest.load(p)
        assert bm.manifest == {}
        assert (tmp_path / "batch_manifest.json.corrupt").exists()

    def test_assign_test_keys_survives_corrupt_manifest(self, tmp_path):
        """The startup-crash regression: run/compare must proceed with fresh
        keys instead of dying on a torn manifest."""
        from dstf.simulators.base import assign_test_keys

        (tmp_path / "batch_manifest.json").write_text("garbage", encoding="utf-8")
        manifest_map, items = assign_test_keys(tmp_path, [_mk_test("Lib.T1")])
        assert [tk for _, tk in items] == ["test_0001"]
        assert manifest_map["test_0001"]["model_id"] == "Lib.T1"

    def test_save_round_trips_and_leaves_no_tmp_files(self, tmp_path):
        bm = BatchManifest(
            batch_id=0,
            work_dir=tmp_path,
            manifest={"test_0001": {"model_id": "Lib.T1", "ref_id": None}},
        )
        path = bm.save()
        assert BatchManifest.load(path).manifest["test_0001"]["model_id"] == "Lib.T1"
        assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Finding 67 — "Python" simulator backend name resolves
# ---------------------------------------------------------------------------


def test_python_backend_name_resolves():
    assert "Python" in SIMULATOR_BACKENDS
    assert _detect_backend("Python 3.12") == "Python"
    assert _detect_backend("Python") == "Python"


# ---------------------------------------------------------------------------
# Finding 71 — nobaseline overlays share the decimation budget
# ---------------------------------------------------------------------------


def test_nobaseline_overlays_are_decimated():
    from dstf.reporting.plot_comparison import _decimate_context_for_html

    big = [float(i) for i in range(5000)]
    context = {
        "nobaseline_trajectories": [
            {
                "name": "x",
                "time": list(big),
                "values": list(big),
                "overlays": [{"name": "sibling", "time": list(big), "values": list(big)}],
            }
        ]
    }
    _decimate_context_for_html(context, 100)
    traj = context["nobaseline_trajectories"][0]
    assert len(traj["time"]) <= 100
    overlay = traj["overlays"][0]
    assert len(overlay["time"]) <= 100
    assert len(overlay["values"]) == len(overlay["time"])


# ---------------------------------------------------------------------------
# Finding 72 — schema mode $defs are wired into the leaf spec
# ---------------------------------------------------------------------------

_MODE_DEF_NAMES = {
    "nrmse": "mode_nrmse",
    "tube": "mode_tube",
    "points": "mode_points",
    "range": "mode_range",
    "event-timing": "mode_event_timing",
    "dominant-frequency": "mode_dominant_frequency",
}


class TestSchemaModeRefs:
    def test_leaf_references_every_mode_def(self):
        from dstf.reporting.schema_export import build_schema

        leaf = build_schema()["$defs"]["leaf"]
        clauses = leaf.get("allOf", [])
        assert clauses, "leaf spec must carry if/then clauses wiring mode $defs"
        by_metric = {
            c["if"]["properties"]["metric"]["const"]: c["then"]["$ref"]
            for c in clauses
        }
        assert by_metric == {
            metric: f"#/$defs/{def_name}"
            for metric, def_name in _MODE_DEF_NAMES.items()
        }

    def test_mode_defs_allow_leaf_coresident_keys(self):
        from dstf.reporting.schema_export import build_schema

        defs = build_schema()["$defs"]
        for def_name in _MODE_DEF_NAMES.values():
            mode = defs[def_name]
            # Lenient posture: unknown keys must remain valid.
            assert mode.get("additionalProperties") is not False, def_name
            for co in ("metric", "variable", "against", "window", "label"):
                assert co in mode["properties"], f"{def_name} missing {co}"
        # Mode-specific knobs still present for autocomplete.
        assert "tube_rel" in defs["mode_tube"]["properties"]
        assert "min_value" in defs["mode_range"]["properties"]

    def test_valid_tube_leaf_validates_and_stays_lenient(self):
        # jsonschema is NOT a project dependency (checked 2026-07-06: absent
        # from the dev env) — skip the validation half there; run it via
        # `uv run --with jsonschema pytest -k schema` or any env that has it.
        jsonschema = pytest.importorskip("jsonschema")
        from dstf.reporting.schema_export import build_schema

        schema = build_schema()
        spec = {
            "tests": [
                {
                    "model": "MyLib.Test",
                    "variables": ["x"],
                    "metrics": {
                        "metric": "tube",
                        "variable": "x",
                        "tube_width_mode": "rel",
                        "tube_rel": 0.02,
                    },
                }
            ]
        }
        jsonschema.validate(spec, schema)
        # Documented lenient posture: unknown extra keys stay valid.
        spec["tests"][0]["metrics"]["future_knob"] = 123
        jsonschema.validate(spec, schema)
        # But the $refs are real: a type violation in a mode field rejects.
        bad = {
            "tests": [
                {
                    "model": "MyLib.Test",
                    "metrics": {"metric": "tube", "variable": "x", "tube_rel": "wide"},
                }
            ]
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


# ---------------------------------------------------------------------------
# Leftover A — ConfigError exits cleanly with code 2
# ---------------------------------------------------------------------------


class TestConfigErrorCleanExit:
    def test_missing_explicit_config_exits_two(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        rc = main(["--config", str(tmp_path / "nope" / "testing.json"), "discover"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error:" in err
        assert "Traceback" not in err

    def test_malformed_config_exits_two(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        bad = tmp_path / "testing.json"
        bad.write_text("{ nope", encoding="utf-8")
        rc = main(["--config", str(bad), "discover"])
        assert rc == 2
        assert "Malformed JSON" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Finding 60 — export --output takes a string path
# ---------------------------------------------------------------------------


class TestExportOutputPath:
    def test_json_export_to_string_path(self, tmp_config_dir, tmp_path):
        out = tmp_path / "exports" / "refs.json"
        rc = main(
            [
                "--config",
                str(tmp_config_dir),
                "export",
                "--format",
                "json",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        json.loads(out.read_text(encoding="utf-8"))

    def test_csv_export_to_string_path(self, tmp_config_dir, tmp_path):
        out = tmp_path / "exports" / "refs.csv"
        rc = main(
            [
                "--config",
                str(tmp_config_dir),
                "export",
                "--format",
                "csv",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
