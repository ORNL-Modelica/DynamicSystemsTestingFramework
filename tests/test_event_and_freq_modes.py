"""Tests for 4.C — event-timing + dominant-frequency leaf metrics."""

from __future__ import annotations

import json

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_event_timing,
)
from dstf.comparison.geometry import Box
from dstf.comparison.modes import (
    DominantFrequencyConfig,
    DominantFrequencyMode,
    EventTimingConfig,
    EventTimingMode,
    resolve_mode,
)
from dstf.comparison.sides import Sides
from dstf.storage.baseline_codec import decode_reference

EMPTY = np.array([])


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
            {"mode": "event-timing", "time_tolerance": 5e-3},
            tolerance=1e-4,
        )
        assert isinstance(mode, EventTimingMode)
        assert mode.config.time_tolerance == Sides(5e-3, 5e-3)


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
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 5.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
            )
        )
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        paired = result.diagnostics["paired_peaks"]
        assert len(paired) == 1
        assert paired[0]["matched_hz"] == pytest.approx(5.0, rel=0.1)

    def test_fails_when_declared_peak_missing_from_actual(self):
        ref_t, ref_v = self._sine(5.0)
        act_t, act_v = self._sine(7.0)  # 40% higher — no peak near 5 Hz
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 5.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
            )
        )
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        paired = result.diagnostics["paired_peaks"]
        assert paired[0]["matched_hz"] is None
        assert "no peak in tolerance window" in paired[0]["reason"]

    def test_too_short_signal_handled_gracefully(self):
        ref_t = np.array([0.0, 0.1])
        ref_v = np.array([0.0, 1.0])
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
            )
        )
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
            {
                "mode": "dominant-frequency",
                "peaks": [{"freq": 5.0, "tolerance": 0.02, "tolerance_mode": "rel"}],
            },
            tolerance=1e-4,
        )
        assert isinstance(mode, DominantFrequencyMode)
        assert len(mode.config.peaks) == 1
        assert mode.config.peaks[0]["freq"] == 5.0

    def test_declared_peaks_multi_all_match(self):
        ref_t, ref_v = self._multi_sine([3.0, 7.0, 11.0])
        act_t, act_v = self._multi_sine([3.0, 7.0, 11.0])
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[
                    {"freq": 3.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                    {"freq": 7.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                    {"freq": 11.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                ],
            )
        )
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
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[
                    {"freq": 3.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                    {"freq": 7.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                    {"freq": 11.0, "tolerance": 0.05, "tolerance_mode": "rel"},
                ],
            )
        )
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
        mode_wide = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 5.0, "tolerance": 1.5, "tolerance_mode": "abs"}],
            )
        )
        r_wide = mode_wide.compare(ref_t, ref_v, act_t, act_v)
        assert r_wide.passed
        mode_tight = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 5.0, "tolerance": 0.3, "tolerance_mode": "abs"}],
            )
        )
        r_tight = mode_tight.compare(ref_t, ref_v, act_t, act_v)
        assert not r_tight.passed

    def test_detected_reference_peaks_always_reported(self):
        """``detected_reference_peaks_hz`` is reported regardless of
        whether the user declared peaks — it always reflects the
        reference spectrum's top peaks, so a spec author can declare the
        ``peaks`` list from it."""
        ref_t, ref_v = self._multi_sine([2.0, 4.0, 6.0])
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 2.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
            )
        )
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        detected = result.diagnostics["detected_reference_peaks_hz"]
        assert len(detected) >= 3
        # Detected should include the 3 we authored.
        assert any(abs(f - 2.0) < 1.0 for f in detected)
        assert any(abs(f - 4.0) < 1.0 for f in detected)
        assert any(abs(f - 6.0) < 1.0 for f in detected)

    def test_spectrum_embedded_in_diagnostics(self):
        ref_t, ref_v = self._multi_sine([4.0])
        mode = DominantFrequencyMode(
            DominantFrequencyConfig(
                peaks=[{"freq": 4.0, "tolerance": 0.05, "tolerance_mode": "rel"}],
            )
        )
        result = mode.compare(ref_t, ref_v, ref_t, ref_v)
        diag = result.diagnostics
        assert len(diag["ref_spectrum_freq"]) > 0
        assert len(diag["ref_spectrum_mag"]) == len(diag["ref_spectrum_freq"])
        assert len(diag["act_spectrum_freq"]) > 0


class TestMetricsAcceptedInTreeSpec:
    """Verify both new metrics are accepted in MetricTree leaf specs (4.C)."""

    def test_event_timing_leaf_parses(self):
        from dstf.comparison.tree_spec import parse_metric_tree

        spec = parse_metric_tree(
            {
                "metric": "event-timing",
                "variable": "evt",
                "time_tolerance": 1e-3,
            }
        )
        assert spec.metric == "event-timing"

    def test_dominant_frequency_leaf_parses(self):
        from dstf.comparison.tree_spec import parse_metric_tree

        spec = parse_metric_tree(
            {
                "metric": "dominant-frequency",
                "variable": "osc",
                "peaks": [{"freq": 1.0, "tolerance": 0.01, "tolerance_mode": "rel"}],
            }
        )
        assert spec.metric == "dominant-frequency"


class TestEventTimingDeclaredEvents:
    """Declared-events semantics: user supplies the reference-side event
    list explicitly; each declared event matches against the nearest
    actual-side auto-detected event within its own tolerance.
    """

    def test_declared_events_match_when_actual_within_tolerance(self):
        # Two declared events at t=1.0 and t=2.0. Actual has events at
        # t=1.005 and t=1.998 (both within 0.01 tolerance). PASS.
        ref_t = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])  # no events in ref
        act_t = np.array([0.0, 0.5, 1.005, 1.005, 1.5, 1.998, 1.998, 2.5])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(
            EventTimingConfig(
                time_tolerance=0.01,
                events=[{"time": 1.0}, {"time": 2.0}],
            )
        )
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["ref_event_count"] == 2  # from declared
        assert result.diagnostics["act_event_count"] == 2
        assert result.diagnostics["max_time_delta"] < 0.01

    def test_declared_events_fail_when_actual_missing(self):
        # Two declared events; actual has only one matching event.
        ref_t = np.array([0.0, 1.0, 2.0])
        act_t = np.array(
            [0.0, 0.999, 0.999, 2.5]
        )  # event at ~1.0 matches; no event at ~2.0
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(
            EventTimingConfig(
                time_tolerance=0.01,
                events=[{"time": 1.0}, {"time": 2.0}],
            )
        )
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["ref_event_count"] == 2
        assert result.diagnostics["act_event_count"] == 1

    def test_declared_events_per_event_tolerance_overrides_global(self):
        # Declared event at t=1.0 with a wide per-event tolerance (0.5)
        # wins over the global strict tolerance (0.01). Actual event at
        # t=1.3 matches only with the per-event override.
        ref_t = np.array([0.0, 1.0, 2.0])
        act_t = np.array([0.0, 1.3, 1.3, 2.0])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(
            EventTimingConfig(
                time_tolerance=0.01,
                events=[{"time": 1.0, "tolerance": 0.5}],
                count_must_match=False,  # actual has one event, declared has one
            )
        )
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["max_time_delta"] == pytest.approx(0.3, abs=1e-9)

    def test_declared_events_empty_list_passes_with_empty_actual(self):
        # Degenerate: declared = [] (user says "no events expected"),
        # actual also has no events → PASS.
        ref_t = np.array([0.0, 0.5, 1.0])
        act_t = np.array([0.0, 0.5, 1.0])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(events=[]))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["ref_event_count"] == 0
        assert result.diagnostics["act_event_count"] == 0

    def test_declared_events_empty_list_fails_with_events_in_actual(self):
        # Declared = [] but actual has events → FAIL (unexpected events).
        ref_t = np.array([0.0, 0.5, 1.0])
        act_t = np.array([0.0, 0.5, 0.5, 1.0])  # event at 0.5
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(events=[]))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["act_event_count"] == 1


# =========================================================================
# Folded in from tests/test_review_fixes_verdict.py — regression tests for the
# 2026-07-06 review, Theme 1: verdict integrity.
#
# Decisions encoded here:
#
# * Declared dominant-frequency peaks each claim a distinct actual peak.
# * Declared event matching finds a feasible assignment when one exists
#   (no spurious FAIL from greedy declaration-order matching).
# =========================================================================


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
# A lower and an upper tolerance: events (early / late) and peaks (below / above)
# ---------------------------------------------------------------------------


def _event_times(*events: float) -> np.ndarray:
    """A time vector whose events (duplicated instants) are ``events``."""
    out = [0.0]
    for e in events:
        out += [e, e]
    return np.array(out + [100.0])


def _window(vc, i):
    return next(p for p in vc.geometry if p.id == f"events/window/{i}")


class TestEventSidesAutoDetected:
    """``d = actual − reference``: ``lower`` is how much EARLIER the actual
    event may come, ``upper`` how much LATER."""

    def test_two_ms_late_passes_with_room_late_and_fails_swapped(self):
        ref, act = _event_times(5.0), _event_times(5.002)
        assert _compare_event_timing(ref, act, time_tolerance=Sides(0.0, 0.005)).passed
        assert not _compare_event_timing(
            ref, act, time_tolerance=Sides(0.005, 0.0)
        ).passed

    def test_early_is_the_mirror(self):
        ref, act = _event_times(5.0), _event_times(4.998)
        assert _compare_event_timing(ref, act, time_tolerance=Sides(0.005, 0.0)).passed
        assert not _compare_event_timing(
            ref, act, time_tolerance=Sides(0.0, 0.005)
        ).passed

    def test_it_is_inclusive_on_each_side(self):
        tol = Sides(0.25, 0.5)
        assert _compare_event_timing(_event_times(5.0), _event_times(5.5), tol).passed
        assert _compare_event_timing(_event_times(5.0), _event_times(4.75), tol).passed
        assert not _compare_event_timing(
            _event_times(5.0), _event_times(4.5), tol
        ).passed
        assert not _compare_event_timing(
            _event_times(5.0), _event_times(5.75), tol
        ).passed

    def test_the_window_the_score_and_what_leaves_as_data(self):
        vc = _compare_event_timing(
            _event_times(5.0), _event_times(5.25), time_tolerance=Sides(0.25, 0.5)
        )
        box = _window(vc, 0)
        assert (box.t0, box.t1, box.passed) == (4.75, 5.5, True)
        assert vc.nrmse == 0.25  # the score stays the largest |d|
        assert vc.diagnostics["max_time_delta"] == 0.25
        assert vc.diagnostics["time_tolerance"] == {"lower": 0.25, "upper": 0.5}
        json.dumps(vc.diagnostics)
        equal = _compare_event_timing(
            _event_times(5.0), _event_times(5.25), time_tolerance=Sides(0.5, 0.5)
        )
        assert equal.diagnostics["time_tolerance"] == 0.5

    def test_one_pair_out_among_pairs_in_fails_and_is_drawn_so(self):
        ref = _event_times(2.0, 5.0, 8.0)
        act = _event_times(2.001, 4.998, 8.001)
        vc = _compare_event_timing(ref, act, time_tolerance=Sides(0.0, 0.005))
        assert not vc.passed
        assert [_window(vc, i).passed for i in range(3)] == [True, False, True]

    def test_a_number_scores_as_it_did(self):
        import random

        rng = random.Random(3)
        on_edge = 0
        for _ in range(2000):
            tol = rng.choice([0.25, 0.125, rng.uniform(0, 1), 0.0, -0.5])
            r = sorted({round(rng.uniform(1, 9), 1) for _ in range(rng.randint(0, 3))})
            a = sorted(
                {rng.choice([x + tol, x - tol, x + rng.uniform(-1, 1)]) for x in r}
            )
            if rng.random() < 0.1:
                a = a[:-1]
            ref, act = _event_times(*r), _event_times(*a)
            new = _compare_event_timing(ref, act, time_tolerance=tol)
            pair = _compare_event_timing(
                ref, act, time_tolerance={"lower": tol, "upper": tol}
            )
            # The arithmetic of the commit before a pair existed.
            n = min(len(r), len(a))
            deltas = [abs(r[i] - a[i]) for i in range(n)]
            worst = max(deltas, default=0.0)
            assert new.nrmse == worst
            assert new.passed == (worst <= tol and len(r) == len(a)), (r, a, tol)
            assert (pair.passed, pair.nrmse) == (new.passed, new.nrmse)
            assert [
                (b.t0, b.t1, b.passed) for b in new.geometry if isinstance(b, Box)
            ] == [
                (r[i] - tol, r[i] + tol, deltas[i] <= tol if i < n else None)
                for i in range(len(r))
            ]
            on_edge += worst == tol
        assert on_edge > 200

    def test_a_negative_number_fails_even_with_no_event_to_pair(self):
        # Today's `max_delta <= time_tolerance` with max_delta 0: a negative
        # tolerance never passed, an empty signal included. An inverted
        # window holds nothing.
        no_events = np.array([0.0, 1.0, 2.0])
        assert _compare_event_timing(no_events, no_events, time_tolerance=0.0).passed
        for tol in (-1.0, Sides(-1.0, -1.0), Sides(-0.5, 0.25)):
            assert not _compare_event_timing(
                no_events, no_events, time_tolerance=tol
            ).passed, tol


class TestEventSidesDeclared:
    def test_two_ms_late_passes_with_room_late_and_fails_swapped(self):
        act = _event_times(5.002)
        late = [{"time": 5.0, "tolerance": {"lower": 0, "upper": 0.005}}]
        early = [{"time": 5.0, "tolerance": {"lower": 0.005, "upper": 0}}]
        assert _compare_event_timing(EMPTY, act, declared_events=late).passed
        assert not _compare_event_timing(EMPTY, act, declared_events=early).passed

    def test_a_row_without_one_takes_the_leafs_pair(self):
        vc = _compare_event_timing(
            EMPTY,
            _event_times(5.25),
            time_tolerance=Sides(0.125, 0.5),
            declared_events=[{"time": 5.0}, {"time": 50.0, "tolerance": None}],
            count_must_match=False,
        )
        assert (_window(vc, 0).t0, _window(vc, 0).t1) == (4.875, 5.5)
        assert (_window(vc, 1).t0, _window(vc, 1).t1) == (49.875, 50.5)
        assert _window(vc, 0).passed is True and _window(vc, 1).passed is False
        assert vc.diagnostics["time_tolerance"] == {"lower": 0.125, "upper": 0.5}

    def test_overlapping_uneven_windows_each_claim_their_own(self):
        # A: [4.0, 6.5] (declared 6.0, 2.0 early, 0.5 late); B: [4.5, 5.5].
        # Actual events 5.0 and 6.2. B's window ends first and takes 5.0; A
        # takes 6.2. In declared order A would take 5.0 and B would starve.
        vc = _compare_event_timing(
            EMPTY,
            _event_times(5.0, 6.2),
            declared_events=[
                {"time": 6.0, "tolerance": {"lower": 2.0, "upper": 0.5}},
                {"time": 5.0, "tolerance": 0.5},
            ],
        )
        assert vc.passed

    def test_the_matching_is_a_maximum_one_for_uneven_windows(self):
        import itertools
        import random

        rng = random.Random(11)
        for _ in range(1500):
            n = rng.randint(1, 4)
            declared = [
                {
                    "time": round(rng.uniform(1, 9), 1),
                    "tolerance": {
                        "lower": round(rng.uniform(0, 2), 1),
                        "upper": round(rng.uniform(0, 2), 1),
                    },
                }
                for _ in range(n)
            ]
            acts = sorted(
                {round(rng.uniform(0, 10), 1) for _ in range(rng.randint(1, 5))}
            )
            vc = _compare_event_timing(
                EMPTY,
                _event_times(*acts),
                declared_events=declared,
                count_must_match=False,
            )
            windows = [
                (
                    d["time"] - d["tolerance"]["lower"],
                    d["time"] + d["tolerance"]["upper"],
                )
                for d in declared
            ]
            feasible = len(acts) >= n and any(
                all(lo <= a <= hi for (lo, hi), a in zip(windows, perm))
                for perm in itertools.permutations(acts, n)
            )
            assert vc.passed == feasible, (declared, acts)

    def test_a_rows_number_and_its_equal_pair_are_one(self):
        act = _event_times(2.002, 4.9995, 8.004)

        def events(pair):
            def v(x):
                return {"lower": x, "upper": x} if pair else x

            return [
                {"time": 2.0},
                {"time": 5.0, "tolerance": v(0.0001)},
                {"time": 8.0, "tolerance": v(0.01)},
            ]

        a = _compare_event_timing(EMPTY, act, 0.003, declared_events=events(False))
        b = _compare_event_timing(
            EMPTY, act, {"lower": 0.003, "upper": 0.003}, declared_events=events(True)
        )
        assert (a.passed, a.nrmse, a.diagnostics) == (b.passed, b.nrmse, b.diagnostics)
        assert [repr(p) for p in a.geometry] == [repr(p) for p in b.geometry]
        assert [_window(a, i).passed for i in range(3)] == [True, False, True]


_PEAK_T = np.linspace(0.0, 20.0, 2001)
#: One mode at 1.03 Hz: 3 % above a peak declared at 1 Hz.
_PEAK_SIG = np.sin(2 * np.pi * 1.03 * _PEAK_T)


class TestPeakSides:
    """A peak's window is ``[f·(1 − lower), f·(1 + upper)]`` (rel) or
    ``[f − lower, f + upper]`` (abs)."""

    def _vc(self, tolerance, mode):
        peak = {"freq": 1.0, "tolerance": tolerance, "tolerance_mode": mode}
        return _compare_dominant_frequency(
            _PEAK_T, _PEAK_SIG, _PEAK_T, _PEAK_SIG, peaks=[peak]
        )

    @pytest.mark.parametrize("mode", ["rel", "abs"])
    def test_three_percent_high_passes_with_room_above_and_fails_swapped(self, mode):
        assert self._vc({"lower": 0.01, "upper": 0.05}, mode).passed
        assert not self._vc({"lower": 0.05, "upper": 0.01}, mode).passed

    def test_the_window_is_computed_once_for_the_score_and_the_drawing(self):
        from dstf.comparison.algorithms import _peak_window

        for mode, want in (
            ("rel", (2.0 * (1.0 - 0.25), 2.0 * (1.0 + 0.5))),
            ("abs", (1.75, 2.5)),
        ):
            peak = {
                "freq": 2.0,
                "tolerance": {"lower": 0.25, "upper": 0.5},
                "tolerance_mode": mode,
            }
            lo, hi, sides, got_mode = _peak_window(peak, 0)
            assert (lo, hi, sides, got_mode) == (*want, Sides(0.25, 0.5), mode)
            vc = _compare_dominant_frequency(
                _PEAK_T, _PEAK_SIG, _PEAK_T, _PEAK_SIG, peaks=[peak]
            )
            box = next(p for p in vc.geometry if p.id == "peaks/window/0")
            assert (box.t0, box.t1) == want
            # 1.03 Hz is below both windows: scored where it is drawn.
            assert not vc.passed and box.passed is False
            paired = vc.diagnostics["paired_peaks"][0]
            assert paired["tolerance"] == {"lower": 0.25, "upper": 0.5}
            json.dumps(vc.diagnostics)

    def test_a_peak_without_a_tolerance_is_one_percent_each_side(self):
        from dstf.comparison.algorithms import _peak_window

        assert _peak_window({"freq": 2.0}, 0) == (1.98, 2.02, Sides(0.01, 0.01), "rel")
        assert _peak_window({"freq": 2.0, "tolerance": None}, 0)[2] == Sides(0.01, 0.01)

    @pytest.mark.parametrize("mode", ["rel", "abs"])
    def test_a_number_and_its_equal_pair_are_one(self, mode):
        a = self._vc(0.05, mode)
        b = self._vc({"lower": 0.05, "upper": 0.05}, mode)
        assert a.passed and b.passed
        assert a.diagnostics["paired_peaks"] == b.diagnostics["paired_peaks"]
        assert a.diagnostics["paired_peaks"][0]["tolerance"] == 0.05
        assert (a.nrmse, a.max_abs_error) == (b.nrmse, b.max_abs_error)
        boxes = [
            [(p.t0, p.t1, p.passed) for p in vc.geometry if isinstance(p, Box)]
            for vc in (a, b)
        ]
        assert boxes[0] == boxes[1]


def _through(leaf, flat, time, values):
    """``leaf`` scored through ``compare_test``: as a metrics leaf, or with
    ``flat`` as the same keys in ``variable_overrides``."""

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.test_registry import TestModel

    kw = dict(
        model_id="T.M",
        source_file=None,
        source_package="T",
        short_name="M",
        n_vars=0,
        source="spec",
        variable_patterns=["x"],
    )
    result = TestResult(
        model_id="T.M",
        success=True,
        variables=[VariableResult(index=1, name="x", time=time, values=values)],
    )
    reference = {
        "test_id": "1",
        "time": time.tolist(),
        "variables": [{"index": 1, "name": "x", "values": values.tolist()}],
    }
    if flat:
        override = {k: v for k, v in leaf.items() if k != "metric"}
        test = TestModel(
            variable_overrides={"x": {**override, "mode": leaf["metric"]}}, **kw
        )
    else:
        test = TestModel(
            metric_tree_spec=parse_metric_tree({**leaf, "variable": "x"}), **kw
        )
    return compare_test(test, result, decode_reference(reference)).variables[0]


@pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
@pytest.mark.parametrize("bad", [{"upper": 1}, "abc", True], ids=repr)
class TestAMalformedRowIsTheLeafsConfigError:
    """On BOTH scoring paths: a row's pair is parsed where the mode is
    built, which is where each path catches a ``ValueError``."""

    def test_an_events_row(self, bad, flat):
        time = _event_times(5.0)
        leaf = {
            "metric": "event-timing",
            "events": [{"time": 1.0}, {"time": 5.0, "tolerance": bad}],
        }
        vc = _through(leaf, flat, time, np.zeros_like(time))
        assert vc.diagnostics["config_error"] is True
        assert vc.diagnostics["error"].endswith(
            f'events[1].tolerance must be a number or {{"lower": …, "upper": …}}, '
            f"got {json.dumps(bad)}"
        )

    def test_a_peaks_row(self, bad, flat):
        leaf = {
            "metric": "dominant-frequency",
            "peaks": [{"freq": 1.0}, {"freq": 2.0, "tolerance": bad}],
        }
        vc = _through(leaf, flat, _PEAK_T, _PEAK_SIG)
        assert vc.diagnostics["config_error"] is True
        assert vc.diagnostics["error"].endswith(
            f'peaks[1].tolerance must be a number or {{"lower": …, "upper": …}}, '
            f"got {json.dumps(bad)}"
        )


def test_the_scorers_called_directly_name_the_row_too():
    with pytest.raises(ValueError, match=r"^events\[0\]\.tolerance must be"):
        _compare_event_timing(
            EMPTY, _event_times(5.0), declared_events=[{"time": 5.0, "tolerance": "x"}]
        )
    with pytest.raises(ValueError, match=r"^peaks\[0\]\.tolerance must be"):
        _compare_dominant_frequency(
            _PEAK_T,
            _PEAK_SIG,
            _PEAK_T,
            _PEAK_SIG,
            peaks=[{"freq": 1.0, "tolerance": "x"}],
        )
