"""One mode-aware summary per leaf, dispatched through the descriptor."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_event_timing,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.registry import metrics, summarize
from dstf.comparison.types import ScoreSummary, VariableComparison


def _vc(**kw) -> VariableComparison:
    base = dict(
        index=0,
        name="x",
        passed=True,
        nrmse=1e-3,
        rmse=1e-3,
        signal_range=1.0,
        max_abs_error=2e-3,
        max_abs_error_time=0.5,
        reference_final=1.0,
        actual_final=1.0,
        tolerance_used=1e-4,
    )
    base.update(kw)
    return VariableComparison(**base)


def test_every_registered_metric_summarizes_a_sentinel_without_raising():
    for desc in metrics():
        s = desc.summarize(_vc(mode=desc.name))
        assert isinstance(s, ScoreSummary) and s.display and s.criterion


def test_an_error_sentinel_is_mode_agnostic():
    s = summarize(_vc(mode="tube", diagnostics={"error": "empty window"}))
    assert s.display == "not scored"
    assert s.criterion == "tube leaf not scored — empty window"


def test_tube_summary_reads_the_inside_fraction():
    t = np.linspace(0.0, 1.0, 11)
    vc = _compare_tube(t, t, t, t + 0.5, {"tube_width_mode": "band", "tube_abs": 0.1})
    s = summarize(vc)
    assert s.display == "0.0% in tube"
    assert s.criterion.endswith("→ FAIL (requires 100%)")
    assert s.score == 0.0 and s.score_label == "in tube"


def test_range_summary_reads_the_max_violation():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(_compare_range(t, t * 2, min_value=None, max_value=1.0))
    assert s.display.startswith("max_viol 1.000e+00")
    assert "→ FAIL (requires 0)" in s.criterion


def test_event_timing_summary_counts_events():
    ref_t = np.array([0.0, 1.0, 1.0, 2.0])
    s = summarize(_compare_event_timing(ref_t, ref_t, time_tolerance=1e-3))
    assert s.display == "Δt 0.000e+00 (1/1 events)"


def test_nrmse_is_the_default_shape():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(_compare_trajectories(t, t, t, t + 0.001, tolerance=0.1))
    assert s.display.startswith("NRMSE ") and s.score_label == "NRMSE"
    assert "→ PASS" in s.criterion


class TestDetails:
    """The mode's own key/value pairs behind the verdict — the owner's
    2026-09-08 ruling: the review loop keeps the old numbers, but only under
    labels that are true for the mode (a range leaf's ``nrmse`` field holds
    its worst violation, so it must never print as "NRMSE")."""

    def test_every_registered_metric_yields_string_pairs(self):
        for desc in metrics():
            s = desc.summarize(_vc(mode=desc.name))
            assert isinstance(s.details, tuple)
            assert all(
                isinstance(k, str) and isinstance(v, str) for k, v in s.details
            ), desc.name

    def test_nrmse_details_are_the_old_review_block(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(_compare_trajectories(t, t, t, t + 0.001, tolerance=0.1))
        keys = [k for k, _ in s.details]
        assert keys == ["RMSE", "max |err|", "final"]
        assert "at t=" in dict(s.details)["max |err|"]
        assert dict(s.details)["final"].startswith("ref=")

    def test_a_constant_reference_says_so(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(
            _compare_trajectories(
                t, np.full_like(t, 2.0), t, np.full_like(t, 2.1), tolerance=0.1
            )
        )
        assert "(constant signal)" in dict(s.details)["RMSE"]

    def test_range_never_labels_its_violation_as_nrmse(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(_compare_range(t, t * 2, min_value=None, max_value=1.0))
        keys = [k for k, _ in s.details]
        assert "NRMSE" not in keys and "RMSE" not in keys
        assert dict(s.details)["bounds"] == "[-inf, 1]"
        assert dict(s.details)["worst violation"].startswith("1.000000e+00 at t=")

    def test_tube_names_its_worst_violation_and_its_real_companions(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(
            _compare_tube(
                t, t, t, t + 0.5, {"tube_width_mode": "band", "tube_abs": 0.1}
            )
        )
        d = dict(s.details)
        assert d["worst violation"].startswith("4.000000e-01 at t=")
        assert "NRMSE" in d and "final" in d

    def test_points_lists_each_failed_checkpoint(self):
        from dstf.comparison.algorithms import _compare_points

        t = np.linspace(0.0, 10.0, 101)
        act = t + 0.05 * (t > 5.0)
        s = summarize(
            _compare_points(
                t,
                t,
                t,
                act,
                points=[
                    {"time": 2.0, "tolerance": 0.01},
                    {"time": 8.0, "tolerance": 0.01},
                ],
            )
        )
        d = dict(s.details)
        assert d["checkpoints"] == "1 of 2 failed"
        assert "exceeds limit" in d["t=8"]
        assert "t=2" not in d

    def test_an_error_sentinel_has_no_details(self):
        s = summarize(_vc(mode="tube", diagnostics={"error": "empty window"}))
        assert s.details == ()


class TestTheToleranceInACriterionSentence:
    """`tolerance_used` is `Sides`: one number when the sides are equal —
    the text a number always gave — and `−lower / +upper` otherwise."""

    def test_equal_sides_read_as_the_number_did(self):
        from dstf.comparison.sides import Sides

        for mode, text in (
            ("nrmse", "NRMSE 1.000e-03 vs tolerance 1.000e-04 → PASS"),
            ("points", "Final value error 2.000e-03 vs tolerance 1.000e-04 → PASS"),
        ):
            assert summarize(_vc(mode=mode)).criterion == text
            assert (
                summarize(_vc(mode=mode, tolerance_used=Sides(1e-4, 1e-4))).criterion
                == text
            )
        scalar = summarize(_vc(mode="scalar", diagnostics={"tolerance_mode": "abs"}))
        assert scalar.criterion == (
            "Scalar 1 vs baseline 1: absolute deviation 1.000e-03 vs "
            "tolerance 1.000e-04 → PASS"
        )

    def test_unequal_sides_read_as_minus_and_plus(self):
        from dstf.comparison.sides import Sides

        uneven = Sides(0.01, 0.02)
        # With uneven sides the deviation is signed (actual − target): which
        # side applies is the reader's to see.
        assert summarize(
            _vc(mode="points", tolerance_used=uneven, actual_final=1.002)
        ).criterion == (
            "Final value error +2.000e-03 vs tolerance −1.000e-02 / +2.000e-02 → PASS"
        )
        assert summarize(
            _vc(mode="scalar", tolerance_used=uneven, actual_final=0.999)
        ).criterion == (
            "Scalar 0.999 vs baseline 1: absolute deviation −1.000e-03 vs "
            "tolerance −1.000e-02 / +2.000e-02 → PASS"
        )
        assert summarize(_vc(mode="nope", tolerance_used=uneven)).criterion == (
            "NRMSE 1.000e-03 vs tolerance −1.000e-02 / +2.000e-02 → PASS"
        )


def test_a_points_criterion_says_both_sides_of_the_leafs_tolerance():
    from dstf.comparison.modes import resolve_mode

    t = np.linspace(0.0, 10.0, 11)
    mode = resolve_mode({"mode": "points"}, {"lower": 0.25, "upper": 0.5})
    vc = mode.compare(t, t, t, t + 0.375)
    vc.tolerance_used = mode.tolerance  # as both scoring paths stamp it
    assert vc.passed
    assert summarize(vc).criterion == (
        "Final value error +3.750e-01 vs tolerance −2.500e-01 / +5.000e-01 → PASS"
    )


class TestAnUnevenToleranceSaysWhichSide:
    """G9: with uneven sides a criterion sentence prints the SIGNED
    deviation — actual − target, ``+`` or U+2212 — so the reader sees which
    side applies. Equal sides print what a number always printed."""

    T = np.linspace(0.0, 10.0, 11)

    def _points(self, act, tolerance, points=None):
        from dstf.comparison.modes import resolve_mode

        override = {"mode": "points", **({"points": points} if points else {})}
        mode = resolve_mode(override, tolerance)
        vc = mode.compare(self.T, self.T, self.T, act)
        vc.tolerance_used = mode.tolerance
        return summarize(vc).criterion

    def test_final_value_late_and_early(self):
        uneven = {"lower": 0.25, "upper": 0.5}
        assert self._points(self.T - 0.125, uneven) == (
            "Final value error −1.250e-01 vs tolerance −2.500e-01 / +5.000e-01 → PASS"
        )
        assert self._points(self.T + 0.75, uneven) == (
            "Final value error +7.500e-01 vs tolerance −2.500e-01 / +5.000e-01 → FAIL"
        )
        assert self._points(self.T - 0.125, 0.5) == (
            "Final value error 1.250e-01 vs tolerance 5.000e-01 → PASS"
        )

    def test_declared_points_say_the_worst_points_signed_delta(self):
        act = self.T.copy()
        act[5] += 0.1
        act[8] -= 0.3
        rows = [{"time": 5.0}, {"time": 8.0}]
        assert self._points(act, {"lower": 0.5, "upper": 0.25}, rows) == (
            "Final value error −3.000e-01 vs tolerance −5.000e-01 / +2.500e-01 → PASS"
        )
        act[5] += 0.4  # now +0.5 at t=5, the worst
        assert self._points(act, {"lower": 0.5, "upper": 0.25}, rows) == (
            "Final value error +5.000e-01 vs tolerance −5.000e-01 / +2.500e-01 → FAIL"
        )
        assert self._points(act, 0.5, rows) == (
            "Final value error 5.000e-01 vs tolerance 5.000e-01 → PASS"
        )

    def test_the_signed_keys_are_recorded_for_uneven_sides_only(self):
        """A leaf with a number carries the diagnostics it always carried."""
        from dstf.comparison.algorithms import _compare_points
        from dstf.comparison.sides import Sides

        rows = [{"time": 5.0}]
        assert "worst_signed_delta" not in (
            _compare_points(self.T, self.T, self.T, self.T + 0.1, rows, 0.5).diagnostics
        )
        uneven = Sides(0.25, 0.5)
        assert _compare_points(
            self.T, self.T, self.T, self.T + 0.1, rows, uneven
        ).diagnostics["worst_signed_delta"] == pytest.approx(0.1)
        ref = np.array([0.0, 5.0, 5.0, 10.0])
        act = np.array([0.0, 4.75, 4.75, 10.0])
        for declared in (None, [{"time": 5.0}]):
            even = _compare_event_timing(
                ref, act, time_tolerance=0.5, declared_events=declared
            )
            assert "max_time_delta_signed" not in even.diagnostics
            odd = _compare_event_timing(
                ref, act, time_tolerance=uneven, declared_events=declared
            )
            assert odd.diagnostics["max_time_delta_signed"] == -0.25

    def test_a_crossing_in_a_box_is_a_signed_zero(self):
        act = self.T.copy()
        act[4], act[6] = 4.5, 5.5  # through the target at t=5
        act[5] = 99.0
        rows = [{"time": 5.0, "time_tolerance": 1.0}]
        crit = self._points(act, {"lower": 0.5, "upper": 0.25}, rows)
        assert crit.startswith("Final value error +0.000e+00 vs tolerance")

    def _scalar(self, act_final, tolerance, mode, ref_final=4.0):
        from dstf.comparison.algorithms import _compare_scalar
        from dstf.comparison.sides import parse_sides

        ref = np.full(3, ref_final)
        act = np.array([ref_final, ref_final, act_final])
        vc = _compare_scalar(
            self.T[:3],
            ref,
            self.T[:3],
            act,
            reduction="final",
            tolerance=tolerance,
            tolerance_mode=mode,
            min_value=None,
            max_value=None,
        )
        vc.tolerance_used = parse_sides(tolerance, "tolerance")
        return summarize(vc).criterion

    def test_scalar_absolute_and_relative_both_signs(self):
        uneven = {"lower": 0.25, "upper": 0.5}
        assert self._scalar(4.375, uneven, "abs") == (
            "Scalar 4.375 vs baseline 4: absolute deviation +3.750e-01 vs "
            "tolerance −2.500e-01 / +5.000e-01 → PASS"
        )
        assert self._scalar(3.5, uneven, "abs") == (
            "Scalar 3.5 vs baseline 4: absolute deviation −5.000e-01 vs "
            "tolerance −2.500e-01 / +5.000e-01 → FAIL"
        )
        uneven_rel = {"lower": 0.05, "upper": 0.1}
        assert self._scalar(4.2, uneven_rel, "rel") == (
            "Scalar 4.2 vs baseline 4: relative deviation +5.000e-02 vs "
            "tolerance −5.000e-02 / +1.000e-01 → PASS"
        )
        assert self._scalar(3.6, uneven_rel, "rel") == (
            "Scalar 3.6 vs baseline 4: relative deviation −1.000e-01 vs "
            "tolerance −5.000e-02 / +1.000e-01 → FAIL"
        )
        assert self._scalar(3.6, 0.1, "rel") == (
            "Scalar 3.6 vs baseline 4: relative deviation 1.000e-01 vs "
            "tolerance 1.000e-01 → PASS"
        )

    def test_scalar_relative_to_a_negative_baseline_keeps_the_sign_of_the_change(
        self,
    ):
        """rel is a fraction of |baseline|: above a baseline of −4 is ``+``,
        as the scorer judged it — divided by the baseline itself it flips."""
        uneven_rel = {"lower": 0.05, "upper": 0.1}
        for act, deviation, verdict in (
            (-3.7, "+7.500e-02", "PASS"),  # 0.3 above: inside the 10 % above
            (-3.5, "+1.250e-01", "FAIL"),
            (-4.1, "−2.500e-02", "PASS"),  # 0.1 below: inside the 5 % below
            (-4.3, "−7.500e-02", "FAIL"),
        ):
            assert self._scalar(act, uneven_rel, "rel", ref_final=-4.0) == (
                f"Scalar {act:g} vs baseline -4: relative deviation {deviation} vs "
                f"tolerance −5.000e-02 / +1.000e-01 → {verdict}"
            )

    def test_event_timing_with_a_one_sided_window(self):
        """A pair with a zero side: late only, or early only. The zero edge
        reads by its side."""
        from dstf.comparison.sides import Sides

        ref = np.array([0.0, 5.0, 5.0, 10.0])
        late, early = 5.25, 4.875
        late_only, early_only = Sides(0.0, 0.5), Sides(0.5, 0.0)
        for at, sides, delta, band, verdict in (
            (late, late_only, "+2.500e-01", "−0.000e+00 / +5.000e-01", "PASS"),
            (early, late_only, "−1.250e-01", "−0.000e+00 / +5.000e-01", "FAIL"),
            (early, early_only, "−1.250e-01", "−5.000e-01 / +0.000e+00", "PASS"),
            (late, early_only, "+2.500e-01", "−5.000e-01 / +0.000e+00", "FAIL"),
        ):
            act = np.array([0.0, at, at, 10.0])
            auto = _compare_event_timing(ref, act, time_tolerance=sides)
            assert summarize(auto).criterion == (
                f"Max event Δt {delta} vs {band} "
                f"(1 actual events / 1 reference) → {verdict}"
            )
            declared = _compare_event_timing(
                ref, act, time_tolerance=sides, declared_events=[{"time": 5.0}]
            )
            assert declared.passed is (verdict == "PASS")
            # Outside its window a declared event claims nothing: it is not
            # found, and there is no Δt to print.
            assert summarize(declared).criterion == (
                f"Max event Δt {delta} vs {band} (1 actual events / 1 reference) → PASS"
                if verdict == "PASS"
                else f"1 of 1 declared events not found within tolerance {band} "
                "(1 actual events) → FAIL"
            )

    def test_event_timing_late_is_positive_early_negative(self):
        from dstf.comparison.sides import Sides

        ref = np.array([0.0, 2.0, 2.0, 5.0, 5.0, 10.0])
        # The worst pair is early (−0.125) here, late (+0.25) below.
        worst_early = np.array([0.0, 2.0625, 2.0625, 4.875, 4.875, 10.0])
        uneven = Sides(0.25, 0.5)
        assert summarize(
            _compare_event_timing(ref, worst_early, time_tolerance=uneven)
        ).criterion == (
            "Max event Δt −1.250e-01 vs −2.500e-01 / +5.000e-01 "
            "(2 actual events / 2 reference) → PASS"
        )
        worst_late = np.array([0.0, 2.25, 2.25, 4.875, 4.875, 10.0])
        assert summarize(
            _compare_event_timing(ref, worst_late, time_tolerance=uneven)
        ).criterion == (
            "Max event Δt +2.500e-01 vs −2.500e-01 / +5.000e-01 "
            "(2 actual events / 2 reference) → PASS"
        )
        declared = summarize(
            _compare_event_timing(
                ref,
                worst_early,
                time_tolerance=uneven,
                declared_events=[{"time": 2.0}, {"time": 5.0}],
            )
        )
        assert declared.criterion.startswith("Max event Δt −1.250e-01 vs ")
        assert summarize(
            _compare_event_timing(ref, worst_early, time_tolerance=0.5)
        ).criterion.startswith("Max event Δt 1.250e-01 vs 5.000e-01 ")


class TestTheSignedNumberIsThePointThatBreaksTheBand:
    """With uneven sides the largest |Δ| need not be the failing one: +0.04
    under an upper side of 0.05 passes while −0.02 under a lower side of
    0.01 fails. The sentence prints the point (or event pair) FURTHEST
    OUTSIDE its band; with every one inside, the largest |Δ|."""

    T = np.linspace(0.0, 1.0, 11)
    BAND = {"lower": 0.01, "upper": 0.05}

    def _points(self, deltas, tolerance, ref=None, **row):
        """Checkpoints at ``T[i]`` for each ``{i: actual − reference}``."""
        from dstf.comparison.modes import resolve_mode

        ref = self.T if ref is None else ref
        act = ref.copy()
        for i, d in deltas.items():
            act[i] += d
        rows = [{"time": float(self.T[i]), **row} for i in deltas]
        mode = resolve_mode({"mode": "points", "points": rows}, tolerance)
        vc = mode.compare(self.T, ref, self.T, act)
        vc.tolerance_used = mode.tolerance
        return vc, summarize(vc).criterion

    def test_points_the_smaller_delta_is_the_failing_one(self):
        vc, crit = self._points({5: +0.04, 8: -0.02}, self.BAND)
        assert crit == (
            "Final value error −2.000e-02 vs tolerance −1.000e-02 / +5.000e-02 → FAIL"
        )
        # The score and where it is stay the largest |Δ|'s, as a number's.
        assert vc.max_abs_error == pytest.approx(0.04)
        assert vc.max_abs_error_time == pytest.approx(0.5)
        assert vc.diagnostics["worst_delta"] == pytest.approx(0.04)
        assert [f["time"] for f in vc.diagnostics["point_failures"]] == [
            pytest.approx(0.8)
        ]

    def test_points_of_two_outside_the_further_one(self):
        # +0.07 is 0.02 above the band, −0.04 is 0.03 below it.
        _, crit = self._points({5: +0.07, 8: -0.04}, self.BAND)
        assert crit.startswith("Final value error −4.000e-02 vs ")
        _, crit = self._points({5: -0.04, 8: +0.07}, self.BAND)
        assert crit.startswith("Final value error −4.000e-02 vs ")
        # +0.09 is 0.04 above: now that one.
        _, crit = self._points({5: +0.09, 8: -0.04}, self.BAND)
        assert crit.startswith("Final value error +9.000e-02 vs ")

    def test_points_all_inside_say_the_largest_delta(self):
        _, crit = self._points({5: +0.04, 8: -0.005}, self.BAND)
        assert crit == (
            "Final value error +4.000e-02 vs tolerance −1.000e-02 / +5.000e-02 → PASS"
        )
        _, crit = self._points({5: +0.004, 8: -0.008}, self.BAND)
        assert crit.startswith("Final value error −8.000e-03 vs ")

    def test_points_relative_sides_are_judged_on_the_scaled_band(self):
        # rel: each side is that fraction of |target|. At target 10 the band
        # is [−1, +5] and +2 lies in it; at target 1 it is [−0.1, +0.5] and
        # −0.5 lies 0.4 below. On the unscaled sides +2 would be the further.
        ref = np.ones_like(self.T)
        ref[5] = 10.0
        vc, crit = self._points(
            {5: +2.0, 8: -0.5},
            {"lower": 0.1, "upper": 0.5},
            ref=ref,
            tolerance_mode="rel",
        )
        assert crit == (
            "Final value error −5.000e-01 vs tolerance −1.000e-01 / +5.000e-01 → FAIL"
        )
        assert vc.max_abs_error == pytest.approx(2.0)

    def test_points_equal_sides_say_what_a_number_said(self):
        for tolerance in (0.03, {"lower": 0.03, "upper": 0.03}):
            vc, crit = self._points({5: +0.04, 8: -0.02}, tolerance)
            assert crit == "Final value error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
            assert "worst_signed_delta" not in vc.diagnostics

    REF = np.array([0.0, 2.0, 2.0, 5.0, 5.0, 10.0])

    def _events(self, first, second, tolerance, declared=None):
        from dstf.comparison.sides import parse_sides

        act = np.array([0.0, 2.0 + first, 2.0 + first, 5.0 + second, 5.0 + second, 10])
        vc = _compare_event_timing(
            self.REF,
            act,
            time_tolerance=parse_sides(tolerance, "time_tolerance"),
            declared_events=declared,
        )
        return vc, summarize(vc).criterion

    def test_events_the_smaller_delta_is_the_failing_one(self):
        vc, crit = self._events(-0.03, +0.04, self.BAND)
        assert crit == (
            "Max event Δt −3.000e-02 vs −1.000e-02 / +5.000e-02 "
            "(2 actual events / 2 reference) → FAIL"
        )
        assert vc.nrmse == pytest.approx(0.04)
        assert vc.max_abs_error_time == 5.0
        assert vc.diagnostics["max_time_delta"] == pytest.approx(0.04)
        # ... whichever comes first.
        _, crit = self._events(+0.04, -0.03, self.BAND)
        assert crit.startswith("Max event Δt −3.000e-02 vs ")

    def test_events_of_two_outside_the_further_one(self):
        _, crit = self._events(+0.07, -0.04, self.BAND)
        assert crit.startswith("Max event Δt −4.000e-02 vs ")
        _, crit = self._events(+0.09, -0.04, self.BAND)
        assert crit.startswith("Max event Δt +9.000e-02 vs ")

    def test_events_all_inside_say_the_largest_delta(self):
        _, crit = self._events(-0.005, +0.04, self.BAND)
        assert crit == (
            "Max event Δt +4.000e-02 vs −1.000e-02 / +5.000e-02 "
            "(2 actual events / 2 reference) → PASS"
        )
        _, crit = self._events(-0.008, +0.004, self.BAND)
        assert crit.startswith("Max event Δt −8.000e-03 vs ")

    def test_events_equal_sides_say_what_a_number_said(self):
        for tolerance in (0.035, {"lower": 0.035, "upper": 0.035}):
            vc, crit = self._events(-0.03, +0.04, tolerance)
            assert crit == (
                "Max event Δt 4.000e-02 vs 3.500e-02 "
                "(2 actual events / 2 reference) → FAIL"
            )
            assert "max_time_delta_signed" not in vc.diagnostics

    def test_declared_events_pair_only_inside_a_window(self):
        """A declared event claims an actual one INSIDE its window, so no
        pair lies outside its band: the largest |Δ| of the pairs."""
        declared = [{"time": 2.0}, {"time": 5.0}]
        vc, crit = self._events(-0.005, +0.04, self.BAND, declared)
        assert crit == (
            "Max event Δt +4.000e-02 vs −1.000e-02 / +5.000e-02 "
            "(2 actual events / 2 reference) → PASS"
        )
        _, crit = self._events(-0.008, +0.004, self.BAND, declared)
        assert crit.startswith("Max event Δt −8.000e-03 vs ")


def test_an_event_timing_criterion_says_early_and_late():
    from dstf.comparison.sides import Sides

    ref = np.array([0.0, 5.0, 5.0, 10.0])
    act = np.array([0.0, 5.25, 5.25, 10.0])
    even = summarize(_compare_event_timing(ref, act, time_tolerance=0.5))
    assert even.criterion == (
        "Max event Δt 2.500e-01 vs 5.000e-01 (1 actual events / 1 reference) → PASS"
    )
    uneven = summarize(
        _compare_event_timing(ref, act, time_tolerance=Sides(0.125, 0.5))
    )
    assert uneven.criterion == (
        "Max event Δt +2.500e-01 vs −1.250e-01 / +5.000e-01 "
        "(1 actual events / 1 reference) → PASS"
    )


class TestADeclaredEventNeverFound:
    """A declared event with no actual event in its window fails the leaf —
    and the sentence used to print the MATCHED events' largest Δt, a number
    inside the tolerance, beside FAIL. It says what failed: how many of the
    declared events were not found, and which."""

    DECLARED = [{"time": 2.0}, {"time": 5.0}, {"time": 8.0}]

    def _events(self, actual, tolerance, declared=DECLARED):
        act = np.array([0.0, *(t for at in actual for t in (at, at)), 10.0])
        ref = np.array([0.0, 2.0, 2.0, 5.0, 5.0, 8.0, 8.0, 10.0])
        vc = _compare_event_timing(
            ref, act, time_tolerance=tolerance, declared_events=declared
        )
        return vc, summarize(vc)

    def test_one_not_found_with_a_number(self):
        vc, s = self._events([2.0, 5.2, 8.0], 0.1)
        assert not vc.passed
        assert s.criterion == (
            "1 of 3 declared events not found within tolerance 1.000e-01 "
            "(3 actual events) → FAIL"
        )
        assert s.details == (
            ("events", "3 actual / 3 reference"),
            ("max Δt", "0.000000e+00 at t=0"),
            ("not found", "t=5"),
        )
        assert vc.diagnostics["unmatched_events"] == 1
        assert vc.diagnostics["declared_events"] == 3
        # The score is the matched events', as it was.
        assert s.display == "Δt 0.000e+00 (3/3 events)" and s.score == 0.0

    def test_one_not_found_with_an_uneven_pair(self):
        # 0.2 late under an upper side of 0.1; 0.2 early would be inside.
        vc, s = self._events([2.0, 5.2, 7.8], {"lower": 0.3, "upper": 0.1})
        assert s.criterion == (
            "1 of 3 declared events not found within tolerance "
            "−3.000e-01 / +1.000e-01 (3 actual events) → FAIL"
        )
        assert dict(s.details)["not found"] == "t=5"
        assert vc.diagnostics["unmatched_events"] == 1

    def test_every_one_found_reads_as_it_did(self):
        vc, s = self._events([2.0, 5.05, 8.0], 0.1)
        assert vc.passed
        assert s.criterion == (
            "Max event Δt 5.000e-02 vs 1.000e-01 (3 actual events / 3 reference) → PASS"
        )
        assert s.details == (
            ("events", "3 actual / 3 reference"),
            ("max Δt", "5.000000e-02 at t=5"),
        )
        assert vc.diagnostics == {
            "ref_event_count": 3,
            "act_event_count": 3,
            "max_time_delta": pytest.approx(0.05),
            "time_tolerance": 0.1,
            "counts_match": True,
            "unmatched_events": 0,
            "declared_events": 3,
        }

    def test_found_but_a_count_mismatch_reads_as_it_did(self):
        """An extra actual event fails the leaf by its count: every declared
        event was found, so the sentence is the Δt one."""
        vc, s = self._events([2.0, 5.0, 6.5, 8.0], 0.1)
        assert not vc.passed and vc.diagnostics["unmatched_events"] == 0
        assert s.criterion == (
            "Max event Δt 0.000e+00 vs 1.000e-01 (4 actual events / 3 reference) → FAIL"
        )
        assert s.details[-1] == ("event count", "mismatch")
        assert "not found" not in dict(s.details)

    def test_none_found(self):
        vc, s = self._events([2.5, 5.5, 8.5], 0.1)
        assert s.criterion == (
            "3 of 3 declared events not found within tolerance 1.000e-01 "
            "(3 actual events) → FAIL"
        )
        assert dict(s.details)["not found"] == "t=2, t=5, t=8"
        assert vc.diagnostics["unmatched_events"] == 3

    def test_not_found_and_a_count_mismatch_says_both(self):
        vc, s = self._events([2.0, 8.0], 0.1)
        assert s.criterion == (
            "1 of 3 declared events not found within tolerance 1.000e-01 "
            "(2 actual events) → FAIL"
        )
        assert s.details[-2:] == (("not found", "t=5"), ("event count", "mismatch"))

    @pytest.mark.parametrize("flat", [False, True])
    def test_both_scoring_paths_name_them(self, flat):
        """The tree evaluator stamps a leaf's geometry ids with its path
        (`/metrics/events/window/1`); the flat path leaves them local."""
        from pathlib import Path

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel

        leaf = {"time_tolerance": 0.1, "events": self.DECLARED}
        time = np.array([0.0, 2.0, 2.0, 5.2, 5.2, 8.3, 8.3, 10.0])
        result = TestResult(
            model_id="T.M",
            success=True,
            variables=[
                VariableResult(index=1, name="e", time=time, values=np.arange(8.0))
            ],
        )
        test = TestModel(
            model_id="T.M",
            source_file=Path(""),
            source_package="T",
            short_name="M",
            n_vars=0,
            source="spec",
            variable_patterns=["e"],
            **(
                {"variable_overrides": {"e": {"mode": "event-timing", **leaf}}}
                if flat
                else {
                    "metric_tree_spec": parse_metric_tree(
                        {"metric": "event-timing", "variable": "e", **leaf}
                    )
                }
            ),
        )
        reference = {
            "test_id": "0001",
            "variables": [
                {"index": 1, "name": "e", "time": time.tolist(), "values": [0.0] * 8}
            ],
        }
        (vc,) = compare_test(test, result, reference, default_tolerance=1e-4).variables
        if not flat:
            assert vc.geometry[0].id.startswith("/")  # stamped
        s = summarize(vc)
        assert s.criterion.startswith("2 of 3 declared events not found ")
        assert dict(s.details)["not found"] == "t=5, t=8"

    def test_the_times_read_as_numbers_do(self):
        _, s = self._events([], 0.1, [{"time": 2.5}, {"time": 1e-7}, {"time": 4000}])
        assert dict(s.details)["not found"] == "t=2.5, t=1e-07, t=4000"

    def test_auto_detected_events_are_untouched(self):
        ref = np.array([0.0, 2.0, 2.0, 5.0, 5.0, 8.0, 8.0, 10.0])
        act = np.array([0.0, 2.0, 2.0, 5.2, 5.2, 8.0, 8.0, 10.0])
        vc = _compare_event_timing(ref, act, time_tolerance=0.1)
        assert set(vc.diagnostics) == {
            "ref_event_count",
            "act_event_count",
            "max_time_delta",
            "time_tolerance",
            "counts_match",
        }
        s = summarize(vc)
        assert s.criterion == (
            "Max event Δt 2.000e-01 vs 1.000e-01 (3 actual events / 3 reference) → FAIL"
        )
        assert [k for k, _ in s.details] == ["events", "max Δt"]
