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


def _edit(leaf, local, x=None, y=None, reference=REF):
    """Call the leaf's metric's editor, and check it left the input alone."""
    before = copy.deepcopy(leaf)
    try:
        return metric(leaf["metric"]).edit_handle(
            HandleEdit(leaf=leaf, local=local, x=x, y=y, reference=reference)
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

    def test_a_noise_level_x_on_a_named_baseline_edits_the_width(self):
        leaf = _with(ABS, against="other", tube_points=[{"time": 0.5, "upper": 1.5}])
        new = _edit(leaf, "tube/upper/0", 0.5 + 1e-12, 2.0)
        assert new["tube_points"][0] == {"time": 0.5, "upper": 2.0}
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(leaf, "tube/upper/0", 0.5 + 0.1, 2.0)

    def test_a_noise_level_x_is_not_a_time_move(self):
        leaf = _with(BAND, tube_points=[{"time": 1.0, "upper": 0.1, "lower": 0.1}])
        new = _edit(leaf, "tube/upper/0", 1.0 + 1e-12, 1.3)
        assert new["tube_points"][0]["time"] == 1.0
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)

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

    def test_min_width_is_written_as_dragged(self):
        leaf = _with(BAND, tube_min_width=0.3)
        new = _edit(leaf, "tube/upper/0", 0.5, 0.6)
        assert new["tube_points"][0]["upper"] == pytest.approx(0.1)

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
    assert metric("nrmse").edit_handle is None
    assert metric("event-timing").edit_handle is None
    for name in ("tube", "range", "scalar", "points", "dominant-frequency"):
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

    def test_the_bounds_still_move(self):
        assert _edit(SCALAR, "scalar/max", None, 1.0)["max_value"] == 1.0
