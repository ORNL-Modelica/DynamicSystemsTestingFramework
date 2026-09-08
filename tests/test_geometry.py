"""Geometry primitives: what a mode scored against, as drawable data."""

from __future__ import annotations

import random

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_scalar,
    _compare_tube,
    _find_event_boundaries,
    _tube_bounds,
)
from dstf.comparison.geometry import Band, Box, HLine, Marker, Series, VLine, Window
from dstf.comparison.types import ScoreSummary, VariableComparison


def _vc(**kw) -> VariableComparison:
    base = dict(
        index=0,
        name="x",
        passed=True,
        nrmse=0.0,
        rmse=0.0,
        signal_range=1.0,
        max_abs_error=0.0,
        max_abs_error_time=0.0,
        reference_final=0.0,
        actual_final=0.0,
    )
    base.update(kw)
    return VariableComparison(**base)


class TestPrimitives:
    def test_prefixed_returns_a_copy_with_the_leaf_path(self):
        m = Marker(id="points/target/0", kind="handle", x=1.0, y=2.0)
        p = m.prefixed("/metrics/children/1")
        assert p.id == "/metrics/children/1/points/target/0"
        assert p.kind == "handle" and p.x == 1.0 and p.panel == "time"
        assert m.id == "points/target/0"  # frozen: the original is untouched

    def test_box_open_height_and_window_open_ends_are_none(self):
        b = Box(id="events/window/0", t0=0.9, t1=1.1, y0=None, y1=None)
        assert b.y0 is None and b.y1 is None and b.passed is None
        w = Window(id="window", start=None, end=5.0)
        assert w.start is None and w.end == 5.0

    def test_band_holds_arrays_and_defaults_to_the_time_panel(self):
        t = np.array([0.0, 1.0])
        b = Band(id="tube/band", time=t, lower=t - 1, upper=t + 1)
        assert b.panel == "time" and b.kind == "result"
        np.testing.assert_array_equal(b.upper, [1.0, 2.0])

    def test_kind_and_panel_are_keyword_only(self):
        # Exact arity, so the only reason to raise is that positionals are
        # refused — a missing-argument TypeError would not prove kw_only.
        with pytest.raises(TypeError, match="positional argument"):
            Marker("points/target/0", "handle", "time", 1.0, 2.0, None)  # type: ignore[misc]


class TestResultTypes:
    def test_variable_comparison_geometry_defaults_empty(self):
        assert _vc().geometry == ()

    def test_score_summary_is_frozen(self):
        s = ScoreSummary(display="NRMSE 1e-3", criterion="NRMSE 1e-3 vs 1e-4 → FAIL")
        with pytest.raises(AttributeError):
            s.display = "x"  # type: ignore[misc]
        assert s.score is None and s.score_label == ""


def _random_tube_case(rng: random.Random):
    """A case drawn to REACH the regimes the exactness claim excludes:
    rel widths, min-width floors, constant interpolation, control points
    off the reference grid. A generator that only produces band+linear
    would verify the claim in the one regime where it holds."""
    n_ref = rng.randint(3, 12)
    ref_time = np.array(sorted(rng.sample(range(0, 100), n_ref)), dtype=float) / 10.0
    ref_values = np.array([rng.uniform(-2.0, 2.0) for _ in ref_time])
    if rng.random() < 0.5:
        # Sampled where the reference is, so a faithful reproduction can pass;
        # a sparse random grid cuts the reference's corners and never does.
        act_time = ref_time.copy()
    else:
        act_time = np.array(
            sorted(
                rng.uniform(ref_time[0], ref_time[-1])
                for _ in range(rng.randint(3, 15))
            )
        )
    # Noise from well inside any band to well outside every band: the test
    # must reach BOTH verdicts, or "the verdict follows the band" is only
    # checked on the failing side.
    scale = rng.choice([0.0, 0.02, 0.1, 0.5, 3.0])
    act_values = np.interp(act_time, ref_time, ref_values) + np.array(
        [rng.uniform(-scale, scale) for _ in act_time]
    )
    mode = rng.choice(["band", "rel", "abs"])
    cfg: dict = {
        "tube_width_mode": mode,
        "tube_min_width": rng.choice([0.0, 0.3]),
        "tube_interpolation": rng.choice(["linear", "constant"]),
    }
    if mode == "abs" or rng.random() < 0.5:
        pts = []
        for _ in range(rng.randint(1, 4)):
            t = rng.uniform(ref_time[0], ref_time[-1])
            if mode == "abs":
                # Literal bounds, half the time bracketing the reference so
                # an absolute tube can pass at all.
                centre = (
                    float(np.interp(t, ref_time, ref_values))
                    if rng.random() < 0.5
                    else rng.uniform(-3.0, 0.0)
                )
                pts.append(
                    {
                        "time": t,
                        "upper": centre + rng.uniform(0.2, 2.0),
                        "lower": centre - rng.uniform(0.2, 2.0),
                    }
                )
            else:
                pts.append(
                    {
                        "time": t,
                        "upper": rng.uniform(0.0, 1.0),
                        "lower": rng.uniform(0.0, 1.0),
                    }
                )
        cfg["tube_points"] = pts
    else:
        cfg["tube_abs"] = rng.uniform(0.0, 1.0)
        cfg["tube_rel"] = rng.uniform(0.0, 0.5)
    return ref_time, ref_values, act_time, act_values, cfg


class TestTubeGeometry:
    def test_band_is_the_scored_envelope_and_the_verdict_follows_it(self):
        rng = random.Random(20260908)
        passing_cases = failing_cases = 0
        for _ in range(400):
            ref_t, ref_v, act_t, act_v, cfg = _random_tube_case(rng)
            vc = _compare_tube(ref_t, ref_v, act_t, act_v, cfg)
            band = next(p for p in vc.geometry if isinstance(p, Band))
            assert band.id == "tube/band" and band.kind == "result"
            # The band IS _tube_bounds on the band's own grid — nothing re-derived.
            up, lo = _tube_bounds(band.time, np.interp(band.time, ref_t, ref_v), cfg)
            np.testing.assert_allclose(band.upper, up)
            np.testing.assert_allclose(band.lower, lo)
            act_on_grid = np.interp(band.time, act_t, act_v)
            inside = (act_on_grid >= band.lower - 1e-12) & (
                act_on_grid <= band.upper + 1e-12
            )
            assert bool(inside.all()) == vc.passed
            if vc.passed:
                passing_cases += 1
            else:
                i = int(np.argmin(np.abs(band.time - vc.tube_worst_violation_time)))
                assert not inside[i], (
                    "the reported worst violation lies inside the band"
                )
                failing_cases += 1
        assert failing_cases > 50, "generator never reached the failing regime"
        assert passing_cases > 50, "generator never reached the passing regime"

    def test_a_control_point_off_the_reference_span_leaves_a_gap_in_the_ids(self):
        # Ids address tube_points/<i> in a patch, so a point the band cannot
        # anchor a handle on is skipped, not renumbered — and the verdict is
        # what it would be without that point.
        ref_t = np.array([0.0, 1.0, 2.0, 3.0])
        ref_v = np.array([1.0, 2.0, 1.0, 2.0])
        on_span = [
            {"time": 0.5, "upper": 0.2, "lower": 0.2},
            {"time": 2.5, "upper": 0.2, "lower": 0.2},
        ]
        with_far = [on_span[0], {"time": 7.0, "upper": 0.2, "lower": 0.2}, on_span[1]]
        cfg = {"tube_width_mode": "rel"}
        vc = _compare_tube(
            ref_t, ref_v, ref_t, ref_v + 0.05, {**cfg, "tube_points": with_far}
        )
        ids = sorted(p.id for p in vc.geometry if isinstance(p, Marker))
        assert ids == ["tube/lower/0", "tube/lower/2", "tube/upper/0", "tube/upper/2"]
        without = _compare_tube(
            ref_t, ref_v, ref_t, ref_v + 0.05, {**cfg, "tube_points": on_span}
        )
        assert vc.passed == without.passed
        assert vc.tube_worst_violation == pytest.approx(without.tube_worst_violation)

    def test_handles_sit_on_the_resolved_band_edges_in_spec_order(self):
        ref_t = np.array([0.0, 1.0, 2.0, 3.0])
        ref_v = np.array([1.0, 2.0, 1.0, 2.0])
        pts = [  # unsorted on purpose: ids follow SPEC order, not time order
            {"time": 2.5, "upper": 0.2, "lower": 0.1},
            {"time": 0.5, "upper": 0.4, "lower": 0.4},
        ]
        vc = _compare_tube(
            ref_t, ref_v, ref_t, ref_v, {"tube_width_mode": "rel", "tube_points": pts}
        )
        handles = {p.id: p for p in vc.geometry if isinstance(p, Marker)}
        assert set(handles) == {
            "tube/upper/0",
            "tube/lower/0",
            "tube/upper/1",
            "tube/lower/1",
        }
        assert all(h.kind == "handle" for h in handles.values())
        band = next(p for p in vc.geometry if isinstance(p, Band))
        assert handles["tube/upper/0"].x == 2.5
        assert handles["tube/upper/0"].y == pytest.approx(
            float(np.interp(2.5, band.time, band.upper))
        )
        assert handles["tube/lower/1"].x == 0.5
        assert handles["tube/lower/1"].y == pytest.approx(
            float(np.interp(0.5, band.time, band.lower))
        )

    def test_no_data_sentinel_has_no_geometry(self):
        vc = _compare_tube(
            np.array([]),
            np.array([]),
            np.array([0.0]),
            np.array([1.0]),
            {"tube_width_mode": "band"},
        )
        assert vc.geometry == () and not vc.passed


class TestPointsGeometry:
    def test_boxes_contain_passed_points_and_exclude_failed_ones(self):
        ref_t = np.linspace(0.0, 10.0, 101)
        ref_v = np.sin(ref_t)
        act_v = ref_v + 0.05 * (ref_t > 5.0)  # off by 0.05 after t=5
        pts = [
            {"time": 2.0, "tolerance": 0.01},
            {"time": 8.0, "tolerance": 0.01},
            {"time": 9.0, "tolerance": 0.1, "time_tolerance": 0.2},
        ]
        vc = _compare_points(ref_t, ref_v, ref_t, act_v, points=pts)
        boxes = {p.id: p for p in vc.geometry if isinstance(p, Box)}
        targets = {p.id: p for p in vc.geometry if isinstance(p, Marker)}
        assert set(boxes) == {"points/box/0", "points/box/1", "points/box/2"}
        assert set(targets) == {"points/target/0", "points/target/1", "points/target/2"}
        assert all(m.kind == "handle" for m in targets.values())
        assert targets["points/target/0"].passed is True
        assert (
            targets["points/target/1"].passed is False
            and boxes["points/box/1"].passed is False
        )
        assert targets["points/target/2"].passed is True
        # Geometry agrees with the verdict: the actual at a failed point is outside its box.
        b = boxes["points/box/1"]
        a = float(np.interp(8.0, ref_t, act_v))
        assert not (b.y0 <= a <= b.y1)
        assert (boxes["points/box/2"].t0, boxes["points/box/2"].t1) == (
            pytest.approx(8.8),
            pytest.approx(9.2),
        )
        assert vc.passed is False

    def test_an_unresolvable_point_has_no_geometry(self):
        ref_t = np.array([0.0, 1.0])
        vc = _compare_points(ref_t, ref_t, ref_t, ref_t, points=[{"time": 5.0}])
        assert vc.geometry == () and not vc.passed

    def test_final_only_emits_one_checkpoint_at_the_trace_end(self):
        ref_t = np.array([0.0, 1.0])
        ref_v = np.array([0.0, 1.0])
        vc = _compare_points(
            ref_t, ref_v, ref_t, np.array([0.0, 1.0005]), points=None, tolerance=1e-3
        )
        assert [p.id for p in vc.geometry] == [
            "points/target/final",
            "points/box/final",
        ]
        box = vc.geometry[1]
        assert isinstance(box, Box)
        assert (box.t0, box.t1) == (1.0, 1.0)
        assert box.y0 == pytest.approx(0.999) and box.y1 == pytest.approx(1.001)
        assert vc.passed and box.passed

    def test_a_nan_sample_beside_a_miss_is_not_a_crossing(self):
        # Actual is 3 units off everywhere, with one NaN sample at the
        # checkpoint. The window search compared (NaN > 0) != (4 > 0) and
        # called that a sign change — delta 0, a false PASS.
        t = np.linspace(0.0, 10.0, 11)
        act = t + 3.0
        act[5] = np.nan
        vc = _compare_points(
            t,
            t,
            t,
            act,
            points=[{"time": 5.0, "tolerance": 0.1, "time_tolerance": 1.5}],
        )
        assert vc.passed is False
        box = next(p for p in vc.geometry if p.id == "points/box/0")
        assert box.passed is False

    def test_a_window_of_only_nan_samples_fails(self):
        t = np.linspace(0.0, 10.0, 11)
        act = t.copy()
        act[4:7] = np.nan
        vc = _compare_points(
            t,
            t,
            t,
            act,
            points=[{"time": 5.0, "tolerance": 0.1, "time_tolerance": 0.5}],
        )
        assert vc.passed is False

    def test_a_nan_reference_at_a_checkpoint_cannot_pass(self):
        # A NaN target makes the relative limit NaN. The old guard
        # `delta > y_limit` was False for NaN and passed the point silently;
        # `delta <= y_limit` is what the verdict, the box and the target now
        # share, and it is False for NaN. Reachable: json.loads accepts NaN
        # and accepted baselines are written with allow_nan.
        ref_t = np.linspace(0.0, 10.0, 11)
        ref_v = ref_t.copy()
        ref_v[5] = np.nan
        vc = _compare_points(
            ref_t,
            ref_v,
            ref_t,
            ref_t,
            points=[{"time": 5.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
        )
        assert vc.passed is False
        target = next(p for p in vc.geometry if p.id == "points/target/0")
        box = next(p for p in vc.geometry if p.id == "points/box/0")
        assert target.passed is False and box.passed is False


class TestRangeGeometry:
    def test_each_declared_bound_is_a_handle_with_its_own_verdict(self):
        t = np.linspace(0.0, 1.0, 11)
        v = np.linspace(0.0, 2.0, 11)  # exceeds max=1.5, never below min=-1
        vc = _compare_range(t, v, min_value=-1.0, max_value=1.5)
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"range/min", "range/max"}
        assert lines["range/min"].value == -1.0 and lines["range/min"].passed is True
        assert lines["range/max"].value == 1.5 and lines["range/max"].passed is False
        assert all(line.kind == "handle" for line in lines.values())
        assert not vc.passed

    def test_an_absent_bound_emits_nothing(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_range(t, t, min_value=None, max_value=5.0)
        assert [p.id for p in vc.geometry] == ["range/max"]


class TestScalarGeometry:
    def test_bounds_form_draws_actual_and_both_bounds(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_scalar(
            t,
            t,
            t,
            t * 3.0,
            reduction="max",
            tolerance=0.1,
            tolerance_mode="abs",
            min_value=0.0,
            max_value=2.0,
        )
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"scalar/actual", "scalar/min", "scalar/max"}
        assert lines["scalar/actual"].value == pytest.approx(3.0)
        assert lines["scalar/actual"].kind == "act"
        assert lines["scalar/actual"].passed is False
        assert lines["scalar/max"].kind == "handle" and lines["scalar/max"].value == 2.0

    def test_baseline_form_draws_actual_and_reference(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_scalar(
            t,
            t,
            t,
            t * 1.01,
            reduction="final",
            tolerance=0.05,
            tolerance_mode="rel",
            min_value=None,
            max_value=None,
        )
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"scalar/actual", "scalar/reference"}
        assert lines["scalar/reference"].kind == "ref"
        assert lines["scalar/reference"].value == pytest.approx(1.0)
        assert lines["scalar/actual"].passed is True and vc.passed


class TestEventTimingGeometry:
    def test_auto_detected_events_become_lines_and_matched_windows(self):
        ref_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])  # one event at t=1.0
        act_t = np.array([0.0, 0.5, 1.0005, 1.0005, 1.5, 2.0])  # reproduced 0.5 ms late
        vc = _compare_event_timing(ref_t, act_t, time_tolerance=1e-3)
        ids = {p.id: p for p in vc.geometry}
        assert set(ids) == {"events/ref/0", "events/window/0", "events/act/0"}
        assert ids["events/ref/0"].kind == "ref" and ids["events/ref/0"].time == 1.0
        assert ids["events/act/0"].kind == "act" and ids[
            "events/act/0"
        ].time == pytest.approx(1.0005)
        w = ids["events/window/0"]
        assert isinstance(w, Box) and (w.y0, w.y1) == (None, None)
        assert (w.t0, w.t1) == (
            pytest.approx(0.999),
            pytest.approx(1.001),
        ) and w.passed is True
        assert vc.passed
        # Every VLine coincides with a detected boundary.
        assert [ids["events/act/0"].time] == [
            float(act_t[b[0]]) for b in _find_event_boundaries(act_t)
        ]

    def test_an_unpaired_auto_window_is_unscored_not_failed(self):
        # Two reference events, one actual. With count_must_match off the
        # leaf passes; the second window contributed nothing to that verdict
        # and must not be drawn as a failure.
        ref_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 1.5, 2.0])
        act_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        vc = _compare_event_timing(
            ref_t, act_t, time_tolerance=1e-3, count_must_match=False
        )
        assert vc.passed
        windows = {p.id: p for p in vc.geometry if isinstance(p, Box)}
        assert windows["events/window/0"].passed is True
        assert windows["events/window/1"].passed is None

    def test_declared_events_mark_the_unmatched_window_as_failed(self):
        ref_t = np.linspace(0.0, 2.0, 5)
        act_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        vc = _compare_event_timing(
            ref_t,
            act_t,
            time_tolerance=1e-3,
            declared_events=[{"time": 1.0}, {"time": 1.8, "tolerance": 0.05}],
        )
        windows = {p.id: p for p in vc.geometry if isinstance(p, Box)}
        assert windows["events/window/0"].passed is True
        assert windows["events/window/1"].passed is False
        assert (windows["events/window/1"].t0, windows["events/window/1"].t1) == (
            pytest.approx(1.75),
            pytest.approx(1.85),
        )
        assert [
            p.id for p in vc.geometry if isinstance(p, VLine) and p.kind == "ref"
        ] == [
            "events/ref/0",
            "events/ref/1",
        ]
        assert not vc.passed


class TestDominantFrequencyGeometry:
    def _signal(self, hz: float):
        t = np.linspace(0.0, 10.0, 1001)
        return t, np.sin(2 * np.pi * hz * t)

    def test_spectra_and_peaks_land_on_the_spectrum_panel(self):
        t, ref = self._signal(2.0)
        _, act = self._signal(2.0)
        vc = _compare_dominant_frequency(
            t,
            ref,
            t,
            act,
            peaks=[{"freq": 2.0, "tolerance": 0.05}, {"freq": 5.0, "tolerance": 0.02}],
        )
        prims = {p.id: p for p in vc.geometry}
        assert all(p.panel == "spectrum" for p in vc.geometry)
        assert (
            isinstance(prims["spectrum/ref"], Series)
            and prims["spectrum/ref"].kind == "ref"
        )
        assert (
            isinstance(prims["spectrum/act"], Series)
            and prims["spectrum/act"].kind == "act"
        )
        assert (
            prims["peaks/declared/0"].kind == "handle"
            and prims["peaks/declared/0"].x == 2.0
        )
        assert prims["peaks/declared/0"].passed is True
        assert prims["peaks/matched/0"].kind == "matched"
        assert prims["peaks/matched/0"].x == pytest.approx(2.0, abs=0.05)
        assert (
            prims["peaks/declared/1"].passed is False and "peaks/matched/1" not in prims
        )
        w = prims["peaks/window/1"]
        assert isinstance(w, Box) and (w.t0, w.t1) == (
            pytest.approx(4.9),
            pytest.approx(5.1),
        )
        assert any(
            p.id.startswith("peaks/detected/") and p.kind == "detected"
            for p in vc.geometry
        )
        # Every matched marker is one of the paired peaks.
        paired = {
            p["matched_hz"]
            for p in vc.diagnostics["paired_peaks"]
            if p["matched_hz"] is not None
        }
        assert {p.x for p in vc.geometry if p.kind == "matched"} == paired
        assert not vc.passed

    def test_markers_sit_on_the_drawn_spectrum_above_the_embed_cap(self):
        # 8001 samples: the diagnostics embed strides the FFT bins and skips
        # the peak; the drawn Series must not, or the matched marker floats
        # above a curve with no peak in it.
        t = np.linspace(0.0, 10.0, 8001)
        sig = np.sin(2 * np.pi * 1.3 * t)
        vc = _compare_dominant_frequency(
            t, sig, t, sig, peaks=[{"freq": 1.3, "tolerance": 0.05}]
        )
        prims = {p.id: p for p in vc.geometry}
        act = prims["spectrum/act"]
        matched = prims["peaks/matched/0"]
        assert isinstance(act, Series) and isinstance(matched, Marker)
        assert len(act.x) > len(vc.diagnostics["act_spectrum_freq"])
        drawn_at_marker = float(np.interp(matched.x, act.x, act.y))
        assert matched.y == pytest.approx(drawn_at_marker)
        assert matched.y == pytest.approx(float(act.y.max()))

    def test_no_declared_peaks_still_draws_the_spectra(self):
        t, ref = self._signal(2.0)
        vc = _compare_dominant_frequency(t, ref, t, ref, peaks=None)
        ids = [p.id for p in vc.geometry]
        assert "spectrum/ref" in ids and "spectrum/act" in ids
        assert not any(i.startswith("peaks/declared/") for i in ids)
