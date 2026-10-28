"""Tests for the schema auto-derived from each metric's Config dataclass."""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass, field

import pytest

from dstf.comparison.mode_schema import FIELD_TYPES, derive_schema
from dstf.comparison.modes import (
    DominantFrequencyConfig,
    EventTimingConfig,
    NrmseConfig,
    PointsConfig,
    RangeConfig,
    ScalarConfig,
    TubeConfig,
)
from dstf.comparison.registry import metrics as registered_metrics
from dstf.comparison.sides import Sides


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
        assert by_name["time_tolerance"].type == "sides"
        assert by_name["time_tolerance"].default == pytest.approx(1e-3)
        assert by_name["count_must_match"].type == "bool"
        assert by_name["count_must_match"].default is True


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
    tuple is the choices with the mode's default first; ``"sides?"`` is
    an optional tolerance — a number or a lower/upper pair — and an entry
    may carry a third element, what its two sides are called (a time
    tolerance's are not ``−`` and ``+``). ``peaks.freq``
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
                ("tolerance", "sides?"),
                ("tolerance_mode", ("abs", "rel")),
                ("time_tolerance", "sides?", ("before", "after")),
            ),
            (EventTimingConfig, "events"): (
                ("time", "float"),
                ("tolerance", "sides?", ("early", "late")),
            ),
            (DominantFrequencyConfig, "peaks"): (
                ("freq", "float"),
                ("tolerance", "sides?"),
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


#: Every Config field that takes a lower/upper pair, its default as the
#: schema says it and what its two sides are called.
SIDES_FIELDS = [
    (PointsConfig, "tolerance", 1e-4, ("−", "+")),
    (ScalarConfig, "tolerance", 1e-4, ("−", "+")),
    (EventTimingConfig, "time_tolerance", 1e-3, ("early", "late")),
    (TubeConfig, "tube_abs", 0.0, ("−", "+")),
    (TubeConfig, "tube_rel", 0.0, ("−", "+")),
]


def _field(cls, name):
    return next(f for f in derive_schema(cls).fields if f.name == name)


@pytest.mark.parametrize(("cls", "name", "default", "labels"), SIDES_FIELDS)
def test_a_sides_field_is_its_own_kind(cls, name, default, labels):
    """A config field typed `Sides` (a tolerance: a number or a lower/upper
    pair) derives as `sides`; its default is what a spec would hold for it
    (`as_written`: the number, the two sides being equal), and it names its
    two sides — `−` and `+` unless the field's metadata says otherwise."""
    assert "sides" in FIELD_TYPES
    f = _field(cls, name)
    assert (f.type, f.optional, f.choices) == ("sides", False, None)
    assert f.default == default and isinstance(f.default, float)
    assert f.side_labels == labels


def test_a_sides_default_with_two_sides_is_the_pair():
    @dataclass(frozen=True)
    class Cfg:
        tolerance: Sides = Sides(0.1, 0.3)

    (f,) = derive_schema(Cfg).fields
    assert f.default == {"lower": 0.1, "upper": 0.3}


def test_nrmse_has_one_ceiling_and_no_sides():
    """nrmse's tolerance is one number: no pair, no side labels — as every
    field that is not a `sides` one."""
    f = _field(NrmseConfig, "tolerance")
    assert (f.type, f.default, f.side_labels) == ("float", 1e-4, None)
    for cls in (TubeConfig, PointsConfig, ScalarConfig, EventTimingConfig):
        for other in derive_schema(cls).fields:
            assert (other.side_labels is None) == (other.type != "sides"), other.name
