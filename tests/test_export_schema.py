"""Tests for reporting/schema_export.py — JSON-Schema emission (6.4.5)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


class TestSchemaBuilder:
    def test_schema_is_valid_json_and_has_expected_shape(self):
        from dstf.reporting.schema_export import build_schema

        schema = build_schema()
        # Round-trips through JSON cleanly
        roundtrip = json.loads(json.dumps(schema))
        assert roundtrip == schema

        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["type"] == "object"
        assert "tests" in schema["properties"]

    def test_mode_definitions_present(self):
        from dstf.reporting.schema_export import build_schema

        defs = build_schema()["$defs"]
        for mode in [
            "mode_nrmse",
            "mode_tube",
            "mode_points",
            "mode_range",
            "mode_event_timing",
            "mode_dominant_frequency",
        ]:
            assert mode in defs, f"Missing mode definition: {mode}"

    def test_tube_mode_has_literal_width_mode(self):
        from dstf.reporting.schema_export import build_schema

        tube = build_schema()["$defs"]["mode_tube"]
        width = tube["properties"]["tube_width_mode"]
        # Optional[Literal[...]] emits oneOf with enum + null
        assert "oneOf" in width
        enum_branch = next(b for b in width["oneOf"] if "enum" in b)
        assert set(enum_branch["enum"]) == {"band", "rel", "abs"}

    def test_nrmse_mode_tolerance_field(self):
        from dstf.reporting.schema_export import build_schema

        nrmse = build_schema()["$defs"]["mode_nrmse"]
        tol = nrmse["properties"]["tolerance"]
        assert tol["type"] == "number"
        assert tol.get("default") == pytest.approx(1e-4)

    def test_leaf_and_combinator_defs_exist(self):
        from dstf.reporting.schema_export import build_schema

        defs = build_schema()["$defs"]
        assert "leaf" in defs
        assert "combinator" in defs
        assert "tree_node" in defs
        # tree_node is a oneOf of leaf | combinator
        assert defs["tree_node"] == {
            "oneOf": [
                {"$ref": "#/$defs/leaf"},
                {"$ref": "#/$defs/combinator"},
            ]
        }

    def test_leaf_allows_window(self):
        from dstf.reporting.schema_export import build_schema

        leaf = build_schema()["$defs"]["leaf"]
        assert "window" in leaf["properties"]
        win = leaf["properties"]["window"]
        assert win["properties"]["start"]["type"] == "number"
        assert win["properties"]["end"]["type"] == "number"


class TestCli:
    def test_cli_emits_to_stdout(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "dstf", "export-schema"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        schema = json.loads(result.stdout)
        assert "$defs" in schema

    def test_cli_writes_to_output_file(self, tmp_path):
        out = tmp_path / "schema.json"
        result = subprocess.run(
            [sys.executable, "-m", "dstf", "export-schema", "--output", str(out)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert out.exists()
        schema = json.loads(out.read_text())
        assert schema["type"] == "object"


def test_event_timing_schema_includes_events_as_passthrough():
    """The declared-events field must export as a passthrough type so
    the interactive HTML gets a raw-JSON fallback renderer *and* can be
    overridden by the JS-side MODE_PLOT_EDITORS table UI."""
    from dstf.reporting.ui.mode_controls import emit_mode_schemas

    schemas = emit_mode_schemas()
    event_timing = schemas.get("event-timing")
    assert event_timing is not None, "event-timing schema missing"
    fields = {f["name"]: f for f in event_timing.get("fields", [])}
    assert "events" in fields, (
        "event-timing should export an 'events' field (declared events "
        "for the reporter's table editor)."
    )
    assert fields["events"]["type"] == "passthrough", (
        f"events should be passthrough (list[dict]), got {fields['events']['type']}"
    )
