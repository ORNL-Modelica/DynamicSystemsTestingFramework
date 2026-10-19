"""The shape of ``test_spec.json`` — its root, one entry, and the parts of a
``metrics`` tree: the keys, and for each its type, its default and what it
is for.

Each table is read twice or more. The spec parser and the tree parser
refuse a key that is not in it — ``"simulaton"`` used to be skipped in
silence, and the test ran on defaults — ``spec schema`` publishes it with
``additionalProperties: false`` and every key's description, and the
reference page is rendered from it. So the grammar an editor checks, the
one the CLI enforces and the one a person reads cannot name different
keys.

Each key is a :class:`~dstf.config_shape.Setting`: its JSON-Schema fragment
(draft 2020-12), its default and a description written for the person who
writes the file. A default is written in its key's row and nowhere else.
``metrics`` refers to the tree grammar, which
:mod:`dstf.reporting.schema_export` builds from these tables and the metric
registry.
"""

from __future__ import annotations

from typing import Any

from ..config_shape import NOTES, Setting, schema_properties

#: One variable of the result: not blank, not a pattern.
_PLOT_NAME = {"type": "string", "pattern": "^[^*?]*[^*?\\s][^*?]*$"}

#: A tolerance with two sides: one number (both sides), or exactly
#: ``lower`` and ``upper`` — what :func:`~dstf.comparison.sides.parse_sides`
#: takes. A pair with one side, or with a third key, is neither.
SIDES: list[dict[str, Any]] = [
    {"type": "number"},
    {
        "type": "object",
        "properties": {
            "lower": {
                "type": "number",
                "description": "How far the value may lie below its target.",
            },
            "upper": {
                "type": "number",
                "description": "How far the value may lie above its target.",
            },
        },
        "required": ["lower", "upper"],
        "additionalProperties": False,
    },
]

#: The ``simulation`` block: every field ``spec_parser`` reads from it. Each
#: is optional: what the entry does not set comes from the model's
#: ``experiment`` annotation, then from the default here (where there is
#: one) or the simulator's own.
SIMULATION: dict[str, Setting] = {
    "stop_time": Setting(
        {"type": "number"},
        1.0,
        "The time the simulation ends at, in seconds; every simulation "
        "starts at 0. Without this key the model's experiment annotation "
        "gives it (StopTime); without that, the default applies and run "
        "says so before it starts.",
    ),
    "tolerance": Setting(
        {"type": "number"},
        1e-4,
        "The solver's relative tolerance. Without this key the model's "
        "experiment annotation gives it (Tolerance); without that, the "
        "default applies. This is the simulator's tolerance, not the "
        "comparison's.",
    ),
    "method": Setting(
        {"type": "string"},
        "Dassl",
        "The solver, by the name the simulator knows it by "
        '("Dassl", "Cvode", "Esdirk45a"). Without this key the model\'s '
        "experiment annotation gives it (__Dymola_Algorithm); without that, "
        "the default applies. FMPy maps it to one of its own solvers; "
        "Julia and Python tests do not read it.",
    ),
    "number_of_intervals": Setting(
        {"type": "integer"},
        None,
        "How many output intervals the result holds. Without this key the "
        "model's experiment annotation gives it (NumberOfIntervals); "
        "without that, the simulator's own default applies. Where both "
        "this and output_interval are set, Dymola and OpenModelica use "
        "this one and FMPy uses output_interval. Julia and Python tests do "
        "not read it.",
    ),
    "output_interval": Setting(
        {"type": "number"},
        None,
        "The time between two output points, in seconds: the other way of "
        "saying number_of_intervals. Without this key the model's "
        "experiment annotation gives it (Interval). Julia and Python tests "
        "do not read it.",
    ),
    "timeout": Setting(
        {"type": "integer"},
        None,
        "The seconds this test may run before it is stopped and fails. "
        "Without this key the run's --timeout applies. Under Dymola's "
        "--batch, the timeouts of a batch's tests add up to one limit for "
        "the batch.",
    ),
}

#: The ``comparison`` block. Each ``variable_overrides`` entry is one
#: metric's flat params (``#/$defs/override``): ``mode`` and that mode's
#: fields.
COMPARISON: dict[str, Setting] = {
    "tolerance": Setting(
        {"type": "number"},
        None,
        "This test's base tolerance: what a criterion is scored with when "
        "neither it nor an override gives it one — for nrmse, the ceiling. "
        "One finite number of 0 or more. Without this key the stored "
        "baseline's applies, then testing.json's tolerance.",
    ),
    "variable_overrides": Setting(
        {
            "type": "object",
            "additionalProperties": {"$ref": "#/$defs/override"},
        },
        {},
        "How a tracked variable is scored, by its result-file name: "
        '{"pipe.T[1]": {"mode": "tube", "tube_rel": 0.02}} — a mode and '
        "that mode's fields, or a tolerance alone for the default nrmse. "
        "An override here wins over one the stored baseline carries. "
        "Under a metrics tree these are not scored.",
    ),
    **NOTES,
}

#: One ``comparison.variable_overrides`` entry's own key; the rest of the
#: entry is its metric's params (:data:`EVERY_METRIC` and the mode's
#: fields).
OVERRIDE: dict[str, Setting] = {
    "mode": Setting(
        {"type": "string"},
        "nrmse",
        "The comparison mode that scores the variable. Without this key "
        "the variable is scored by the default, or by its final value "
        "under --default-points.",
    ),
}

#: The params read off EVERY leaf and override, whatever its metric
#: (``strict_keys.EVERY_METRIC``). A mode whose config has a field of the
#: same name describes it there.
EVERY_METRIC: dict[str, Setting] = {
    "tolerance": Setting(
        {"oneOf": SIDES},
        None,
        "The tolerance this criterion is scored with, where its mode reads "
        "one: one number for both sides, or a lower/upper pair. It wins "
        "over the test's and the stored baseline's.",
    ),
    "quantity": Setting(
        {},
        None,
        "Read by the scalar mode only, which scores the number it names. "
        "On any other mode it is accepted and not used.",
    ),
}

#: A ``plots`` entry's keys.
PLOT: dict[str, Setting] = {
    "var": Setting(
        _PLOT_NAME,
        None,
        "One variable of the result, by its result-file name; not a "
        "pattern. If a criterion scores it, this entry adds to that "
        "variable's plot; otherwise it is a plot of its own, after the "
        "plots of the criteria.",
    ),
    "context": Setting(
        {"type": "array", "uniqueItems": True, "items": _PLOT_NAME},
        [],
        "Variables drawn grey and thinner on the plot of var, on that "
        "plot's y axis, and not scored. Each named once, and not var "
        "itself.",
    ),
}

#: A leaf's ``window``.
WINDOW: dict[str, Setting] = {
    "start": Setting(
        {"type": "number"},
        None,
        "The time the window starts at, in seconds. Without it the window "
        "is open at that end.",
    ),
    "end": Setting(
        {"type": "number"},
        None,
        "The time the window ends at, in seconds; after start. Without it "
        "the window is open at that end.",
    ),
}

#: The keys the tree parser itself reads off a leaf; every other key of a
#: leaf is one of its metric's params.
LEAF: dict[str, Setting] = {
    "metric": Setting(
        {"type": "string"},
        None,
        "The comparison mode that scores this criterion. A key the criterion "
        "holds beside metric, variable, against and window is one of that "
        "mode's fields.",
    ),
    "variable": Setting(
        {"type": "string"},
        None,
        "The variable this criterion scores, by its result-file name. A "
        "variable named here is recorded and stored even when the entry's "
        "variables do not match it — except a name in diagnostic_variables "
        "that only a scalar's final, min or max over the whole run names: "
        "its summary is stored, not its curve. A scalar criterion whose "
        "quantity is a path may leave it out.",
    ),
    "against": Setting(
        {"type": "string", "minLength": 1},
        "primary",
        'The baseline this criterion is scored against: "primary", the '
        "test's stored baseline, or the name of one of its soft checks. A "
        "criterion against a soft check must sit under a warn. A companion "
        "is drawn, never scored.",
    ),
    "window": Setting(
        {
            "type": "object",
            "properties": schema_properties(WINDOW),
            "additionalProperties": False,
        },
        None,
        "Score only the part of the trajectory between start and end, in "
        "seconds, both included. Without it the whole run is scored.",
    ),
}

#: A combinator's keys: the two every kind has, and the ones only some
#: kinds read (``tree_spec.COMBINATOR_KIND_KEYS`` says which).
COMBINATOR: dict[str, Setting] = {
    "combinator": Setting(
        {"type": "string"},
        None,
        "The kind of group: and, or, k-of-n, warn or weighted, or a plug-in's.",
    ),
    "children": Setting(
        {"type": "array", "items": {"$ref": "#/$defs/tree_node"}, "minItems": 1},
        None,
        "The criteria and groups this group combines, at least one; a warn "
        "takes exactly one.",
    ),
    "k": Setting(
        {"type": "integer", "minimum": 1},
        None,
        "On a k-of-n, and required there: how many of the children must "
        "pass, from 1 to their number.",
    ),
    "weights": Setting(
        {"type": "array", "items": {"type": "number"}},
        None,
        "On a weighted, and required there: one weight per child, in the "
        "children's order.",
    ),
    "threshold": Setting(
        {"type": "number"},
        None,
        "On a weighted, and required there: the number the weighted sum of "
        "the children's scores is held to.",
    ),
    "direction": Setting(
        {"type": "string", "enum": ["less", "greater"]},
        "less",
        'On a weighted: "less" passes when the weighted sum is below the '
        'threshold (scores that are errors), "greater" when it is above '
        "(scores that are coverage).",
    ),
}

#: A test entry's own keys.
ENTRY: dict[str, Setting] = {
    "model": Setting(
        {"type": "string"},
        None,
        "The test's model, by its full name (MyLib.Examples.PipeTest). "
        "Required. An entry for a model a recognizer also finds adds to "
        "that test; the first entry for a model is the one read.",
    ),
    "source": Setting(
        {"type": "string"},
        None,
        "The file the test runs, relative to this file: an .fmu for FMPy, "
        "a .jl for Julia, a .py for Python. A Modelica test leaves it out; "
        "its model is found in the library.",
    ),
    "variables": Setting(
        {"type": "array", "items": {"type": "string"}},
        [],
        "The variables to track, record and score, by result-file name; * "
        'and ? are wildcards, and ["*"] tracks every variable the result '
        "holds. A UnitTests "
        "variable and a variable a criterion names are tracked whether "
        "or not they are listed here — but a name in diagnostic_variables "
        "that only a scalar's final, min or max over the whole run names is "
        "not: its summary is stored, and its curve only when this list "
        "matches it. An entry that tracks nothing and "
        "scores nothing passes when its simulation completes.",
    ),
    "simulation": Setting(
        {
            "type": "object",
            "properties": schema_properties(SIMULATION),
            "additionalProperties": False,
        },
        None,
        "How this test is simulated. A field set here wins over the "
        "model's experiment annotation.",
    ),
    "comparison": Setting(
        {
            "type": "object",
            "properties": schema_properties(COMPARISON),
            "additionalProperties": False,
        },
        None,
        "How this test's variables are scored when it has no metrics tree, "
        "and its base tolerance.",
    ),
    "metrics": Setting(
        {"$ref": "#/$defs/tree_node"},
        None,
        "The test's criteria as a tree: a criterion scores one variable by one "
        "comparison mode, a group (and, or, k-of-n, warn, weighted) "
        "combines its children. Without it every tracked variable is scored "
        "by its override or by nrmse, and all must pass.",
    ),
    "plots": Setting(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": schema_properties(PLOT),
                "required": ["var"],
                "additionalProperties": False,
            },
        },
        [],
        "Plots added to the ones the criteria give: an entry on a scored "
        "variable adds grey context series to its plot, an entry on an "
        "unscored one is a plot of its own. Read by the report and the "
        "authoring tool; scoring ignores it.",
    ),
    "baseline_points": Setting(
        # What `config.baseline_points_refused` accepts — but for
        # `2000.0`, an integer to JSON Schema and refused there.
        {"oneOf": [{"const": "all"}, {"type": "integer", "minimum": 2}]},
        None,
        "The points this test's stored baseline keeps of its run: "
        '"all" — every point the simulator wrote — or a whole number of 2 '
        "or more, which thins a longer run to at most that many. Without "
        "this key testing.json's baseline_points applies.",
    ),
    "diagnostic_variables": Setting(
        {"type": "array", "items": {"type": "string"}},
        [],
        "Names whose summary (final, min, max) this test's baseline stores, "
        "in addition to `testing.json`'s list. A listed name's curve is "
        "stored only when a criterion needs it — any criterion but a "
        "scalar's final, min or max over the whole run — or when the "
        "entry's variables match it.",
    ),
    **NOTES,
}

#: The spec file's own keys: the tests, and the ``$schema`` an editor reads
#: to check the file as it is typed (``spec schema`` writes the schema to
#: point it at). ``{"Tests": [...]}`` used to be a spec of no tests.
ROOT: dict[str, Setting] = {
    "$schema": Setting(
        {"type": "string"},
        None,
        "The JSON Schema an editor checks this file against as it is typed "
        "(`dstf spec schema` writes one). Nothing else reads it.",
    ),
    "tests": Setting(
        {"type": "array", "items": {"$ref": "#/$defs/test_entry"}},
        [],
        "The test entries, one object per test.",
    ),
}
