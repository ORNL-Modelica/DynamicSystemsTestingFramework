"""Tests for the schema auto-derived from each metric's Config dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pytest

from dstf.comparison.mode_schema import derive_schema
from dstf.comparison.modes import (
    DominantFrequencyConfig,
    EventTimingConfig,
    NrmseConfig,
    PointsConfig,
    RangeConfig,
    TubeConfig,
)


class TestDeriveSchemaPerMode:
    """Every bundled ModeConfig yields a usable schema."""

    def test_nrmse(self):
        s = derive_schema(NrmseConfig, mode="nrmse")
        assert s.mode == "nrmse"
        names = [f.name for f in s.fields]
        assert names == ["tolerance"]
        f = s.fields[0]
        assert f.type == "float"
        assert f.default == pytest.approx(1e-4)
        assert f.optional is False

    def test_tube_literal_choices_extracted(self):
        s = derive_schema(TubeConfig, mode="tube")
        by_name = {f.name: f for f in s.fields}

        wm = by_name["tube_width_mode"]
        assert wm.type == "enum"
        assert wm.choices == ["band", "rel", "abs"]
        assert wm.optional is True  # Optional[Literal[...]]

        interp = by_name["tube_interpolation"]
        assert interp.type == "enum"
        assert interp.choices == ["linear", "constant"]
        assert interp.optional is False
        assert interp.default == "linear"

    def test_tube_complex_field_is_passthrough(self):
        """``tube_points: Optional[list[dict]]`` can't be auto-rendered —
        it degrades to a passthrough textarea instead of crashing."""
        s = derive_schema(TubeConfig, mode="tube")
        pts = next(f for f in s.fields if f.name == "tube_points")
        assert pts.type == "passthrough"
        assert pts.optional is True

    def test_default_points(self):
        s = derive_schema(PointsConfig, mode="points")
        names = [f.name for f in s.fields]
        assert set(names) == {"points", "tolerance"}
        tol = next(f for f in s.fields if f.name == "tolerance")
        assert tol.default == pytest.approx(1e-4)

    def test_range_optional_floats(self):
        s = derive_schema(RangeConfig, mode="range")
        names = [f.name for f in s.fields]
        assert set(names) == {"min_value", "max_value"}
        for f in s.fields:
            assert f.type == "float"
            assert f.optional is True
            assert f.default is None

    def test_event_timing(self):
        s = derive_schema(EventTimingConfig, mode="event-timing")
        by_name = {f.name: f for f in s.fields}
        assert by_name["time_tolerance"].type == "float"
        assert by_name["time_tolerance"].default == pytest.approx(1e-3)
        assert by_name["count_must_match"].type == "bool"
        assert by_name["count_must_match"].default is True

    def test_dominant_frequency(self):
        """Schema reflects the declared-peaks config (D75) — a single
        ``peaks`` field of passthrough type (list of dicts isn't a
        scalar)."""
        s = derive_schema(DominantFrequencyConfig, mode="dominant-frequency")
        by_name = {f.name: f for f in s.fields}
        assert "peaks" in by_name
        # list[dict] shape → passthrough fallback.
        assert by_name["peaks"].type == "passthrough"
        assert by_name["peaks"].optional is True


class TestDeriveSchemaEdgeCases:
    def test_non_dataclass_raises(self):
        class Plain:
            pass

        with pytest.raises(TypeError):
            derive_schema(Plain)

    def test_optional_literal_preserves_choices(self):
        @dataclass
        class Cfg:
            color: Literal["r", "g", "b"] | None = None

        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].choices == ["r", "g", "b"]
        assert s.fields[0].optional is True

    def test_metadata_label_and_help(self):
        @dataclass
        class Cfg:
            tol: float = field(
                default=1e-3, metadata={"label": "Tolerance", "help": "NRMSE threshold"}
            )

        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].label == "Tolerance"
        assert s.fields[0].help == "NRMSE threshold"

    def test_default_label_is_titleized_name(self):
        @dataclass
        class Cfg:
            tube_min_width: float = 0.0

        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].label == "Tube min width"
