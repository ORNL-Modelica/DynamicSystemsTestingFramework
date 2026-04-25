"""Tests for the Phase 6.1.1 auto-derived mode UI controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import pytest

from dstf.comparison.modes import (
    DominantFrequencyConfig,
    EventTimingConfig,
    PointsConfig,
    NrmseConfig,
    RangeConfig,
    TubeConfig,
)
from dstf.reporting.ui.mode_controls import (
    PlotContribution,
    derive_schema,
    emit_mode_schemas,
    get_mode_ui,
    register_mode_ui,
    registered_modes,
    render_schema_html,
    render_window_controls_html,
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
            "nrmse", "tube", "points", "range",
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


class TestWindowControls:
    """Idea #46 UI surfacing — universal window fragment emitted for
    tree-backed leaves. Window lives on LeafSpec (not any ModeConfig),
    so the renderer is standalone rather than threaded through a Schema."""

    def test_empty_values_renders_blank_inputs(self):
        html = render_window_controls_html(variable="h")
        assert 'class="window-controls"' in html
        assert 'data-variable="h"' in html
        assert 'data-field="window_start"' in html
        assert 'data-field="window_end"' in html
        # No value= when unset
        assert 'value=' not in html

    def test_values_fill_inputs(self):
        html = render_window_controls_html(variable="h", values={"start": 2.0, "end": 5.0})
        assert 'value="2.0"' in html
        assert 'value="5.0"' in html

    def test_open_start_only_fills_end(self):
        html = render_window_controls_html(variable="h", values={"end": 5.0})
        assert html.count('value=') == 1
        assert 'value="5.0"' in html

    def test_escapes_variable_name(self):
        html = render_window_controls_html(variable='<script>')
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_step_any_on_number_inputs(self):
        html = render_window_controls_html(variable="h")
        assert 'type="number"' in html
        assert 'step="any"' in html

    def test_time_bounds_surface_as_placeholders(self):
        """Simulation bounds fed via ``time_start`` / ``time_end`` render as
        the inputs' ``placeholder`` — guidance without auto-committing."""
        html = render_window_controls_html(
            variable="h", time_start=0.0, time_end=10.0,
        )
        assert 'placeholder="0"' in html
        assert 'placeholder="10"' in html
        # Values stay blank — placeholder is a hint, not a commit.
        assert 'value=' not in html


class TestPlotContributionSlot:
    """Stage 1 — every ModeUI carries an optional plot_contribution callable.
    Default None means 'no static visual' (NRMSE today). Stage 2 fills in
    the per-mode functions alongside the JS-side registry."""

    def test_default_is_none(self):
        ui = get_mode_ui("nrmse")
        assert ui is not None
        assert ui.plot_contribution is None
        assert ui.contribute_to_plot({"tolerance": 1e-3}) is None

    def test_contribute_to_plot_invokes_registered_fn(self):
        from dataclasses import dataclass as _dc

        @_dc
        class Cfg:
            min_value: float = 0.0

        def contrib(values):
            return PlotContribution(
                shapes=[{"type": "line", "y0": values["min_value"], "y1": values["min_value"]}],
            )

        ui = register_mode_ui("contrib-test", Cfg, plot_contribution=contrib)
        result = ui.contribute_to_plot({"min_value": -0.5})
        assert isinstance(result, PlotContribution)
        assert result.shapes == [{"type": "line", "y0": -0.5, "y1": -0.5}]
        assert result.traces == []

    def test_secondary_panel_request(self):
        from dataclasses import dataclass as _dc

        @_dc
        class Cfg:
            pass

        def contrib(_values):
            return PlotContribution(secondary_panel="spectrum")

        ui = register_mode_ui("panel-test", Cfg, plot_contribution=contrib)
        assert ui.contribute_to_plot({}).secondary_panel == "spectrum"


class TestHasPlotEditor:
    """Python-side marker that MODE_PLOT_EDITORS[name] is wired JS-side.
    Used by the recommender / schema export for discoverability."""

    def test_tube_has_plot_editor(self):
        ui = get_mode_ui("tube")
        assert ui is not None
        assert ui.has_plot_editor is True

    def test_range_has_plot_editor(self):
        ui = get_mode_ui("range")
        assert ui is not None
        assert ui.has_plot_editor is True

    def test_nrmse_has_no_plot_editor(self):
        ui = get_mode_ui("nrmse")
        assert ui is not None
        assert ui.has_plot_editor is False

    def test_flag_exposed_on_register(self):
        from dataclasses import dataclass as _dc

        @_dc
        class Cfg:
            pass

        ui = register_mode_ui("editor-test", Cfg, has_plot_editor=True)
        assert ui.has_plot_editor is True


class TestEmitModeSchemas:
    """Bulk export for embedding into interactive.html (Stage 2 JS renderer)."""

    def test_includes_every_bundled_mode(self):
        schemas = emit_mode_schemas()
        for mode in ("nrmse", "tube", "points", "range",
                     "event-timing", "dominant-frequency"):
            assert mode in schemas, f"{mode} missing from emit_mode_schemas"

    def test_schema_entries_are_json_safe(self):
        import json
        schemas = emit_mode_schemas()
        # Round-trip through JSON — catches any non-serializable field
        round_trip = json.loads(json.dumps(schemas))
        assert round_trip["nrmse"]["fields"][0]["name"] == "tolerance"

    def test_tube_enum_choices_survive_serialization(self):
        schemas = emit_mode_schemas()
        tube_fields = {f["name"]: f for f in schemas["tube"]["fields"]}
        assert tube_fields["tube_width_mode"]["choices"] == ["band", "rel", "absolute"]


def test_event_timing_render_html_includes_passthrough_events():
    """render_schema_html should emit a textarea for the events field
    (standard passthrough fallback). The JS-side MODE_PLOT_EDITORS
    table editor overlays this when the leaf is activated; the
    textarea is the fallback when JS fails to load."""
    from dstf.comparison.modes import EventTimingConfig
    from dstf.reporting.ui.mode_controls import (
        derive_schema, render_schema_html,
    )
    schema = derive_schema(EventTimingConfig, mode="event-timing")
    html = render_schema_html(schema, values={
        "time_tolerance": 1e-3,
        "count_must_match": True,
        "events": None,
    })
    assert 'data-field="time_tolerance"' in html
    assert 'data-field="count_must_match"' in html
    # Passthrough field emits a textarea; events should be there.
    assert 'data-field="events"' in html
    assert 'data-passthrough="true"' in html
