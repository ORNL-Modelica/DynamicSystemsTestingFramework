"""The figure layer: a view per variable, one Plotly dict, no mode names."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.geometry import Band, Box, HLine, Marker, Series, Window
from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
from dstf.figures.decimate import decimate_band
from dstf.figures.plotly import PLOT_CONFIG, variable_figure
from dstf.figures.view import OverlayView, build_view, decimate_view


def _stamped(vc, path="/metrics/children/0"):
    stamp_leaf_path(leaf_from_variable(vc), path)
    return path, vc


def _tube_view(n=11, **cfg):
    t = np.linspace(0.0, 1.0, n)
    ref = np.sin(t)
    act = ref + 0.01
    vc = _compare_tube(
        t, ref, t, act, {"tube_width_mode": "band", "tube_abs": 0.05, **cfg}
    )
    vc.name = "x"
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


class TestView:
    def test_error_series_exist_only_when_both_curves_do(self):
        t = np.linspace(0.0, 1.0, 5)
        with_ref = build_view(
            "x", act_time=t, act_values=t, ref_time=t, ref_values=t * 2
        )
        assert [s.id for s in with_ref.errors] == [
            "error/signed",
            "error/abs",
            "error/nrmse",
        ]
        np.testing.assert_allclose(with_ref.errors[0].y, t - t * 2)
        assert build_view("x", act_time=t, act_values=t).errors == ()
        assert build_view("x").act_time is None and build_view("x").ref_time is None

    def test_leaf_views_carry_geometry_and_summary(self):
        view = _tube_view()
        (leaf,) = view.leaves
        assert leaf.path == "/metrics/children/0" and leaf.metric == "tube"
        assert leaf.summary.display.endswith("% in tube")
        assert any(isinstance(p, Band) for p in leaf.geometry)

    def test_decimate_view_keeps_band_edges_on_one_axis_and_stamps_counts(self):
        view = decimate_view(_tube_view(n=2001), 100)
        assert len(view.act_time) <= 100
        assert view.full_samples["act"] == view.full_samples["ref"] == 2001
        band = next(
            p for leaf in view.leaves for p in leaf.geometry if isinstance(p, Band)
        )
        # Every decimated primitive records what it was, by id.
        assert view.full_samples[band.id] == 2001
        assert all(view.full_samples[s.id] == 2001 for s in view.errors)
        assert len(band.time) <= 100 and len(band.lower) == len(band.upper) == len(
            band.time
        )
        assert all(len(s.x) <= 100 for s in view.errors)

    def test_decimating_an_already_decimated_view_keeps_the_original_counts(self):
        """The Session caches a decimated base view and decimates it again
        with the leaves; the banner must still say 2001, not 100."""
        base = _tube_view(n=2001)
        overlay = OverlayView(
            name="prev",
            role="companion",
            kind=None,
            time=base.act_time,
            values=base.act_values,
        )
        once = decimate_view(replace(base, overlays=(overlay,)), 100)
        twice = decimate_view(once, 100)
        assert twice.full_samples["act"] == 2001
        assert twice.full_samples["ref"] == 2001
        assert all(twice.full_samples[s.id] == 2001 for s in twice.errors)
        assert twice.overlays[0].full_samples == 2001
        np.testing.assert_array_equal(twice.act_time, once.act_time)

    def test_decimate_band_reads_lower_at_the_kept_nodes(self):
        t = np.linspace(0.0, 1.0, 1001)
        t2, lo2, up2 = decimate_band(t, -t, t, 50)
        np.testing.assert_allclose(lo2, -t2)
        np.testing.assert_allclose(up2, t2)


class TestErrorSeriesFollowTheComparator:
    def test_an_event_is_not_an_error_spike(self):
        # Modelica writes an event as two samples at one instant. The
        # comparator pairs pre-event actual with pre-event reference; a raw
        # np.interp pairs the post-event actual with the pre-event reference
        # and draws an error the verdict does not contain.
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        v = np.array([0.0, 0.5, 1.0, 5.0, 5.5, 6.0])
        view = build_view("x", act_time=t, act_values=v, ref_time=t, ref_values=v)
        for s in view.errors:
            np.testing.assert_array_equal(s.y, 0.0)

    def test_constant_reference_normalises_by_magnitude(self):
        from dstf.comparison.algorithms import _compare_trajectories

        t = np.linspace(0.0, 1.0, 11)
        ref = np.full_like(t, 37e9)
        act = ref + 100.0
        view = build_view("x", act_time=t, act_values=act, ref_time=t, ref_values=ref)
        per_point = next(s for s in view.errors if s.id == "error/nrmse")
        vc = _compare_trajectories(t, ref, t, act, tolerance=1.0)
        # Every per-point value equals the scored NRMSE for a constant offset.
        np.testing.assert_allclose(per_point.y, vc.nrmse)


class TestViewInputs:
    def test_overlays_accept_arrays_as_well_as_lists(self):
        t = np.linspace(0.0, 1.0, 5)
        view = build_view(
            "x",
            act_time=t,
            act_values=t,
            overlays=[
                {"name": "a", "role": "companion", "time": t, "values": t * 2},
                {"name": "b", "role": "soft_check", "time": t.tolist(), "values": []},
            ],
        )
        assert len(view.overlays[0].time) == 5 and len(view.overlays[1].values) == 0


class TestFigure:
    def test_figure_is_a_plain_dict_with_ids_on_every_trace_and_shape(self):
        fig = variable_figure(_tube_view())
        assert set(fig) == {"data", "layout", "config"}
        names = [tr["name"] for tr in fig["data"]]
        assert names[:2] == ["Actual", "Reference"]
        band = next(
            tr
            for tr in fig["data"]
            if tr["meta"].get("id") == "/metrics/children/0/tube/band"
        )
        assert band["fill"] == "toself" and len(band["x"]) == 2 * 11
        assert all(
            isinstance(tr["x"], list) for tr in fig["data"]
        )  # JSON-ready, no arrays
        assert "xaxis2" not in fig["layout"]

    def test_handles_group_into_one_trace_per_family_with_ids_in_customdata(self):
        fig = variable_figure(
            _tube_view(
                tube_points=[
                    {"time": 0.2, "upper": 0.1, "lower": 0.1},
                    {"time": 0.8, "upper": 0.1, "lower": 0.1},
                ]
            )
        )
        upper = next(
            tr for tr in fig["data"] if tr["meta"].get("family") == "tube/upper"
        )
        assert upper["customdata"] == [
            "/metrics/children/0/tube/upper/0",
            "/metrics/children/0/tube/upper/1",
        ]
        assert upper["meta"]["kind"] == "handle" and upper["mode"] == "markers"

    def test_each_marker_carries_its_own_verdict_colour(self):
        from dstf.comparison.algorithms import _compare_points

        t = np.linspace(0.0, 10.0, 101)
        act = t + 0.05 * (t > 5.0)
        vc = _compare_points(
            t,
            t,
            t,
            act,
            points=[{"time": 2.0, "tolerance": 0.01}, {"time": 8.0, "tolerance": 0.01}],
        )
        vc.name = "x"
        fig = variable_figure(
            build_view(
                "x",
                act_time=t,
                act_values=act,
                ref_time=t,
                ref_values=t,
                leaves=[_stamped(vc)],
            )
        )
        targets = next(
            tr for tr in fig["data"] if tr["meta"].get("family") == "points/target"
        )
        colours = targets["marker"]["color"]
        assert isinstance(colours, list) and len(colours) == 2
        assert colours[0] != colours[1]
        # The failed marker and its box agree on red; the passed pair is not red.
        from dstf.figures.plotly import FAIL_LINE

        boxes = {s["name"]: s for s in fig["layout"]["shapes"]}
        assert colours[1] == FAIL_LINE and colours[0] != FAIL_LINE
        assert boxes["/metrics/children/0/points/box/1"]["line"]["color"] == FAIL_LINE
        assert boxes["/metrics/children/0/points/box/0"]["line"]["color"] != FAIL_LINE

    def test_window_and_range_bounds_become_shapes_clipped_to_the_window(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_range(t, t, min_value=None, max_value=0.5)
        vc.name = "x"
        vc.geometry = vc.geometry + (Window(id="window", start=0.25, end=None),)
        view = build_view("x", act_time=t, act_values=t, leaves=[_stamped(vc)])
        fig = variable_figure(view)
        shapes = {s["name"]: s for s in fig["layout"]["shapes"]}
        assert (
            shapes["/metrics/children/0/window"]["xref"] == "x"
            and shapes["/metrics/children/0/window"]["x1"] == 1.0
        )
        strong = shapes["/metrics/children/0/range/max"]
        assert (strong["x0"], strong["x1"], strong["y0"]) == (0.25, 1.0, 0.5)
        assert (
            shapes["/metrics/children/0/range/max/outside"]["line"]["color"]
            != strong["line"]["color"]
        )
        # An invisible anchor keeps the bound inside the autorange.
        assert any(
            tr.get("meta", {}).get("anchor_for") == "/metrics/children/0/range/max"
            for tr in fig["data"]
        )

    @pytest.mark.parametrize("editable", [False, True])
    def test_area_shapes_come_before_every_line_so_a_bound_takes_its_drag(
        self, editable
    ):
        """Under `edits.shapePosition` the topmost shape takes a drag. The
        window's rect came last and swallowed every drag on the bound inside
        it, which `edit_on_plot` then refused (measured in Chromium on
        plotly.js 3.1.1, 2026-09-26)."""
        t = np.linspace(0.0, 10.0, 101)
        vc = _compare_range(t, t, min_value=None, max_value=0.9)
        vc.name = "x"
        vc.geometry = vc.geometry + (Window(id="window", start=2.0, end=8.0),)
        view = build_view("x", act_time=t, act_values=t, leaves=[_stamped(vc)])
        shapes = variable_figure(view, editable=editable)["layout"]["shapes"]
        kinds = [s["type"] for s in shapes]
        first_line = next(i for i, k in enumerate(kinds) if k != "rect")
        assert all(k != "rect" for k in kinds[first_line:]), kinds
        names = [s.get("name") for s in shapes]
        leaf_path = "/metrics/children/0"
        assert names.index(f"{leaf_path}/window") < names.index(
            f"{leaf_path}/range/max"
        )

    def test_spectrum_primitives_open_a_second_panel(self):
        fig = variable_figure(_spectrum_view())
        assert fig["layout"]["xaxis2"]["title"]["text"] == "Frequency [Hz]"
        spectrum = next(
            tr
            for tr in fig["data"]
            if tr["meta"].get("id") == "/metrics/children/0/spectrum/act"
        )
        assert spectrum["xaxis"] == "x2" and spectrum["yaxis"] == "y2"
        window = next(
            s
            for s in fig["layout"]["shapes"]
            if s["name"] == "/metrics/children/0/peaks/window/0"
        )
        assert window["xref"] == "x2" and window["yref"] == "paper"

    def test_error_series_ride_on_two_right_axes_hidden_by_default(self):
        """Brief §3.4: one labelled right axis per unit family — signed and
        absolute error in signal units, per-point NRMSE dimensionless —
        both laid out hidden until a series on them is shown (axes.js)."""
        fig = variable_figure(_tube_view())
        err = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
        assert {tr["meta"]["id"]: tr["yaxis"] for tr in err} == {
            "error/signed": "y3",
            "error/abs": "y3",
            "error/nrmse": "y4",
        }
        assert all(tr["visible"] == "legendonly" for tr in err)
        layout = fig["layout"]
        for key, title in (
            ("yaxis3", "Error [signal units]"),
            ("yaxis4", "NRMSE [–]"),
        ):
            axis = layout[key]
            assert axis["title"]["text"] == title
            assert axis["overlaying"] == "y" and axis["side"] == "right"
            assert axis["visible"] is False and axis["zeroline"] is False
            assert axis["automargin"] is True
        # The second right axis floats past the first (spike: 58 px under
        # plotly.js 2.35.0), instead of drawing over it.
        assert layout["yaxis4"]["anchor"] == "free"
        assert layout["yaxis4"]["autoshift"] is True
        assert "anchor" not in layout["yaxis3"]

    def test_an_error_series_with_no_declared_axis_is_refused(self):
        """A fourth error family would otherwise be drawn silently against
        the signed-error axis, in units that are not its own. ERROR_AXES is
        the fence: adding a series without adding its axis is a KeyError at
        the moment it is introduced, not a wrong scale in the report."""
        view = _tube_view()
        unknown = replace(view.errors[0], id="error/relative")
        with pytest.raises(KeyError, match="error/relative"):
            variable_figure(replace(view, errors=(*view.errors, unknown)))

    def test_a_view_without_errors_lays_out_no_secondary_axis(self):
        t = np.linspace(0.0, 1.0, 5)
        fig = variable_figure(build_view("x", act_time=t, act_values=t))
        assert "yaxis3" not in fig["layout"] and "yaxis4" not in fig["layout"]

    def test_budget_decimates_inside_variable_figure(self):
        fig = variable_figure(_tube_view(n=5001), max_samples=200)
        actual = next(tr for tr in fig["data"] if tr["name"] == "Actual")
        assert len(actual["x"]) <= 200 and actual["meta"]["full_samples"] == 5001
        # Band and error traces are decimated too, and say so.
        band = next(
            tr for tr in fig["data"] if tr["meta"].get("id", "").endswith("tube/band")
        )
        assert len(band["x"]) <= 400 and band["meta"]["full_samples"] == 5001
        errors = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
        assert errors and all(tr["meta"]["full_samples"] == 5001 for tr in errors)

    def test_every_decimatable_trace_stamps_its_sample_count(self):
        """``meta.samples`` is samples; ``x`` is points on the wire.

        A band is a closed polygon — 2N points for N samples — so a reader
        that measures decimation off ``len(x)`` misreads it. Each trace the
        figure layer can decimate carries its own count instead.
        """
        view = decimate_view(_tube_view(n=5001), 200)
        fig = variable_figure(view)
        band = next(
            tr for tr in fig["data"] if tr["meta"].get("id", "").endswith("tube/band")
        )
        band_prim = next(p for p in view.leaves[0].geometry if isinstance(p, Band))
        assert band["meta"]["samples"] == len(band_prim.time)
        assert len(band["x"]) == 2 * band["meta"]["samples"]

        for tr in fig["data"]:
            if "full_samples" not in tr.get("meta", {}):
                continue
            assert "samples" in tr["meta"], tr["name"]
        by_name = {tr["name"]: tr for tr in fig["data"]}
        for name in ("Actual", "Reference"):
            assert by_name[name]["meta"]["samples"] == len(by_name[name]["x"])
        errors = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
        assert errors and all(tr["meta"]["samples"] == len(tr["x"]) for tr in errors)

    def test_a_view_with_no_trajectory_still_renders_its_lines(self):
        from dstf.comparison.algorithms import _compare_scalar

        one = np.array([0.0])
        vc = _compare_scalar(
            one,
            np.array([3.0]),
            one,
            np.array([3.1]),
            reduction="final",
            tolerance=0.5,
            tolerance_mode="abs",
            min_value=None,
            max_value=None,
        )
        vc.name = "CPUtime.final"
        fig = variable_figure(build_view("CPUtime.final", leaves=[_stamped(vc)]))
        names = {s["name"] for s in fig["layout"]["shapes"]}
        assert {
            "/metrics/children/0/scalar/actual",
            "/metrics/children/0/scalar/reference",
        } <= names
        assert fig["data"] == [] or all(
            "anchor_for" in tr.get("meta", {}) for tr in fig["data"]
        )


def _handle_view():
    """Two time-panel handle Markers, one spectrum-panel handle Marker, two
    non-handle Markers (a detected spectrum peak, the kind a mode really
    emits, and one left at the DEFAULT kind — what a third-party metric's
    Marker gets), an HLine handle and a Box handle."""
    t = np.linspace(0.0, 1.0, 11)
    vc = _compare_range(t, t, min_value=None, max_value=None)
    vc.name = "x"
    vc.geometry = (
        Marker(id="tube/upper/0", kind="handle", x=0.5, y=0.6),
        Marker(id="tube/lower/0", kind="handle", x=0.5, y=0.3),
        Marker(id="peaks/declared/0", kind="handle", panel="spectrum", x=2.0, y=1.5),
        Marker(id="peaks/detected/0", kind="detected", panel="spectrum", x=3.0, y=0.5),
        Marker(id="custom/marker/0", x=0.7, y=0.7),
        HLine(id="range/max", kind="handle", value=0.9),
        Box(id="points/box/0", kind="handle", t0=0.2, t1=0.3, y0=0.2, y1=0.3),
        # A full-height window rectangle covers every handle inside it: the
        # rings must still come after it.
        Window(id="window", start=0.1, end=0.95),
    )
    return build_view(
        "x", act_time=t, act_values=t, ref_time=t, ref_values=t, leaves=[_stamped(vc)]
    )


class TestEditableFigure:
    def test_editable_turns_on_shape_dragging(self):
        fig = variable_figure(_handle_view(), editable=True)
        assert fig["config"]["edits"] == {"shapePosition": True}

    def test_the_tools_figure_leaves_the_wheel_to_the_page(self):
        """The Test page is a full-width stack of plots: with `scrollZoom` on,
        one wheel notch puts a plot under the cursor and the page never
        scrolls again, so the cards below are never drawn (measured in
        Chromium). The report's page keeps today's config."""
        view = _handle_view()
        assert variable_figure(view, editable=True)["config"]["scrollZoom"] is False
        assert variable_figure(view)["config"]["scrollZoom"] is True
        assert variable_figure(view, editable=False)["config"] == PLOT_CONFIG

    def test_one_ring_per_handle_marker_and_everything_else_unchanged(self):
        view = _handle_view()
        plain = variable_figure(view)
        fig = variable_figure(view, editable=True)
        rings = [s for s in fig["layout"]["shapes"] if s["type"] == "circle"]
        p = "/metrics/children/0"
        expected = {
            f"{p}/tube/upper/0": (0.5, 0.6, "x", "y"),
            f"{p}/tube/lower/0": (0.5, 0.3, "x", "y"),
            f"{p}/peaks/declared/0": (2.0, 1.5, "x2", "y2"),
        }
        assert len(rings) == len(expected)
        got = {
            r["name"]: (r["xanchor"], r["yanchor"], r["xref"], r["yref"]) for r in rings
        }
        assert got == expected
        assert all(r["xsizemode"] == r["ysizemode"] == "pixel" for r in rings)
        # Neither non-handle Marker gets a ring: not the detected peak, and
        # not one at the default "result" kind.
        assert f"{p}/peaks/detected/0" not in got
        assert f"{p}/custom/marker/0" not in got
        # Rings come LAST: the later shape takes a drag, and a point's
        # target ring sits at the centre of its box rect.
        shapes = fig["layout"]["shapes"]
        assert shapes[-len(rings) :] == rings
        others = [s for s in shapes if s["type"] != "circle"]
        assert others == plain["layout"]["shapes"]
        assert {s["name"] for s in others} >= {f"{p}/range/max", f"{p}/points/box/0"}
        assert fig["data"] == plain["data"]

    def test_rings_follow_every_leafs_shapes(self):
        """Rings are collected across ALL leaves and appended once: a later
        leaf's line must not be drawn above an earlier leaf's ring, or it
        takes the drag."""
        t = np.linspace(0.0, 1.0, 11)
        first = _compare_range(t, t, min_value=None, max_value=None)
        first.name = "x"
        first.geometry = (Marker(id="tube/lower/0", kind="handle", x=0.0, y=-0.5),)
        second = _compare_range(t, t, min_value=-0.5, max_value=None)
        second.name = "x"
        view = build_view(
            "x",
            act_time=t,
            act_values=t,
            ref_time=t,
            ref_values=t,
            leaves=[
                _stamped(first, "/metrics/children/0"),
                _stamped(second, "/metrics/children/1"),
            ],
        )
        shapes = variable_figure(view, editable=True)["layout"]["shapes"]
        rings = [s for s in shapes if s["type"] == "circle"]
        assert [r["name"] for r in rings] == ["/metrics/children/0/tube/lower/0"]
        # The second leaf really draws a non-ring shape, after the ring's leaf.
        assert any(
            s["name"].startswith("/metrics/children/1/") and s["type"] != "circle"
            for s in shapes
        )
        assert shapes[-len(rings) :] == rings

    def test_the_default_figure_is_not_editable(self):
        view = _handle_view()
        plain = variable_figure(view)
        assert "edits" not in plain["config"]
        assert not any(s["type"] == "circle" for s in plain["layout"]["shapes"])
        assert plain == variable_figure(view, editable=False)


def _two_ring_view(name="x", paths=("/metrics/children/0", "/metrics/children/1")):
    """Two leaves, EACH with a handle Marker: what `rings_for` tells apart."""
    t = np.linspace(0.0, 1.0, 11)
    leaves = []
    for k, path in enumerate(paths):
        vc = _compare_range(t, t, min_value=None, max_value=None)
        vc.name = name
        vc.geometry = (
            Marker(id=f"tube/upper/{k}", kind="handle", x=0.2 + 0.3 * k, y=0.6),
            HLine(id="range/max", kind="handle", value=0.9),
        )
        leaves.append(_stamped(vc, path))
    return build_view(
        name, act_time=t, act_values=t, ref_time=t, ref_values=t, leaves=leaves
    )


def _ring_names(fig: dict) -> set[str]:
    return {s["name"] for s in fig["layout"]["shapes"] if s.get("type") == "circle"}


def test_rings_for_limits_the_rings_to_the_named_leaves():
    view = _two_ring_view()
    every = _ring_names(variable_figure(view, None, editable=True))
    first = next(
        lv.path for lv in view.leaves if any(n.startswith(lv.path + "/") for n in every)
    )
    only = _ring_names(variable_figure(view, None, editable=True, rings_for=(first,)))
    assert only and only < every
    assert all(name.startswith(first + "/") for name in only)
    assert (
        _ring_names(variable_figure(view, None, editable=True, rings_for=())) == set()
    )
    # Exact membership, never a prefix: "/metrics" is an ancestor of both
    # leaf paths and names neither leaf.
    assert (
        _ring_names(variable_figure(view, None, editable=True, rings_for=("/metrics",)))
        == set()
    )


def test_rings_for_refuses_a_str():
    """A str IS a Collection, and its membership is by substring — a caller
    handing one path instead of a one-tuple would ring every leaf whose path
    is a substring of it, silently. `_leaf_traces` is the one place every
    figure builder reaches, so the refusal lands there."""
    view = _two_ring_view()
    with pytest.raises(TypeError, match="not a str"):
        variable_figure(view, None, editable=True, rings_for="/metrics/children/0")
    # The refusal is the argument's, not the drawing's: it fires where
    # nothing would have been ringed anyway.
    with pytest.raises(TypeError, match="not a str"):
        variable_figure(build_view("x"), None, editable=False, rings_for="/x")


def test_rings_for_changes_nothing_but_the_rings():
    view = _two_ring_view()
    full = variable_figure(view, None, editable=True)
    none = variable_figure(view, None, editable=True, rings_for=())
    assert none["data"] == full["data"] and none["config"] == full["config"]
    assert [s for s in none["layout"]["shapes"]] == [
        s for s in full["layout"]["shapes"] if s.get("type") != "circle"
    ]


def test_view_from_trajectory_reads_the_report_shape():
    from dstf.figures.assemble import view_from_trajectory

    traj = {
        "act_time": [0, 1],
        "act_values": [1, 2],
        "ref_time": [0, 1],
        "ref_values": [1, 3],  # differs from act, so a swap is caught
        "overlays": [
            {
                "name": "s",
                "role": "soft_check",
                "kind": "json",
                "time": [0, 1],
                "values": [1, 1],
            }
        ],
    }
    view = view_from_trajectory("x", traj, [])
    assert view.name == "x" and len(view.overlays) == 1
    assert list(view.act_values) == [1, 2] and list(view.ref_values) == [1, 3]
    assert view_from_trajectory("x", {}, []).act_time is None


def test_leaves_by_variable_groups_stamped_leaves():
    from dstf.comparison.metric_tree import implicit_and_tree
    from dstf.figures.assemble import leaves_by_variable

    t = np.linspace(0.0, 1.0, 11)
    vcs = [
        _compare_trajectories(t, np.sin(t), t, np.sin(t) + 0.01, 0.1) for _ in range(3)
    ]
    vcs[0].name, vcs[1].name, vcs[2].name = "x", "y", "x"
    tree = implicit_and_tree(vcs)  # stamps /metrics/children/{i}

    grouped = leaves_by_variable(tree)
    assert set(grouped) == {"x", "y"}
    assert [path for path, _ in grouped["x"]] == [
        "/metrics/children/0",
        "/metrics/children/2",
    ]
    assert all(vc.name == name for name, pairs in grouped.items() for _, vc in pairs)
    assert leaves_by_variable(None) == {}


def _spectrum_view():
    """One dominant-frequency leaf on a 2 Hz sine: its spectrum opens the
    second panel (``xaxis2`` / ``yaxis2``)."""
    t = np.linspace(0.0, 10.0, 1001)
    sig = np.sin(2 * np.pi * 2.0 * t)
    vc = _compare_dominant_frequency(
        t, sig, t, sig, peaks=[{"freq": 2.0, "tolerance": 0.05}]
    )
    vc.name = "x"
    return build_view(
        "x",
        act_time=t,
        act_values=sig,
        ref_time=t,
        ref_values=sig,
        leaves=[_stamped(vc)],
    )


def _dominant_view(sig, peaks):
    """A dominant-frequency leaf declaring ``peaks`` over ``sig`` on
    `_spectrum_view`'s grid (t 0..10 s, 1001 samples: Nyquist near 50 Hz)."""
    t = np.linspace(0.0, 10.0, 1001)
    vc = _compare_dominant_frequency(t, sig, t, sig, peaks=peaks)
    vc.name = "x"
    return build_view(
        "x",
        act_time=t,
        act_values=sig,
        ref_time=t,
        ref_values=sig,
        leaves=[_stamped(vc)],
    )


def _spectrum_x(view) -> list[float] | None:
    return variable_figure(view)["layout"]["xaxis2"].get("range")


SINE_2HZ = np.sin(2 * np.pi * 2.0 * np.linspace(0.0, 10.0, 1001))


class TestSpectrumRange:
    """Autoranged, the spectrum ran 0..Nyquist, and Nyquist follows the
    sample count, not the physics: TRANSFORM's peaks near 1.2e-5 Hz sat in
    the leftmost ~10 px of an axis to ~1.7e-3 Hz (2026-09-26)."""

    def test_it_runs_to_three_times_the_declared_peaks_window(self):
        layout = variable_figure(_spectrum_view())["layout"]
        lo, hi = layout["xaxis2"]["range"]
        assert lo == 0.0
        assert hi == pytest.approx(3 * 2.1)  # the 2 Hz peak's window, +-5%
        assert layout["xaxis2"]["uirevision"] == "keep"
        wide = _dominant_view(SINE_2HZ, [{"freq": 2.0, "tolerance": 0.5}])
        assert _spectrum_x(wide) == [0.0, pytest.approx(9.0)]

    def test_with_nothing_declared_it_runs_to_the_strongest_detected_peak(self):
        """The reference's ten detected peaks include leakage sidelobes to
        13.6 Hz: ranged to the highest of them the axis ran to 40.8 Hz."""
        lo, hi = _spectrum_x(_dominant_view(SINE_2HZ, []))
        assert lo == 0.0 and 2.0 < hi <= 7.2, hi

    def test_it_stops_at_nyquist_but_keeps_a_declared_peak_on_the_axis(self):
        t = np.linspace(0.0, 10.0, 1001)
        high = _dominant_view(
            np.sin(2 * np.pi * 30.0 * t), [{"freq": 30.0, "tolerance": 0.05}]
        )
        nyquist = max(
            float(p.x.max())
            for leaf in high.leaves
            for p in leaf.geometry
            if p.panel == "spectrum" and isinstance(p, Series)
        )
        assert _spectrum_x(high) == [0.0, pytest.approx(nyquist)]
        above = _dominant_view(SINE_2HZ, [{"freq": 60.0, "tolerance": 0.05}])
        assert _spectrum_x(above)[1] >= 63.0

    def test_an_empty_spectrum_has_no_nyquist_and_does_not_raise(self):
        """A spectrum Series with no samples (a result too short for an FFT)
        gives no Nyquist to clamp to: the axis runs to the declared peak's
        headroom."""
        from dstf.figures.plotly import _spectrum_range

        view = _spectrum_view()
        empty = np.array([])
        leaves = [
            replace(
                leaf,
                geometry=tuple(
                    replace(p, x=empty, y=empty)
                    if isinstance(p, Series) and p.panel == "spectrum"
                    else p
                    for p in leaf.geometry
                ),
            )
            for leaf in view.leaves
        ]
        assert any(
            isinstance(p, Series) and p.panel == "spectrum"
            for leaf in leaves
            for p in leaf.geometry
        )
        assert _spectrum_range(replace(view, leaves=leaves)) == [
            0.0,
            pytest.approx(3 * 2.1),
        ]

    def test_nothing_to_range_to_keeps_the_autorange(self):
        flat = np.zeros(1001)
        assert _spectrum_x(_dominant_view(flat, [])) is None
        assert _spectrum_x(_dominant_view(flat, [{"freq": 0.0}])) is None


# -- legend b: what the page chose to show (the form-row + legend plan, Task 1) --

TUBE_P = "/metrics/children/0"
RANGE_P = "/metrics/children/1"


def _tube_and_range_view():
    """A tube leaf (a band, two marker groups) and a range leaf (two lines
    and their anchor traces) on one variable, with its error series."""
    t = np.linspace(0.0, 1.0, 11)
    ref = np.sin(t)
    act = ref + 0.01
    tube = _compare_tube(
        t,
        ref,
        t,
        act,
        {
            "tube_width_mode": "band",
            "tube_abs": 0.05,
            "tube_points": [{"time": 0.2, "upper": 0.1, "lower": 0.1}],
        },
    )
    tube.name = "x"
    rng = _compare_range(t, act, min_value=-1.0, max_value=2.0)
    rng.name = "x"
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(tube, TUBE_P), _stamped(rng, RANGE_P)],
    )


def _leaf_of(trace: dict) -> str | None:
    return (trace.get("meta") or {}).get("leaf")


def test_every_leaf_trace_names_its_leaf():
    view = _tube_and_range_view()
    fig = variable_figure(view, editable=True)
    kinds = {}
    for tr in fig["data"]:
        meta = tr.get("meta") or {}
        if meta.get("kind") in ("act", "ref", "error", "overlay"):
            assert "leaf" not in meta, tr["name"]
        else:
            assert _leaf_of(tr) in (TUBE_P, RANGE_P), tr
            kinds.setdefault(_leaf_of(tr), set()).add(
                "marker"
                if "family" in meta
                else "anchor"
                if "anchor_for" in meta
                else "band"
            )
    assert kinds[TUBE_P] >= {"marker", "band"}
    assert kinds[RANGE_P] == {"anchor"}


def test_the_legend_sits_above_the_plot_area():
    from dstf.figures.plotly import plot_figure

    want = {
        "orientation": "h",
        "x": 0,
        "xanchor": "left",
        "y": 1,
        "yanchor": "bottom",
        "bgcolor": "rgba(0,0,0,0)",
    }
    view = _tube_and_range_view()
    assert variable_figure(view)["layout"]["legend"] == want
    two = plot_figure([view, replace(view, name="y", leaves=())])
    assert two["layout"]["legend"] == want


def test_without_visibility_the_figure_is_the_reports():
    view = _tube_and_range_view()
    fig = variable_figure(view)
    for tr in fig["data"]:
        assert tr.get("visible") in (None, "legendonly"), tr
    err = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
    assert err and all(
        tr["visible"] == "legendonly" and tr.get("showlegend", True) for tr in err
    )
    band = next(tr for tr in fig["data"] if tr["meta"].get("kind") == "result")
    assert band["showlegend"] is True
    assert all("visible" not in s for s in fig["layout"]["shapes"])


def test_visibility_hides_a_leafs_traces_and_shapes():
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()
    plain = variable_figure(view, editable=True, rings_for=(TUBE_P,))
    fig = variable_figure(
        view,
        editable=True,
        rings_for=(TUBE_P,),
        visibility=Visibility(hidden=frozenset({RANGE_P})),
    )
    ranged = [tr for tr in fig["data"] if _leaf_of(tr) == RANGE_P]
    assert ranged and all(tr["visible"] is False for tr in ranged)
    assert all("visible" not in tr for tr in fig["data"] if _leaf_of(tr) == TUBE_P)
    shapes = fig["layout"]["shapes"]
    assert [s["name"] for s in shapes] == [s["name"] for s in plain["layout"]["shapes"]]
    hid = [s for s in shapes if s["name"].startswith(RANGE_P + "/")]
    assert hid and all(s["visible"] is False for s in hid)
    rest = [s for s in shapes if not s["name"].startswith(RANGE_P + "/")]
    assert any(s["type"] == "circle" for s in rest)
    assert all("visible" not in s for s in rest)
    # And a hidden OPEN leaf's rings go with it (the page never asks for
    # that — the open leaf always draws — but the figure does not care).
    rings_hidden = variable_figure(
        view,
        editable=True,
        rings_for=(TUBE_P,),
        visibility=Visibility(hidden=frozenset({TUBE_P})),
    )
    circles = [s for s in rings_hidden["layout"]["shapes"] if s["type"] == "circle"]
    assert circles and all(s["visible"] is False for s in circles)


def test_visibility_puts_only_signals_in_the_legend():
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()
    plain = variable_figure(view)
    fig = variable_figure(view, visibility=Visibility())
    for before, after in zip(plain["data"], fig["data"]):
        kind = before["meta"].get("kind")
        if kind in ("act", "ref", "overlay"):
            assert after == before
        else:
            assert after["showlegend"] is False, after.get("name")


def test_visibility_shows_the_chosen_error_and_its_axis():
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()

    def state(error):
        fig = variable_figure(view, visibility=Visibility(error=error))
        vis = {
            tr["meta"]["id"]: tr["visible"]
            for tr in fig["data"]
            if tr["meta"].get("kind") == "error"
        }
        lay = fig["layout"]
        return vis, lay["yaxis3"]["visible"], lay["yaxis4"]["visible"]

    assert state("error/abs") == (
        {"error/signed": False, "error/abs": True, "error/nrmse": False},
        True,
        False,
    )
    assert state("error/nrmse") == (
        {"error/signed": False, "error/abs": False, "error/nrmse": True},
        False,
        True,
    )
    assert state(None) == (
        {"error/signed": False, "error/abs": False, "error/nrmse": False},
        False,
        False,
    )


def test_visibility_refuses_a_bare_string_and_an_unknown_error():
    from dstf.figures.plotly import Visibility

    with pytest.raises(TypeError, match="not a str"):
        Visibility(hidden="/metrics/children/0")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="error/signed, error/abs, error/nrmse"):
        Visibility(error="error/bogus")


def test_the_toolbar_stands_vertical_on_both_tiers():
    """The owner's 2026-09-24 ruling: the legend sits above the plot area,
    where a horizontal hover toolbar covered a wrapped report legend's
    second row. The toolbar stands on the right edge instead."""
    from dstf.figures.plotly import Visibility, plot_figure

    view = _tube_and_range_view()
    assert variable_figure(view)["layout"]["modebar"] == {"orientation": "v"}
    shown = variable_figure(view, visibility=Visibility(error="error/abs"))
    assert shown["layout"]["modebar"] == {"orientation": "v"}
    two = plot_figure([view, replace(view, name="y", leaves=())])
    assert two["layout"]["modebar"] == {"orientation": "v"}


def test_an_error_axis_takes_margin_only_while_it_shows():
    """With the page's Visibility, an error axis's ``automargin`` is its
    ``visible``: a hidden axis left at ``automargin: True`` kept the right
    margin it had grown (55 px after Abs error, 103 after NRMSE, against 20)
    through every later react in Chromium. The report's figure keeps both
    axes as they were — its axes.js flips ``visible`` in the page."""
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()
    for error in (None, "error/signed", "error/abs", "error/nrmse"):
        lay = variable_figure(view, visibility=Visibility(error=error))["layout"]
        for axis in ("yaxis3", "yaxis4"):
            assert lay[axis]["automargin"] is lay[axis]["visible"], (error, axis)
    lay = variable_figure(view, visibility=Visibility(error="error/nrmse"))["layout"]
    assert lay["yaxis4"]["automargin"] is True
    assert lay["yaxis3"]["automargin"] is False
    report = variable_figure(view)["layout"]
    for axis in ("yaxis3", "yaxis4"):
        assert report[axis]["automargin"] is True
        assert report[axis]["visible"] is False


def test_an_error_axis_keeps_its_zoom_only_under_one_choice():
    """With the page's Visibility, each error axis's ``uirevision`` is the
    chosen series, or "none": a box zoom made while the axes hide scales
    them too, and under the figure's "keep" that range outlived the choice
    (Abs error drew flat on [-0.49, 3.51] in Chromium). The report's figure
    sets none — its axes inherit the layout's."""
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()
    for error in (None, "error/signed", "error/abs", "error/nrmse"):
        lay = variable_figure(view, visibility=Visibility(error=error))["layout"]
        for axis in ("yaxis3", "yaxis4"):
            assert lay[axis]["uirevision"] == (error or "none"), (error, axis)
    report = variable_figure(view)["layout"]
    for axis in ("yaxis3", "yaxis4"):
        assert "uirevision" not in report[axis]


def test_every_y_axis_reads_automatic_labels_with_e_exponents():
    """The owner's 2026-09-24 ruling, both tiers: `tickformat: ".4g"` printed
    `0.2000` for 0.2 and `0.00002000` for an error around 1e-5 (54 px, into
    the toolbar's strip, against 23 px for `2e−5`). Plotly's automatic
    labels with `e` exponents read short; a hover still reads 4 significant
    digits (`hoverformat`), which a trace's own hovertemplate overrides."""
    from dstf.figures.plotly import Visibility, plot_figure

    view = _tube_and_range_view()
    report = variable_figure(view)["layout"]
    tool = variable_figure(view, visibility=Visibility(error="error/abs"))["layout"]
    two = plot_figure([view, replace(view, name="y", leaves=())])["layout"]
    axes = [
        *(("report", k, report[k]) for k in ("yaxis", "yaxis3", "yaxis4")),
        *(("tool", k, tool[k]) for k in ("yaxis", "yaxis3", "yaxis4")),
        ("plot", "yaxis", two["yaxis"]),
    ]
    for tier, key, axis in axes:
        assert "tickformat" not in axis, (tier, key)
        assert axis["exponentformat"] == "e", (tier, key)
        # `.4~g`, not `.4g`: 4 significant digits with the trailing zeros
        # trimmed (the owner's 2026-09-24 ruling), so 0.6 hovers as `0.6`
        # and 2.5e-5 as `0.000025`, not `0.6000` and `0.00002500`.
        assert axis["hoverformat"] == ".4~g", (tier, key)


def test_the_spectrum_axis_reads_the_same_labels():
    """The owner's ruling was "all y axes": the spectrum panel's magnitude
    axis prints its ticks and its hover the way the time panel's do."""
    from dstf.figures.plotly import Visibility

    view = _spectrum_view()
    for tier, fig in (
        ("report", variable_figure(view)),
        ("tool", variable_figure(view, visibility=Visibility())),
    ):
        axis = fig["layout"]["yaxis2"]
        assert "tickformat" not in axis, tier
        assert axis["exponentformat"] == "e", tier
        assert axis["hoverformat"] == ".4~g", tier


def test_every_hover_trims_to_four_significant_digits():
    """A trace's own hovertemplate overrides its axis's `hoverformat`, so
    the owner's `.4~g` ruling holds only if every template says it too: the
    labelled signals of a multi-series plot (`hover_of`) and the three error
    series (`_error_trace`), on the report's figure and on the tool's."""
    from dstf.figures.plotly import Visibility, plot_figure

    view = _tube_and_range_view()
    figures = {
        "report": variable_figure(view),
        "tool": variable_figure(view, visibility=Visibility(error="error/abs")),
        "plot": plot_figure([view, replace(view, name="y", leaves=())]),
    }
    kinds = set()
    for tier, fig in figures.items():
        for tr in fig["data"]:
            template = tr.get("hovertemplate")
            if template is None:
                continue
            kinds.add(tr["meta"]["kind"])
            assert "%{y:.4~g}" in template, (tier, tr["name"], template)
            assert ".4g" not in template, (tier, tr["name"], template)
    # Both builders were read: a vacuous pass over no template says nothing.
    assert {"act", "error"} <= kinds, kinds


def test_the_tools_nrmse_axis_is_anchored_to_the_plot():
    """With the page's Visibility only one error axis ever shows, so the
    NRMSE axis sits on the plot's edge like the other: left `free` with
    `autoshift`, it floated off the plot once Abs error had shown. The
    report keeps `free` + `autoshift`: its legend can show signed and
    NRMSE together, and the second axis must stand past the first."""
    from dstf.figures.plotly import Visibility

    view = _tube_and_range_view()
    for error in (None, "error/signed", "error/abs", "error/nrmse"):
        lay = variable_figure(view, visibility=Visibility(error=error))["layout"]
        assert lay["yaxis4"]["anchor"] == "x", error
        assert "autoshift" not in lay["yaxis4"], error
    report = variable_figure(view)["layout"]
    assert report["yaxis4"]["anchor"] == "free"
    assert report["yaxis4"]["autoshift"] is True


def test_a_context_series_draws_no_criteria_of_its_own():
    """A context variable scored on ANOTHER plot keeps its criteria there:
    beside this plot's subject it is one grey line, nothing else."""
    from dstf.figures.plotly import plot_figure

    view = _tube_and_range_view()
    subject = replace(view, name="y", leaves=())
    alone = plot_figure([subject])
    fig = plot_figure([subject], context=[view])
    added = fig["data"][len(alone["data"]) :]
    assert [tr["meta"]["kind"] for tr in added] == ["context"]
    assert fig["layout"]["shapes"] == alone["layout"]["shapes"]
