"""The figure layer: a view per variable, one Plotly dict, no mode names."""

from __future__ import annotations

import numpy as np

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.geometry import Band, Box, HLine, Marker, Window
from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
from dstf.figures.decimate import decimate_band
from dstf.figures.plotly import variable_figure
from dstf.figures.view import build_view, decimate_view


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
        once = decimate_view(_tube_view(n=2001), 100)
        twice = decimate_view(once, 100)
        assert twice.full_samples["act"] == 2001
        assert twice.full_samples["ref"] == 2001
        assert all(twice.full_samples[s.id] == 2001 for s in twice.errors)
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

    def test_spectrum_primitives_open_a_second_panel(self):
        t = np.linspace(0.0, 10.0, 1001)
        sig = np.sin(2 * np.pi * 2.0 * t)
        vc = _compare_dominant_frequency(
            t, sig, t, sig, peaks=[{"freq": 2.0, "tolerance": 0.05}]
        )
        vc.name = "x"
        view = build_view(
            "x",
            act_time=t,
            act_values=sig,
            ref_time=t,
            ref_values=sig,
            leaves=[_stamped(vc)],
        )
        fig = variable_figure(view)
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
