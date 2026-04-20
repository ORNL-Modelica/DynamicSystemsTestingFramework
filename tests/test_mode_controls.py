"""Tests for the Phase 6.1.1 auto-derived mode UI controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import pytest

from modelica_testing.comparison.modes import (
    DominantFrequencyConfig,
    EventTimingConfig,
    FinalOnlyConfig,
    NrmseConfig,
    RangeConfig,
    TubeConfig,
)
from modelica_testing.reporting.ui.mode_controls import (
    derive_schema,
    get_mode_ui,
    register_mode_ui,
    registered_modes,
    render_schema_html,
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
        assert wm.choices == ["band", "rel", "absolute"]
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

    def test_final_only(self):
        s = derive_schema(FinalOnlyConfig, mode="final_only")
        assert [f.name for f in s.fields] == ["tolerance"]
        assert s.fields[0].default == pytest.approx(1e-4)

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
        s = derive_schema(DominantFrequencyConfig, mode="dominant-frequency")
        by_name = {f.name: f for f in s.fields}
        assert by_name["rel_tolerance"].type == "float"
        assert by_name["min_frequency"].type == "float"
        assert by_name["min_frequency"].default == pytest.approx(0.0)


class TestDeriveSchemaEdgeCases:
    def test_non_dataclass_raises(self):
        class Plain:
            pass
        with pytest.raises(TypeError):
            derive_schema(Plain)

    def test_optional_literal_preserves_choices(self):
        @dataclass
        class Cfg:
            color: Optional[Literal["r", "g", "b"]] = None
        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].choices == ["r", "g", "b"]
        assert s.fields[0].optional is True

    def test_metadata_label_and_help(self):
        @dataclass
        class Cfg:
            tol: float = field(default=1e-3,
                               metadata={"label": "Tolerance", "help": "NRMSE threshold"})
        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].label == "Tolerance"
        assert s.fields[0].help == "NRMSE threshold"

    def test_default_label_is_titleized_name(self):
        @dataclass
        class Cfg:
            tube_min_width: float = 0.0
        s = derive_schema(Cfg, mode="x")
        assert s.fields[0].label == "Tube min width"


class TestRenderSchemaHtml:
    def test_root_element_has_mode_and_variable(self):
        s = derive_schema(NrmseConfig, mode="nrmse")
        html = render_schema_html(s, mode="nrmse", variable="h")
        assert 'class="mode-controls"' in html
        assert 'data-mode="nrmse"' in html
        assert 'data-variable="h"' in html

    def test_float_field_renders_number_input(self):
        s = derive_schema(NrmseConfig, mode="nrmse")
        html = render_schema_html(s)
        assert 'type="number"' in html
        assert 'step="any"' in html
        assert 'data-field="tolerance"' in html

    def test_bool_field_renders_checkbox(self):
        s = derive_schema(EventTimingConfig, mode="event-timing")
        html = render_schema_html(s)
        assert 'type="checkbox"' in html
        assert 'data-field="count_must_match"' in html
        assert 'checked' in html  # default is True

    def test_enum_field_renders_select(self):
        s = derive_schema(TubeConfig, mode="tube")
        html = render_schema_html(s)
        assert '<select' in html
        assert '<option value="linear" selected>' in html
        assert '<option value="constant">' in html

    def test_optional_enum_has_unset_option(self):
        s = derive_schema(TubeConfig, mode="tube")
        html = render_schema_html(s)
        assert '<option value="" selected>(unset)</option>' in html

    def test_values_override_defaults(self):
        s = derive_schema(NrmseConfig, mode="nrmse")
        html = render_schema_html(s, values={"tolerance": 0.5})
        assert 'value="0.5"' in html

    def test_passthrough_emits_textarea(self):
        s = derive_schema(TubeConfig, mode="tube")
        html = render_schema_html(s)
        assert '<textarea' in html
        assert 'data-passthrough="true"' in html

    def test_html_escaped_variable_name(self):
        """Variable names containing HTML specials are escaped."""
        s = derive_schema(NrmseConfig, mode="nrmse")
        html = render_schema_html(s, variable='h<script>')
        assert '<script>' not in html
        assert '&lt;script&gt;' in html


class TestRegistry:
    def test_bundled_modes_registered(self):
        assert set(registered_modes()) >= {
            "nrmse", "tube", "final_only", "range",
            "event-timing", "dominant-frequency",
        }

    def test_get_returns_mode_ui(self):
        ui = get_mode_ui("nrmse")
        assert ui is not None
        assert ui.name == "nrmse"
        assert ui.config_cls is NrmseConfig

    def test_unknown_mode_returns_none(self):
        assert get_mode_ui("does-not-exist") is None

    def test_register_and_render_custom_mode(self):
        @dataclass
        class CustomCfg:
            rate: float = 1.0
            enabled: bool = True

        ui = register_mode_ui("custom-test", CustomCfg)
        html = ui.render(variable="y")
        assert 'data-mode="custom-test"' in html
        assert 'data-field="rate"' in html
        assert 'data-field="enabled"' in html

    def test_custom_renderer_overrides_default(self):
        @dataclass
        class C:
            tol: float = 0.1

        def custom(schema, *, mode, variable, values):
            return f"<custom-panel mode='{mode}'/>"

        ui = register_mode_ui("custom-over", C, custom_renderer=custom)
        html = ui.render(variable="z")
        assert html == "<custom-panel mode='custom-over'/>"
