"""JSON-Schema export for ``test_spec.json`` (PHASE_6_PLAN 6.4.5).

Derives a JSON-Schema document from:
  * every registered metric's Config dataclass (reusing the 6.1.1
    introspection — one definition per mode);
  * the :class:`LeafSpec` / :class:`CombinatorSpec` grammar (the
    MetricTree shape);
  * the per-test entry shape (simulation block, comparison block,
    variables, metrics tree, window, baselines).

Emits JSON-Schema draft 2020-12 shape. Thin layer — no validation
logic here (that lives in ``comparison/validator.py`` + parse-time).
Downstream uses: IDE autocomplete, LLM authoring, tool handoff.
"""

from __future__ import annotations

from typing import Any

from ..comparison.registry import combinator_names, metric_names, metrics
from .ui.mode_controls import derive_schema

_JSON_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"


def _field_to_schema(field) -> dict:
    """Translate a 6.1.1 FieldSpec into a JSON-Schema property dict."""
    node: dict[str, Any] = {}
    if field.type == "float":
        node["type"] = ["number", "null"] if field.optional else "number"
    elif field.type == "int":
        node["type"] = ["integer", "null"] if field.optional else "integer"
    elif field.type == "bool":
        node["type"] = ["boolean", "null"] if field.optional else "boolean"
    elif field.type == "str":
        node["type"] = ["string", "null"] if field.optional else "string"
    elif field.type == "enum":
        base: dict[str, Any] = {"type": "string", "enum": list(field.choices or [])}
        if field.optional:
            node["oneOf"] = [base, {"type": "null"}]
        else:
            node.update(base)
    elif field.type == "passthrough":
        # Complex / unknown — allow any JSON shape. Documentation hints
        # via description if we have one.
        node = {}
    if field.default is not None:
        node["default"] = field.default
    if field.label:
        node["title"] = field.label
    if field.help:
        node["description"] = field.help
    return node


def _modes() -> list[tuple[str, type]]:
    """Registered metrics in registration order, as ``(name, config_cls)``.

    Single source for both the ``$defs`` emission and the leaf's if/then
    wiring (finding 72) — and the reason a registered metric shows up in
    the exported schema without editing this module.
    """
    return [(desc.name, desc.config_cls) for desc in metrics()]


def _mode_def_name(mode_name: str) -> str:
    return f"mode_{mode_name.replace('-', '_')}"


def _quantity_metric_names() -> list[str]:
    """Registered metrics whose ``quantity`` may stand in for ``variable``.

    Mirrors the parser rule in :func:`~dstf.comparison.tree_spec._parse_leaf`
    — both read ``MetricDescriptor.accepts_quantity``, so the exported
    grammar and the one the CLI enforces can't drift apart.
    """
    return [desc.name for desc in metrics() if desc.accepts_quantity]


def _leaf_identity_rule() -> dict:
    """The "how a leaf names its data" requirement, as JSON-Schema.

    Every leaf needs ``metric``. It then needs either a ``variable`` (the
    tracked series it scores) or — only for a metric that declares
    ``accepts_quantity`` — a string ``quantity`` naming a stored scalar.
    """
    alternatives: list[dict] = [{"required": ["variable"]}]
    quantity_metrics = _quantity_metric_names()
    if quantity_metrics:
        alternatives.append(
            {
                "properties": {
                    "metric": {"enum": quantity_metrics},
                    "quantity": {"type": "string", "minLength": 1},
                },
                "required": ["metric", "quantity"],
            }
        )
    return {"anyOf": alternatives}


def _leaf_common_properties() -> dict[str, Any]:
    """Leaf keys that co-reside with mode-specific knobs in the same object.

    Shared between the leaf spec and every mode ``$def`` — the mode defs are
    ``$ref``'d from inside the leaf (if metric == <mode> then $ref), so they
    describe the same flat object and must list these keys for editor/LLM
    autocomplete (review 2026-07-06 finding 72).
    """
    return {
        "metric": {
            "type": "string",
            "enum": sorted(metric_names()),
            "description": "The per-variable metric to score with.",
        },
        "variable": {
            "type": "string",
            "description": "Variable name as reported by the backend.",
        },
        "against": {
            "type": "string",
            "default": "primary",
            "description": (
                "Which baseline to score against — 'primary' or the name "
                "of a registered soft_check. Companions are not scorable."
            ),
        },
        "window": {
            "type": "object",
            "description": "Scope the leaf to [start, end] (idea #46).",
            "properties": {
                "start": {"type": "number"},
                "end": {"type": "number"},
            },
            "additionalProperties": False,
        },
        "label": {
            "type": "string",
            "description": "Human-readable label shown in reports.",
        },
    }


def _mode_def(config_cls: type, mode_name: str) -> dict:
    sch = derive_schema(config_cls, mode=mode_name)
    # review 2026-07-06 finding 72: deliberately NO "additionalProperties":
    # false — the spec's posture is lenient (unknown keys pass through as
    # metric params so newer specs still validate against older schemas).
    # The complete properties map — mode knobs plus the leaf's co-resident
    # keys — exists for autocomplete and type-checking, not hard rejection.
    props: dict[str, Any] = _leaf_common_properties()
    for f in sch.fields:
        props[f.name] = _field_to_schema(f)
    return {
        "type": "object",
        "title": f"{mode_name} mode config",
        "properties": props,
    }


def build_schema() -> dict:
    """Build the top-level JSON-Schema document."""
    modes = _modes()
    mode_defs = {_mode_def_name(name): _mode_def(cls, name) for name, cls in modes}

    leaf_spec = {
        "type": "object",
        "title": "Metric tree leaf",
        # ``variable`` is not unconditionally required: a metric that
        # declares ``accepts_quantity`` may name its data with a string
        # ``quantity`` instead (see _leaf_identity_rule, folded into allOf
        # below so it composes with the per-mode if/then clauses).
        "required": ["metric"],
        # Mode-specific knobs live inline alongside metric/variable per the
        # historical override shape — e.g., a leaf with metric=tube may set
        # tube_rel, tube_width_mode, etc. (typed via the allOf below).
        "properties": _leaf_common_properties(),
        "additionalProperties": True,
        # review 2026-07-06 finding 72: make the mode $defs real — the
        # ``metric`` discriminator selects the matching mode definition, so
        # validators type-check mode knobs and editors get autocomplete.
        "allOf": [
            _leaf_identity_rule(),
            *(
                {
                    "if": {
                        "properties": {"metric": {"const": name}},
                        "required": ["metric"],
                    },
                    "then": {"$ref": f"#/$defs/{_mode_def_name(name)}"},
                }
                for name, _ in modes
            ),
        ],
    }

    combinator_spec = {
        "type": "object",
        "title": "Metric tree combinator",
        "required": ["combinator", "children"],
        "properties": {
            "combinator": {
                "type": "string",
                "enum": sorted(combinator_names()),
            },
            "children": {
                "type": "array",
                "items": {"$ref": "#/$defs/tree_node"},
                "minItems": 1,
            },
            "k": {
                "type": "integer",
                "minimum": 1,
                "description": "Required when combinator = 'k-of-n'.",
            },
            "weights": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Weights for 'weighted' combinator; parallel to children.",
            },
            "threshold": {"type": "number"},
            "direction": {"type": "string", "enum": ["less", "greater"]},
        },
        "additionalProperties": False,
    }

    test_entry = {
        "type": "object",
        "required": ["model"],
        "properties": {
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
                "properties": {
                    "stop_time": {"type": "number"},
                    "tolerance": {"type": "number"},
                    "method": {"type": "string"},
                    "number_of_intervals": {"type": "integer"},
                    "output_interval": {"type": "number"},
                    "timeout": {"type": "integer"},
                },
                "additionalProperties": True,
            },
            "comparison": {
                "type": "object",
                "properties": {
                    "tolerance": {"type": "number"},
                    "variable_overrides": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "description": {"type": "string"},
                    "info": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": True,
            },
            "metrics": {"$ref": "#/$defs/tree_node"},
            "description": {"type": "string"},
            "info": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "additionalProperties": True,
    }

    return {
        "$schema": _JSON_SCHEMA_URI,
        "title": "DSTF test_spec.json",
        "type": "object",
        "required": ["tests"],
        "properties": {
            "tests": {
                "type": "array",
                "items": {"$ref": "#/$defs/test_entry"},
            },
        },
        "$defs": {
            "test_entry": test_entry,
            "tree_node": {
                "oneOf": [
                    {"$ref": "#/$defs/leaf"},
                    {"$ref": "#/$defs/combinator"},
                ]
            },
            "leaf": leaf_spec,
            "combinator": combinator_spec,
            **mode_defs,
        },
    }
