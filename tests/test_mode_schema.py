"""Tests for the schema auto-derived from each metric's Config dataclass."""

from __future__ import annotations

import dataclasses
import types
import typing
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
from dstf.comparison.registry import metrics as registered_metrics


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


def _is_list_of_dicts(hint) -> bool:
    """``list[dict]`` (or ``list[dict[...]]``), bare or ``| None``."""
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) != 1:
            return False
        hint = args[0]
    if typing.get_origin(hint) is not list:
        return False
    (item,) = typing.get_args(hint) or (None,)
    return item is dict or typing.get_origin(item) is dict


class TestListFieldColumns:
    """The Config's list-of-dict fields declare their row columns.

    ``"float"`` is a key the reader in ``algorithms.py`` indexes
    (required), ``"float?"`` one it reads with ``.get`` (optional), a
    tuple is the choices with the mode's default first. ``peaks.freq``
    stays required although its reader tolerates a missing one: a peak
    without a frequency is meaningless. ``tube_points.time`` and
    ``events.time`` are required because their readers index them.
    ``points.time`` is optional: a blank one is the trace-end sentinel.
    """

    def test_the_four_fields_columns(self):
        expected = {
            (TubeConfig, "tube_points"): (
                ("time", "float"),
                ("upper", "float?"),
                ("lower", "float?"),
            ),
            (PointsConfig, "points"): (
                ("time", "float?"),
                ("value", "float?"),
                ("tolerance", "float?"),
                ("tolerance_mode", ("abs", "rel")),
                ("time_tolerance", "float?"),
            ),
            (EventTimingConfig, "events"): (
                ("time", "float"),
                ("tolerance", "float?"),
            ),
            (DominantFrequencyConfig, "peaks"): (
                ("freq", "float"),
                ("tolerance", "float?"),
                ("tolerance_mode", ("rel", "abs")),
            ),
        }
        for (cls, name), columns in expected.items():
            f = next(f for f in dataclasses.fields(cls) if f.name == name)
            assert f.metadata.get("columns") == columns, (cls.__name__, name)

    def test_every_builtin_list_of_dicts_field_has_columns(self):
        builtin = [
            d.config_cls
            for d in registered_metrics()
            if d.config_cls.__module__.startswith("dstf.")
        ]
        list_fields = []
        for cls in builtin:
            hints = typing.get_type_hints(cls)
            for f in dataclasses.fields(cls):
                if _is_list_of_dicts(hints[f.name]):
                    list_fields.append((cls.__name__, f.name))
                    assert "columns" in f.metadata, (cls.__name__, f.name)
        # The scan found the fields it guards (a broken type check would pass vacuously).
        assert set(list_fields) >= {
            ("TubeConfig", "tube_points"),
            ("PointsConfig", "points"),
            ("EventTimingConfig", "events"),
            ("DominantFrequencyConfig", "peaks"),
        }
        # scalar's ``quantity`` is passthrough but not a list: no table.
        assert ("ScalarConfig", "quantity") not in list_fields


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
