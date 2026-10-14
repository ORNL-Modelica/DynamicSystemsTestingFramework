"""Tests for reporting/schema_export.py — JSON-Schema emission (6.4.5)."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

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

    def test_simulation_block_documents_every_parsed_field(self):
        """spec_parser reads number_of_intervals / output_interval — the
        exported schema must document them too."""
        from dstf.reporting.schema_export import build_schema

        sim = build_schema()["$defs"]["test_entry"]["properties"]["simulation"]
        props = sim["properties"]
        assert props["number_of_intervals"]["type"] == "integer"
        assert props["output_interval"]["type"] == "number"

    def test_source_is_the_only_source_file_key(self):
        """``source`` names the simulation source; ``fmu`` is not a spec key."""
        from dstf.reporting.schema_export import build_schema

        props = build_schema()["$defs"]["test_entry"]["properties"]
        assert props["source"]["type"] == "string"
        assert "fmu" not in props

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
            [sys.executable, "-m", "dstf", "spec", "schema"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        schema = json.loads(result.stdout)
        assert "$defs" in schema

    def test_cli_writes_to_output_file(self, tmp_path):
        out = tmp_path / "schema.json"
        result = subprocess.run(
            [sys.executable, "-m", "dstf", "spec", "schema", "--output", str(out)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert out.exists()
        schema = json.loads(out.read_text())
        assert schema["type"] == "object"

    def test_output_file_written_with_lf(self, tmp_path, monkeypatch):
        """The exported schema gets committed next to the spec it describes,
        so its line ending must not depend on the OS that wrote it.

        A byte assertion cannot fail on Linux — CPython
        compiles the CRLF write translation in rather than reading
        os.linesep — so pin the argument at the call site instead.
        """
        from dstf.cli.spec_cmds import cmd_spec_schema

        seen: dict[str, object] = {}
        real = Path.write_text

        def spy(self, data, *args, **kwargs):
            seen[self.name] = kwargs.get("newline")
            return real(self, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", spy)
        out = tmp_path / "schema.json"
        assert (
            cmd_spec_schema(argparse.Namespace(schema="test_spec", output=str(out)))
            == 0
        )

        assert seen["schema.json"] == "\n"


def test_event_timing_schema_includes_events_as_passthrough():
    """The declared-events field is a ``list[dict]``, which introspection
    cannot reduce to a scalar or an enum — it must export as a passthrough
    so a consumer renders it as raw JSON rather than dropping it."""
    from dstf.comparison.mode_schema import derive_schema
    from dstf.comparison.modes import EventTimingConfig

    schema = derive_schema(EventTimingConfig, mode="event-timing").to_dict()
    fields = {f["name"]: f for f in schema.get("fields", [])}
    assert "events" in fields, (
        "event-timing should export an 'events' field (the declared events)."
    )
    assert fields["events"]["type"] == "passthrough", (
        f"events should be passthrough (list[dict]), got {fields['events']['type']}"
    )


# ---------------------------------------------------------------------------
# Schema ↔ parser agreement on "how a leaf names its data"
# ---------------------------------------------------------------------------


def _identity_rule() -> dict:
    """The anyOf clause the leaf $def carries for variable-or-quantity."""
    from dstf.reporting.schema_export import build_schema

    leaf = build_schema()["$defs"]["leaf"]
    clauses = [c for c in leaf.get("allOf", []) if "anyOf" in c]
    assert len(clauses) == 1, "leaf must carry exactly one identity anyOf clause"
    return clauses[0]


def _satisfies(payload: dict, rule: dict) -> bool:
    """Evaluate the emitted identity rule against a leaf payload.

    A miniature validator covering exactly the constructs the rule uses
    (``required`` plus ``properties`` with ``enum`` / ``type`` /
    ``minLength``); ``jsonschema`` isn't a dependency of this project.
    """
    for alternative in rule["anyOf"]:
        if any(key not in payload for key in alternative.get("required", [])):
            continue
        ok = True
        for name, constraint in (alternative.get("properties") or {}).items():
            value = payload.get(name)
            if "enum" in constraint and value not in constraint["enum"]:
                ok = False
            if constraint.get("type") == "string" and not isinstance(value, str):
                ok = False
            if isinstance(value, str) and len(value) < constraint.get("minLength", 0):
                ok = False
        if ok:
            return True
    return False


@pytest.mark.parametrize(
    "payload,valid",
    [
        ({"metric": "nrmse", "variable": "x"}, True),
        ({"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}}, True),
        # scalar declares accepts_quantity → a string quantity names the data
        ({"metric": "scalar", "quantity": "translation.scalar_unknowns"}, True),
        # nrmse does not → a quantity can't stand in for the variable
        ({"metric": "nrmse", "quantity": "translation.scalar_unknowns"}, False),
        ({"metric": "nrmse"}, False),
        ({"metric": "scalar"}, False),
        # the parser's truthiness check rejects an empty path; so must the schema
        ({"metric": "scalar", "quantity": ""}, False),
    ],
)
def test_leaf_identity_rule_matches_the_parser(payload, valid):
    """The exported grammar and the parser must accept the same leaves —
    both read ``MetricDescriptor.accepts_quantity``."""
    from dstf.comparison.tree_spec import MetricSpecError, parse_metric_tree
    from dstf.reporting.schema_export import build_schema

    assert build_schema()["$defs"]["leaf"]["required"] == ["metric"]
    assert _satisfies(payload, _identity_rule()) is valid

    try:
        parse_metric_tree(dict(payload))
        parsed_ok = True
    except MetricSpecError:
        parsed_ok = False
    assert parsed_ok is valid, (
        f"schema says valid={valid} but the parser disagreed for {payload}"
    )


# =========================================================================
# Folded in from tests/test_review_fixes_cli.py — regression tests for the
# 2026-07-06 review, final CLI/config/manifest batch (CODE_REVIEW_2026-07-06.md).
#
# Finding 72 — the exported JSON-Schema wires the per-mode ``$defs`` into
# the leaf spec via if/then. Its posture was lenient (unknown keys valid)
# until D154 closed every def the parser is strict about.
# =========================================================================


_MODE_DEF_NAMES = {
    "nrmse": "mode_nrmse",
    "tube": "mode_tube",
    "points": "mode_points",
    "range": "mode_range",
    "event-timing": "mode_event_timing",
    "dominant-frequency": "mode_dominant_frequency",
    "scalar": "mode_scalar",
}


class TestSchemaModeRefs:
    def test_leaf_references_every_mode_def(self):
        from dstf.reporting.schema_export import build_schema

        leaf = build_schema()["$defs"]["leaf"]
        # allOf also carries the variable-or-quantity identity rule, which
        # has no ``if`` — select the mode-dispatch clauses.
        clauses = [c for c in leaf.get("allOf", []) if "if" in c]
        assert clauses, "leaf spec must carry if/then clauses wiring mode $defs"
        by_metric = {
            c["if"]["properties"]["metric"]["const"]: c["then"]["$ref"] for c in clauses
        }
        assert by_metric == {
            metric: f"#/$defs/{def_name}"
            for metric, def_name in _MODE_DEF_NAMES.items()
        }

    def test_mode_defs_allow_leaf_coresident_keys(self):
        from dstf.reporting.schema_export import build_schema

        defs = build_schema()["$defs"]
        for def_name in _MODE_DEF_NAMES.values():
            mode = defs[def_name]
            # Closed, as the parser is: a key nothing reads is refused. (It
            # was lenient until a misspelled bound scored as no bound.)
            assert mode["additionalProperties"] is False, def_name
            for co in ("metric", "variable", "against", "window"):
                assert co in mode["properties"], f"{def_name} missing {co}"
            # `label` was advertised here and read by nothing.
            assert "label" not in mode["properties"], def_name
        # Mode-specific knobs still present for autocomplete.
        assert "tube_rel" in defs["mode_tube"]["properties"]
        assert "min_value" in defs["mode_range"]["properties"]

    def test_valid_tube_leaf_validates_and_an_unknown_key_does_not(self):
        # jsonschema is NOT a project dependency (checked 2026-07-06: absent
        # from the dev env) — skip the validation half there; run it via
        # `uv run --with jsonschema pytest -k schema` or any env that has it.
        jsonschema = pytest.importorskip("jsonschema")
        from dstf.reporting.schema_export import build_schema

        schema = build_schema()
        spec = {
            "tests": [
                {
                    "model": "MyLib.Test",
                    "variables": ["x"],
                    "metrics": {
                        "metric": "tube",
                        "variable": "x",
                        "tube_width_mode": "rel",
                        "tube_rel": 0.02,
                    },
                }
            ]
        }
        jsonschema.validate(spec, schema)
        # As strict as the parser: a key the metric does not read rejects.
        unknown = copy.deepcopy(spec)
        unknown["tests"][0]["metrics"]["future_knob"] = 123
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(unknown, schema)
        # And the $refs are real: a type violation in a mode field rejects.
        bad = {
            "tests": [
                {
                    "model": "MyLib.Test",
                    "metrics": {"metric": "tube", "variable": "x", "tube_rel": "wide"},
                }
            ]
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


def _entry_schema() -> dict:
    """The schema of one `tests[]` entry, inline or behind a `$ref`."""
    from dstf.reporting.schema_export import build_schema

    schema = build_schema()
    entry = schema["properties"]["tests"]["items"]
    if "$ref" in entry:
        entry = schema["$defs"][entry["$ref"].rsplit("/", 1)[-1]]
    return entry


def test_the_entry_schema_describes_plots():
    plots = _entry_schema()["properties"]["plots"]
    assert plots["type"] == "array"
    extra = plots["items"]
    assert extra["required"] == ["var"]
    assert extra["additionalProperties"] is False
    assert set(extra["properties"]) == {"var", "context"}
    assert extra["properties"]["context"]["type"] == "array"


def test_the_plots_schema_refuses_what_the_parser_refuses():
    extra = _entry_schema()["properties"]["plots"]["items"]["properties"]
    assert extra["var"]["pattern"] == "^[^*?]*[^*?\\s][^*?]*$"
    assert extra["context"]["uniqueItems"] is True
    assert extra["context"]["items"]["pattern"] == extra["var"]["pattern"]


#: What a tolerance that takes two sides exports as, beside its title,
#: description and default: a number, or exactly `lower` and `upper`.
NUMBER_OR_PAIR = [
    {"type": "number"},
    {
        "type": "object",
        "properties": {"lower": {"type": "number"}, "upper": {"type": "number"}},
        "required": ["lower", "upper"],
        "additionalProperties": False,
    },
]


@pytest.mark.parametrize(
    ("mode", "name", "default", "title"),
    [
        ("mode_points", "tolerance", 1e-4, "Default tolerance"),
        ("mode_scalar", "tolerance", 1e-4, "Tolerance"),
        ("mode_event_timing", "time_tolerance", 1e-3, "Time tolerance (s)"),
        ("mode_tube", "tube_abs", 0.0, "Tube abs"),
        ("mode_tube", "tube_rel", 0.0, "Tube rel"),
    ],
)
def test_a_two_sided_tolerance_exports_as_a_number_or_a_pair(
    mode, name, default, title
):
    """The whole node, asserted as a dict (no validator is a dependency CI
    installs): a number validates, a pair with both sides validates, and a
    pair with one side or a third key does not — `required` names both and
    `additionalProperties` is false."""
    from dstf.reporting.schema_export import build_schema

    defs = build_schema()["$defs"]
    node = dict(defs[mode]["properties"][name])
    assert node.pop("description")
    # The pair's two sides are described too (`test_descriptions.py`); the
    # shape is what this asserts.
    node["oneOf"] = _without_descriptions(node["oneOf"])
    assert node == {"oneOf": NUMBER_OR_PAIR, "default": default, "title": title}
    json.dumps(defs)


def _without_descriptions(node):
    """``node`` with every ``description`` in it removed."""
    if isinstance(node, dict):
        return {
            key: _without_descriptions(value)
            for key, value in node.items()
            if key != "description"
        }
    if isinstance(node, list):
        return [_without_descriptions(value) for value in node]
    return node


def test_nrmse_tolerance_exports_as_one_number():
    from dstf.reporting.schema_export import build_schema

    node = dict(build_schema()["$defs"]["mode_nrmse"]["properties"]["tolerance"])
    assert node.pop("description")
    assert node == {"type": "number", "default": 1e-4, "title": "Tolerance"}
