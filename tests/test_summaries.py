"""One mode-aware summary per leaf, dispatched through the descriptor."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.registry import metrics, summarize
from dstf.comparison.types import ScoreSummary, VariableComparison
from dstf.storage.baseline_codec import decode_reference
from scoring import score


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
    assert s.criterion == "tube criterion not scored — empty window"


def test_tube_summary_reads_the_inside_fraction():
    t = np.linspace(0.0, 1.0, 11)
    vc = score(
        {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.1}, t, t, t, t + 0.5
    )
    s = summarize(vc)
    assert s.display == "0.0% in tube"
    assert s.criterion.endswith("→ FAIL (requires 100%)")
    assert s.score == 0.0 and s.score_label == "in tube"


def test_range_summary_reads_the_max_violation():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(
        score({"mode": "range", "max_value": 1.0}, t, np.zeros_like(t), t, t * 2)
    )
    assert s.display.startswith("max_viol 1.000e+00")
    assert "→ FAIL (requires 0)" in s.criterion


def test_a_range_on_one_sample_outside_its_bounds_fails_at_that_sample():
    s = summarize(
        score(
            {"mode": "range", "max_value": 1.0},
            np.array([]),
            np.array([]),
            np.array([2.5]),
            np.array([3.0]),
        )
    )
    assert s.criterion == "max violation 2.000e+00 → FAIL (requires 0)"
    assert s.details == (
        ("bounds", "[-inf, 1]"),
        ("worst violation", "2.000000e+00 at t=2.5"),
        ("inside", "0.0% of samples"),
    )


def test_event_timing_summary_counts_events():
    ref_t = np.array([0.0, 1.0, 1.0, 2.0])
    s = summarize(
        score(
            {"mode": "event-timing", "time_tolerance": 1e-3},
            ref_t,
            np.zeros_like(ref_t),
            ref_t,
            np.zeros_like(ref_t),
        )
    )
    assert s.display == "Δt 0.000e+00 (1/1 events)"


def test_nrmse_is_the_default_shape():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(score({"mode": "nrmse"}, t, t, t, t + 0.001, tolerance=0.1))
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
        s = summarize(score({"mode": "nrmse"}, t, t, t, t + 0.001, tolerance=0.1))
        keys = [k for k, _ in s.details]
        assert keys == ["RMSE", "max |err|", "final"]
        assert "at t=" in dict(s.details)["max |err|"]
        assert dict(s.details)["final"].startswith("ref=")

    def test_a_constant_reference_says_so(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(
            score(
                {"mode": "nrmse"},
                t,
                np.full_like(t, 2.0),
                t,
                np.full_like(t, 2.1),
                tolerance=0.1,
            )
        )
        assert "(constant signal)" in dict(s.details)["RMSE"]

    def test_range_never_labels_its_violation_as_nrmse(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(
            score({"mode": "range", "max_value": 1.0}, t, np.zeros_like(t), t, t * 2)
        )
        keys = [k for k, _ in s.details]
        assert "NRMSE" not in keys and "RMSE" not in keys
        assert dict(s.details)["bounds"] == "[-inf, 1]"
        assert dict(s.details)["worst violation"].startswith("1.000000e+00 at t=")

    def test_tube_names_its_worst_violation_and_its_real_companions(self):
        t = np.linspace(0.0, 1.0, 11)
        s = summarize(
            score(
                {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.1},
                t,
                t,
                t,
                t + 0.5,
            )
        )
        d = dict(s.details)
        assert d["worst violation"].startswith("4.000000e-01 at t=")
        assert "max |err|" in d and "final" in d
        # the tube's own measures: its NRMSE against the reference is
        # computed and scores nothing, and is not printed as its number
        assert "NRMSE" not in d

    def test_points_lists_each_failed_checkpoint(self):
        t = np.linspace(0.0, 10.0, 101)
        act = t + 0.05 * (t > 5.0)
        s = summarize(
            score(
                {
                    "mode": "points",
                    "points": [
                        {"time": 2.0, "tolerance": 0.01},
                        {"time": 8.0, "tolerance": 0.01},
                    ],
                },
                t,
                t,
                t,
                act,
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
            "Point at t=8: error −3.000e-01 vs tolerance −5.000e-01 / +2.500e-01 → PASS"
        )
        act[5] += 0.4  # now +0.5 at t=5, the worst
        assert self._points(act, {"lower": 0.5, "upper": 0.25}, rows) == (
            "Point at t=5: error +5.000e-01 vs tolerance −5.000e-01 / +2.500e-01 → FAIL"
        )
        assert self._points(act, 0.5, rows) == (
            "Largest point error 5.000e-01 vs tolerance 5.000e-01 → PASS"
        )

    def test_the_signed_keys_are_recorded_for_uneven_sides_only(self):
        """A leaf with a number carries the diagnostics it always carried."""
        from dstf.comparison.sides import Sides

        rows = [{"time": 5.0}]
        assert "worst_signed_delta" not in (
            score(
                {"mode": "points", "points": rows},
                self.T,
                self.T,
                self.T,
                self.T + 0.1,
                tolerance=0.5,
            ).diagnostics
        )
        uneven = Sides(0.25, 0.5)
        assert score(
            {"mode": "points", "points": rows},
            self.T,
            self.T,
            self.T,
            self.T + 0.1,
            tolerance=uneven,
        ).diagnostics["worst_signed_delta"] == pytest.approx(0.1)
        ref = np.array([0.0, 5.0, 5.0, 10.0])
        act = np.array([0.0, 4.75, 4.75, 10.0])
        for declared in (None, [{"time": 5.0}]):
            even = score(
                {"mode": "event-timing", "time_tolerance": 0.5, "events": declared},
                ref,
                np.zeros_like(ref),
                act,
                np.zeros_like(act),
            )
            assert "max_time_delta_signed" not in even.diagnostics
            odd = score(
                {"mode": "event-timing", "time_tolerance": uneven, "events": declared},
                ref,
                np.zeros_like(ref),
                act,
                np.zeros_like(act),
            )
            assert odd.diagnostics["max_time_delta_signed"] == -0.25

    def test_a_crossing_in_a_box_is_a_signed_zero(self):
        act = self.T.copy()
        act[4], act[6] = 4.5, 5.5  # through the target at t=5
        act[5] = 99.0
        rows = [{"time": 5.0, "time_tolerance": 1.0}]
        crit = self._points(act, {"lower": 0.5, "upper": 0.25}, rows)
        assert crit.startswith("Point at t=5: error +0.000e+00 vs tolerance")

    def _scalar(self, act_final, tolerance, mode, ref_final=4.0):
        from dstf.comparison.sides import parse_sides

        ref = np.full(3, ref_final)
        act = np.array([ref_final, ref_final, act_final])
        vc = score(
            {"mode": "scalar", "quantity": {"reduce": "final"}, "tolerance_mode": mode},
            self.T[:3],
            ref,
            self.T[:3],
            act,
            tolerance=tolerance,
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
            auto = score(
                {"mode": "event-timing", "time_tolerance": sides},
                ref,
                np.zeros_like(ref),
                act,
                np.zeros_like(act),
            )
            assert summarize(auto).criterion == (
                f"Max event Δt {delta} vs {band} "
                f"(1 actual events / 1 reference) → {verdict}"
            )
            declared = score(
                {
                    "mode": "event-timing",
                    "time_tolerance": sides,
                    "events": [{"time": 5.0}],
                },
                ref,
                np.zeros_like(ref),
                act,
                np.zeros_like(act),
            )
            assert declared.passed is (verdict == "PASS")
            # Outside its window a declared event claims nothing: it is not
            # found, and there is no Δt to print.
            assert summarize(declared).criterion == (
                f"Event at t=5: Δt {delta} vs {band} "
                "(1 actual events / 1 reference) → PASS"
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
            score(
                {"mode": "event-timing", "time_tolerance": uneven},
                ref,
                np.zeros_like(ref),
                worst_early,
                np.zeros_like(worst_early),
            )
        ).criterion == (
            "Max event Δt −1.250e-01 vs −2.500e-01 / +5.000e-01 "
            "(2 actual events / 2 reference) → PASS"
        )
        worst_late = np.array([0.0, 2.25, 2.25, 4.875, 4.875, 10.0])
        assert summarize(
            score(
                {"mode": "event-timing", "time_tolerance": uneven},
                ref,
                np.zeros_like(ref),
                worst_late,
                np.zeros_like(worst_late),
            )
        ).criterion == (
            "Max event Δt +2.500e-01 vs −2.500e-01 / +5.000e-01 "
            "(2 actual events / 2 reference) → PASS"
        )
        declared = summarize(
            score(
                {
                    "mode": "event-timing",
                    "time_tolerance": uneven,
                    "events": [{"time": 2.0}, {"time": 5.0}],
                },
                ref,
                np.zeros_like(ref),
                worst_early,
                np.zeros_like(worst_early),
            )
        )
        assert declared.criterion.startswith("Event at t=5: Δt −1.250e-01 vs ")
        assert summarize(
            score(
                {"mode": "event-timing", "time_tolerance": 0.5},
                ref,
                np.zeros_like(ref),
                worst_early,
                np.zeros_like(worst_early),
            )
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
            "Point at t=0.8: error −2.000e-02 vs tolerance "
            "−1.000e-02 / +5.000e-02 → FAIL"
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
        assert crit.startswith("Point at t=0.8: error −4.000e-02 vs ")
        _, crit = self._points({5: -0.04, 8: +0.07}, self.BAND)
        assert crit.startswith("Point at t=0.5: error −4.000e-02 vs ")
        # +0.09 is 0.04 above: now that one.
        _, crit = self._points({5: +0.09, 8: -0.04}, self.BAND)
        assert crit.startswith("Point at t=0.5: error +9.000e-02 vs ")

    def test_points_all_inside_say_the_largest_delta(self):
        _, crit = self._points({5: +0.04, 8: -0.005}, self.BAND)
        assert crit == (
            "Point at t=0.5: error +4.000e-02 vs tolerance "
            "−1.000e-02 / +5.000e-02 → PASS"
        )
        _, crit = self._points({5: +0.004, 8: -0.008}, self.BAND)
        assert crit.startswith("Point at t=0.8: error −8.000e-03 vs ")

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
            "Point at t=0.8: error −5.000e-01 vs tolerance "
            "−1.000e-01 / +5.000e-01 (relative) → FAIL"
        )
        assert vc.max_abs_error == pytest.approx(2.0)

    # Binary-exact numbers: a tie is a tie to the bit.
    TEN = np.linspace(0.0, 10.0, 11)
    TIE = {"lower": 0.125, "upper": 0.25}

    def _ten(self, deltas, tolerance=TIE, ref=None, **row):
        """As ``_points``, on whole times, rows in the order given."""
        from dstf.comparison.modes import resolve_mode

        ref = self.TEN if ref is None else ref
        act = ref.copy()
        for i, d in deltas.items():
            act[i] += d
        rows = [{"time": float(i), **row} for i in deltas]
        mode = resolve_mode({"mode": "points", "points": rows}, tolerance)
        vc = mode.compare(self.TEN, ref, self.TEN, act)
        vc.tolerance_used = mode.tolerance
        return vc, summarize(vc).criterion

    def test_points_equally_far_outside_say_the_first_row(self):
        # +0.5 is 0.25 above the band, −0.375 is 0.25 below it.
        _, crit = self._ten({5: +0.5, 8: -0.375})
        assert crit.startswith("Point at t=5: error +5.000e-01 vs ")
        _, crit = self._ten({8: -0.375, 5: +0.5})
        assert crit.startswith("Point at t=8: error −3.750e-01 vs ")

    def test_points_all_inside_and_equally_far_say_the_first_row(self):
        vc, crit = self._ten({5: +0.125, 8: -0.125})
        assert vc.passed
        assert crit.startswith("Point at t=5: error +1.250e-01 vs ")
        assert vc.max_abs_error_time == 5.0
        vc, crit = self._ten({8: -0.125, 5: +0.125})
        assert crit.startswith("Point at t=8: error −1.250e-01 vs ")
        assert vc.max_abs_error_time == 8.0

    def test_points_relative_rows_rank_by_the_distance_beyond_the_scaled_band(self):
        """In the signal's units, not the band's: at target 1 the band is
        [−0.125, +0.25] and +1.25 lies 1 beyond it — four band-heights; at
        target 16 it is [−2, +4] and +6 lies 2 beyond — half of one. The
        further in the signal's units is the reported one, first or not."""
        ref = np.ones_like(self.TEN)
        ref[5] = 16.0
        for deltas in ({8: +1.25, 5: +6.0}, {5: +6.0, 8: +1.25}):
            vc, crit = self._ten(deltas, ref=ref, tolerance_mode="rel")
            assert vc.diagnostics["failed_points"] == 2
            assert crit == (
                "Point at t=5: error +6.000e+00 vs tolerance "
                "−1.250e-01 / +2.500e-01 (relative) → FAIL"
            )

    def test_points_equal_sides_say_what_a_number_said(self):
        for tolerance in (0.03, {"lower": 0.03, "upper": 0.03}):
            vc, crit = self._points({5: +0.04, 8: -0.02}, tolerance)
            assert crit == "Largest point error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
            assert "worst_signed_delta" not in vc.diagnostics

    def test_declared_rows_never_say_final_value(self):
        """The sentence of a leaf with NO rows — the final value alone is
        scored — was said of declared rows whose worst deferred to an equal
        tolerance: an error at t=0.5, called the final value's. Where it is
        stays in the details."""
        vc, crit = self._points({5: +0.04, 8: -0.02}, 0.03)
        assert crit == "Largest point error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
        # ... the verdict and the score as they were: the largest |Δ|.
        assert not vc.passed and vc.max_abs_error == pytest.approx(0.04)
        assert vc.max_abs_error_time == pytest.approx(0.5)
        assert ("worst delta", "4.000000e-02 at t=0.5") in summarize(vc).details
        vc, crit = self._points({5: +0.01, 8: -0.02}, 0.03)
        assert crit == "Largest point error 2.000e-02 vs tolerance 3.000e-02 → PASS"
        assert vc.passed

    REF = np.array([0.0, 2.0, 2.0, 5.0, 5.0, 10.0])

    def _events(self, first, second, tolerance, declared=None):
        act = np.array([0.0, 2.0 + first, 2.0 + first, 5.0 + second, 5.0 + second, 10])
        vc = score(
            {"mode": "event-timing", "time_tolerance": tolerance, "events": declared},
            self.REF,
            np.zeros_like(self.REF),
            act,
            np.zeros_like(act),
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

    def test_events_equally_far_outside_say_the_first_pair(self):
        # 0.5 late is 0.25 past the window, 0.375 early is 0.25 before it.
        _, crit = self._events(+0.5, -0.375, self.TIE)
        assert crit.startswith("Max event Δt +5.000e-01 vs ")
        _, crit = self._events(-0.375, +0.5, self.TIE)
        assert crit.startswith("Max event Δt −3.750e-01 vs ")

    def test_events_all_inside_and_equally_far_say_the_first_pair(self):
        vc, crit = self._events(+0.125, -0.125, self.TIE)
        assert vc.passed and crit.startswith("Max event Δt +1.250e-01 vs ")
        assert vc.max_abs_error_time == 2.0
        vc, crit = self._events(-0.125, +0.125, self.TIE)
        assert crit.startswith("Max event Δt −1.250e-01 vs ")
        declared = [{"time": 2.0}, {"time": 5.0}]
        vc, crit = self._events(+0.125, -0.125, self.TIE, declared)
        assert crit.startswith("Event at t=2: Δt +1.250e-01 vs ")
        assert vc.max_abs_error_time == 2.0

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
            "Event at t=5: Δt +4.000e-02 vs −1.000e-02 / +5.000e-02 "
            "(2 actual events / 2 reference) → PASS"
        )
        _, crit = self._events(-0.008, +0.004, self.BAND, declared)
        assert crit.startswith("Event at t=2: Δt −8.000e-03 vs ")


def test_an_event_timing_criterion_says_early_and_late():
    from dstf.comparison.sides import Sides

    ref = np.array([0.0, 5.0, 5.0, 10.0])
    act = np.array([0.0, 5.25, 5.25, 10.0])
    even = summarize(
        score(
            {"mode": "event-timing", "time_tolerance": 0.5},
            ref,
            np.zeros_like(ref),
            act,
            np.zeros_like(act),
        )
    )
    assert even.criterion == (
        "Max event Δt 2.500e-01 vs 5.000e-01 (1 actual events / 1 reference) → PASS"
    )
    uneven = summarize(
        score(
            {"mode": "event-timing", "time_tolerance": Sides(0.125, 0.5)},
            ref,
            np.zeros_like(ref),
            act,
            np.zeros_like(act),
        )
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
        vc = score(
            {"mode": "event-timing", "time_tolerance": tolerance, "events": declared},
            ref,
            np.zeros_like(ref),
            act,
            np.zeros_like(act),
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
            source_file=None,
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
            "time": time.tolist(),
            "variables": [{"index": 1, "name": "e", "values": [0.0] * 8}],
        }
        (vc,) = compare_test(
            test, result, decode_reference(reference), default_tolerance=1e-4
        ).variables
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
        vc = score(
            {"mode": "event-timing", "time_tolerance": 0.1},
            ref,
            np.zeros_like(ref),
            act,
            np.zeros_like(act),
        )
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


class TestARowsOwnToleranceIsTheBandTheSentenceNames:
    """A declared point (or event) with its own tolerance is judged on it,
    and the sentence printed the LEAF's: the reported number beside a band
    it was never held to. Where the reported row's sides are not the leaf's
    — a pair or a number of its own — or are relative, or its delta is not
    the largest, or the leaf's are a pair, the sentence names the row and
    ITS sides; a checkpoint never scored is named before any of them. Rows
    that all defer to a leaf's one number, absolute, print what they
    always printed."""

    T = np.linspace(0.0, 1.0, 11)
    PAIR = {"lower": 0.01, "upper": 0.05}
    POINT_KEYS = {
        "scored_points",
        "failed_points",
        "worst_delta",
        "worst_time",
        "point_failures",
    }

    def _points(self, tolerance, rows, ref=None):
        """``rows``: ``(index into T, actual − reference, the row's keys)``."""
        from dstf.comparison.modes import resolve_mode

        ref = self.T if ref is None else ref
        act = ref.copy()
        for i, d, _ in rows:
            act[i] += d
        points = [{"time": float(self.T[i]), **row} for i, _, row in rows]
        mode = resolve_mode({"mode": "points", "points": points}, tolerance)
        vc = mode.compare(self.T, ref, self.T, act)
        vc.tolerance_used = mode.tolerance
        return vc, summarize(vc).criterion

    def test_a_number_leaf_and_a_row_with_its_own_pair(self):
        # −0.02 breaks the row's own lower side of 0.01; +0.025 is inside
        # the leaf's 0.03 — and was the number the sentence printed.
        vc, crit = self._points(
            0.03, [(5, -0.02, {"tolerance": self.PAIR}), (8, +0.025, {})]
        )
        assert not vc.passed
        assert crit == (
            "Point at t=0.5: error −2.000e-02 vs tolerance "
            "−1.000e-02 / +5.000e-02 → FAIL"
        )
        assert vc.diagnostics["worst_sides"] == self.PAIR
        assert vc.diagnostics["worst_sides_relative"] is False
        assert vc.diagnostics["worst_point_time"] == 0.5
        assert vc.diagnostics["worst_signed_delta"] == pytest.approx(-0.02)

    def test_a_pair_leaf_and_a_row_with_its_own_number(self):
        # The row's sides are equal: the magnitude, against its number.
        vc, crit = self._points(
            self.PAIR, [(5, +0.002, {"tolerance": 0.001}), (8, 0.0, {})]
        )
        assert not vc.passed
        assert crit == "Point at t=0.5: error 2.000e-03 vs tolerance 1.000e-03 → FAIL"
        assert vc.diagnostics["worst_sides"] == 0.001

    def test_an_early_row_under_its_own_number_reads_unsigned(self):
        _, crit = self._points(
            self.PAIR, [(5, -0.002, {"tolerance": 0.001}), (8, 0.0, {})]
        )
        assert crit == "Point at t=0.5: error 2.000e-03 vs tolerance 1.000e-03 → FAIL"

    @pytest.mark.parametrize("leaf", [0.03, {"lower": 0.03, "upper": 0.03}])
    def test_rows_deferring_to_an_equal_leaf_record_what_they_did(self, leaf):
        vc, crit = self._points(leaf, [(5, +0.04, {}), (8, -0.02, {})])
        assert crit == "Largest point error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
        assert set(vc.diagnostics) == self.POINT_KEYS

    def test_a_number_leaf_and_a_row_with_its_own_number(self):
        """No pair anywhere, and the failing row is still the one named:
        +0.002 breaks its own 0.001, and the sentence said "error 2.500e-02
        vs tolerance 3.000e-02 → FAIL" — the other row's delta, inside the
        leaf's number, beside a verdict neither explains."""
        vc, crit = self._points(
            0.03, [(5, +0.002, {"tolerance": 0.001}), (8, +0.025, {})]
        )
        assert not vc.passed
        assert crit == "Point at t=0.5: error 2.000e-03 vs tolerance 1.000e-03 → FAIL"
        assert vc.diagnostics["worst_sides"] == 0.001
        assert vc.diagnostics["worst_point_time"] == 0.5

    def test_a_row_whose_own_number_is_the_leafs_records_what_it_did(self):
        """Its sides ARE the leaf's: no key of its own is recorded."""
        vc, crit = self._points(0.03, [(5, +0.04, {"tolerance": 0.03}), (8, -0.02, {})])
        assert crit == "Largest point error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
        assert set(vc.diagnostics) == self.POINT_KEYS

    def test_a_number_row_that_is_not_the_reported_one_changes_nothing(self):
        # +0.0005 is inside the row's own 0.001; −0.05 breaks the leaf's.
        vc, crit = self._points(
            0.03, [(5, +0.0005, {"tolerance": 0.001}), (8, -0.05, {})]
        )
        assert crit == "Largest point error 5.000e-02 vs tolerance 3.000e-02 → FAIL"
        assert set(vc.diagnostics) == self.POINT_KEYS

    def test_a_pair_row_that_is_not_the_reported_one_changes_nothing(self):
        # +0.04 is inside the row's pair; −0.05 breaks the leaf's number.
        vc, crit = self._points(
            0.03, [(5, +0.04, {"tolerance": self.PAIR}), (8, -0.05, {})]
        )
        assert crit == "Largest point error 5.000e-02 vs tolerance 3.000e-02 → FAIL"
        assert set(vc.diagnostics) == self.POINT_KEYS

    def test_all_inside_the_largest_delta_is_the_pair_rows(self):
        vc, crit = self._points(
            0.03, [(5, +0.04, {"tolerance": self.PAIR}), (8, +0.02, {})]
        )
        assert vc.passed
        assert crit == (
            "Point at t=0.5: error +4.000e-02 vs tolerance "
            "−1.000e-02 / +5.000e-02 → PASS"
        )

    def test_a_relative_row_says_so(self):
        # At target 10 the row's band is [−1, +5]: −2 lies 1 below it.
        ref = np.ones_like(self.T)
        ref[5] = 10.0
        row = {"tolerance": {"lower": 0.1, "upper": 0.5}, "tolerance_mode": "rel"}
        vc, crit = self._points(0.03, [(5, -2.0, row), (8, +0.02, {})], ref=ref)
        assert crit == (
            "Point at t=0.5: error −2.000e+00 vs tolerance "
            "−1.000e-01 / +5.000e-01 (relative) → FAIL"
        )
        assert vc.diagnostics["worst_sides_relative"] is True

    @pytest.mark.parametrize("own", [{}, {"tolerance": 0.03}])
    def test_a_relative_row_is_named_though_its_sides_are_the_leafs(self, own):
        """The row defers to the leaf's 0.03, or holds that number itself,
        and is RELATIVE: at target 0.5 its band is 0.015, which +0.02
        breaks. The leaf's sentence read "Final value error 2.000e-02 vs
        tolerance 3.000e-02 → FAIL" — a number inside the band it names."""
        vc, crit = self._points(0.03, [(5, +0.02, {**own, "tolerance_mode": "rel"})])
        assert not vc.passed
        assert crit == (
            "Point at t=0.5: error 2.000e-02 vs tolerance 3.000e-02 (relative) → FAIL"
        )
        assert vc.diagnostics["worst_sides"] == 0.03
        assert vc.diagnostics["worst_sides_relative"] is True

    def test_a_relative_row_that_passes_is_named_too(self):
        vc, crit = self._points(0.03, [(5, +0.01, {"tolerance_mode": "rel"})])
        assert vc.passed
        assert crit == (
            "Point at t=0.5: error 1.000e-02 vs tolerance 3.000e-02 (relative) → PASS"
        )

    def test_the_failing_row_is_named_when_a_passing_one_has_the_larger_delta(self):
        """+0.04 breaks the leaf's 0.03 it defers to; +0.08 is inside its
        row's own 0.1 and is the largest |Δ|. The sentence read "Final value
        error 8.000e-02 vs tolerance 3.000e-02 → FAIL": the passing row's
        delta against a band it was never held to."""
        vc, crit = self._points(0.03, [(5, +0.04, {}), (8, +0.08, {"tolerance": 0.1})])
        assert not vc.passed
        assert crit == "Point at t=0.5: error 4.000e-02 vs tolerance 3.000e-02 → FAIL"
        assert vc.diagnostics["worst_point_time"] == 0.5

    def test_of_two_failing_rows_the_one_furthest_outside_its_own_band(self):
        """Ranked on each row's OWN band: +0.05 lies 0.02 outside the
        leaf's 0.03, +0.022 lies 0.021 outside its row's 0.001 — the
        smaller delta, further out."""
        vc, crit = self._points(
            0.03, [(5, +0.05, {}), (8, +0.022, {"tolerance": 0.001})]
        )
        assert crit == "Point at t=0.8: error 2.200e-02 vs tolerance 1.000e-03 → FAIL"
        # ... and 0.022 outside the leaf's is further: that row defers and
        # its delta is the largest, which is what the leaf's sentence says.
        vc, crit = self._points(
            0.03, [(5, +0.052, {}), (8, +0.022, {"tolerance": 0.001})]
        )
        assert crit == "Largest point error 5.200e-02 vs tolerance 3.000e-02 → FAIL"
        assert set(vc.diagnostics) == self.POINT_KEYS

    def test_a_relative_rows_distance_is_on_its_scaled_band(self):
        """At target 0.5 a relative 0.03 is 0.015: +0.03 lies 0.015 outside
        it, further than +0.04 lies outside the leaf's absolute 0.03."""
        vc, crit = self._points(
            0.03, [(5, +0.03, {"tolerance_mode": "rel"}), (8, +0.04, {})]
        )
        assert crit == (
            "Point at t=0.5: error 3.000e-02 vs tolerance 3.000e-02 (relative) → FAIL"
        )

    @pytest.mark.parametrize(
        ("point", "reason"),
        [
            ({"time": 5.0}, "checkpoint time outside reference range"),
            ({"time": 5.0, "value": 1.0}, "checkpoint time outside actual trajectory"),
        ],
    )
    def test_an_unscored_checkpoint_is_what_the_sentence_says(self, point, reason):
        """A checkpoint outside the reference, or one the run never
        reached, fails unscored — and the sentence named the row beside it,
        which passes: "Final value error 1.000e-02 vs tolerance 3.000e-02 →
        FAIL". It is reported before any scored row, a failing one too."""
        from dstf.comparison.modes import resolve_mode

        act = self.T.copy()
        act[5] += 0.01
        act[8] += 0.04  # a scored row that fails as well
        for points, also in (
            ([{"time": 0.5}, point], ""),
            ([{"time": 0.5}, {"time": 0.8}, point], " (and 1 scored point fails)"),
        ):
            mode = resolve_mode({"mode": "points", "points": points}, 0.03)
            vc = mode.compare(self.T, self.T, self.T, act)
            vc.tolerance_used = mode.tolerance
            assert not vc.passed
            assert summarize(vc).criterion == (
                f"Point at t=5: not scored — {reason}{also} → FAIL"
            )
            assert vc.diagnostics["unscored_point"] == {"time": 5.0, "reason": reason}

    @pytest.mark.parametrize(
        ("ref_time", "act_time", "reason"),
        [
            ("T", "empty", "no actual samples (empty trajectory)"),
            ("empty", "T", "no reference data for ref-relative point"),
        ],
    )
    def test_a_checkpoint_with_nothing_to_score_is_recorded_unscored(
        self, ref_time, act_time, reason
    ):
        """An empty run, or a ref-relative checkpoint with no reference: the
        row fails without a delta, so it is the `unscored_point` the
        sentence reports — not a row among the scored ones."""
        from dstf.comparison.sides import Sides

        times = {"T": self.T, "empty": np.array([])}
        vc = score(
            {"mode": "points", "points": [{"time": 0.5}]},
            times[ref_time],
            times[ref_time],
            times[act_time],
            times[act_time],
            tolerance=Sides(0.03, 0.03),
        )
        vc.tolerance_used = Sides(0.03, 0.03)
        assert not vc.passed
        assert vc.diagnostics["unscored_point"] == {"time": 0.5, "reason": reason}
        assert vc.diagnostics["unscored_points"] == 1
        assert summarize(vc).criterion == (
            f"Point at t=0.5: not scored — {reason} → FAIL"
        )

    @pytest.mark.parametrize(
        ("failing", "also"),
        [
            ((), ""),
            ((5,), " (and 1 scored point fails)"),
            ((5, 8), " (and 2 scored points fail)"),
        ],
    )
    def test_an_unscored_checkpoint_goes_ahead_of_a_named_failing_row(
        self, failing, also
    ):
        """Rows holding their OWN tolerance are named by the sentence
        ("Point at t=0.5: error …") — and still the checkpoint never scored
        is the one reported, with how many scored rows fail beside it: the
        sentence hid that one did. Two checkpoints are outside the
        reference here, and neither counts as a scored failure."""
        from dstf.comparison.modes import resolve_mode

        act = self.T.copy()
        for i in failing:
            act[i] += 0.04
        points = [
            {"time": 0.5, "tolerance": 0.001},
            {"time": 0.8, "tolerance": 0.001},
            {"time": 5.0},
            {"time": 6.0},
        ]
        mode = resolve_mode({"mode": "points", "points": points}, 0.03)
        vc = mode.compare(self.T, self.T, self.T, act)
        vc.tolerance_used = mode.tolerance
        assert "worst_sides" in vc.diagnostics  # a scored row IS named
        assert vc.diagnostics["failed_points"] == 2 + len(failing)
        assert vc.diagnostics["unscored_points"] == 2
        assert summarize(vc).criterion == (
            "Point at t=5: not scored — checkpoint time outside reference range"
            f"{also} → FAIL"
        )

    def test_the_first_unscored_checkpoint_is_the_one_named(self):
        from dstf.comparison.modes import resolve_mode

        points = [{"time": 7.0, "value": 1.0}, {"time": 5.0}]
        mode = resolve_mode({"mode": "points", "points": points}, 0.03)
        vc = mode.compare(self.T, self.T, self.T, self.T)
        assert summarize(vc).criterion == (
            "Point at t=7: not scored — checkpoint time outside actual trajectory → FAIL"
        )

    def test_the_trace_end_row_is_named_by_its_time(self):
        act = self.T.copy()
        act[-1] += 0.02
        vc = score(
            {"mode": "points", "points": [{"time": None}]},
            self.T,
            self.T,
            self.T,
            act,
            tolerance={"lower": 0.01, "upper": 0.05},
        )
        assert summarize(vc).criterion.startswith("Point at t=1: error +2.000e-02 ")

    def test_a_row_with_a_time_box_is_named_by_its_declared_time(self):
        """Not by where in its box the curve came closest (the box's first
        sample here, t=0.4), which is the score's time."""
        from dstf.comparison.sides import Sides

        ref = np.ones_like(self.T)
        act = ref.copy()
        act[4:7] += 0.1
        vc = score(
            {"mode": "points", "points": [{"time": 0.5, "time_tolerance": 0.1}]},
            self.T,
            ref,
            self.T,
            act,
            tolerance=Sides(0.01, 0.05),
        )
        assert vc.max_abs_error_time == pytest.approx(0.4)
        assert vc.diagnostics["worst_point_time"] == 0.5
        assert summarize(vc).criterion == (
            "Point at t=0.5: error +1.000e-01 vs tolerance "
            "−1.000e-02 / +5.000e-02 → FAIL"
        )

    def test_with_no_scored_point_the_unscored_one_is_named(self):
        """A checkpoint the run never reached is not scored, and it is the
        failure: the sentence read "Final value error +0.000e+00 vs
        tolerance −1.000e+00 / +2.000e+00 → FAIL", an error of nothing."""
        from dstf.comparison.sides import Sides

        vc = score(
            {"mode": "points", "points": [{"time": 5.0, "value": 1.0}]},
            self.T,
            self.T,
            self.T,
            self.T,
            tolerance=Sides(1, 2),
        )
        vc.tolerance_used = Sides(1, 2)
        assert vc.diagnostics["scored_points"] == 0
        assert "worst_sides" not in vc.diagnostics
        assert summarize(vc).criterion == (
            "Point at t=5: not scored — checkpoint time outside actual trajectory → FAIL"
        )

    @pytest.mark.parametrize(
        ("extra", "failing", "said"),
        [
            # No checkpoint unscored: the line as it always read.
            ([], (), "0 of 2 failed"),
            ([], (5,), "1 of 2 failed"),
            ([], (5, 8), "2 of 2 failed"),
            # One or two outside the reference: counted among the declared,
            # and said.
            ([5.0], (), "1 of 3 failed, 1 not scored"),
            ([5.0], (5, 8), "3 of 3 failed, 1 not scored"),
            ([5.0, 6.0], (8,), "3 of 4 failed, 2 not scored"),
        ],
    )
    def test_the_checkpoints_line_counts_failures_of_the_declared_rows(
        self, extra, failing, said
    ):
        """`failed_points` counts an unscored checkpoint and `scored_points`
        does not: with one outside the reference beside two scored rows that
        fail, the detail read "3 of 2 failed". The failures are of the
        DECLARED rows — scored and not — and how many were not scored is
        said beside them."""
        from dstf.comparison.modes import resolve_mode

        act = self.T.copy()
        for i in failing:
            act[i] += 0.04
        points = [{"time": 0.5}, {"time": 0.8}, *({"time": t} for t in extra)]
        mode = resolve_mode({"mode": "points", "points": points}, 0.03)
        vc = mode.compare(self.T, self.T, self.T, act)
        vc.tolerance_used = mode.tolerance
        assert vc.diagnostics["scored_points"] == 2
        assert vc.diagnostics["failed_points"] == len(failing) + len(extra)
        assert dict(summarize(vc).details)["checkpoints"] == said

    def test_with_no_scored_point_the_checkpoints_line_counts_the_unscored(self):
        from dstf.comparison.sides import Sides

        vc = score(
            {"mode": "points", "points": [{"time": 5.0, "value": 1.0}]},
            self.T,
            self.T,
            self.T,
            self.T,
            tolerance=Sides(1, 2),
        )
        assert dict(summarize(vc).details)["checkpoints"] == (
            "1 of 1 failed, 1 not scored"
        )

    # ---- declared events

    EVENT_KEYS = {
        "ref_event_count",
        "act_event_count",
        "max_time_delta",
        "time_tolerance",
        "counts_match",
        "unmatched_events",
        "declared_events",
    }

    def _events(self, actual, tolerance, rows):
        act = np.array([0.0, *(t for at in actual for t in (at, at)), 10.0])
        vc = score(
            {"mode": "event-timing", "time_tolerance": tolerance, "events": rows},
            np.array([]),
            np.array([]),
            act,
            np.zeros_like(act),
        )
        return vc, summarize(vc).criterion

    def test_a_number_leaf_and_an_event_with_its_own_pair(self):
        # 0.3 early is inside the event's own window, far outside the leaf's.
        own = {"lower": 0.5, "upper": 0.05}
        vc, crit = self._events(
            [2.0, 4.7, 8.0],
            0.1,
            [{"time": 2.0}, {"time": 5.0, "tolerance": own}, {"time": 8.0}],
        )
        assert vc.passed
        assert crit == (
            "Event at t=5: Δt −3.000e-01 vs −5.000e-01 / +5.000e-02 "
            "(3 actual events / 3 reference) → PASS"
        )
        assert vc.diagnostics["worst_sides"] == own
        assert vc.diagnostics["worst_event_time"] == 5.0
        assert vc.diagnostics["max_time_delta_signed"] == pytest.approx(-0.3)

    def test_a_pair_leaf_and_an_event_with_its_own_number(self):
        vc, crit = self._events(
            [2.0, 5.3, 8.0],
            {"lower": 0.01, "upper": 0.05},
            [{"time": 2.0}, {"time": 5.0, "tolerance": 0.5}, {"time": 8.0}],
        )
        assert vc.passed
        assert crit == (
            "Event at t=5: Δt 3.000e-01 vs 5.000e-01 "
            "(3 actual events / 3 reference) → PASS"
        )
        assert vc.diagnostics["worst_sides"] == 0.5

    @pytest.mark.parametrize("leaf", [0.1, {"lower": 0.1, "upper": 0.1}])
    def test_events_deferring_to_an_equal_leaf_read_as_they_did(self, leaf):
        vc, crit = self._events(
            [2.0, 5.05, 8.0], leaf, [{"time": 2.0}, {"time": 5.0}, {"time": 8.0}]
        )
        assert crit == (
            "Max event Δt 5.000e-02 vs 1.000e-01 (3 actual events / 3 reference) → PASS"
        )
        assert set(vc.diagnostics) == self.EVENT_KEYS

    def test_a_number_leaf_and_an_event_with_its_own_number(self):
        """0.3 late is inside the event's own 0.5: the sentence said "Δt
        3.000e-01 vs 1.000e-01 → PASS", three times the window it named."""
        vc, crit = self._events(
            [2.0, 5.3, 8.0],
            0.1,
            [{"time": 2.0}, {"time": 5.0, "tolerance": 0.5}, {"time": 8.0}],
        )
        assert vc.passed
        assert crit == (
            "Event at t=5: Δt 3.000e-01 vs 5.000e-01 "
            "(3 actual events / 3 reference) → PASS"
        )
        assert vc.diagnostics["worst_sides"] == 0.5
        assert vc.diagnostics["worst_event_time"] == 5.0

    def test_an_event_whose_own_number_is_the_leafs_reads_as_it_did(self):
        vc, crit = self._events(
            [2.0, 5.05, 8.0],
            0.1,
            [{"time": 2.0}, {"time": 5.0, "tolerance": 0.1}, {"time": 8.0}],
        )
        assert crit == (
            "Max event Δt 5.000e-02 vs 1.000e-01 (3 actual events / 3 reference) → PASS"
        )
        assert set(vc.diagnostics) == self.EVENT_KEYS

    def test_a_pair_event_that_is_not_the_reported_one_changes_nothing(self):
        vc, crit = self._events(
            [2.05, 5.0, 8.0],
            0.1,
            [{"time": 2.0}, {"time": 5.0, "tolerance": {"lower": 0.5, "upper": 0.05}}],
        )
        assert crit == (
            "Max event Δt 5.000e-02 vs 1.000e-01 (3 actual events / 2 reference) → FAIL"
        )
        assert set(vc.diagnostics) == self.EVENT_KEYS

    def test_an_exact_event_is_named_by_its_own_time(self):
        """Every Δt is zero: the first declared event is the reported one,
        and its time is the sentence's — not the 0 the score's time stays."""
        vc, crit = self._events(
            [2.0, 5.0], {"lower": 0.01, "upper": 0.05}, [{"time": 2.0}, {"time": 5.0}]
        )
        assert crit == (
            "Event at t=2: Δt +0.000e+00 vs −1.000e-02 / +5.000e-02 "
            "(2 actual events / 2 reference) → PASS"
        )
        assert vc.max_abs_error_time == 0.0

    def test_no_declared_event_reads_as_it_did(self):
        vc, crit = self._events([2.0], {"lower": 0.01, "upper": 0.05}, [])
        assert "worst_sides" not in vc.diagnostics
        assert crit == (
            "Max event Δt +0.000e+00 vs −1.000e-02 / +5.000e-02 "
            "(1 actual events / 0 reference) → FAIL"
        )

    def test_an_unmatched_event_is_still_what_the_sentence_says(self):
        _, crit = self._events(
            [2.0, 4.7],
            0.1,
            [
                {"time": 2.0},
                {"time": 5.0, "tolerance": {"lower": 0.5, "upper": 0.05}},
                {"time": 8.0},
            ],
        )
        assert crit.startswith("1 of 3 declared events not found within tolerance ")

    def test_auto_detected_events_keep_the_leafs_sentence(self):
        ref = np.array([0.0, 5.0, 5.0, 10.0])
        act = np.array([0.0, 5.04, 5.04, 10.0])
        vc = score(
            {"mode": "event-timing", "time_tolerance": self.PAIR},
            ref,
            np.zeros_like(ref),
            act,
            np.zeros_like(act),
        )
        assert "worst_sides" not in vc.diagnostics
        assert summarize(vc).criterion == (
            "Max event Δt +4.000e-02 vs −1.000e-02 / +5.000e-02 "
            "(1 actual events / 1 reference) → PASS"
        )
