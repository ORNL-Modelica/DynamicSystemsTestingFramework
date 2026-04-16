"""Tests for 4.C — event-timing + dominant-frequency leaf metrics."""

from __future__ import annotations

import numpy as np
import pytest

from modelica_testing.comparison.modes import (
    DominantFrequencyConfig,
    DominantFrequencyMode,
    EventTimingConfig,
    EventTimingMode,
    resolve_mode,
)


class TestEventTimingMode:
    def test_passes_when_events_align(self):
        # Both signals: events at t=1.0 and t=2.0 (duplicate-time markers)
        ref_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5])
        act_t = np.array([0.0, 0.5, 1.0001, 1.0001, 1.5, 2.0001, 2.0001, 2.5])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(time_tolerance=1e-2))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["counts_match"] is True
        assert result.diagnostics["max_time_delta"] < 1e-2

    def test_fails_when_event_count_differs(self):
        ref_t = np.array([0.0, 1.0, 1.0, 2.0])  # 1 event
        act_t = np.array([0.0, 1.0, 1.0, 1.5, 1.5, 2.0])  # 2 events
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(time_tolerance=1e-2))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["ref_event_count"] == 1
        assert result.diagnostics["act_event_count"] == 2

    def test_fails_when_event_drift_exceeds_tolerance(self):
        ref_t = np.array([0.0, 1.0, 1.0, 2.0])
        act_t = np.array([0.0, 1.5, 1.5, 2.0])  # event 0.5 late
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(time_tolerance=1e-2))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["max_time_delta"] == pytest.approx(0.5)

    def test_resolves_via_factory(self):
        mode = resolve_mode(
            {"mode": "event-timing", "time_tolerance": 5e-3}, tolerance=1e-4,
        )
        assert isinstance(mode, EventTimingMode)
        assert mode.config.time_tolerance == 5e-3


class TestDominantFrequencyMode:
    def _sine(self, freq: float, n: int = 256, t_end: float = 1.0):
        t = np.linspace(0.0, t_end, n)
        return t, np.sin(2 * np.pi * freq * t)

    def test_passes_when_frequencies_match(self):
        ref_t, ref_v = self._sine(5.0)
        act_t, act_v = self._sine(5.0)
        mode = DominantFrequencyMode(DominantFrequencyConfig(rel_tolerance=0.05))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["ref_dominant_hz"] == pytest.approx(5.0, rel=0.1)
        assert result.diagnostics["act_dominant_hz"] == pytest.approx(5.0, rel=0.1)

    def test_fails_on_significant_shift(self):
        ref_t, ref_v = self._sine(5.0)
        act_t, act_v = self._sine(7.0)  # 40% higher
        mode = DominantFrequencyMode(DominantFrequencyConfig(rel_tolerance=0.1))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed

    def test_too_short_signal_handled_gracefully(self):
        ref_t = np.array([0.0, 0.1])
        ref_v = np.array([0.0, 1.0])
        mode = DominantFrequencyMode(DominantFrequencyConfig())
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        assert not result.passed
        assert "too short" in result.diagnostics.get("reason", "")

    def test_resolves_via_factory(self):
        mode = resolve_mode(
            {"mode": "dominant-frequency", "rel_tolerance": 0.02}, tolerance=1e-4,
        )
        assert isinstance(mode, DominantFrequencyMode)
        assert mode.config.rel_tolerance == 0.02


class TestMetricsAcceptedInTreeSpec:
    """Verify both new metrics are accepted in MetricTree leaf specs (4.C)."""

    def test_event_timing_leaf_parses(self):
        from modelica_testing.comparison.tree_spec import parse_metric_tree
        spec = parse_metric_tree({
            "metric": "event-timing",
            "variable": "evt",
            "time_tolerance": 1e-3,
        })
        assert spec.metric == "event-timing"

    def test_dominant_frequency_leaf_parses(self):
        from modelica_testing.comparison.tree_spec import parse_metric_tree
        spec = parse_metric_tree({
            "metric": "dominant-frequency",
            "variable": "osc",
            "rel_tolerance": 0.01,
        })
        assert spec.metric == "dominant-frequency"
