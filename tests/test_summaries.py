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

    def _scalar(self, act_final, tolerance, mode):
        from dstf.comparison.algorithms import _compare_scalar
        from dstf.comparison.sides import parse_sides

        ref = np.full(3, 4.0)
        act = np.array([4.0, 4.0, act_final])
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
