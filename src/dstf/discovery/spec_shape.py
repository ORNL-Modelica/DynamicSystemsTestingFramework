"""The shape of ``test_spec.json`` — its root and one entry: the keys, and
what each holds.

ONE table, read twice. The spec parser refuses a key that is not in
it — ``"simulaton"`` used to be skipped in silence, and the test ran on
defaults — and ``spec schema`` publishes it with ``additionalProperties:
false``. So the grammar an editor checks and the one the CLI enforces
cannot name different keys.

Each value is the key's JSON-Schema fragment (draft 2020-12). ``metrics``
refers to the tree grammar, which :mod:`dstf.reporting.schema_export`
builds from the metric registry.
"""

from __future__ import annotations

from typing import Any

#: One variable of the result: not blank, not a pattern.
_PLOT_NAME = {"type": "string", "pattern": "^[^*?]*[^*?\\s][^*?]*$"}

#: A person's notes. Read by nothing, by design — the documented place for
#: free text beside an entry or its comparison settings, so that no other
#: unread key has to be tolerated to leave room for one. ``metadata`` is an
#: object of any shape.
NOTES: dict[str, Any] = {
    "description": {"type": "string"},
    "info": {"type": "string"},
    "metadata": {"type": "object"},
}

#: The ``simulation`` block: every field ``spec_parser`` reads from it.
SIMULATION: dict[str, Any] = {
    "stop_time": {"type": "number"},
    "tolerance": {"type": "number"},
    "method": {"type": "string"},
    "number_of_intervals": {"type": "integer"},
    "output_interval": {"type": "number"},
    "timeout": {"type": "integer"},
}

#: The ``comparison`` block. Each ``variable_overrides`` entry is one
#: metric's flat params (``#/$defs/override``): ``mode`` and that mode's
#: fields.
COMPARISON: dict[str, Any] = {
    "tolerance": {"type": "number"},
    "variable_overrides": {
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/override"},
    },
    **NOTES,
}

#: A test entry's own keys.
ENTRY: dict[str, Any] = {
    "model": {"type": "string"},
    "source": {
        "type": "string",
        "description": (
            "Path to the simulation source file "
            "(.jl / .py / .fmu), relative to the spec file."
        ),
    },
    "variables": {"type": "array", "items": {"type": "string"}},
    "simulation": {
        "type": "object",
        "properties": SIMULATION,
        "additionalProperties": False,
    },
    "comparison": {
        "type": "object",
        "properties": COMPARISON,
        "additionalProperties": False,
    },
    "metrics": {"$ref": "#/$defs/tree_node"},
    "plots": {
        "type": "array",
        "description": (
            "A person's plot extras, read by the report and the authoring "
            "tool and ignored by scoring. A test's plots are derived from "
            "its criteria tree; an entry on a scored variable adds grey "
            "context series to its plot, an entry on an unscored one is a "
            "watch plot. Absent or []: no extras."
        ),
        "items": {
            "type": "object",
            "properties": {
                # One variable, not blank, not a pattern — what
                # `parse_plots` accepts.
                "var": _PLOT_NAME,
                "context": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _PLOT_NAME,
                },
            },
            "required": ["var"],
            "additionalProperties": False,
        },
    },
    "baseline_points": {
        "description": (
            "The points this test's stored baseline keeps of its run: "
            '"all" — every point the simulator wrote — or a whole '
            "number of 2 or more, which thins a longer run to at most "
            "that many. Overrides testing.json's baseline_points; "
            "absent, that one applies."
        ),
        # What `config.baseline_points_refused` accepts — but for
        # `2000.0`, an integer to JSON Schema and refused there.
        "oneOf": [{"const": "all"}, {"type": "integer", "minimum": 2}],
    },
    **NOTES,
}

#: The spec file's own keys: the tests, and the ``$schema`` an editor reads
#: to check the file as it is typed (``spec schema`` writes the schema to
#: point it at). ``{"Tests": [...]}`` used to be a spec of no tests.
ROOT: dict[str, Any] = {
    "$schema": {"type": "string"},
    "tests": {"type": "array", "items": {"$ref": "#/$defs/test_entry"}},
}
