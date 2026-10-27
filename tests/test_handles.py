"""The inverse of each mode's handles (``comparison/handles.py``): a dragged
position back to a raw leaf, and §6's "every handle's id resolves to the spec
path it edits" over the Session."""

from __future__ import annotations

import copy
import re

import numpy as np
import pytest

from dstf.authoring.session import Session
from dstf.authoring.spec_document import PatchOp
from dstf.comparison.geometry import Box, HLine, Marker
from dstf.comparison.handles import HandleEdit, HandleEditError
from dstf.comparison.registry import metric
from test_session import A, D, _project

REF = (np.array([0, 0.25, 0.5, 0.75, 1.0]), np.array([0, 0.25, 0.5, 0.75, 1.0]))
# A TRANSFORM-sized baseline: time 0..10, where a pixel is ~1e-3 of the span.
WIDE = (np.linspace(0.0, 10.0, 2001), np.sin(np.linspace(0.0, 10.0, 2001)))

TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.5, "upper": 0.1, "lower": 0.2},
                {"time": 0.9, "upper": 0.1, "lower": 0.1},
            ],
        },
        {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 2.0},
        {
            "metric": "points",
            "variable": "x",
            "points": [
                {"time": 0.25, "tolerance": 0.05},
                {"time": 0.75, "value": 0.75, "tolerance": 0.05},
            ],
        },
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": "max"},
            "min_value": 0.0,
            "max_value": 2.0,
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "rel",
            "tube_points": [{"time": 0.5, "upper": 0.1, "lower": 0.1}],
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "abs",
            "tube_points": [{"time": 0.0, "upper": 1.5, "lower": -0.5}],
        },
    ],
}

PEAKS = {
    "metric": "dominant-frequency",
    "variable": "x",
    "peaks": [{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
}

BAND, RANGE, POINTS, SCALAR, REL, ABS = TREE["children"]


def _edit(leaf, local, x=None, y=None, reference=REF, span=None):
    """Call the leaf's metric's editor, and check it left the input alone."""
    before = copy.deepcopy(leaf)
    try:
        return metric(leaf["metric"]).edit_handle(
            HandleEdit(leaf=leaf, local=local, x=x, y=y, reference=reference, span=span)
        )
    finally:
        assert leaf == before


def _with(leaf, **extra):
    return {**copy.deepcopy(leaf), **extra}


class TestTube:
    def test_band(self):
        new = _edit(BAND, "tube/upper/0", 0.5, 0.8)
        assert new["tube_points"][0] == {
            "time": 0.5,
            "upper": pytest.approx(0.3),
            "lower": 0.2,
        }
        # Written to 12 significant digits: 0.8 - 0.5 is
        # 0.30000000000000004 in floats, never what the spec file shows.
        assert new["tube_points"][0]["upper"] == 0.3
        new = _edit(BAND, "tube/lower/1", 0.9, 0.6)
        assert new["tube_points"][1]["lower"] == pytest.approx(0.3)
        assert new["tube_points"][1]["upper"] == 0.1
        # Width measured against ref at the NEW time: ref(0.6) = 0.6.
        new = _edit(BAND, "tube/upper/0", 0.6, 0.8)
        assert new["tube_points"][0]["time"] == 0.6
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)
        new = _edit(BAND, "tube/upper/0", None, 0.8)
        assert new["tube_points"][0]["time"] == 0.5
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)

    def test_rel(self):
        new = _edit(REL, "tube/upper/0", 0.5, 0.6)
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)
        with pytest.raises(HandleEditError, match="reference is zero"):
            _edit(REL, "tube/upper/0", 0.0, 0.1)

    def test_abs(self):
        new = _edit(ABS, "tube/lower/0", 0.0, -1.0)
        assert new["tube_points"][0]["lower"] == -1.0
        with pytest.raises(HandleEditError, match="below lower"):
            _edit(ABS, "tube/upper/0", 0.0, -0.6)
        with pytest.raises(HandleEditError, match="above upper"):
            _edit(ABS, "tube/lower/0", 0.0, 1.6)

    def test_abs_missing_side_reads_as_zero(self):
        # The side NOT dragged is the missing one, so its 0.0 is what is read.
        leaf = _with(ABS, tube_points=[{"time": 0.0, "upper": 1.0}])
        with pytest.raises(HandleEditError, match="below lower"):
            _edit(leaf, "tube/upper/0", None, -0.1)
        assert _edit(leaf, "tube/upper/0", None, 0.1)["tube_points"][0]["upper"] == 0.1
        leaf = _with(ABS, tube_points=[{"time": 0.0, "lower": -1.0}])
        with pytest.raises(HandleEditError, match="above upper"):
            _edit(leaf, "tube/lower/0", None, 0.1)
        assert (
            _edit(leaf, "tube/lower/0", None, -0.1)["tube_points"][0]["lower"] == -0.1
        )

    def test_negative_width_refused(self):
        with pytest.raises(HandleEditError, match="negative"):
            _edit(BAND, "tube/upper/0", 0.5, 0.4)
        with pytest.raises(HandleEditError, match="negative"):
            _edit(BAND, "tube/lower/0", 0.5, 0.6)
        with pytest.raises(HandleEditError, match="negative"):
            _edit(REL, "tube/upper/0", 0.5, 0.4)

    def test_time_outside_the_reference_span_is_clamped_to_its_edge(self):
        # The y part of the drag still applies, at the clamped time.
        new = _edit(BAND, "tube/upper/0", 1.5, 1.3)
        assert new["tube_points"][0]["time"] == 1.0
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)
        new = _edit(ABS, "tube/upper/0", -0.1, 1.2)
        assert new["tube_points"][0] == {"time": 0.0, "upper": 1.2, "lower": -0.5}

    def test_a_handle_grabbed_on_the_span_edge_keeps_its_vertical_drag(self):
        # The pixel round trip sends a ring at t=1.0 back a hair outside.
        leaf = _with(
            BAND,
            tube_points=[
                {"time": 0.0, "upper": 0.1, "lower": 0.1},
                {"time": 1.0, "upper": 0.1, "lower": 0.1},
            ],
        )
        new = _edit(leaf, "tube/upper/1", 1.0 + 1e-12, 1.3)
        assert new["tube_points"][1]["time"] == 1.0
        assert new["tube_points"][1]["upper"] == pytest.approx(0.3)
        new = _edit(leaf, "tube/upper/0", -1e-12, 0.2)
        assert new["tube_points"][0]["time"] == 0.0
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)

    def test_window_bounds_the_span_by_its_reference_samples(self):
        leaf = _with(BAND, window={"start": 0.6})
        # Before the window, and inside it but before its first reference
        # sample 0.75: both clamp to 0.75, where ref = 0.75.
        for x in (0.55, 0.65):
            new = _edit(leaf, "tube/upper/1", x, 0.95)
            assert new["tube_points"][1]["time"] == 0.75
            assert new["tube_points"][1]["upper"] == pytest.approx(0.2)
        assert _edit(leaf, "tube/upper/1", 0.8, None)["tube_points"][1]["time"] == 0.8
        leaf = _with(BAND, window={"end": 0.6})
        new = _edit(leaf, "tube/upper/0", 0.55, 0.8)
        assert new["tube_points"][0]["time"] == 0.5
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)
        assert _edit(leaf, "tube/upper/0", 0.5, None)["tube_points"][0]["time"] == 0.5

    def test_a_window_starting_on_a_reference_sample_includes_it(self):
        leaf = _with(
            BAND,
            window={"start": 0.5},
            tube_points=[{"time": 0.9, "upper": 0.1, "lower": 0.1}],
        )
        new = _edit(leaf, "tube/upper/0", 0.5, 0.8)
        assert new["tube_points"][0]["time"] == 0.5
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)

    def test_window_holding_no_reference_sample_refused(self):
        leaf = _with(BAND, window={"start": 0.3, "end": 0.4})
        with pytest.raises(HandleEditError, match="no reference sample"):
            _edit(leaf, "tube/upper/0", 0.35, None)

    def test_abs_without_reference_uses_the_windows_own_ends(self):
        # abs needs no reference.
        new = _edit(ABS, "tube/upper/0", 0.7, 2.0, reference=None)
        assert new["tube_points"][0] == {"time": 0.7, "upper": 2.0, "lower": -0.5}
        leaf = _with(ABS, window={"start": 0.5})
        assert (
            _edit(leaf, "tube/upper/0", 0.7, None, reference=None)["tube_points"][0][
                "time"
            ]
            == 0.7
        )
        leaf = _with(ABS, window={"end": 0.5})
        assert (
            _edit(leaf, "tube/upper/0", 0.7, None, reference=None)["tube_points"][0][
                "time"
            ]
            == 0.5
        )

    def test_band_and_rel_need_the_primary_reference(self):
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(BAND, "tube/upper/0", 0.5, 0.8, reference=None)
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(REL, "tube/upper/0", None, 0.6, reference=None)
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(_with(BAND, against="other"), "tube/upper/0", 0.5, 0.8)
        assert _edit(_with(BAND, against="primary"), "tube/upper/0", 0.5, 0.8)

    def test_band_and_rel_refuse_a_named_baseline_even_vertically(self):
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(_with(BAND, against="other"), "tube/upper/0", None, 0.8)
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(_with(REL, against="other"), "tube/upper/0", None, 0.6)

    def test_abs_refuses_a_time_drag_against_a_named_baseline(self):
        leaf = _with(ABS, against="other")
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(leaf, "tube/upper/0", 0.1, 2.0)
        # A vertical drag needs no reference, so it still edits — also when
        # the ring sends its x back within float noise of the point's time.
        for x in (None, 0.0, 1e-12):
            new = _edit(leaf, "tube/upper/0", x, 2.0)
            assert new["tube_points"][0] == {"time": 0.0, "upper": 2.0, "lower": -0.5}

    def test_a_real_browsers_vertical_drag_is_not_a_time_move(self):
        # Measured on a TRANSFORM baseline (time 0..10, 2000 samples, ~800 px
        # plot): the pixel round trip sent this x for a ring at t=5.
        leaf = _with(BAND, tube_points=[{"time": 5.0, "upper": 0.1, "lower": 0.1}])
        new = _edit(leaf, "tube/upper/0", 4.99998293759, 1.5, reference=WIDE)
        assert new["tube_points"][0]["time"] == 5.0
        assert new["tube_points"][0]["upper"] == pytest.approx(1.5 - np.sin(5.0))

    def test_a_small_real_time_move_above_the_tolerance_moves(self):
        # 2e-5 of the span: above the 1e-5 no-move tolerance.
        leaf = _with(BAND, tube_points=[{"time": 5.0, "upper": 0.1, "lower": 0.1}])
        new = _edit(leaf, "tube/upper/0", 5.0 + 2e-4, None, reference=WIDE)
        assert new["tube_points"][0]["time"] == 5.0002

    def test_without_a_reference_only_float_noise_is_no_time_move(self):
        leaf = _with(ABS, tube_points=[{"time": 5.0, "upper": 1.5, "lower": -0.5}])
        new = _edit(leaf, "tube/upper/0", 5.0 + 1e-6, 2.0, reference=None)
        assert new["tube_points"][0] == {"time": 5.000001, "upper": 2.0, "lower": -0.5}

    def test_a_refusal_after_a_clamp_names_the_clamp(self):
        leaf = _with(
            BAND,
            tube_points=[
                {"time": 0.5, "upper": 0.1, "lower": 0.1},
                {"time": 1.0, "upper": 0.1, "lower": 0.1},
            ],
        )
        with pytest.raises(HandleEditError) as err:
            _edit(leaf, "tube/upper/0", 1.3, 1.2)
        assert "control points 0 and 1 share time 1" in str(err.value)
        assert "x=1.3 was clamped to t=1" in str(err.value)
        # The tool shows the reason alone: the clamp is part of it, the
        # handle's id is not.
        assert "x=1.3 was clamped to t=1" in err.value.reason
        assert "control points 0 and 1 share time 1" in err.value.reason
        assert "tube/upper/0" not in err.value.reason
        # An unclamped refusal does not claim a clamp.
        with pytest.raises(HandleEditError) as err:
            _edit(leaf, "tube/upper/0", 1.0, 1.2)
        assert "clamped" not in str(err.value)

    def test_a_refusal_prints_numbers_without_float_noise(self):
        # ref(0.75) = 0.75; -0.777 - 0.75 is -1.5270000000000001 as a float.
        with pytest.raises(HandleEditError, match=r"upper width -1\.527 would"):
            _edit(BAND, "tube/upper/1", 0.75, -0.777)

    def test_an_empty_reference_is_no_reference(self):
        empty = (np.array([]), np.array([]))
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(BAND, "tube/upper/0", None, 0.8, reference=empty)
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(REL, "tube/upper/0", 0.5, 0.8, reference=empty)
        # abs needs none: only the window's own ends bound the time.
        new = _edit(ABS, "tube/upper/0", 5.0, 2.0, reference=empty)
        assert new["tube_points"][0] == {"time": 5.0, "upper": 2.0, "lower": -0.5}
        new = _edit(POINTS, "points/target/0", 5.0, None, reference=empty)
        assert new["points"][0]["time"] == 5.0

    def test_rel_on_a_negative_reference_writes_a_positive_width(self):
        negative = (REF[0], -REF[1])
        new = _edit(REL, "tube/upper/0", 0.5, -0.4, reference=negative)
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)

    def test_a_time_drag_onto_another_points_time_refused(self):
        with pytest.raises(
            HandleEditError, match=r"control points 0 and 1 share time 0\.9"
        ):
            _edit(BAND, "tube/upper/0", 0.9, 1.0)

    def test_any_drag_of_a_point_sharing_its_time_refused(self):
        leaf = _with(
            BAND,
            tube_points=[
                {"time": 0.5, "upper": 0.1, "lower": 0.1},
                {"time": 0.5, "upper": 0.2, "lower": 0.2},
            ],
        )
        with pytest.raises(
            HandleEditError, match=r"control points 0 and 1 share time 0\.5"
        ):
            _edit(leaf, "tube/upper/1", None, 0.8)
        with pytest.raises(HandleEditError, match="control points 0 and 1"):
            _edit(leaf, "tube/lower/0", 0.25, 0.1)

    def test_an_authored_null_width_read_by_abs_refused(self):
        leaf = _with(ABS, tube_points=[{"time": 0.0, "upper": 1.5, "lower": None}])
        with pytest.raises(HandleEditError, match="lower is None, not a number"):
            _edit(leaf, "tube/upper/0", None, 1.0)
        leaf = _with(ABS, tube_points=[{"time": 0.0, "upper": None, "lower": -0.5}])
        with pytest.raises(HandleEditError, match="upper is None, not a number"):
            _edit(leaf, "tube/lower/0", None, -1.0)

    def test_a_refusal_prints_a_tiny_negative_width_as_nonzero(self):
        with pytest.raises(HandleEditError) as err:
            _edit(BAND, "tube/upper/0", 0.5, 0.5 - 1e-9)
        assert re.search(r"width -1(\.\d+)?e-09 would be negative", str(err.value))
        assert "-0 " not in str(err.value)

    @pytest.mark.parametrize(
        "local",
        [
            "tube/upper/-1",
            "tube/upper/01",
            "tube/upper/x",
            "tube/upper/\u00b2",
            "tube/upper",
            "tube/band",
            "tube/middle/0",
            "tube/upper/0/extra",
            "range/max",
            "",
        ],
    )
    def test_a_local_it_does_not_name_is_refused(self, local):
        # (0.5, 0.3) is a drag child 0's lower handle takes, so a side the
        # editor fell through to is not refused for some other reason.
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(BAND, local, 0.5, 0.3)

    def test_no_such_point(self):
        with pytest.raises(HandleEditError, match="no entry 7"):
            _edit(BAND, "tube/upper/7", 0.5, 0.8)
        with pytest.raises(HandleEditError, match="no entry 0"):
            _edit(_with(BAND, tube_points=None), "tube/upper/0", 0.5, 0.8)


class TestBounds:
    def test_range(self):
        new = _edit(RANGE, "range/max", 123.0, 1.5)
        assert new == {**RANGE, "max_value": 1.5}
        with pytest.raises(HandleEditError, match="exceed"):
            _edit(RANGE, "range/min", None, 3.0)
        with pytest.raises(HandleEditError, match="exceed"):
            _edit(RANGE, "range/max", None, -1.0)
        only_max = {"metric": "range", "variable": "x", "max_value": 2.0}
        assert _edit(only_max, "range/max", None, -50.0)["max_value"] == -50.0
        only_min = {"metric": "range", "variable": "x", "min_value": 2.0}
        assert _edit(only_min, "range/min", None, 50.0)["min_value"] == 50.0

    def test_equal_bounds_accepted_for_range(self):
        assert _edit(RANGE, "range/min", None, 2.0)["min_value"] == 2.0
        assert _edit(RANGE, "range/max", None, -0.5)["max_value"] == -0.5

    def test_equal_bounds_refused_for_scalar(self):
        # modes._validate_scalar_config requires min_value < max_value.
        with pytest.raises(HandleEditError, match="would equal max_value 2;"):
            _edit(SCALAR, "scalar/min", None, 2.0)
        with pytest.raises(HandleEditError, match="would equal"):
            _edit(SCALAR, "scalar/max", None, 0.0)

    def test_a_bound_just_past_the_other_prints_distinguishably(self):
        with pytest.raises(HandleEditError) as err:
            _edit(RANGE, "range/min", None, 2.0000001)
        assert "min_value 2.0000001 would exceed max_value 2" in str(err.value)

    def test_scalar(self):
        assert _edit(SCALAR, "scalar/min", None, -1.0)["min_value"] == -1.0
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(SCALAR, "scalar/actual", None, 1.0)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(SCALAR, "scalar/reference", None, 1.0)

    @pytest.mark.parametrize(
        ("leaf", "local"),
        [
            (RANGE, "scalar/min"),
            (SCALAR, "range/max"),
            (RANGE, "range/max/outside"),
            (RANGE, "range"),
            ({"variable": "x", "max_value": 1.0}, "range/max"),
        ],
    )
    def test_a_local_it_does_not_name_is_refused(self, leaf, local):
        with pytest.raises(HandleEditError, match="cannot be moved"):
            metric("range").edit_handle(
                HandleEdit(leaf=leaf, local=local, x=None, y=1.0, reference=REF)
            )


class TestPoints:
    def test_a_baseline_following_point_moves_in_time_only(self):
        new = _edit(POINTS, "points/target/0", 0.3, 0.9)
        assert new["points"][0] == {"time": 0.3, "tolerance": 0.05}

    def test_a_point_with_a_value_moves_in_both_axes(self):
        new = _edit(POINTS, "points/target/1", 0.8, 0.7)
        assert new["points"][1] == {"time": 0.8, "value": 0.7, "tolerance": 0.05}

    def test_a_vertical_drag_of_a_following_point_changes_nothing(self):
        assert _edit(POINTS, "points/target/0", 0.25, 0.9) == POINTS

    def test_a_real_browsers_vertical_drag_of_a_following_point_changes_nothing(
        self,
    ):
        # The TRANSFORM-measured round trip (see TestTube): x off t=5 by
        # 1.7e-6 of the span must not move the checkpoint's time.
        leaf = _with(POINTS, points=[{"time": 5.0, "tolerance": 0.05}])
        assert _edit(leaf, "points/target/0", 4.99998293759, 0.9, reference=WIDE) == (
            leaf
        )

    def test_a_following_points_time_is_clamped_into_the_reference_span(self):
        new = _edit(POINTS, "points/target/0", 1.5, 0.9)
        assert new["points"][0] == {"time": 1.0, "tolerance": 0.05}
        leaf = _with(POINTS, window={"end": 0.8})
        new = _edit(leaf, "points/target/0", 0.95, 0.9)
        assert new["points"][0] == {"time": 0.75, "tolerance": 0.05}

    def test_a_value_points_time_is_clamped_into_the_windows_ends_only(self):
        assert _edit(POINTS, "points/target/1", 1.7, None)["points"][1]["time"] == 1.7
        leaf = _with(POINTS, window={"end": 0.8})
        assert _edit(leaf, "points/target/1", 1.7, None)["points"][1]["time"] == 0.8
        leaf = _with(POINTS, window={"start": 0.1})
        assert _edit(leaf, "points/target/1", -3.0, None)["points"][1]["time"] == 0.1

    def test_a_value_points_time_is_clamped_into_the_runs_span(self):
        """A checkpoint with a value of its own is scored where its time
        meets the ACTUAL trajectory: dropped past the run's end — the time
        axis runs a little past the data — it read "not scored" and its
        ring was not drawn. `span` is the run's first and last time as the
        scorer receives it (windowed); each side lands on its end."""
        run = (0.0, 1.0)

        def landed(leaf, x, span):
            return _edit(leaf, "points/target/1", x, None, span=span)["points"][1][
                "time"
            ]

        assert landed(POINTS, 1.00077, run) == 1.0
        assert landed(POINTS, 1.7, run) == 1.0
        assert landed(POINTS, -0.0567, run) == 0.0
        assert landed(POINTS, 0.6, run) == 0.6  # inside: as dragged
        # A run that starts and ends elsewhere: its own ends, each side.
        assert landed(POINTS, 5.0, (0.2, 0.9)) == 0.9
        assert landed(POINTS, -5.0, (0.2, 0.9)) == 0.2
        # With a window the span is the run's samples INSIDE it — the last
        # one at or before 0.8 is 0.75, the first at or after 0.1 is 0.25.
        ending = _with(POINTS, window={"end": 0.8})
        assert landed(ending, 1.7, (0.0, 0.75)) == 0.75
        starting = _with(POINTS, window={"start": 0.1})
        assert landed(starting, -3.0, (0.25, 1.0)) == 0.25
        # The window's own ends bind as they did: a span that reaches past
        # them (no caller's, but no reason to write a time outside both).
        assert landed(ending, 1.7, run) == 0.8
        assert landed(starting, -3.0, run) == 0.1
        # No span — a caller with no actual series: the window's ends only.
        assert landed(POINTS, 1.7, None) == 1.7
        assert landed(ending, 1.7, None) == 0.8

    def test_a_following_points_time_is_bound_by_the_baseline_and_the_run(self):
        # A checkpoint that follows the baseline needs BOTH at its time: the
        # baseline for its target, the run to be scored against. On a run
        # that ends at 0.75 under a baseline 0..1, a ring let go at 0.9
        # stayed there — "not scored — checkpoint time outside actual
        # trajectory", and its ring gone. It lands where the two overlap.
        def landed(leaf, x, span):
            return _edit(leaf, "points/target/0", x, None, span=span)["points"][0]

        assert landed(POINTS, 0.9, (0.0, 0.75)) == {"time": 0.75, "tolerance": 0.05}
        assert (
            landed(POINTS, 0.1, (0.25, 2.0))["time"] == 0.25
        )  # a run that starts late
        # The baseline's own ends, where the run goes past them ...
        assert landed(POINTS, 1.5, (0.0, 2.0))["time"] == 1.0
        assert landed(POINTS, -0.5, (-1.0, 2.0))["time"] == 0.0
        # ... inside both, the time it was let go at ...
        assert landed(POINTS, 0.6, (0.0, 0.75))["time"] == 0.6
        # ... and with no span — a caller with no actual series — the
        # baseline's alone, as it was.
        assert landed(POINTS, 0.9, None)["time"] == 0.9
        assert landed(POINTS, 1.5, None)["time"] == 1.0

    def test_a_following_point_is_held_inside_its_window_and_the_run(self):
        # The window narrows the baseline's span, the run's span is the
        # samples the scorer keeps: the overlap of the two bounds the time.
        leaf = _with(POINTS, window={"start": 0.1, "end": 0.8})

        def landed(x, span):
            return _edit(leaf, "points/target/0", x, None, span=span)["points"][0][
                "time"
            ]

        ends = _edit(leaf, "points/target/0", 5.0, None)["points"][0]["time"]
        starts = _edit(leaf, "points/target/0", -5.0, None)["points"][0]["time"]
        assert starts < 0.5 < ends <= 0.8
        assert landed(5.0, (0.25, 0.5)) == 0.5 and landed(-5.0, (0.25, 0.5)) == 0.25
        assert landed(5.0, (0.0, 2.0)) == ends and landed(-5.0, (0.0, 2.0)) == starts

    def test_a_run_that_never_meets_the_baseline_leaves_the_baselines_span(self):
        # No time is in both: nothing there is scored whatever the ring
        # does, and it is held to the baseline's span as it was — where
        # its target is, never dragged off to a run it has no target on.
        new = _edit(POINTS, "points/target/0", 0.9, None, span=(2.0, 3.0))
        assert new["points"][0]["time"] == 0.9
        new = _edit(POINTS, "points/target/0", 2.5, None, span=(2.0, 3.0))
        assert new["points"][0]["time"] == 1.0
        # A run that starts where the baseline ends meets it at ONE time,
        # and that is where the checkpoint is held.
        for x in (0.9, 1.5):
            new = _edit(POINTS, "points/target/0", x, None, span=(1.0, 2.0))
            assert new["points"][0]["time"] == 1.0, x
        # The same rule for a checkpoint with a value of its own, whose own
        # span is its window: a run wholly outside that leaves the window.
        leaf = _with(POINTS, window={"start": 0.1, "end": 0.8})
        new = _edit(leaf, "points/target/1", 0.95, None, span=(0.9, 1.0))
        assert new["points"][1]["time"] == 0.8
        new = _edit(leaf, "points/target/1", 0.95, None, span=(0.8, 1.0))
        assert new["points"][1]["time"] == 0.8  # they touch: the one time

    def test_the_runs_span_bounds_a_time_MOVE_and_nothing_else(self):
        # A point authored outside the run, dragged vertically: no time
        # move, so nothing to clamp — its time is the author's.
        leaf = _with(POINTS, points=[{"time": 1.5, "value": 1.5, "tolerance": 0.1}])
        new = _edit(leaf, "points/target/0", 1.5, 1.6, span=(0.0, 1.0))
        assert new["points"][0] == {"time": 1.5, "value": 1.6, "tolerance": 0.1}
        # And the value a ring is dragged to is not a time: unbounded.
        new = _edit(POINTS, "points/target/1", 0.75, 7.0, span=(0.0, 1.0))
        assert new["points"][1] == {"time": 0.75, "value": 7.0, "tolerance": 0.05}

    def test_a_vertical_drag_keeps_a_time_outside_the_reference_span(self):
        # The review's case: the ring sends x back at the point's own time.
        leaf = _with(POINTS, points=[{"time": 1.5, "value": 1.5, "tolerance": 0.1}])
        new = _edit(leaf, "points/target/0", 1.5, 1.6)
        assert new["points"][0] == {"time": 1.5, "value": 1.6, "tolerance": 0.1}

    def test_without_a_reference_only_the_windows_ends_bound_a_time(self):
        assert (
            _edit(POINTS, "points/target/0", 5.0, None, reference=None)["points"][0][
                "time"
            ]
            == 5.0
        )
        leaf = _with(POINTS, window={"start": 0.1, "end": 0.8})
        for x, landed in ((0.95, 0.8), (0.05, 0.1)):
            new = _edit(leaf, "points/target/0", x, None, reference=None)
            assert new["points"][0]["time"] == landed

    def test_a_null_value_follows_the_baseline(self):
        leaf = _with(POINTS, points=[{"time": 0.25, "value": None, "tolerance": 0.05}])
        new = _edit(leaf, "points/target/0", 0.3, 0.9)
        assert new["points"][0] == {"time": 0.3, "value": None, "tolerance": 0.05}

    @pytest.mark.parametrize(
        "point", [{"tolerance": 0.05}, {"time": None, "tolerance": 0.05}]
    )
    def test_a_trace_end_checkpoint_keeps_its_time(self, point):
        # x is ignored; y follows the value rule.
        leaf = _with(POINTS, points=[copy.deepcopy(point)])
        assert _edit(leaf, "points/target/0", 0.3, 0.9)["points"][0] == point
        leaf = _with(POINTS, points=[{**point, "value": 0.5}])
        assert _edit(leaf, "points/target/0", 0.3, 0.9)["points"][0] == {
            **point,
            "value": 0.9,
        }

    def test_a_time_drag_against_a_named_baseline_refused(self):
        leaf = _with(POINTS, against="other")
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(leaf, "points/target/0", 0.3, None)
        # A vertical drag of an explicit value needs no reference.
        new = _edit(leaf, "points/target/1", None, 0.7)
        assert new["points"][1] == {"time": 0.75, "value": 0.7, "tolerance": 0.05}

    def test_refusals(self):
        with pytest.raises(HandleEditError, match="no declared point"):
            _edit(POINTS, "points/target/final", 0.3, 0.9)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(POINTS, "points/box/0", 0.3, 0.9)
        with pytest.raises(HandleEditError, match="no entry 2"):
            _edit(POINTS, "points/target/2", 0.3, 0.9)


class TestPeaks:
    def test_declared(self):
        new = _edit(PEAKS, "peaks/declared/0", 2.0, 99.0)
        assert new["peaks"][0] == {
            "freq": 2.0,
            "tolerance": 0.1,
            "tolerance_mode": "rel",
        }

    def test_refusals(self):
        with pytest.raises(HandleEditError, match="positive"):
            _edit(PEAKS, "peaks/declared/0", 0.0, None)
        with pytest.raises(HandleEditError, match="positive"):
            _edit(PEAKS, "peaks/declared/0", -1.0, None)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(PEAKS, "peaks/window/0", 2.0, None)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(PEAKS, "peaks/detected/0", 2.0, None)


FAMILIES = [
    (BAND, "tube/upper/0", 0.5, 0.8),
    (BAND, "tube/lower/0", 0.5, 0.2),
    (REL, "tube/upper/0", 0.5, 0.6),
    (ABS, "tube/lower/0", 0.0, -1.0),
    (RANGE, "range/min", None, -1.0),
    (RANGE, "range/max", None, 1.0),
    (SCALAR, "scalar/min", None, -1.0),
    (SCALAR, "scalar/max", None, 1.0),
    (POINTS, "points/target/1", 0.8, 0.7),
    (PEAKS, "peaks/declared/0", 2.0, 1.0),
]


@pytest.mark.parametrize(("leaf", "local", "x", "y"), FAMILIES)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_non_finite_position_is_refused(leaf, local, x, y, bad, axis):
    # Each case is accepted with its finite position first, so the refusal
    # below is the non-finite number's, not some other rule's.
    assert _edit(leaf, local, x, y) != leaf
    pos = {"x": x, "y": y, axis: bad}
    with pytest.raises(HandleEditError, match="not a finite number"):
        _edit(leaf, local, pos["x"], pos["y"])


@pytest.mark.parametrize(("leaf", "local", "x", "y"), FAMILIES)
@pytest.mark.parametrize("bad", ["0.5", True, [1.0]])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_non_numeric_position_is_refused(leaf, local, x, y, bad, axis):
    pos = {"x": x, "y": y, axis: bad}
    with pytest.raises(HandleEditError, match="is not a number"):
        _edit(leaf, local, pos["x"], pos["y"])


def test_the_builtins_that_have_editors():
    # nrmse is one ceiling over a whole trajectory: nothing of it is drawn.
    assert metric("nrmse").edit_handle is None
    for name in (
        "tube",
        "range",
        "scalar",
        "points",
        "event-timing",
        "dominant-frequency",
    ):
        assert metric(name).edit_handle is not None, name


# -- §6: every handle's id resolves to the spec path it edits -----------------


#: The claim under test: the address in the leaf each handle family edits.
ADDRESS = {
    "tube/upper": "/tube_points/{i}",
    "tube/lower": "/tube_points/{i}",
    "range/min": "/min_value",
    "scalar/min": "/min_value",
    "range/max": "/max_value",
    "scalar/max": "/max_value",
    "points/target": "/points/{i}",
    "peaks/declared": "/peaks/{i}",
}


def _address(local: str) -> tuple[str, str] | None:
    """The family and the address a local names, per ``ADDRESS``."""
    for family, template in ADDRESS.items():
        if local == family:
            return family, template
        if local.startswith(family + "/"):
            rest = local[len(family) + 1 :]
            if rest.isdigit():
                return family, template.format(i=rest)
    return None


def _diff(old, new, at: str = ""):
    """Every JSON Pointer at which ``old`` and ``new`` differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            here = f"{at}/{key}"
            if key not in old or key not in new:
                yield here
            else:
                yield from _diff(old[key], new[key], here)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            if i >= len(old) or i >= len(new):
                yield f"{at}/{i}"
            else:
                yield from _diff(old[i], new[i], f"{at}/{i}")
    elif old != new or type(old) is not type(new):
        yield at


def _walk(tree: dict, leaf_path: str) -> dict:
    """The raw node at ``leaf_path`` (a pointer rooted at ``/metrics``)."""
    assert leaf_path == "/metrics" or leaf_path.startswith("/metrics/")
    node = tree
    for part in leaf_path.split("/")[2:]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


@pytest.fixture
def session(tmp_path) -> Session:
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def test_every_handle_id_resolves_to_the_spec_path_it_edits(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(PEAKS))])
    edited: set[str] = set()
    seen_handles = 0
    for model_id in (D, A):
        view = session.view(model_id, "x")
        tree = session.tree(model_id)
        reference = (
            (view.ref_time, view.ref_values) if view.ref_time is not None else None
        )
        for leaf_view in view.leaves:
            raw = _walk(tree, leaf_view.path)
            editor = metric(leaf_view.metric).edit_handle
            for prim in leaf_view.geometry:
                if prim.kind != "handle":
                    continue
                seen_handles += 1
                assert prim.id.startswith(leaf_view.path + "/"), prim.id
                local = prim.id[len(leaf_view.path) + 1 :]
                if isinstance(prim, Marker):
                    x, y = prim.x + 0.01, prim.y + 0.01
                elif isinstance(prim, HLine):
                    x, y = None, prim.value + 0.01
                elif isinstance(prim, Box):
                    x, y = None, prim.y0
                else:
                    pytest.fail(f"unexpected handle primitive {prim!r}")
                assert editor is not None, leaf_view.metric
                try:
                    new = editor(
                        HandleEdit(leaf=raw, local=local, x=x, y=y, reference=reference)
                    )
                except HandleEditError:
                    continue
                named = _address(local)
                assert named is not None, f"{prim.id} edited a leaf with no address"
                family, address = named
                changed = list(_diff(raw, new))
                assert changed, prim.id
                for pointer in changed:
                    assert pointer == address or pointer.startswith(address + "/"), (
                        prim.id,
                        pointer,
                    )
                edited.add(family)
    assert seen_handles
    # A family that only ever raised would make the claim vacuous.
    assert edited == set(ADDRESS)


def _marker(session, model_id, handle_id):
    view = session.view(model_id, "x")
    (prim,) = [p for leaf in view.leaves for p in leaf.geometry if p.id == handle_id]
    return prim


@pytest.mark.parametrize(
    ("child", "local", "x", "y"),
    [
        (0, "tube/upper/0", 0.5, 0.8),
        (4, "tube/upper/0", 0.5, 0.6),
        (1, "range/max", None, 1.5),
    ],
)
def test_staging_the_edit_puts_the_handle_where_it_was_dragged(
    session, child, local, x, y
):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = session.tree(D)["children"][child]
    new = _edit(leaf, local, x, y)
    session.stage([PatchOp(D, "replace", f"/metrics/children/{child}", new)])
    prim = _marker(session, D, f"/metrics/children/{child}/{local}")
    landed = prim.value if isinstance(prim, HLine) else prim.y
    assert landed == pytest.approx(y)


def test_a_vertical_drag_keeps_a_checkpoint_drawn_before_its_window(session):
    # The box [0, 0.8] meets the windowed trajectory (0.75, 1.0), so the
    # checkpoint at 0.4 is drawn though the window starts at 0.6; the ring's
    # noise-level x must not clamp it onto the window.
    leaf = {
        "metric": "points",
        "variable": "x",
        "window": {"start": 0.6},
        "points": [{"time": 0.4, "value": 0.5, "time_tolerance": 0.4}],
    }
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(leaf))])
    before = _marker(session, D, "/metrics/points/target/0")
    assert (before.x, before.y) == (0.4, 0.5)
    view = session.view(D, "x")
    new = _edit(
        session.tree(D),
        "points/target/0",
        0.4 + 1e-12,
        0.6,
        reference=(view.ref_time, view.ref_values),
    )
    assert new["points"][0]["time"] == 0.4
    session.stage([PatchOp(D, "replace", "/metrics", new)])
    prim = _marker(session, D, "/metrics/points/target/0")
    assert (prim.x, prim.y) == (0.4, pytest.approx(0.6))


def test_a_width_dragged_under_the_min_width_rescores_onto_the_minimum(session):
    tree = copy.deepcopy(TREE)
    tree["children"][0]["tube_min_width"] = 0.3
    session.stage([PatchOp(D, "add", "/metrics", tree)])
    view = session.view(D, "x")
    leaf = session.tree(D)["children"][0]
    new = _edit(
        leaf, "tube/upper/0", 0.5, 0.6, reference=(view.ref_time, view.ref_values)
    )
    assert new["tube_points"][0]["upper"] == pytest.approx(0.1)  # written as dragged
    session.stage([PatchOp(D, "replace", "/metrics/children/0", new)])
    prim = _marker(session, D, "/metrics/children/0/tube/upper/0")
    # D's baseline is x = t: ref(0.5) + tube_min_width.
    assert prim.y == pytest.approx(0.5 + 0.3)


# -- scalar: the two lines of what the baseline form allows ---------------------

BASELINE_SCALAR = {
    "metric": "scalar",
    "variable": "x",
    "quantity": {"reduce": "max"},
    "tolerance": 0.25,
}


def _side(leaf, local, y, target=4.0):
    before = copy.deepcopy(leaf)
    try:
        return metric("scalar").edit_handle(
            HandleEdit(
                leaf=leaf, local=local, x=None, y=y, reference=REF, target=target
            )
        )
    finally:
        assert leaf == before


class TestScalarSides:
    """`scalar/upper` / `scalar/lower` → that side of the leaf's `tolerance`,
    measured from the baseline's scalar (`HandleEdit.target`)."""

    def test_a_number_stays_a_number_and_both_sides_move(self):
        assert _side(BASELINE_SCALAR, "scalar/upper", 4.5)["tolerance"] == 0.5
        assert _side(BASELINE_SCALAR, "scalar/lower", 3.5)["tolerance"] == 0.5
        new = _side(BASELINE_SCALAR, "scalar/lower", 3.875)
        assert new == {**BASELINE_SCALAR, "tolerance": 0.125}

    def test_an_absent_tolerance_becomes_a_number(self):
        leaf = {k: v for k, v in BASELINE_SCALAR.items() if k != "tolerance"}
        assert _side(leaf, "scalar/upper", 4.5)["tolerance"] == 0.5

    def test_a_pair_changes_only_that_side(self):
        leaf = _with(BASELINE_SCALAR, tolerance={"lower": 0.125, "upper": 0.25})
        assert _side(leaf, "scalar/upper", 4.5)["tolerance"] == {
            "lower": 0.125,
            "upper": 0.5,
        }
        assert _side(leaf, "scalar/lower", 3.0)["tolerance"] == {
            "lower": 1.0,
            "upper": 0.25,
        }
        # An equal pair stays a pair: only the editor's toggle changes the shape.
        even = _with(BASELINE_SCALAR, tolerance={"lower": 0.25, "upper": 0.25})
        assert _side(even, "scalar/upper", 4.5)["tolerance"] == {
            "lower": 0.25,
            "upper": 0.5,
        }

    def test_rel_divides_by_the_baselines_magnitude(self):
        leaf = _with(BASELINE_SCALAR, tolerance_mode="rel")
        # Baseline −4: the line at −2 is 2 above it, 50 % of |−4|.
        assert _side(leaf, "scalar/upper", -2.0, target=-4.0)["tolerance"] == 0.5
        assert _side(leaf, "scalar/lower", -5.0, target=-4.0)["tolerance"] == 0.25

    def test_what_is_written_is_rounded_to_twelve_digits(self):
        # 4.3 − 4.0 is 0.2999999999999998 in floats.
        assert _side(BASELINE_SCALAR, "scalar/upper", 4.3)["tolerance"] == 0.3
        # Whichever shape the leaf held: none, and a pair's one side.
        absent = {k: v for k, v in BASELINE_SCALAR.items() if k != "tolerance"}
        assert _side(absent, "scalar/upper", 4.3)["tolerance"] == 0.3
        pair = _with(BASELINE_SCALAR, tolerance={"lower": 0.125, "upper": 0.25})
        assert _side(pair, "scalar/upper", 4.3)["tolerance"]["upper"] == 0.3

    def test_a_side_dragged_across_its_target_is_refused(self):
        with pytest.raises(HandleEditError) as box:
            _side(BASELINE_SCALAR, "scalar/upper", 3.9)
        assert box.value.reason == "the upper side would cross its target"
        assert str(box.value) == "scalar/upper: the upper side would cross its target"
        with pytest.raises(HandleEditError, match="the lower side would cross"):
            _side(BASELINE_SCALAR, "scalar/lower", 4.1)
        # ON the target is a zero side, not a crossing.
        assert _side(BASELINE_SCALAR, "scalar/upper", 4.0)["tolerance"] == 0.0
        assert _side(BASELINE_SCALAR, "scalar/lower", 4.0)["tolerance"] == 0.0

    def test_a_tiny_baseline_is_not_zero(self):
        """ONE condition with the scorer, which falls back to the absolute
        test only at a baseline of exactly 0: at 1e-20 it scores — and the
        figure draws — a fraction of 1e-20, so the editor edits that."""
        rel = _with(BASELINE_SCALAR, tolerance_mode="rel")
        assert _side(rel, "scalar/upper", 3e-20, target=1e-20)["tolerance"] == 2.0
        assert _side(rel, "scalar/lower", -3e-20, target=-1e-20)["tolerance"] == 2.0

    def test_refusals(self):
        with pytest.raises(HandleEditError, match="no baseline scalar to measure"):
            _side(BASELINE_SCALAR, "scalar/upper", 4.5, target=None)
        rel = _with(BASELINE_SCALAR, tolerance_mode="rel")
        with pytest.raises(HandleEditError, match="the baseline scalar is zero"):
            _side(rel, "scalar/upper", 0.5, target=0.0)
        with pytest.raises(HandleEditError, match="the baseline scalar is zero"):
            _side(rel, "scalar/lower", -0.5, target=-0.0)
        bad = _with(BASELINE_SCALAR, tolerance="abc")
        with pytest.raises(HandleEditError, match="tolerance must be a number or"):
            _side(bad, "scalar/upper", 4.5)
        with pytest.raises(HandleEditError, match="not a finite number"):
            _side(BASELINE_SCALAR, "scalar/upper", float("nan"))

    @pytest.mark.parametrize("side,y", [("upper", 1e10), ("lower", -1e10)])
    def test_a_side_that_would_not_be_finite_is_refused(self, side, y):
        """A rel side is the drag over |baseline|: over a tiny baseline a
        long drag overflows, and the leaf would hold ``inf``."""
        rel = _with(BASELINE_SCALAR, tolerance_mode="rel")
        with pytest.raises(HandleEditError) as box:
            _side(rel, f"scalar/{side}", y, target=1e-300)
        assert box.value.reason == "the tolerance would not be a finite number"
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _side(BASELINE_SCALAR, "scalar/upper/0", 4.5)

    def test_a_horizontal_only_drag_changes_nothing(self):
        assert _side(BASELINE_SCALAR, "scalar/upper", None) == BASELINE_SCALAR


# -- a dragged grip back to its side (D147) ------------------------------------


def _grip(leaf, local, x=None, y=None, reference=REF):
    return _edit(leaf, local, x, y, reference)


POINTS_SIDES = {
    "metric": "points",
    "variable": "x",
    "tolerance": 0.25,
    "points": [
        # 0: follows the baseline (ref(0.25) = 0.25) and the leaf's tolerance.
        {"time": 0.25},
        # 1: its own number, in value and in time.
        {"time": 0.75, "value": 0.5, "tolerance": 0.125, "time_tolerance": 0.0625},
        # 2: a pair in each, rel about a NEGATIVE target.
        {
            "time": 0.5,
            "value": -4.0,
            "tolerance": {"lower": 0.125, "upper": 0.25},
            "tolerance_mode": "rel",
            "time_tolerance": {"lower": 0.125, "upper": 0.25},
        },
    ],
}


#: Rows that DEFER to the criterion's tolerance in two modes — 0 `abs` (the
#: default; it follows the baseline, ref(0.25) = 0.25), 1 `rel` about 2.0 —
#: and one that holds its own.
MIXED = {
    "metric": "points",
    "variable": "x",
    "tolerance": 0.25,
    "points": [
        {"time": 0.25},
        {"time": 0.5, "value": 2.0, "tolerance_mode": "rel"},
        {"time": 0.75, "value": 0.5, "tolerance": 0.125, "tolerance_mode": "rel"},
    ],
}


class TestPointsSides:
    """`points/upper|lower/i` → that side of the value tolerance row `i` is
    held to; `points/before|after/i` → that side of its `time_tolerance`."""

    def test_a_rows_own_number_stays_a_number_and_both_sides_move(self):
        new = _grip(POINTS_SIDES, "points/upper/1", 0.75, 0.75)
        assert new["points"][1]["tolerance"] == 0.25
        new = _grip(POINTS_SIDES, "points/lower/1", 0.75, 0.25)
        assert new["points"][1]["tolerance"] == 0.25
        # Nothing else moved: not the row's time or value, not the leaf's.
        expected = copy.deepcopy(POINTS_SIDES)
        expected["points"][1]["tolerance"] = 0.25
        assert new == expected

    def test_a_pair_changes_only_that_side_and_rel_divides_by_the_magnitude(self):
        # Target −4: −2 is 2 above it, 50 % of |−4|; −5 is 1 below, 25 %.
        new = _grip(POINTS_SIDES, "points/upper/2", 0.5, -2.0)
        assert new["points"][2]["tolerance"] == {"lower": 0.125, "upper": 0.5}
        new = _grip(POINTS_SIDES, "points/lower/2", 0.5, -5.0)
        assert new["points"][2]["tolerance"] == {"lower": 0.25, "upper": 0.25}

    def test_a_row_that_defers_writes_the_criterions_tolerance(self):
        """Row 0 holds no `tolerance`: its box is the leaf's, so its grip
        moves the leaf's — and every other deferring row's box with it."""
        new = _grip(POINTS_SIDES, "points/upper/0", 0.25, 0.75)
        assert new["tolerance"] == 0.5 and "tolerance" not in new["points"][0]
        # A `null` is an absent key, as the scorer reads it.
        nulled = copy.deepcopy(POINTS_SIDES)
        nulled["points"][0]["tolerance"] = None
        new = _grip(nulled, "points/lower/0", 0.25, 0.125)
        assert new["tolerance"] == 0.125 and new["points"][0]["tolerance"] is None
        # The leaf holds none either (the test's is in effect): a number.
        bare = {k: v for k, v in POINTS_SIDES.items() if k != "tolerance"}
        assert _grip(bare, "points/upper/0", 0.25, 0.75)["tolerance"] == 0.5
        # The leaf holds a pair: only that side.
        paired = _with(POINTS_SIDES, tolerance={"lower": 0.125, "upper": 0.25})
        new = _grip(paired, "points/upper/0", 0.25, 0.75)
        assert new["tolerance"] == {"lower": 0.125, "upper": 0.5}

    def test_a_deferring_row_among_rows_of_another_mode_takes_its_own(self):
        """The criterion's tolerance is read by each deferring row in the
        row's OWN mode. Where they differ, the fraction dragged on a `rel`
        row, written to the criterion, became that many signal units for
        the `abs` rows (0.634 of a target, then ±0.634). The dragged row
        takes a tolerance of its own — it stops deferring — and the
        criterion's stays what the other rows read."""
        new = _grip(MIXED, "points/upper/1", 0.5, 3.0)  # target 2: half of it
        assert new["points"][1] == {**MIXED["points"][1], "tolerance": 0.5}
        expected = copy.deepcopy(MIXED)
        expected["points"][1]["tolerance"] = 0.5
        assert new == expected  # the criterion's 0.25 and every other row
        # An `abs` row among them, the same: its own, in signal units.
        new = _grip(MIXED, "points/lower/0", 0.25, 0.125)
        assert new["points"][0] == {"time": 0.25, "tolerance": 0.125}
        assert new["tolerance"] == 0.25 and new["points"][1:] == MIXED["points"][1:]

    def test_the_row_that_stops_deferring_keeps_the_criterions_other_side(self):
        """It was held to the criterion's tolerance: where that is a pair,
        its own is that pair with the dragged side moved — the other edge
        of its box does not jump; where it is a number, or absent, the
        number dragged (linked, as a number is everywhere)."""
        paired = _with(MIXED, tolerance={"lower": 0.125, "upper": 0.25})
        new = _grip(paired, "points/upper/1", 0.5, 3.0)
        assert new["points"][1]["tolerance"] == {"lower": 0.125, "upper": 0.5}
        assert new["tolerance"] == {"lower": 0.125, "upper": 0.25}
        bare = {k: v for k, v in MIXED.items() if k != "tolerance"}
        new = _grip(bare, "points/upper/1", 0.5, 3.0)
        assert new["points"][1]["tolerance"] == 0.5 and "tolerance" not in new
        # A `null` is an absent key: that row defers too, and takes its own.
        nulled = copy.deepcopy(MIXED)
        nulled["points"][1]["tolerance"] = None
        new = _grip(nulled, "points/upper/1", 0.5, 3.0)
        assert new["points"][1]["tolerance"] == 0.5 and new["tolerance"] == 0.25

    def test_deferring_rows_of_one_mode_still_write_the_criterions(self):
        """Only the rows that DEFER are asked: a row of another mode that
        holds its own tolerance reads nothing of the criterion's."""
        shared = copy.deepcopy(MIXED)
        shared["points"][1]["tolerance"] = 0.5  # the rel row: its own now
        new = _grip(shared, "points/upper/0", 0.25, 0.75)
        assert new["tolerance"] == 0.5 and "tolerance" not in new["points"][0]
        assert new["points"] == shared["points"]
        # And all of them `rel`: the criterion's fraction, for every one.
        rel = copy.deepcopy(MIXED)
        rel["points"][0] = {"time": 0.25, "value": 4.0, "tolerance_mode": "rel"}
        new = _grip(rel, "points/upper/1", 0.5, 3.0)
        assert new["tolerance"] == 0.5 and new["points"] == rel["points"]
        # A refusal is the same refusal either way.
        with pytest.raises(HandleEditError, match="the upper side would cross"):
            _grip(MIXED, "points/upper/1", 0.5, 1.0)
        assert _grip(MIXED, "points/upper/1", 0.5, None) == MIXED

    def test_the_time_grips_write_the_rows_time_tolerance(self):
        new = _grip(POINTS_SIDES, "points/before/1", 0.5, 0.5)
        assert new["points"][1]["time_tolerance"] == 0.25  # a number: both
        new = _grip(POINTS_SIDES, "points/after/1", 0.875, 0.5)
        assert new["points"][1]["time_tolerance"] == 0.125
        new = _grip(POINTS_SIDES, "points/after/2", 1.0, -4.0)
        assert new["points"][2]["time_tolerance"] == {"lower": 0.125, "upper": 0.5}
        new = _grip(POINTS_SIDES, "points/before/2", 0.25, -4.0)
        assert new["points"][2]["time_tolerance"] == {"lower": 0.25, "upper": 0.25}
        # The value tolerance is not the time grip's to touch.
        assert new["points"][2]["tolerance"] == POINTS_SIDES["points"][2]["tolerance"]

    def test_only_the_grips_own_axis_is_read(self):
        """A value grip is given its own resolved x and a time grip its own
        y; neither is a position to write."""
        assert _grip(POINTS_SIDES, "points/upper/1", 99.0, 0.75) == _grip(
            POINTS_SIDES, "points/upper/1", None, 0.75
        )
        assert _grip(POINTS_SIDES, "points/before/1", 0.5, 99.0) == _grip(
            POINTS_SIDES, "points/before/1", 0.5, None
        )
        # No move on its own axis: nothing changes.
        assert _grip(POINTS_SIDES, "points/upper/1", 0.9, None) == POINTS_SIDES
        assert _grip(POINTS_SIDES, "points/after/1", None, 0.9) == POINTS_SIDES

    def test_a_side_dragged_across_its_target_is_refused(self):
        with pytest.raises(HandleEditError) as box:
            _grip(POINTS_SIDES, "points/upper/1", 0.75, 0.25)
        assert box.value.reason == "the upper side would cross its target"
        with pytest.raises(HandleEditError, match="the lower side would cross"):
            _grip(POINTS_SIDES, "points/lower/2", 0.5, -3.0)
        # A time side says its own name, and what it would cross.
        with pytest.raises(HandleEditError) as box:
            _grip(POINTS_SIDES, "points/before/1", 0.875, 0.5)
        assert box.value.reason == "the before side would cross its point's time"
        with pytest.raises(HandleEditError) as box:
            _grip(POINTS_SIDES, "points/after/1", 0.5, 0.5)
        assert box.value.reason == "the after side would cross its point's time"
        # ON the target is a zero side, not a crossing.
        assert (
            _grip(POINTS_SIDES, "points/upper/1", 0.75, 0.5)["points"][1]["tolerance"]
            == 0.0
        )
        assert (
            _grip(POINTS_SIDES, "points/after/1", 0.75, 0.5)["points"][1][
                "time_tolerance"
            ]
            == 0.0
        )

    def test_refusals(self):
        zero = copy.deepcopy(POINTS_SIDES)
        zero["points"][2]["value"] = 0.0
        with pytest.raises(HandleEditError, match="the target is zero"):
            _grip(zero, "points/upper/2", 0.5, 0.5)
        # The tube's rule, not "exactly zero": a fraction of 1e-20 wrote a
        # tolerance of 5e18.
        for tiny in (1e-20, -1e-20):
            zero["points"][2]["value"] = tiny
            with pytest.raises(HandleEditError) as box:
                _grip(zero, "points/upper/2", 0.5, 0.5)
            assert box.value.reason == "the target is zero"
        # ... which the smallest magnitude it is not still divides by.
        zero["points"][2]["value"] = 1e-10
        new = _grip(zero, "points/upper/2", 0.5, 3e-10)
        assert new["points"][2]["tolerance"] == {"lower": 0.125, "upper": 2.0}
        # An `abs` row measures no fraction: any target.
        zero["points"][2]["value"] = 1e-20
        zero["points"][2]["tolerance_mode"] = "abs"
        new = _grip(zero, "points/upper/2", 0.5, 0.5)
        assert new["points"][2]["tolerance"] == {"lower": 0.125, "upper": 0.5}
        other = _with(POINTS_SIDES, against="golden")
        with pytest.raises(HandleEditError, match="scores against 'golden'"):
            _grip(other, "points/upper/0", 0.25, 0.75)
        # A row with a value of its own is measured from that value.
        assert (
            _grip(other, "points/upper/1", 0.75, 0.75)["points"][1]["tolerance"] == 0.25
        )
        with pytest.raises(HandleEditError, match="needs a reference"):
            _grip(POINTS_SIDES, "points/upper/0", 0.25, 0.75, reference=None)
        with pytest.raises(HandleEditError, match="not a finite number"):
            _grip(POINTS_SIDES, "points/upper/1", 0.75, float("nan"))
        with pytest.raises(HandleEditError, match="not a finite number"):
            _grip(POINTS_SIDES, "points/before/1", float("inf"), 0.5)
        with pytest.raises(HandleEditError, match="holds no time_tolerance"):
            _grip(POINTS_SIDES, "points/before/0", 0.125, 0.25)
        with pytest.raises(HandleEditError, match="no entry 7"):
            _grip(POINTS_SIDES, "points/upper/7", 0.5, 0.5)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _grip(POINTS_SIDES, "points/sideways/1", 0.5, 0.5)
        bad = copy.deepcopy(POINTS_SIDES)
        bad["points"][1]["tolerance"] = "abc"
        with pytest.raises(HandleEditError, match="tolerance must be a number or"):
            _grip(bad, "points/upper/1", 0.75, 0.75)

    def test_the_final_checks_grips_write_the_criterions_tolerance(self):
        final = {"metric": "points", "variable": "x", "tolerance": 0.25}
        assert _grip(final, "points/upper/final", 1.0, 1.5)["tolerance"] == 0.5
        assert _grip(final, "points/lower/final", 1.0, 0.875)["tolerance"] == 0.125
        pair = _with(final, tolerance={"lower": 0.125, "upper": 0.25})
        assert _grip(pair, "points/lower/final", 1.0, 0.5)["tolerance"] == {
            "lower": 0.5,
            "upper": 0.25,
        }
        bare = {"metric": "points", "variable": "x"}
        assert _grip(bare, "points/upper/final", 1.0, 1.5)["tolerance"] == 0.5
        with pytest.raises(HandleEditError, match="the upper side would cross"):
            _grip(final, "points/upper/final", 1.0, 0.5)
        with pytest.raises(HandleEditError, match="scores against 'golden'"):
            _grip(_with(final, against="golden"), "points/upper/final", 1.0, 1.5)
        with pytest.raises(HandleEditError, match="needs a reference"):
            _grip(final, "points/upper/final", 1.0, 1.5, reference=None)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _grip(final, "points/before/final", 0.5, 1.0)

    def test_the_final_checks_target_is_the_windowed_references(self):
        """The scorer takes the final value of the reference AS WINDOWED:
        with `window.end` 0.5 the target is 0.5, not the trace's 1.0."""
        final = {"metric": "points", "variable": "x", "tolerance": 0.25}
        ended = _with(final, window={"end": 0.5})
        assert _grip(ended, "points/upper/final", 0.5, 0.75)["tolerance"] == 0.25
        with pytest.raises(HandleEditError, match="the upper side would cross"):
            _grip(ended, "points/upper/final", 0.5, 0.375)
        # A start alone leaves the end where it was.
        started = _with(final, window={"start": 0.3})
        assert _grip(started, "points/upper/final", 1.0, 1.5)["tolerance"] == 0.5
        # Between samples the window ends on the last one inside it.
        between = _with(final, window={"start": 0.3, "end": 0.9})
        assert _grip(between, "points/lower/final", 0.75, 0.5)["tolerance"] == 0.25


CONST_BAND = {
    "metric": "tube",
    "variable": "x",
    "tube_width_mode": "band",
    "tube_abs": 0.125,
}
CONST_REL = {
    "metric": "tube",
    "variable": "x",
    "tube_width_mode": "rel",
    "tube_rel": 0.125,
}
#: A baseline below zero: ref(t) = −4 t.
NEGATIVE = (REF[0], -4.0 * REF[1])


class TestTubeConstantSides:
    """`tube/upper|lower/const` → that side of `tube_abs` (band) or
    `tube_rel` (rel), measured from the reference at the grip's own x."""

    def test_a_number_stays_a_number(self):
        assert _grip(CONST_BAND, "tube/upper/const", 0.5, 0.75) == {
            **CONST_BAND,
            "tube_abs": 0.25,
        }
        assert _grip(CONST_BAND, "tube/lower/const", 0.5, 0.25)["tube_abs"] == 0.25
        bare = {k: v for k, v in CONST_BAND.items() if k != "tube_abs"}
        assert _grip(bare, "tube/upper/const", 0.5, 0.75)["tube_abs"] == 0.25

    def test_a_pair_changes_only_that_side(self):
        pair = _with(CONST_BAND, tube_abs={"lower": 0.125, "upper": 0.25})
        assert _grip(pair, "tube/lower/const", 0.5, 0.0)["tube_abs"] == {
            "lower": 0.5,
            "upper": 0.25,
        }
        assert _grip(pair, "tube/upper/const", 0.75, 1.75)["tube_abs"] == {
            "lower": 0.125,
            "upper": 1.0,
        }

    def test_rel_divides_by_the_references_magnitude_at_the_grips_time(self):
        assert _grip(CONST_REL, "tube/upper/const", 0.5, 0.75)["tube_rel"] == 0.5
        # ref(0.5) = −2: −1 is 1 above it, 50 % of |−2|; −2.5 is 25 % below.
        assert (
            _grip(CONST_REL, "tube/upper/const", 0.5, -1.0, reference=NEGATIVE)[
                "tube_rel"
            ]
            == 0.5
        )
        assert (
            _grip(CONST_REL, "tube/lower/const", 0.5, -2.5, reference=NEGATIVE)[
                "tube_rel"
            ]
            == 0.25
        )
        assert "tube_abs" not in _grip(CONST_REL, "tube/upper/const", 0.5, 0.75)

    def test_refusals(self):
        with pytest.raises(HandleEditError) as box:
            _grip(CONST_BAND, "tube/upper/const", 0.5, 0.25)
        assert box.value.reason == "the upper side would cross its target"
        with pytest.raises(HandleEditError, match="the lower side would cross"):
            _grip(CONST_BAND, "tube/lower/const", 0.5, 0.75)
        with pytest.raises(HandleEditError, match="the reference is zero at t=0"):
            _grip(CONST_REL, "tube/upper/const", 0.0, 0.5)
        with pytest.raises(HandleEditError, match="scores against 'golden'"):
            _grip(_with(CONST_BAND, against="golden"), "tube/upper/const", 0.5, 0.75)
        with pytest.raises(HandleEditError, match="needs a reference"):
            _grip(CONST_BAND, "tube/upper/const", 0.5, 0.75, reference=None)
        with pytest.raises(HandleEditError, match="not a finite number"):
            _grip(CONST_BAND, "tube/upper/const", 0.5, float("nan"))
        # `const` is no index: a tube WITH control points has no such grip,
        # and says so rather than "there is no entry const".
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _grip(BAND, "tube/upper/const", 0.5, 0.75)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _grip(CONST_BAND, "tube/middle/const", 0.5, 0.75)
        with pytest.raises(HandleEditError, match="'abs' has no constant width"):
            _grip(
                _with(CONST_BAND, tube_width_mode="abs"), "tube/upper/const", 0.5, 0.75
            )

    def test_no_move_in_y_changes_nothing(self):
        assert _grip(CONST_BAND, "tube/upper/const", 0.5, None) == CONST_BAND


DECLARED = {
    "metric": "event-timing",
    "variable": "x",
    "time_tolerance": 0.0625,
    "events": [
        {"time": 0.5},
        {"time": 0.75, "tolerance": {"lower": 0.125, "upper": 0.25}},
        {"time": 0.25, "tolerance": 0.125},
    ],
}
AUTO = {"metric": "event-timing", "variable": "x", "time_tolerance": 0.0625}
#: A baseline with an event (a duplicated time) at 0.25 and at 0.75.
EVENTS_REF = (
    np.array([0.0, 0.25, 0.25, 0.5, 0.75, 0.75, 1.0]),
    np.array([0.0, 0.25, 1.25, 1.5, 1.75, 0.75, 1.0]),
)


class TestEventSides:
    """`events/early|late/i` → that side of event `i`'s window: its row's
    `tolerance` where it has one, else the criterion's `time_tolerance`."""

    def test_a_declared_row_that_defers_writes_the_criterions(self):
        new = _grip(DECLARED, "events/late/0", 0.75)
        assert new["time_tolerance"] == 0.25 and "tolerance" not in new["events"][0]
        assert _grip(DECLARED, "events/early/0", 0.375)["time_tolerance"] == 0.125
        bare = {k: v for k, v in DECLARED.items() if k != "time_tolerance"}
        assert _grip(bare, "events/late/0", 0.75)["time_tolerance"] == 0.25
        pair = _with(DECLARED, time_tolerance={"lower": 0.0625, "upper": 0.125})
        assert _grip(pair, "events/early/0", 0.25)["time_tolerance"] == {
            "lower": 0.25,
            "upper": 0.125,
        }

    def test_a_rows_own_tolerance_is_the_one_written(self):
        new = _grip(DECLARED, "events/early/1", 0.25)
        assert new["events"][1]["tolerance"] == {"lower": 0.5, "upper": 0.25}
        assert new["time_tolerance"] == 0.0625
        new = _grip(DECLARED, "events/late/2", 0.5)
        assert new["events"][2]["tolerance"] == 0.25  # a number: both sides
        # y — the arrow's paper anchor, moved by a diagonal drag — is not read.
        assert _grip(DECLARED, "events/late/2", 0.5, 0.123) == new

    def test_auto_detected_events_are_the_references(self):
        """With no `events` the scorer's events are the baseline's own
        duplicated times: event 1 is at 0.75."""
        new = _grip(AUTO, "events/late/1", 1.0, reference=EVENTS_REF)
        assert new == {**AUTO, "time_tolerance": 0.25}
        new = _grip(AUTO, "events/early/0", 0.125, reference=EVENTS_REF)
        assert new["time_tolerance"] == 0.125

    def test_the_event_index_counts_inside_the_window(self):
        """The scorer detects events on the reference AS WINDOWED: from 0.5
        on, the one event left — at 0.75 — is event 0."""
        windowed = _with(AUTO, window={"start": 0.5})
        new = _grip(windowed, "events/late/0", 1.0, reference=EVENTS_REF)
        assert new["time_tolerance"] == 0.25
        with pytest.raises(HandleEditError, match="there is no event 1"):
            _grip(windowed, "events/late/1", 1.0, reference=EVENTS_REF)

    def test_refusals(self):
        with pytest.raises(HandleEditError) as box:
            _grip(DECLARED, "events/late/0", 0.25)
        assert box.value.reason == "the late side would cross its event's time"
        with pytest.raises(HandleEditError) as box:
            _grip(DECLARED, "events/early/1", 0.875)
        assert box.value.reason == "the early side would cross its event's time"
        assert _grip(DECLARED, "events/late/0", 0.5)["time_tolerance"] == 0.0
        with pytest.raises(HandleEditError, match="scores against 'golden'"):
            _grip(
                _with(AUTO, against="golden"),
                "events/late/0",
                0.5,
                reference=EVENTS_REF,
            )
        # A declared event's time is its own: another baseline moves nothing.
        assert (
            _grip(_with(DECLARED, against="golden"), "events/late/0", 0.75)[
                "time_tolerance"
            ]
            == 0.25
        )
        with pytest.raises(HandleEditError, match="needs a reference"):
            _grip(AUTO, "events/late/0", 0.5, reference=None)
        with pytest.raises(HandleEditError, match="there is no event 0"):
            _grip(AUTO, "events/late/0", 0.5)  # REF holds no event
        with pytest.raises(HandleEditError, match="no entry 9"):
            _grip(DECLARED, "events/late/9", 0.5)
        with pytest.raises(HandleEditError, match="not a finite number"):
            _grip(DECLARED, "events/late/0", float("nan"))
        for local in (
            "events/window/0",
            "events/ref/0",
            "events/late",
            "peaks/lower/0",
        ):
            with pytest.raises(HandleEditError, match="cannot be moved"):
                _grip(DECLARED, local, 0.75)

    def test_no_move_in_x_changes_nothing(self):
        assert _grip(DECLARED, "events/late/0", None, 0.3) == DECLARED


PEAK_SIDES = {
    "metric": "dominant-frequency",
    "variable": "x",
    "peaks": [
        {"freq": 2.0, "tolerance": 0.125, "tolerance_mode": "rel"},
        {"freq": 4.0, "tolerance": {"lower": 0.125, "upper": 0.25}},
        {"freq": 8.0, "tolerance": 0.5, "tolerance_mode": "abs"},
        {"freq": 16.0},
    ],
}


class TestPeakSides:
    """`peaks/lower|upper/i` → that side of `peaks[i]["tolerance"]`."""

    def test_rel_is_the_fraction_of_the_declared_frequency(self):
        new = _grip(PEAK_SIDES, "peaks/upper/0", 2.5)
        assert new["peaks"][0] == {
            "freq": 2.0,
            "tolerance": 0.25,
            "tolerance_mode": "rel",
        }
        assert _grip(PEAK_SIDES, "peaks/lower/0", 1.5)["peaks"][0]["tolerance"] == 0.25

    def test_a_pair_changes_only_that_side(self):
        # No `tolerance_mode`: rel, as the scorer reads it.
        new = _grip(PEAK_SIDES, "peaks/lower/1", 2.0)
        assert new["peaks"][1]["tolerance"] == {"lower": 0.5, "upper": 0.25}
        new = _grip(PEAK_SIDES, "peaks/upper/1", 6.0)
        assert new["peaks"][1]["tolerance"] == {"lower": 0.125, "upper": 0.5}

    def test_abs_is_the_distance_in_hz(self):
        assert _grip(PEAK_SIDES, "peaks/upper/2", 9.0)["peaks"][2]["tolerance"] == 1.0
        assert _grip(PEAK_SIDES, "peaks/lower/2", 7.75)["peaks"][2]["tolerance"] == 0.25

    def test_a_row_without_a_tolerance_gets_one_on_its_first_drag(self):
        new = _grip(PEAK_SIDES, "peaks/upper/3", 20.0)
        assert new["peaks"][3] == {"freq": 16.0, "tolerance": 0.25}

    def test_the_amplitude_is_not_read(self):
        assert _grip(PEAK_SIDES, "peaks/upper/0", 2.5, 123.0) == _grip(
            PEAK_SIDES, "peaks/upper/0", 2.5
        )
        assert _grip(PEAK_SIDES, "peaks/upper/0", None, 123.0) == PEAK_SIDES

    def test_refusals(self):
        with pytest.raises(HandleEditError) as box:
            _grip(PEAK_SIDES, "peaks/upper/0", 1.5)
        assert box.value.reason == "the upper side would cross its target"
        with pytest.raises(HandleEditError, match="the lower side would cross"):
            _grip(PEAK_SIDES, "peaks/lower/2", 8.5)
        assert _grip(PEAK_SIDES, "peaks/lower/0", 2.0)["peaks"][0]["tolerance"] == 0.0
        zero = copy.deepcopy(PEAK_SIDES)
        zero["peaks"][0]["freq"] = 0.0
        with pytest.raises(HandleEditError, match="the declared frequency is zero"):
            _grip(zero, "peaks/upper/0", 0.5)

    def test_a_lower_edge_dragged_to_zero_hertz_or_below_is_refused(self):
        """Dragged left of 0 Hz, a `rel` lower side came out above 1 — and
        on a row holding a number was written to BOTH sides (2.61). No
        window reaches 0 Hz, in either mode, whatever the row holds."""
        for i in range(4):
            for x in (0.0, -1.0, -3.22):
                with pytest.raises(HandleEditError) as box:
                    _grip(PEAK_SIDES, f"peaks/lower/{i}", x)
                assert box.value.reason == "the window would reach 0 Hz", (i, x)
        # Just short of it is a wide window, and is written.
        new = _grip(PEAK_SIDES, "peaks/lower/1", 0.5)
        assert new["peaks"][1]["tolerance"] == {"lower": 0.875, "upper": 0.25}
        # The upper edge is not the lower's rule: 0 Hz is its crossing.
        with pytest.raises(HandleEditError, match="the upper side would cross"):
            _grip(PEAK_SIDES, "peaks/upper/0", 0.0)
        with pytest.raises(HandleEditError, match="not a finite number"):
            _grip(PEAK_SIDES, "peaks/upper/0", float("inf"))
        with pytest.raises(HandleEditError, match="no entry 9"):
            _grip(PEAK_SIDES, "peaks/upper/9", 2.5)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _grip(PEAK_SIDES, "peaks/window/0", 2.5)

    @pytest.mark.parametrize(
        ("row", "refused", "widest"),
        [
            # rel, a number at 2 Hz: twice the frequency is a lower side of 1.
            (0, (4.0, 5.0), (3.5, 0.75)),
            # abs, a number at 8 Hz: the lower side reaches the frequency.
            (2, (16.0, 20.0), (15.5, 7.5)),
            # No tolerance yet: its first drag writes a number, rel.
            (3, (32.0, 40.0), (28.0, 0.75)),
        ],
    )
    def test_a_linked_upper_edge_dragged_wide_is_refused(self, row, refused, widest):
        """A NUMBER is both sides: its upper grip dragged past twice the
        peak's frequency wrote a fraction of 1 or more — and the window's
        LOWER edge, which nobody dragged, reached 0 Hz or went below it.
        Refused by what the write would leave, not by which grip moved."""
        for x in refused:
            with pytest.raises(HandleEditError) as box:
                _grip(PEAK_SIDES, f"peaks/upper/{row}", x)
            assert box.value.reason == "the window would reach 0 Hz", x
        # Just short of it is a wide window, and is written — to both sides.
        x, written = widest
        assert _grip(PEAK_SIDES, f"peaks/upper/{row}", x)["peaks"][row][
            "tolerance"
        ] == pytest.approx(written)

    def test_a_pairs_upper_edge_is_dragged_as_wide_as_one_likes(self):
        """A pair's upper side is its own: the lower edge stays where it
        was, short of 0 Hz, however far the upper one goes."""
        new = _grip(PEAK_SIDES, "peaks/upper/1", 40.0)
        assert new["peaks"][1]["tolerance"] == {"lower": 0.125, "upper": 9.0}
        # ... and one whose lower edge ALREADY reaches 0 Hz takes no write —
        # in the words for what is so: the upper grip moved nothing there.
        wide = copy.deepcopy(PEAK_SIDES)
        wide["peaks"][1]["tolerance"] = {"lower": 1.0, "upper": 0.25}
        with pytest.raises(HandleEditError) as box:
            _grip(wide, "peaks/upper/1", 6.0)
        assert box.value.reason == "the window's lower side already reaches 0 Hz"

    @pytest.mark.parametrize(
        ("tolerance", "mode"),
        [({"lower": 1.5, "upper": 0.25}, "rel"), ({"lower": 9.0, "upper": 1.0}, "abs")],
    )
    def test_already_is_said_of_a_pairs_upper_grip_alone(self, tolerance, mode):
        """ "Already" is for the side the drag did not write: a pair's lower
        side under its upper grip. The lower grip, and either grip of a
        number, write the side that reaches 0 Hz: that "would"."""
        wide = copy.deepcopy(PEAK_SIDES)
        wide["peaks"][2] = {"freq": 8.0, "tolerance": tolerance, "tolerance_mode": mode}
        with pytest.raises(HandleEditError) as box:
            _grip(wide, "peaks/upper/2", 12.0)
        assert box.value.reason == "the window's lower side already reaches 0 Hz"
        with pytest.raises(HandleEditError) as box:
            _grip(wide, "peaks/lower/2", -1.0)
        assert box.value.reason == "the window would reach 0 Hz"
        # Its lower grip brought short of 0 Hz is the way out, and is written.
        new = _grip(wide, "peaks/lower/2", 4.0)["peaks"][2]["tolerance"]
        assert new == {
            "lower": 0.5 if mode == "rel" else 4.0,
            "upper": tolerance["upper"],
        }
        # A number that reaches it, dragged by its upper grip to one that
        # still does: the write is what reaches 0 Hz.
        linked = copy.deepcopy(PEAK_SIDES)
        linked["peaks"][2] = {"freq": 8.0, "tolerance": 9.0, "tolerance_mode": "abs"}
        with pytest.raises(HandleEditError) as box:
            _grip(linked, "peaks/upper/2", 16.5)
        assert box.value.reason == "the window would reach 0 Hz"

    @pytest.mark.parametrize(
        ("tolerance", "refused", "nearest"),
        [
            # A number: both sides, 3 Hz.
            (3.0, (2.0, 3.0, 0.5), 3.25),
            # A pair: its lower side, 3 Hz.
            ({"lower": 3.0, "upper": 0.5}, (2.0, 3.0), 3.25),
            # None of its own: 0.01 Hz each side, as the scorer reads it.
            (None, (0.01, 0.005), 0.02),
        ],
    )
    def test_a_ring_dragged_left_cannot_put_an_abs_window_at_zero_hertz(
        self, tolerance, refused, nearest
    ):
        """An `abs` window keeps its width in Hz and moves with its ring: a
        peak at 5 Hz, 3 Hz either side, its ring dragged to 2 Hz was a
        window from −1 to 5 Hz. The grips refuse a window that reaches
        0 Hz; so does the ring, in the same words."""
        leaf = copy.deepcopy(PEAK_SIDES)
        row = {"freq": 5.0, "tolerance_mode": "abs"}
        if tolerance is not None:
            row["tolerance"] = tolerance
        leaf["peaks"][2] = row
        for x in refused:
            with pytest.raises(HandleEditError) as box:
                _edit(leaf, "peaks/declared/2", x)
            assert box.value.reason == "the window would reach 0 Hz", x
            assert str(box.value) == "peaks/declared/2: the window would reach 0 Hz"
        new = _edit(leaf, "peaks/declared/2", nearest)["peaks"][2]
        assert new == {**row, "freq": nearest}
        # Dragged right there is nothing to refuse.
        assert _edit(leaf, "peaks/declared/2", 40.0)["peaks"][2]["freq"] == 40.0

    @pytest.mark.parametrize(
        "tolerance",
        [9.0, {"lower": 9.0, "upper": 1.0}, 5.0, {"lower": 5.0, "upper": 1.0}],
    )
    def test_a_ring_on_a_window_already_at_zero_hertz_says_already(self, tolerance):
        """A window written by hand that reaches 0 Hz before anything is
        dragged — 5 Hz with 9 Hz below it, or with exactly its own 5 — is
        not the drag's doing, and the ring says so in the words the upper
        arrow has for the same state: it said "would reach", of a window
        that already did. Dragged far enough right it is short of 0 Hz
        again, and that is written."""
        leaf = copy.deepcopy(PEAK_SIDES)
        row = {"freq": 5.0, "tolerance": tolerance, "tolerance_mode": "abs"}
        leaf["peaks"][2] = row
        for x in (2.0, 4.0, 5.0):
            with pytest.raises(HandleEditError) as box:
                _edit(leaf, "peaks/declared/2", x)
            assert box.value.reason == "the window's lower side already reaches 0 Hz", x
        assert _edit(leaf, "peaks/declared/2", 9.5)["peaks"][2] == {**row, "freq": 9.5}
        # One a hair short of 0 Hz is brought there by the drag: "would".
        row["tolerance"] = 4.99
        with pytest.raises(HandleEditError) as box:
            _edit(leaf, "peaks/declared/2", 4.0)
        assert box.value.reason == "the window would reach 0 Hz"

    def test_a_ring_moves_a_rel_window_wherever_its_frequency_is_positive(self):
        """A `rel` window is a fraction of its frequency: it scales with
        the ring and no drag brings it to 0 Hz — whatever the fraction."""
        assert _edit(PEAK_SIDES, "peaks/declared/0", 0.001)["peaks"][0]["freq"] == 0.001
        assert _edit(PEAK_SIDES, "peaks/declared/3", 0.001)["peaks"][3]["freq"] == 0.001
        wide = copy.deepcopy(PEAK_SIDES)
        wide["peaks"][1]["tolerance"] = {"lower": 1.0, "upper": 0.25}
        assert _edit(wide, "peaks/declared/1", 3.0)["peaks"][1]["freq"] == 3.0

    def test_a_ring_of_an_abs_row_with_a_tolerance_the_scorer_refuses_says_so(self):
        """The window is the scorer's (`_peak_window`): a tolerance it
        cannot read is refused in its words, as a grip's write is."""
        leaf = copy.deepcopy(PEAK_SIDES)
        leaf["peaks"][2]["tolerance"] = {"lower": 0.5}
        with pytest.raises(HandleEditError, match=r"peaks\[2\]\.tolerance"):
            _edit(leaf, "peaks/declared/2", 6.0)
