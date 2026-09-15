"""compare --accept (accept without re-simulating) + the run-verdict guard
that keeps a partial/stale .mat from becoming a baseline.

2026-07-08: `compare` reads results from disk, so a test the run recorded as
failed/timed_out could read back as success just because a partial `.mat`
survived — letting `compare`/`compare --accept` bless garbage. The guard was
a status.json cross-check.

C-v round 1 (D93 follow-on): the verdict is now persisted per test in the
batch manifest (`last_run_status`), which is what `read_last_results` reads.
The status.json cross-check survives ONLY as the fallback for manifest
entries written before the field existed — old data, not old code.
"""

from __future__ import annotations

import json

from dstf.backends.base import _apply_run_verdicts
from dstf.backends.types import TestResult, TestRunResult
from dstf.storage.batch_manifest import BatchManifest


def _write_status(work_dir, rows):
    (work_dir / "status.json").write_text(json.dumps({"tests": rows}), encoding="utf-8")


class TestApplyStatusVerdicts:
    def test_failed_status_forces_success_false(self, tmp_path):
        _write_status(
            tmp_path,
            [
                {"model_id": "Lib.A", "status": "failed"},
                {"model_id": "Lib.B", "status": "passed"},
            ],
        )
        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True),
            "Lib.B": TestResult(model_id="Lib.B", success=True),
        }
        _apply_run_verdicts(tmp_path, [], results)
        assert results["Lib.A"].success is False  # partial .mat overridden
        assert results["Lib.A"].error_message  # reason stamped
        assert results["Lib.B"].success is True  # genuine pass untouched

    def test_timed_out_status_forces_success_false(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.T", "status": "timed_out"}])
        results = {"Lib.T": TestResult(model_id="Lib.T", success=True)}
        _apply_run_verdicts(tmp_path, [], results)
        assert results["Lib.T"].success is False

    def test_existing_error_message_preserved(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.A", "status": "failed"}])
        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True, error_message="boom")
        }
        _apply_run_verdicts(tmp_path, [], results)
        assert results["Lib.A"].error_message == "boom"

    def test_missing_status_json_is_noop(self, tmp_path):
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [], results)  # no status.json written
        assert results["Lib.A"].success is True

    def test_unreadable_status_json_is_noop(self, tmp_path):
        (tmp_path / "status.json").write_text("{ not json", encoding="utf-8")
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [], results)
        assert results["Lib.A"].success is True

    def test_model_not_in_results_is_ignored(self, tmp_path):
        _write_status(tmp_path, [{"model_id": "Lib.Gone", "status": "failed"}])
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [], results)  # must not KeyError
        assert results["Lib.A"].success is True


# =========================================================================
# C-v round 1, item 7 — the per-test outcome lives in the batch manifest.
# =========================================================================


def _manifest(tmp_path, entries) -> BatchManifest:
    return BatchManifest(work_dir=tmp_path, manifest=entries)


def _rr(model_id, *, success=True, timed_out=False) -> TestRunResult:
    return TestRunResult(
        model_id=model_id,
        test_key="test_0001",
        success=success,
        timed_out=timed_out,
    )


class TestManifestRecordsRunOutcome:
    def test_round_trip_through_the_saved_manifest(self, tmp_path):
        """The writer always writes the field, and it survives save/load —
        that is the whole point: `compare` runs in a later process."""
        m = _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.Ok", "ref_id": None},
                "test_0002": {"model_id": "Lib.Bad", "ref_id": None},
                "test_0003": {"model_id": "Lib.Slow", "ref_id": None},
            },
        )
        for rr in (
            _rr("Lib.Ok"),
            _rr("Lib.Bad", success=False),
            _rr("Lib.Slow", success=False, timed_out=True),
        ):
            m.record_result(rr)

        reloaded = BatchManifest.load(tmp_path / "batch_manifest.json").manifest
        assert reloaded["test_0001"]["last_run_status"] == "passed"
        assert reloaded["test_0002"]["last_run_status"] == "failed"
        assert reloaded["test_0003"]["last_run_status"] == "timed_out"

    def test_entries_without_a_result_this_run_keep_their_prior_status(self, tmp_path):
        """--merge / --rerun report over tests this invocation didn't run;
        their last recorded verdict must not be erased."""
        m = _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.A", "last_run_status": "failed"},
                "test_0002": {"model_id": "Lib.B", "ref_id": None},
            },
        )
        m.record_result(_rr("Lib.B"))

        assert m.manifest["test_0001"]["last_run_status"] == "failed"
        assert m.manifest["test_0002"]["last_run_status"] == "passed"


class TestApplyRunVerdicts:
    def test_manifest_verdict_forces_sim_fail_on_compare(self, tmp_path):
        """The headline consequence: a compare over a work dir whose manifest
        says the run failed reports SIM_FAIL rather than comparing against
        whatever stale .mat is on disk."""
        m = _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.A", "last_run_status": "failed"},
                "test_0002": {"model_id": "Lib.B", "last_run_status": "timed_out"},
                "test_0003": {"model_id": "Lib.C", "last_run_status": "passed"},
            },
        )
        results = {
            k: TestResult(model_id=k, success=True) for k in ("Lib.A", "Lib.B", "Lib.C")
        }
        _apply_run_verdicts(tmp_path, [m], results)

        assert results["Lib.A"].success is False
        assert "failed" in (results["Lib.A"].error_message or "")
        assert results["Lib.B"].success is False
        assert "timed_out" in (results["Lib.B"].error_message or "")
        assert results["Lib.C"].success is True

    def test_manifest_verdict_wins_over_a_stale_status_json(self, tmp_path):
        """status.json is a single snapshot of the LAST invocation; the
        manifest is per test. After a --filter rerun of one test, the
        snapshot no longer describes the others — the manifest does."""
        _write_status(tmp_path, [{"model_id": "Lib.A", "status": "failed"}])
        m = _manifest(
            tmp_path, {"test_0001": {"model_id": "Lib.A", "last_run_status": "passed"}}
        )
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [m], results)
        assert results["Lib.A"].success is True

    def test_old_manifest_without_the_field_falls_back_to_status_json(self, tmp_path):
        """Old data, not old code: a work dir written before this change has
        no last_run_status, and must still be guarded."""
        _write_status(
            tmp_path,
            [
                {"model_id": "Lib.Old", "status": "failed"},
                {"model_id": "Lib.New", "status": "failed"},
            ],
        )
        m = _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.Old", "ref_id": None},  # pre-field
                "test_0002": {"model_id": "Lib.New", "last_run_status": "passed"},
            },
        )
        results = {
            "Lib.Old": TestResult(model_id="Lib.Old", success=True),
            "Lib.New": TestResult(model_id="Lib.New", success=True),
        }
        _apply_run_verdicts(tmp_path, [m], results)

        assert results["Lib.Old"].success is False  # status.json fallback applied
        assert results["Lib.New"].success is True  # manifest is authoritative

    def test_no_manifest_field_and_no_status_json_keeps_the_disk_read(self, tmp_path):
        m = _manifest(tmp_path, {"test_0001": {"model_id": "Lib.A", "ref_id": None}})
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [m], results)
        assert results["Lib.A"].success is True


def test_compare_accept_still_refuses_a_test_whose_last_run_failed(tmp_path):
    """End of the chain: accept_results skips non-success results, so the
    manifest verdict is what keeps a failed run out of the baselines."""
    from dstf.config import Config
    from dstf.storage.reference_store import ReferenceStore

    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib end FakeLib;\n")
    config = Config(source_path=lib, reference_root=tmp_path / "refs")

    m = _manifest(
        tmp_path, {"test_0001": {"model_id": "FakeLib.A", "last_run_status": "failed"}}
    )
    results = {"FakeLib.A": TestResult(model_id="FakeLib.A", success=True)}
    _apply_run_verdicts(tmp_path, [m], results)

    from dstf.discovery.test_registry import TestModel

    test = TestModel(
        model_id="FakeLib.A",
        source_file=lib / "A.mo",
        source_package="FakeLib",
        short_name="A",
        n_vars=1,
        variable_patterns=["x"],
        stop_time=1.0,
        tolerance=1e-6,
        method="Dassl",
    )
    stored, skipped = ReferenceStore(config).accept_results([test], results)
    assert stored == 0
    assert [m_id for m_id, _reason in skipped] == ["FakeLib.A"]


# =========================================================================
# Review K1 — the abort window.
#
# assign_test_keys carries every prior manifest entry forward, and the
# manifest is saved at run START. So run 2's manifest opened holding run 1's
# "passed". If run 2 then failed a test and was interrupted before any
# epilogue, the reader would see "passed", treat the entry as authoritative,
# skip the status.json fallback, and report green over a partial .mat —
# a window the pre-manifest code did not have.
#
# Two independent defenses, both pinned here:
#   (a) mark_pending() clears this run's scope at start, so an unrecorded
#       entry reads as "pending" → NOT authoritative → status.json fallback;
#   (b) record_result() persists each verdict the moment it lands, so an
#       abort keeps the verdicts the run actually reached.
# =========================================================================


class TestAbortWindow:
    def _run_one_manifest(self, tmp_path):
        """Run 1: both tests passed, recorded and saved."""
        m = _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.A", "ref_id": None},
                "test_0002": {"model_id": "Lib.B", "ref_id": None},
            },
        )
        m.record_result(_rr("Lib.A"))
        m.record_result(_rr("Lib.B"))
        return m

    def test_run_start_clears_the_previous_verdict_for_this_run_scope(self, tmp_path):
        """Half (a): a carried-forward "passed" must not survive into the
        next run's manifest for a test that run is about to re-run."""
        self._run_one_manifest(tmp_path)

        # Run 2 opens the manifest and stamps its scope pending.
        m2 = BatchManifest.load(tmp_path / "batch_manifest.json")
        m2.mark_pending({"Lib.A", "Lib.B"})
        m2.save()

        reloaded = BatchManifest.load(tmp_path / "batch_manifest.json").manifest
        assert reloaded["test_0001"]["last_run_status"] == "pending"
        assert reloaded["test_0002"]["last_run_status"] == "pending"

    def test_out_of_scope_entries_keep_their_prior_verdict(self, tmp_path):
        """mark_pending is scoped: --filter/--rerun runs must not blank the
        record for tests they aren't touching."""
        m = self._run_one_manifest(tmp_path)
        m.mark_pending({"Lib.A"})
        assert m.manifest["test_0001"]["last_run_status"] == "pending"
        assert m.manifest["test_0002"]["last_run_status"] == "passed"

    def test_abort_after_a_failure_landed_keeps_the_failure(self, tmp_path):
        """The reviewer's exact scenario. Run 1 passed; run 2 fails Lib.A and
        is interrupted before any epilogue. With incremental recording the
        field says "failed", so compare reports SIM_FAIL on its own."""
        self._run_one_manifest(tmp_path)

        m2 = BatchManifest.load(tmp_path / "batch_manifest.json")
        m2.mark_pending({"Lib.A", "Lib.B"})
        m2.save()
        m2.record_result(_rr("Lib.A", success=False))
        # ...and then the process dies. No epilogue of any kind runs.

        # A fresh reader (the later `compare` process) sees only what is on disk.
        loaded = BatchManifest.load(tmp_path / "batch_manifest.json")
        assert loaded.manifest["test_0001"]["last_run_status"] == "failed"
        assert loaded.manifest["test_0002"]["last_run_status"] == "pending"

        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True),  # stale .mat
            "Lib.B": TestResult(model_id="Lib.B", success=True),
        }
        _apply_run_verdicts(tmp_path, [loaded], results)
        assert results["Lib.A"].success is False

    def test_abort_before_any_result_falls_back_to_status_json(self, tmp_path):
        """Half (a) alone, with no verdict recorded at all: "pending" must
        NOT be treated as authoritative, so the status.json cross-check still
        gets its say. Without the reset this entry would say "passed"."""
        self._run_one_manifest(tmp_path)

        m2 = BatchManifest.load(tmp_path / "batch_manifest.json")
        m2.mark_pending({"Lib.A", "Lib.B"})
        m2.save()
        # Run 2 died before recording anything, but the progress reporter had
        # already written Lib.A's failure into status.json.
        _write_status(tmp_path, [{"model_id": "Lib.A", "status": "failed"}])

        loaded = BatchManifest.load(tmp_path / "batch_manifest.json")
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [loaded], results)
        assert results["Lib.A"].success is False, (
            "a pending entry must fall through to the status.json fallback"
        )

    def test_pending_with_no_status_json_keeps_the_disk_read(self, tmp_path):
        """Nothing known either way → the pre-existing behaviour, unchanged."""
        m = _manifest(
            tmp_path, {"test_0001": {"model_id": "Lib.A", "last_run_status": "pending"}}
        )
        results = {"Lib.A": TestResult(model_id="Lib.A", success=True)}
        _apply_run_verdicts(tmp_path, [m], results)
        assert results["Lib.A"].success is True

    def test_happy_path_is_unchanged(self, tmp_path):
        """A run that completes normally still ends with real verdicts and no
        pending left behind."""
        m = self._run_one_manifest(tmp_path)
        m.mark_pending({"Lib.A", "Lib.B"})
        m.save()
        m.record_result(_rr("Lib.A"))
        m.record_result(_rr("Lib.B", success=False, timed_out=True))

        reloaded = BatchManifest.load(tmp_path / "batch_manifest.json").manifest
        assert reloaded["test_0001"]["last_run_status"] == "passed"
        assert reloaded["test_0002"]["last_run_status"] == "timed_out"

        results = {
            "Lib.A": TestResult(model_id="Lib.A", success=True),
            "Lib.B": TestResult(model_id="Lib.B", success=True),
        }
        _apply_run_verdicts(tmp_path, [_manifest(tmp_path, reloaded)], results)
        assert results["Lib.A"].success is True
        assert results["Lib.B"].success is False


# =========================================================================
# Review L2 — `compare --accept` refuses a pending entry.
#
# The in-flight-at-abort test has manifest "pending" and status.json
# "running": no FAILED_RUN_STATUS anywhere forces it down, so if its
# truncated .mat happens to parse it would be blessed as a baseline.
# Policy: acceptance requires an authoritative SUCCESSFUL run.
# =========================================================================


class TestUnfinishedRunModels:
    def test_pending_entries_are_reported(self, tmp_path):
        from dstf.storage.batch_manifest import unfinished_run_models

        _manifest(
            tmp_path,
            {
                "test_0001": {"model_id": "Lib.Done", "last_run_status": "passed"},
                "test_0002": {"model_id": "Lib.InFlight", "last_run_status": "pending"},
                "test_0003": {"model_id": "Lib.Failed", "last_run_status": "failed"},
            },
        ).save()

        assert unfinished_run_models(tmp_path) == {"Lib.InFlight"}

    def test_field_less_entries_are_not_reported(self, tmp_path):
        """Old data, not old code: a manifest predating the field says nothing
        about completeness, so it must not be treated as unfinished — those
        keep today's status.json-guard behaviour."""
        from dstf.storage.batch_manifest import unfinished_run_models

        _manifest(
            tmp_path, {"test_0001": {"model_id": "Lib.Old", "ref_id": None}}
        ).save()
        assert unfinished_run_models(tmp_path) == set()

    def test_no_manifest_is_empty(self, tmp_path):
        from dstf.storage.batch_manifest import unfinished_run_models

        assert unfinished_run_models(tmp_path) == set()


def _accept_fixture(tmp_path, manifest_entries, status_rows=None):
    """A library + reference root + work dir wired for `compare --accept`."""
    from dstf.config import Config
    from dstf.discovery.test_registry import TestModel

    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib end FakeLib;\n")
    work = tmp_path / "work"
    work.mkdir()
    _manifest(work, manifest_entries).save()
    # A spec so `compare` can DISCOVER these models — the CLI-level test
    # drives the real cmd_compare, whose scope comes from discovery.
    (tmp_path / "test_spec.json").write_text(
        json.dumps(
            {
                "tests": [
                    {"model": e["model_id"], "variables": ["x"]}
                    for e in manifest_entries.values()
                ]
            }
        ),
        encoding="utf-8",
    )
    if status_rows is not None:
        _write_status(work, status_rows)

    config = Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=work)
    tests = [
        TestModel(
            model_id=entry["model_id"],
            source_file=lib / "X.mo",
            source_package="FakeLib",
            short_name=entry["model_id"].rsplit(".", 1)[-1],
            n_vars=1,
            variable_patterns=["x"],
            stop_time=1.0,
            tolerance=1e-6,
            method="Dassl",
        )
        for entry in manifest_entries.values()
    ]
    return config, tests


def _good_result(model_id):
    """A result that reads back as a clean success — i.e. a .mat that parses.
    The whole point of L2 is that this is not enough to accept."""
    import numpy as np

    from dstf.backends.types import VariableResult

    return TestResult(
        model_id=model_id,
        success=True,
        variables=[
            VariableResult(
                index=1,
                name="x",
                time=np.array([0.0, 1.0]),
                values=np.array([0.0, 1.0]),
            ),
        ],
    )


class TestAcceptRefusesPending:
    def _accept(self, config, tests, results):
        """Drive the same refusal cmd_compare's accept branch applies."""
        from dstf.storage.accept import accept_run_results
        from dstf.storage.reference_store import ReferenceStore

        return accept_run_results(
            ReferenceStore(config), tests, results, config.work_dir
        )

    def test_pending_plus_running_is_refused(self, tmp_path):
        """The reviewer's case: nothing marks it failed, its .mat parses, and
        it must still not become a baseline."""
        config, tests = _accept_fixture(
            tmp_path,
            {
                "test_0001": {
                    "model_id": "FakeLib.InFlight",
                    "last_run_status": "pending",
                }
            },
            status_rows=[{"model_id": "FakeLib.InFlight", "status": "running"}],
        )
        results = {"FakeLib.InFlight": _good_result("FakeLib.InFlight")}
        _apply_run_verdicts(config.work_dir, [], results)
        assert results["FakeLib.InFlight"].success is True  # nothing forced it down

        stored, skipped = self._accept(config, tests, results)

        assert stored == 0
        assert [m for m, _ in skipped] == ["FakeLib.InFlight"]
        assert "re-run before accepting" in skipped[0][1]

    def test_the_refusal_message_names_the_aborted_run(self, tmp_path, capsys):
        """Through the real CLI, so the printed line is what a user sees."""
        from dstf.cli import main

        config, _tests = _accept_fixture(
            tmp_path,
            {
                "test_0001": {
                    "model_id": "FakeLib.InFlight",
                    "last_run_status": "pending",
                }
            },
            status_rows=[{"model_id": "FakeLib.InFlight", "status": "running"}],
        )
        rc = main(
            [
                "--source-path",
                str(config.source_path),
                "--reference-root",
                str(config.reference_root),
                "--test-spec",
                str(tmp_path / "test_spec.json"),
                "compare",
                "--work-dir",
                str(config.work_dir),
                "--accept",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1, "a partial accept must exit nonzero"
        assert "last run did not complete" in out
        assert "re-run before accepting" in out
        assert str(config.work_dir) in out

    def test_a_run_that_passed_but_compares_failed_is_still_acceptable(self, tmp_path):
        """Re-baselining drift is exactly what --accept is for: the refusal is
        keyed on the RUN's verdict, never on the comparison's."""
        config, tests = _accept_fixture(
            tmp_path,
            {"test_0001": {"model_id": "FakeLib.Drifted", "last_run_status": "passed"}},
        )
        results = {"FakeLib.Drifted": _good_result("FakeLib.Drifted")}
        _apply_run_verdicts(config.work_dir, [], results)

        stored, skipped = self._accept(config, tests, results)

        assert stored == 1
        assert skipped == []

    def test_an_old_field_less_manifest_is_unchanged(self, tmp_path):
        """Old data tolerated: no field means no pending refusal, and the
        status.json guard alone decides as it did before."""
        config, tests = _accept_fixture(
            tmp_path,
            {"test_0001": {"model_id": "FakeLib.Old", "ref_id": None}},
            status_rows=[{"model_id": "FakeLib.Old", "status": "passed"}],
        )
        results = {"FakeLib.Old": _good_result("FakeLib.Old")}

        stored, skipped = self._accept(config, tests, results)

        assert skipped == []
        assert stored == 1

    def test_an_old_field_less_manifest_still_honors_a_failed_status(self, tmp_path):
        config, tests = _accept_fixture(
            tmp_path,
            {"test_0001": {"model_id": "FakeLib.Old", "ref_id": None}},
            status_rows=[{"model_id": "FakeLib.Old", "status": "failed"}],
        )
        results = {"FakeLib.Old": _good_result("FakeLib.Old")}
        _apply_run_verdicts(
            config.work_dir,
            [BatchManifest.load(config.work_dir / "batch_manifest.json")],
            results,
        )

        stored, skipped = self._accept(config, tests, results)

        assert stored == 0
        assert [m for m, _r in skipped] == ["FakeLib.Old"]
