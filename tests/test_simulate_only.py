"""Tests for PTA.5 — simulate_only end-to-end."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_test
from dstf.discovery.test_registry import TestModel


def _make_test(simulate_only: bool) -> TestModel:
    return TestModel(
        model_id="My.Sim.Only",
        source_file=Path(""),
        source_package="My.Sim",
        short_name="Only",
        n_vars=1,
        variable_patterns=["x"],
        simulate_only=simulate_only,
    )


def _make_successful_result() -> TestResult:
    t = np.linspace(0, 1, 11)
    return TestResult(
        model_id="My.Sim.Only",
        success=True,
        variables=[VariableResult(index=1, time=t, values=t * 2, name="x")],
    )


def _make_failed_result() -> TestResult:
    return TestResult(
        model_id="My.Sim.Only",
        success=False,
        error_message="boom",
        variables=[],
    )


class TestSimulateOnly:
    def test_passes_on_successful_sim_no_baseline_consulted(self):
        # Reference is intentionally garbage — simulate_only should ignore it.
        garbage_ref = {"test_id": "0001", "variables": [{"NONSENSE": True}]}
        comp = compare_test(
            _make_test(simulate_only=True), _make_successful_result(), garbage_ref
        )
        assert comp.passed is True
        assert comp.variables == []
        assert comp.metric_tree is not None
        assert comp.metric_tree.label == "simulate-only"

    def test_fails_when_sim_fails(self):
        # Same path as any failed sim — error_message propagates, passed=False.
        comp = compare_test(_make_test(simulate_only=True), _make_failed_result(), {})
        assert comp.passed is False
        assert comp.sim_success is False
        assert "boom" in (comp.error_message or "")

    def test_normal_path_when_simulate_only_false(self):
        # When simulate_only is False, the ordinary per-variable comparison
        # runs — empty reference would normally produce a failure (no ref var
        # for index 1). That's the contrast we want to confirm.
        comp = compare_test(
            _make_test(simulate_only=False),
            _make_successful_result(),
            {"test_id": "0001", "variables": []},
        )
        # Without simulate_only, the comparator falls into per-variable mode
        # and the missing ref-variable yields a failed VariableComparison.
        assert comp.passed is False
        assert comp.variables  # not empty

    def test_simulate_only_default_is_false(self):
        # PTA.4 added the field as an additive default — pre-PTA tests must
        # see no change.
        t = TestModel(
            model_id="X.Y",
            source_file=Path(""),
            source_package="X",
            short_name="Y",
            n_vars=1,
        )
        assert t.simulate_only is False

    def test_compare_all_simulate_only_without_baseline_passes(self):
        """Regression: SimulateOnlyTest on a fresh backend/OS pair has no
        stored reference, but it must still pass. Pre-fix, compare_all
        short-circuited to NO_REF for any test without a baseline; that
        collapsed simulate_only tests to NO_REF and later tripped the
        per-test template's sim_failed heuristic.
        """
        from dstf.comparison.comparator import compare_all

        class _FakeStore:
            def get_reference(self, _model_id):
                return None  # no baseline exists yet

            def get_soft_checks(self, _model_id):
                return {}

            def get_companions(self, _model_id):
                return {}

        test = _make_test(simulate_only=True)
        results = {test.model_id: _make_successful_result()}
        comps = compare_all([test], results, _FakeStore())

        assert len(comps) == 1
        c = comps[0]
        assert c.passed is True
        assert c.sim_success is True
        # has_reference stays False (the baseline really is missing) —
        # downstream renderers recognize simulate_only + passed and show
        # PASS regardless.
        assert c.has_reference is False
        assert c.metric_tree is not None
        assert c.metric_tree.label == "simulate-only"

    def test_compare_all_simulate_only_sim_failure_still_fails(self):
        """When sim itself fails, simulate_only follows the regular sim-fail
        path — FAIL, not PASS.
        """
        from dstf.comparison.comparator import compare_all

        class _FakeStore:
            def get_reference(self, _):
                return None

            def get_soft_checks(self, _):
                return {}

            def get_companions(self, _):
                return {}

        test = _make_test(simulate_only=True)
        results = {test.model_id: _make_failed_result()}
        comps = compare_all([test], results, _FakeStore())
        assert comps[0].passed is False
        assert comps[0].sim_success is False


def test_the_report_page_of_a_test_that_declares_nothing_says_simulate_only():
    """`dstf spec add <model>`'s entry: no comparison and no variable is what a
    simulate-only run looks like, not a failed simulation's banner."""
    from conftest import make_test_model
    from dstf.reporting.context_builder import _compute_summary_flags

    ran = TestResult(model_id="Lib.T", success=True, variables=[])
    declares_nothing = make_test_model()
    assert not declares_nothing.simulate_only
    assert _compute_summary_flags([], ran, declares_nothing) == (0, 0, True, False)
    tracks = make_test_model(variable_patterns=["x"])
    assert _compute_summary_flags([], ran, tracks) == (0, 0, False, True)


def test_the_report_page_names_what_its_two_counts_count(tmp_path):
    """`Tracked Variables | 3 current / 2 reference` set the test's
    CRITERIA beside the variables its baseline stores, as one quantity now
    and then. Each is named for what it is, and a count that is not there
    is no row."""
    from dstf.reporting.context_builder import _build_ref_info

    def rows(comparisons, ref_data) -> dict:
        info = _build_ref_info(comparisons, ref_data, None, None)
        return {row["label"]: row["value"] for row in info}

    three, reference = [object()] * 3, {"n_vars": 2}
    assert rows(three, reference) == {"Criteria": "3", "Reference Variables": "2"}
    assert rows(three, None) == {"Criteria": "3"}
    assert rows([], reference) == {"Reference Variables": "2"}
    assert rows([], {}) == {} and rows([], None) == {}
