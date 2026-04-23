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

    def _multi_sine(self, freqs, n: int = 512, t_end: float = 2.0):
        t = np.linspace(0.0, t_end, n)
        v = sum(np.sin(2 * np.pi * f * t) for f in freqs)
        return t, v

    def test_passes_when_declared_peak_matches(self):
        ref_t, ref_v = self._sine(5.0)
        act_t, act_v = self._sine(5.0)
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 5.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        paired = result.diagnostics["paired_peaks"]
        assert len(paired) == 1
        assert paired[0]["matched_hz"] == pytest.approx(5.0, rel=0.1)

    def test_fails_when_declared_peak_missing_from_actual(self):
        ref_t, ref_v = self._sine(5.0)
        act_t, act_v = self._sine(7.0)  # 40% higher — no peak near 5 Hz
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 5.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        paired = result.diagnostics["paired_peaks"]
        assert paired[0]["matched_hz"] is None
        assert "no peak in tolerance window" in paired[0]["reason"]

    def test_too_short_signal_handled_gracefully(self):
        ref_t = np.array([0.0, 0.1])
        ref_v = np.array([0.0, 1.0])
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
        ))
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        assert not result.passed
        assert "too short" in result.diagnostics.get("reason", "")

    def test_empty_peaks_list_fails_with_hint(self):
        """No declared peaks → hard fail with a pointer at the Detect
        button. Explicit contract per D75."""
        ref_t, ref_v = self._sine(3.0)
        mode = DominantFrequencyMode(DominantFrequencyConfig(peaks=None))
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        assert not result.passed
        assert "no peaks declared" in result.diagnostics.get("reason", "")
        # The reporter seeds its table from detected_reference_peaks_hz.
        assert len(result.diagnostics["detected_reference_peaks_hz"]) >= 1

    def test_resolves_via_factory(self):
        mode = resolve_mode(
            {"mode": "dominant-frequency",
             "peaks": [{"freq": 5.0, "tolerance": 0.02, "tolerance_mode": "rel"}]},
            tolerance=1e-4,
        )
        assert isinstance(mode, DominantFrequencyMode)
        assert len(mode.config.peaks) == 1
        assert mode.config.peaks[0]["freq"] == 5.0

    def test_declared_peaks_multi_all_match(self):
        ref_t, ref_v = self._multi_sine([3.0, 7.0, 11.0])
        act_t, act_v = self._multi_sine([3.0, 7.0, 11.0])
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[
                {"freq": 3.0,  "tolerance": 0.05, "tolerance_mode": "rel"},
                {"freq": 7.0,  "tolerance": 0.05, "tolerance_mode": "rel"},
                {"freq": 11.0, "tolerance": 0.05, "tolerance_mode": "rel"},
            ],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        paired = result.diagnostics["paired_peaks"]
        assert len(paired) == 3
        assert all(p["matched_hz"] is not None for p in paired)

    def test_declared_peaks_one_shifted_fails_whole_leaf(self):
        """If any declared peak can't find a match in its window, the
        whole leaf fails — even if the other peaks match."""
        ref_t, ref_v = self._multi_sine([3.0, 7.0, 11.0])
        act_t, act_v = self._multi_sine([3.0, 9.0, 11.0])  # 7 → 9 Hz
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[
                {"freq": 3.0,  "tolerance": 0.05, "tolerance_mode": "rel"},
                {"freq": 7.0,  "tolerance": 0.05, "tolerance_mode": "rel"},
                {"freq": 11.0, "tolerance": 0.05, "tolerance_mode": "rel"},
            ],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        paired = result.diagnostics["paired_peaks"]
        failed = [p for p in paired if not p["passed"]]
        assert len(failed) == 1
        assert failed[0]["declared_hz"] == 7.0
        assert failed[0]["matched_hz"] is None

    def test_absolute_tolerance_mode_uses_hz_window(self):
        """'abs' mode measures the tolerance in Hz, not fractional. A
        longer test window gives tighter FFT bin resolution so the Hz
        shift actually falls into a different bin."""
        # 8-second window → 0.125 Hz FFT bin resolution → 1 Hz shift is
        # eight bins.
        ref_t, ref_v = self._sine(5.0, n=2048, t_end=8.0)
        act_t, act_v = self._sine(6.0, n=2048, t_end=8.0)  # 1.0 Hz shift
        mode_wide = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 5.0, "tolerance": 1.5, "tolerance_mode": "abs"}],
        ))
        r_wide = mode_wide.compare(ref_t, ref_v, act_t, act_v)
        assert r_wide.passed
        mode_tight = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 5.0, "tolerance": 0.3, "tolerance_mode": "abs"}],
        ))
        r_tight = mode_tight.compare(ref_t, ref_v, act_t, act_v)
        assert not r_tight.passed

    def test_detected_reference_peaks_seeded_for_reporter_button(self):
        """Reporter's 'Detect peaks from reference' button reads from
        ``detected_reference_peaks_hz`` regardless of whether the user
        declared peaks — it always reflects the reference spectrum's top
        peaks so users can bootstrap from it."""
        ref_t, ref_v = self._multi_sine([2.0, 4.0, 6.0])
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 2.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
        ))
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        detected = result.diagnostics["detected_reference_peaks_hz"]
        assert len(detected) >= 3
        # Detected should include the 3 we authored.
        assert any(abs(f - 2.0) < 1.0 for f in detected)
        assert any(abs(f - 4.0) < 1.0 for f in detected)
        assert any(abs(f - 6.0) < 1.0 for f in detected)

    def test_spectrum_embedded_in_diagnostics(self):
        ref_t, ref_v = self._multi_sine([4.0])
        mode = DominantFrequencyMode(DominantFrequencyConfig(
            peaks=[{"freq": 4.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
        ))
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        diag = result.diagnostics
        assert len(diag["ref_spectrum_freq"]) > 0
        assert len(diag["ref_spectrum_mag"]) == len(diag["ref_spectrum_freq"])
        assert len(diag["act_spectrum_freq"]) > 0


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
            "peaks": [{"freq": 1.0, "tolerance": 0.01, "tolerance_mode": "rel"}],
        })
        assert spec.metric == "dominant-frequency"
