"""Regression tests for the 2026-07-06 review — Theme 1: verdict integrity.

Decisions encoded here (see CODE_REVIEW_2026-07-06.md):

* "Nothing to compare" (empty window slice, empty baseline, empty actual)
  is a hard FAIL with a diagnostic — never a vacuous pass, never a crash.
* A declared checkpoint whose time-box lies outside the actual trajectory
  FAILS that point (a truncated simulation must not pass its tail checks).
* An explicit tolerance of 0 is honored, not replaced by a default.
* A failing test counts as FAILED on every report surface regardless of
  ``has_reference`` (baseline-free modes); NO_REF is reserved for tests
  where no comparison ran at all (``TestComparison.evaluated``).
* Reference variables pair to result variables by NAME, falling back to
  index only when names are unavailable.
* Declared dominant-frequency peaks each claim a distinct actual peak.
* Declared event matching finds a feasible assignment when one exists
  (no spurious FAIL from greedy declaration-order matching).
"""

from pathlib import Path

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.comparator import (
    _check_structural_changes,
    _test_is_baseline_free,
    compare_test,
)
from dstf.comparison.modes import resolve_mode
from dstf.comparison.types import TestComparison
from dstf.discovery.test_registry import TestModel
from dstf.simulators.base import TestResult, VariableResult


def _mk_test(model_id="MyLib.Test", **kw) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path("x.mo"),
        source_package="MyLib",
        short_name="Test",
        n_vars=1,
        **kw,
    )


def _mk_result(name="x", time=None, values=None, index=1) -> TestResult:
    time = np.linspace(0.0, 10.0, 11) if time is None else np.asarray(time, float)
    values = np.ones_like(time) if values is None else np.asarray(values, float)
    return TestResult(
        model_id="MyLib.Test",
        success=True,
        variables=[VariableResult(index=index, time=time, values=values, name=name)],
    )


EMPTY = np.array([])


# ---------------------------------------------------------------------------
# Empty data → hard fail (never vacuous pass, never crash)
# ---------------------------------------------------------------------------


class TestEmptyDataFails:
    def test_nrmse_empty_reference_fails(self):
        vc = _compare_trajectories(
            EMPTY, EMPTY, np.array([0.0, 1.0]), np.array([5.0, 5.0]), 1e-4
        )
        assert not vc.passed
        assert "error" in vc.diagnostics

    def test_nrmse_empty_actual_fails_without_crashing(self):
        vc = _compare_trajectories(
            np.array([0.0, 1.0]), np.array([1.0, 1.0]), EMPTY, EMPTY, 1e-4
        )
        assert not vc.passed

    def test_tube_empty_reference_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = _compare_tube(EMPTY, EMPTY, np.array([0.0, 1.0]), np.array([0.0, 0.0]), cfg)
        assert not vc.passed

    def test_tube_empty_actual_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = _compare_tube(np.array([0.0, 1.0]), np.array([1.0, 1.0]), EMPTY, EMPTY, cfg)
        assert not vc.passed

    def test_range_empty_actual_fails(self):
        vc = _compare_range(EMPTY, EMPTY, 0.0, 1.0)
        assert not vc.passed
        assert "error" in vc.diagnostics

    def test_windowed_leaf_excluding_everything_fails(self):
        """A window that slices away every sample must fail the leaf."""
        from dstf.comparison.tree_spec import parse_metric_tree

        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "window": {"start": 50.0, "end": 60.0},
            }
        )
        test = _mk_test(metric_tree_spec=spec)
        result = _mk_result()
        reference = {
            "variables": [
                {"index": 1, "name": "x", "time": [0.0, 5.0, 10.0], "values": [1.0, 1.0, 1.0]}
            ]
        }
        comp = compare_test(test, result, reference)
        assert not comp.passed


# ---------------------------------------------------------------------------
# Points mode: clipped checkpoints fail
# ---------------------------------------------------------------------------


class TestPointsClipping:
    def test_fully_clipped_point_fails(self):
        """Checkpoint at t=90 on a sim that died at t=40 must FAIL, not skip."""
        ref_t = np.linspace(0.0, 100.0, 101)
        ref_v = ref_t.copy()
        act_t = np.linspace(0.0, 40.0, 41)
        act_v = act_t.copy()
        points = [
            {"time": 20.0, "tolerance": 0.5},
            {"time": 90.0, "tolerance": 0.5},
        ]
        vc = _compare_points(ref_t, ref_v, act_t, act_v, points=points)
        assert not vc.passed
        assert vc.diagnostics.get("failed_points", 0) >= 1

    def test_empty_actual_with_declared_points_fails(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        vc = _compare_points(ref_t, ref_t, EMPTY, EMPTY, points=[{"time": 20.0}])
        assert not vc.passed

    def test_unclipped_points_still_pass(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        act_t = np.linspace(0.0, 100.0, 201)
        vc = _compare_points(
            ref_t, ref_t, act_t, act_t, points=[{"time": 20.0, "tolerance": 0.5}]
        )
        assert vc.passed

    def test_ref_relative_point_beyond_reference_end_fails(self):
        """np.interp would clamp to ref_final — that target is untrustworthy."""
        ref_t = np.linspace(0.0, 40.0, 41)
        ref_v = ref_t.copy()  # still rising at truncation
        act_t = np.linspace(0.0, 100.0, 101)
        act_v = act_t.copy()
        vc = _compare_points(
            ref_t, ref_v, act_t, act_v, points=[{"time": 90.0, "tolerance": 0.5}]
        )
        assert not vc.passed


# ---------------------------------------------------------------------------
# Dominant frequency: declared peaks claim distinct actual peaks
# ---------------------------------------------------------------------------


class TestDominantFrequencyClaiming:
    def test_two_declared_peaks_cannot_share_one_actual_peak(self):
        t = np.linspace(0.0, 100.0, 4096)
        v = np.sin(2 * np.pi * 1.0 * t)
        peaks = [
            {"freq": 0.9, "tolerance": 0.2, "tolerance_mode": "abs"},
            {"freq": 1.1, "tolerance": 0.2, "tolerance_mode": "abs"},
        ]
        vc = _compare_dominant_frequency(EMPTY, EMPTY, t, v, peaks=peaks)
        assert not vc.passed

    def test_two_distinct_peaks_both_match(self):
        t = np.linspace(0.0, 100.0, 4096)
        v = np.sin(2 * np.pi * 0.9 * t) + np.sin(2 * np.pi * 1.1 * t)
        peaks = [
            {"freq": 0.9, "tolerance": 0.05, "tolerance_mode": "abs"},
            {"freq": 1.1, "tolerance": 0.05, "tolerance_mode": "abs"},
        ]
        vc = _compare_dominant_frequency(EMPTY, EMPTY, t, v, peaks=peaks)
        assert vc.passed


# ---------------------------------------------------------------------------
# Event timing: feasible assignment must pass
# ---------------------------------------------------------------------------


class TestEventAssignment:
    def test_alternate_assignment_found(self):
        """declared 1.0→act 0.96 / declared 1.02→act 1.01 satisfies both
        tolerances; nearest-first greedy would steal 1.01 for declared 1.0."""
        act_t = np.array([0.0, 0.96, 0.96, 1.01, 1.01, 2.0])
        declared = [
            {"time": 1.0, "tolerance": 0.05},
            {"time": 1.02, "tolerance": 0.05},
        ]
        vc = _compare_event_timing(EMPTY, act_t, declared_events=declared)
        assert vc.passed

    def test_truly_unmatchable_still_fails(self):
        act_t = np.array([0.0, 0.5, 0.5, 2.0])
        declared = [
            {"time": 1.0, "tolerance": 0.05},
            {"time": 1.02, "tolerance": 0.05},
        ]
        vc = _compare_event_timing(EMPTY, act_t, declared_events=declared)
        assert not vc.passed


# ---------------------------------------------------------------------------
# Tolerance resolution: explicit zero honored
# ---------------------------------------------------------------------------


class TestExplicitZeroTolerance:
    def test_zero_test_tolerance_fails_nonzero_error(self):
        test = _mk_test(comparison_tolerance=0.0)
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ]
        }
        comp = compare_test(test, result, reference)
        assert not comp.passed
        assert comp.variables[0].tolerance_used == 0.0

    def test_zero_reference_tolerance_honored(self):
        test = _mk_test()
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "comparison": {"tolerance": 0.0},
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ],
        }
        comp = compare_test(test, result, reference)
        assert not comp.passed


# ---------------------------------------------------------------------------
# Baseline-free classification
# ---------------------------------------------------------------------------


class TestBaselineFreeClassification:
    def test_mixed_test_is_not_baseline_free(self):
        """A tracked variable without an override needs a baseline."""
        test = _mk_test(
            variable_patterns=["a", "b"],
            variable_overrides={
                "a": {"mode": "range", "min_value": -10, "max_value": 10}
            },
        )
        assert _test_is_baseline_free(test, 1e-4) is False

    def test_fully_covered_range_test_is_baseline_free(self):
        test = _mk_test(
            variable_patterns=["a", "b"],
            variable_overrides={
                "a": {"mode": "range", "min_value": -10, "max_value": 10},
                "b": {"mode": "range", "min_value": 0, "max_value": 1},
            },
        )
        assert _test_is_baseline_free(test, 1e-4) is True


# ---------------------------------------------------------------------------
# Structural warnings: no phantoms when statistics are absent
# ---------------------------------------------------------------------------


class TestStructuralWarnings:
    def test_no_phantom_warnings_without_reference_statistics(self):
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            statistics={
                "translation": {"continuous_time_states": 5},
                "EventCounter": 3,
            },
        )
        assert _check_structural_changes({}, result) == []


# ---------------------------------------------------------------------------
# Name-based reference pairing
# ---------------------------------------------------------------------------


class TestNameBasedPairing:
    def test_reference_pairs_by_name_after_spec_reorder(self):
        """Baseline stored with [a, b]; 'a' later removed from the spec.
        'b' (now index 1) must score against ref 'b', not ref 'a'."""
        reference = {
            "variables": [
                {"index": 1, "name": "a", "time": [0.0, 1.0], "values": [0.0, 0.0]},
                {"index": 2, "name": "b", "time": [0.0, 1.0], "values": [10.0, 10.0]},
            ]
        }
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    time=np.array([0.0, 1.0]),
                    values=np.array([10.0, 10.0]),
                    name="b",
                )
            ],
        )
        comp = compare_test(_mk_test(), result, reference)
        assert comp.passed

    def test_unnamed_variable_falls_back_to_index(self):
        reference = {
            "variables": [
                {"index": 1, "name": "a", "time": [0.0, 1.0], "values": [1.0, 1.0]},
            ]
        }
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    time=np.array([0.0, 1.0]),
                    values=np.array([1.0, 1.0]),
                    name="",
                )
            ],
        )
        comp = compare_test(_mk_test(), result, reference)
        assert comp.passed


# ---------------------------------------------------------------------------
# Unknown comparison mode: loud, not silent NRMSE
# ---------------------------------------------------------------------------


class TestUnknownMode:
    def test_resolve_mode_raises_on_unknown_mode(self):
        with pytest.raises(ValueError, match="[Uu]nknown.*mode"):
            resolve_mode({"mode": "Tube"}, 0.01)

    def test_typo_mode_fails_variable_not_silently_nrmse(self):
        test = _mk_test(variable_overrides={"x": {"mode": "Tube", "tube_abs": 5.0}})
        result = _mk_result()
        reference = {
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ]
        }
        comp = compare_test(test, result, reference)
        assert not comp.passed


# ---------------------------------------------------------------------------
# Report surfaces: failing baseline-free tests are FAILURES
# ---------------------------------------------------------------------------


def _failing_baseline_free_comp() -> TestComparison:
    from dstf.comparison.metric_tree import MetricResult

    return TestComparison(
        model_id="MyLib.RangeTest",
        passed=False,
        sim_success=True,
        has_reference=False,
        metric_tree=MetricResult(passed=False, score=1.0, label="range"),
    )


def _no_ref_comp() -> TestComparison:
    return TestComparison(
        model_id="MyLib.FreshTest",
        passed=True,
        has_reference=False,
        error_message="No reference baseline stored",
    )


class TestReportClassification:
    def test_console_exit_code_nonzero_for_failing_baseline_free(self, capsys):
        from dstf.reporting.console_report import print_report

        rc = print_report([_failing_baseline_free_comp()])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_console_no_ref_still_exit_zero(self, capsys):
        from dstf.reporting.console_report import print_report

        rc = print_report([_no_ref_comp()])
        assert rc == 0
        assert "NO_REF" in capsys.readouterr().out

    def test_junit_failing_baseline_free_is_failure(self, tmp_path):
        import xml.etree.ElementTree as ET

        from dstf.reporting.junit_report import generate_junit_report

        out = tmp_path / "junit.xml"
        generate_junit_report([_failing_baseline_free_comp(), _no_ref_comp()], out)
        root = ET.parse(out).getroot()
        assert root.get("failures") == "1"
        cases = {tc.get("name"): tc for tc in root.iter("testcase")}
        assert cases["MyLib.RangeTest"].find("failure") is not None
        assert cases["MyLib.RangeTest"].find("skipped") is None
        assert cases["MyLib.FreshTest"].find("skipped") is not None

    def test_should_review_failed_matches_baseline_free_failure(self):
        from dstf.cli import _should_review

        assert _should_review(_failing_baseline_free_comp(), {"failed"}) is True
        assert _should_review(_no_ref_comp(), {"failed"}) is False
