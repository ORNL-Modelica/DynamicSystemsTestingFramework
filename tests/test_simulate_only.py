"""Tests for PTA.5 — simulate_only end-to-end."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from modelica_testing.comparison.comparator import compare_test
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.base import TestResult, VariableResult


def _make_test(simulate_only: bool) -> TestModel:
    return TestModel(
        model_id="My.Sim.Only",
        source_file=Path(""),
        source_package="My.Sim",
        short_name="Only",
        n_vars=1,
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
        comp = compare_test(_make_test(simulate_only=True),
                            _make_successful_result(), garbage_ref)
        assert comp.passed is True
        assert comp.variables == []
        assert comp.metric_tree is not None
        assert comp.metric_tree.label == "simulate-only"

    def test_fails_when_sim_fails(self):
        # Same path as any failed sim — error_message propagates, passed=False.
        comp = compare_test(_make_test(simulate_only=True),
                            _make_failed_result(), {})
        assert comp.passed is False
        assert comp.sim_success is False
        assert "boom" in (comp.error_message or "")

    def test_normal_path_when_simulate_only_false(self):
        # When simulate_only is False, the ordinary per-variable comparison
        # runs — empty reference would normally produce a failure (no ref var
        # for index 1). That's the contrast we want to confirm.
        comp = compare_test(_make_test(simulate_only=False),
                            _make_successful_result(),
                            {"test_id": "0001", "variables": []})
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
