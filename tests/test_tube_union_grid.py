"""The tube is enforced between reference samples, not only at them.

A tolerance tube is a continuous envelope; the reference grid is only the
coarse basis on which it is declared. These tests pin the two defects that
followed from evaluating it on ``ref_time`` alone.
"""

import numpy as np
import pytest

from dstf.comparison.geometry import Band
from dstf.comparison.sides import Sides
from scoring import score

BAND_HALF_WIDTH = {"tube_width_mode": "band", "tube_abs": 0.5}


def _band(cfg, ref_time, ref_values, act_time, act_values) -> Band:
    """The band a tube draws: the envelope it scored, on the grid it
    scored it on."""
    vc = score({"mode": "tube", **cfg}, ref_time, ref_values, act_time, act_values)
    return next(p for p in vc.geometry if isinstance(p, Band))


def _flat_reference(n_points=4, stop=3.0):
    t = np.linspace(0.0, stop, n_points)
    return t, np.zeros_like(t)


class TestEvalGrid:
    def test_grid_is_the_union_of_all_breakpoints(self):
        ref_time = np.array([0.0, 1.0, 2.0, 3.0])
        act_time = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        cfg = {
            "tube_width_mode": "band",
            "tube_points": [{"time": 1.25, "upper": 0.5, "lower": 0.5}],
        }
        band = _band(cfg, ref_time, np.zeros(4), act_time, np.zeros(7))
        assert band.time.tolist() == [0.0, 0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]

    def test_grid_is_clipped_to_the_reference_span(self):
        # The band is only declared where the reference exists, so an actual
        # sample past the end of the baseline has no envelope to be judged
        # against. This also keeps the evaluated span identical to what
        # evaluating on ref_time gave.
        act_time = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        band = _band(
            BAND_HALF_WIDTH, np.array([0.0, 1.0]), np.zeros(2), act_time, np.zeros(5)
        )
        assert band.time.tolist() == [0.0, 0.5, 1.0]

    def test_duplicate_timestamps_collapse_to_one_grid_point(self):
        # A discontinuity is stored as two samples at one time. This grid
        # carries the instant once, which loses the pre-event band centre --
        # which is why the tube mode keeps the reference-grid check too. See
        # TestDuplicateReferenceTimestamps.
        time = np.array([0.0, 1.0, 1.0, 2.0])
        band = _band(BAND_HALF_WIDTH, time, np.zeros(4), time.copy(), np.zeros(4))
        assert band.time.tolist() == [0.0, 1.0, 2.0]


class TestExcursionBetweenReferenceSamples:
    def test_an_excursion_inside_one_reference_interval_fails(self):
        # Flat-zero baseline on a 1.0 s grid, band +/- 0.5. The run samples
        # every 0.1 s and excurses to 2.0 entirely between t=1 and t=2, so a
        # tube evaluated only at 0,1,2,3 sees zero at all four.
        ref_time, ref_values = _flat_reference()
        act_time = np.arange(0.0, 3.0001, 0.1)
        act_values = np.zeros_like(act_time)
        act_values[(act_time > 1.2) & (act_time < 1.8)] = 2.0

        result = score(
            {"mode": "tube", **BAND_HALF_WIDTH},
            ref_time,
            ref_values,
            act_time,
            act_values,
        )

        assert result.passed is False
        assert result.tube_worst_violation == pytest.approx(1.5)
        assert 1.2 <= result.tube_worst_violation_time <= 1.8

    def test_a_trajectory_inside_the_band_everywhere_still_passes(self):
        ref_time, ref_values = _flat_reference()
        act_time = np.arange(0.0, 3.0001, 0.1)
        act_values = np.full_like(act_time, 0.25)

        result = score(
            {"mode": "tube", **BAND_HALF_WIDTH},
            ref_time,
            ref_values,
            act_time,
            act_values,
        )

        assert result.passed is True
        assert result.tube_points_inside == pytest.approx(1.0)


class TestControlPointsOffTheReferenceGrid:
    def test_a_control_point_the_baseline_does_not_sample_is_honoured(self):
        # Band tapers from width 1.0 at t=0 to 0.1 at t=1.5; the baseline
        # samples 0,1,2,3, so t=1.5 is not on its grid. Reconstructing the
        # widths from ref_time alone gives an upper bound of 0.25 at t=1.5, so
        # an actual of 0.12 there would pass a band declared as 0.10. Grid
        # density cannot fix this -- the control point has to be evaluated.
        config = {
            "tube_width_mode": "band",
            "tube_interpolation": "linear",
            "tube_points": [
                {"time": 0.0, "upper": 1.0, "lower": 1.0},
                {"time": 1.5, "upper": 0.1, "lower": 0.1},
            ],
        }

        result = score(
            {"mode": "tube", **config},
            np.array([0.0, 1.0, 2.0, 3.0]),
            np.zeros(4),
            np.array([0.0, 1.0, 1.5, 2.0, 3.0]),
            np.array([0.0, 0.35, 0.12, 0.1, 0.1]),
        )

        assert result.passed is False
        assert result.tube_worst_violation_time == pytest.approx(1.5)


class TestReportedErrorIsUnchanged:
    def test_the_largest_error_stays_on_the_reference_grid(self):
        # The verdict moved to the union grid; the largest error the summary
        # reports (`max |err|`) did not: it is read where the baseline has a
        # sample, as an nrmse criterion reads the same pair. The run spikes
        # to 1.0 at t=0.5, between two baseline samples; at the baseline's
        # samples it is 0, 0.2 and 0.
        result = score(
            {"mode": "tube", "tube_width_mode": "band", "tube_abs": 2.0},
            np.array([0.0, 1.0, 2.0]),
            np.zeros(3),
            np.array([0.0, 0.5, 1.0, 1.5, 2.0]),
            np.array([0.0, 1.0, 0.2, 0.0, 0.0]),
        )
        assert result.passed is True
        assert (result.max_abs_error, result.max_abs_error_time) == (0.2, 1.0)


class TestDiscontinuitiesInTheBaseline:
    """A discontinuity is stored as two samples at one timestamp."""

    CONFIG = {"tube_width_mode": "band", "tube_abs": 0.25}

    def test_a_faithful_reproduction_of_a_jump_passes(self):
        # Indexing ref_values directly would compare the PRE-event band centre
        # against the POST-event actual and report a violation of the full jump
        # height -- a false FAIL on a perfect reproduction. Interpolating both
        # sides onto the collapsed instant agrees with itself, so this passes.
        ref_time = np.array([0.0, 1.0, 1.0, 2.0])
        ref_values = np.array([0.0, 0.0, 1.0, 1.0])

        result = score(
            {"mode": "tube", **self.CONFIG},
            ref_time,
            ref_values,
            ref_time.copy(),
            ref_values.copy(),
        )

        assert result.passed is True

    def test_a_wrong_value_beside_the_jump_still_fails(self):
        # The samples either side of a discontinuity are ordinary grid points
        # and are checked normally, which is what makes the gap below narrow.
        ref_time = np.array([0.0, 0.9, 1.0, 1.0, 2.0])
        ref_values = np.array([0.0, 0.0, 0.0, 1.0, 1.0])
        act_values = np.array([0.0, 0.75, 0.0, 1.0, 1.0])

        result = score(
            {"mode": "tube", **self.CONFIG},
            ref_time,
            ref_values,
            ref_time.copy(),
            act_values,
        )

        assert result.passed is False


class TestKnownGapAtDiscontinuities:
    """Pins a gap this change does NOT close, so the follow-up has a target.

    At a duplicated timestamp the pre-event band centre is not checked, so an
    actual that is wrong ONLY at that instant -- and correct at every
    surrounding sample -- passes. Closing this means pairing pre with pre and
    post with post, and widening to the union of both bands where only one
    series carries the duplicate. When that lands, this test flips and should
    be rewritten to assert the failure.
    """

    def test_a_wrong_pre_event_value_is_currently_missed(self):
        ref_time = np.array([0.0, 1.0, 1.0, 2.0])
        ref_values = np.array([0.0, 0.0, 1.0, 1.0])
        act_values = np.array([0.0, 0.75, 1.0, 1.0])  # 3x the band, pre-event only

        result = score(
            {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.25},
            ref_time,
            ref_values,
            ref_time.copy(),
            act_values,
        )

        assert result.passed is True, "gap closed -- rewrite this test to assert FAIL"


class TestScopedExactnessProperty:
    """The guarantee the docstring claims, as a runnable check.

    For ``band`` and ``abs`` widths with linear interpolation and no
    ``tube_min_width``, the worst violation the tube reports must be every
    violation a dense scan of the same continuous envelope finds. This is the
    fence for the claim; the three documented exceptions (``rel``,
    ``tube_min_width``, ``constant`` interpolation) are deliberately excluded.
    """

    @staticmethod
    def _dense_worst(ref_time, ref_values, act_time, act_values, width):
        """The worst violation of ``ref ± width`` on 20,000 points of the
        reference's span, both curves read as the scorer reads them."""
        dense = np.linspace(ref_time[0], ref_time[-1], 20000)
        ref = np.interp(dense, ref_time, ref_values)
        act = np.interp(dense, act_time, act_values)
        return float(np.max(np.maximum(act - (ref + width), (ref - width) - act)))

    def test_union_grid_matches_a_dense_scan(self):
        rng = np.random.default_rng(4242)
        worst_miss = 0.0
        for _ in range(400):
            n_ref = int(rng.integers(3, 10))
            n_act = int(rng.integers(3, 40))
            ref_time = np.sort(rng.uniform(0.0, 10.0, n_ref))
            ref_time = np.unique(ref_time)
            act_time = np.unique(np.sort(rng.uniform(0.0, 10.0, n_act)))
            if len(ref_time) < 2 or len(act_time) < 2:
                continue
            ref_values = rng.uniform(-2.0, 2.0, len(ref_time))
            act_values = rng.uniform(-3.0, 3.0, len(act_time))
            width = float(rng.uniform(0.05, 2.0))
            scored = score(
                {"mode": "tube", "tube_width_mode": "band", "tube_abs": width},
                ref_time,
                ref_values,
                act_time,
                act_values,
            )
            on_dense = self._dense_worst(
                ref_time, ref_values, act_time, act_values, width
            )
            worst_miss = max(worst_miss, on_dense - scored.tube_worst_violation)

        assert worst_miss < 1e-9, f"dense scan beat the union grid by {worst_miss:g}"


class TestDegenerateInput:
    def test_a_nan_reference_bound_fails_rather_than_crashing(self):
        # The span clip compares against ref_time[0] / ref_time[-1]; a NaN there
        # makes every comparison False and empties the grid. That must not
        # reach np.argmax.
        result = score(
            {"mode": "tube", **BAND_HALF_WIDTH},
            np.array([0.0, 1.0, np.nan]),
            np.array([0.0, 0.0, 0.0]),
            np.array([0.0, 0.5, 1.0]),
            np.array([0.0, 0.0, 0.0]),
        )
        assert result.passed is False


class TestAConstantBandsTwoSides:
    """``tube_abs`` / ``tube_rel`` as a lower/upper pair: the band's edges
    are ``ref + upper`` and ``ref − lower`` (those fractions of ``|ref|`` in
    ``rel``)."""

    T = np.linspace(0.0, 10.0, 11)

    def _tube(self, offset, cfg):
        ref = np.full_like(self.T, 10.0)
        return score({"mode": "tube", **cfg}, self.T, ref, self.T, ref + offset)

    @pytest.mark.parametrize(
        "cfg,off",
        [
            # band: −0.125 / +0.5 around 10.
            ({"tube_width_mode": "band", "tube_abs": Sides(0.125, 0.5)}, 0.375),
            # rel: −12.5 % / +50 % of |10| = −1.25 / +5.
            ({"tube_width_mode": "rel", "tube_rel": Sides(0.125, 0.5)}, 3.75),
            (
                {"tube_width_mode": "band", "tube_abs": {"lower": 0.125, "upper": 0.5}},
                0.375,
            ),
        ],
        ids=["band", "rel", "band-from-a-dict"],
    )
    def test_it_holds_a_curve_above_and_fails_one_below(self, cfg, off):
        assert self._tube(off, cfg).passed
        assert not self._tube(-off, cfg).passed

    def test_the_sides_swapped_is_the_mirror(self):
        cfg = {"tube_width_mode": "band", "tube_abs": Sides(0.5, 0.125)}
        assert self._tube(-0.375, cfg).passed
        assert not self._tube(0.375, cfg).passed

    @pytest.mark.parametrize("at", [2.5, 5.0], ids=["between-samples", "on-a-sample"])
    def test_the_lower_side_is_enforced_between_reference_samples_and_on_them(self, at):
        # An excursion BETWEEN two reference samples is seen on the union
        # grid only; one ON a reference sample by either grid.
        ref_t = np.array([0.0, 5.0, 10.0])
        act_t = np.array([0.0, 2.5, 5.0, 10.0])
        ref = np.full(3, 10.0)
        cfg = {"tube_width_mode": "band", "tube_abs": Sides(0.125, 0.5)}

        def act(offset):
            return np.where(act_t == at, 10.0 + offset, 10.0)

        above = score({"mode": "tube", **cfg}, ref_t, ref, act_t, act(0.375))
        below = score({"mode": "tube", **cfg}, ref_t, ref, act_t, act(-0.375))
        assert above.passed and not below.passed
        assert below.tube_worst_violation == 0.25
        assert below.tube_worst_violation_time == at

    def test_the_band_carries_each_edge(self):
        from dstf.comparison.geometry import Band

        vc = self._tube(0.0, {"tube_width_mode": "band", "tube_abs": Sides(0.125, 0.5)})
        band = next(p for p in vc.geometry if isinstance(p, Band))
        assert np.array_equal(band.upper, np.full_like(band.time, 10.5))
        assert np.array_equal(band.lower, np.full_like(band.time, 9.875))
        vc = self._tube(0.0, {"tube_width_mode": "rel", "tube_rel": Sides(0.125, 0.5)})
        band = next(p for p in vc.geometry if isinstance(p, Band))
        assert np.array_equal(band.upper, np.full_like(band.time, 15.0))
        assert np.array_equal(band.lower, np.full_like(band.time, 8.75))

    def test_the_minimum_width_floors_both_sides(self):
        cfg = {
            "tube_width_mode": "band",
            "tube_abs": Sides(0.0, 0.5),
            "tube_min_width": 0.25,
        }
        ref = np.full_like(self.T, 10.0)
        band = _band(cfg, self.T, ref, self.T, ref)
        assert (band.upper[0], band.lower[0]) == (10.5, 9.75)

    def test_a_number_and_its_equal_pair_build_the_same_band(self):
        ref = np.linspace(-1.0, 3.0, 11)
        for key, mode in (("tube_abs", "band"), ("tube_rel", "rel")):
            a = _band({"tube_width_mode": mode, key: 0.3}, self.T, ref, self.T, ref)
            b = _band(
                {"tube_width_mode": mode, key: Sides(0.3, 0.3)},
                self.T,
                ref,
                self.T,
                ref,
            )
            assert np.array_equal(a.upper, b.upper)
            assert np.array_equal(a.lower, b.lower)


class TestTubeConfigKeepsAOneSidedWidth:
    def test_to_dict_omits_only_a_width_that_is_zero_on_both_sides(self):
        from dstf.comparison.modes import TubeConfig

        assert TubeConfig().to_dict() == {}
        # A dataclass is always truthy and its upper side may be the zero one.
        assert TubeConfig(tube_abs=Sides(0.5, 0.0)).to_dict() == {
            "tube_abs": Sides(0.5, 0.0)
        }
        assert TubeConfig(tube_rel=Sides(0.0, 0.5)).to_dict() == {
            "tube_rel": Sides(0.0, 0.5)
        }
        assert "tube_rel" not in TubeConfig(tube_abs=Sides(0.0, 0.5)).to_dict()
        assert TubeConfig(tube_abs=0.0, tube_rel=Sides(0.0, 0.0)).to_dict() == {}

    def test_a_band_with_room_below_only_scores_through_the_mode(self):
        from dstf.comparison.modes import resolve_mode

        t = np.linspace(0.0, 10.0, 11)
        ref = np.full_like(t, 10.0)
        mode = resolve_mode(
            {
                "mode": "tube",
                "tube_width_mode": "band",
                "tube_abs": {"lower": 0.5, "upper": 0},
            },
            1e-4,
        )
        assert mode.compare(t, ref, t, ref - 0.375).passed
        assert not mode.compare(t, ref, t, ref + 0.375).passed
