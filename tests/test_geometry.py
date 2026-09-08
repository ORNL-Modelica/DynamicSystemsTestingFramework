"""Geometry primitives: what a mode scored against, as drawable data."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.geometry import Band, Box, Marker, Window
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
        with pytest.raises(TypeError):
            Marker("points/target/0", 1.0, 2.0)  # type: ignore[misc]


class TestResultTypes:
    def test_variable_comparison_geometry_defaults_empty(self):
        assert _vc().geometry == ()

    def test_score_summary_is_frozen(self):
        s = ScoreSummary(display="NRMSE 1e-3", criterion="NRMSE 1e-3 vs 1e-4 → FAIL")
        with pytest.raises(AttributeError):
            s.display = "x"  # type: ignore[misc]
        assert s.score is None and s.score_label == ""
