"""JSON-Schema export for ``test_spec.json`` (PHASE_6_PLAN 6.4.5) and for
``testing.json`` (:func:`build_config_schema`, from the one table ``Config``
reads the file by).

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

The schema is as strict as the parser: wherever a key nobody reads is a
spec error (``comparison/strict_keys.py``) the object says
``additionalProperties: false``, and its property names come from the
same data the parser checks against — a metric's config dataclass, the
tree parser's key tables, ``discovery/spec_shape.py`` for the entry.
"""

from __future__ import annotations

import copy
import dataclasses
import re
from typing import Any

from ..comparison.mode_schema import derive_schema
from ..comparison.modes import QUANTITY_KEYS
from ..comparison.registry import (
    combinator_names,
    metric_names,
    metrics,
    unnamed_metrics,
)
from ..comparison.strict_keys import EVERY_METRIC, declares_quantity, row_fields
from ..comparison.tree_spec import COMBINATOR_KIND_KEYS
from ..config_shape import TESTING_JSON, schema_properties
from ..discovery import spec_shape

_JSON_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"

#: A tolerance with two sides: one number (both sides), or exactly
#: ``lower`` and ``upper`` — what :func:`~dstf.comparison.sides.parse_sides`
#: takes. A pair with one side, or with a third key, is neither.
_SIDES = [
    {"type": "number"},
    {
        "type": "object",
        "properties": {"lower": {"type": "number"}, "upper": {"type": "number"}},
        "required": ["lower", "upper"],
        "additionalProperties": False,
    },
]


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
    elif field.type == "sides":
        node["oneOf"] = copy.deepcopy(_SIDES)
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
    }


def _param_properties(config_cls: type, mode_name: str) -> dict[str, Any] | None:
    """One metric's params as schema properties: its config's fields — a
    row table's rows closed to its ``columns`` — and the two params read
    off every leaf (``strict_keys.EVERY_METRIC``). None for a metric whose
    config is not a dataclass: nothing says what it reads, the parser
    does not check it, and neither does the schema."""
    if not dataclasses.is_dataclass(config_cls):
        return None
    props: dict[str, Any] = {
        # A leaf's own tolerance, a number or a pair, where the metric's
        # config does not type it itself; `quantity` of any shape.
        "tolerance": {"oneOf": copy.deepcopy(_SIDES)},
        "quantity": {},
    }
    assert set(props) == set(EVERY_METRIC)
    for f in derive_schema(config_cls, mode=mode_name).fields:
        props[f.name] = _field_to_schema(f)
    if declares_quantity(mode_name):
        # Read here: a path, or an object closed to the keys the parser
        # takes inside one (`strict_keys`).
        props["quantity"] = {
            **props["quantity"],
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {key: {} for key in QUANTITY_KEYS},
                    "additionalProperties": False,
                },
                {"type": "null"},
            ],
        }
    for name, columns in row_fields(mode_name).items():
        props[name] = {
            **props[name],
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {column: {} for column in sorted(columns)},
                "additionalProperties": False,
            },
        }
    return props


def _mode_def(config_cls: type, mode_name: str) -> dict:
    """A leaf of one metric, whole: the leaf's own keys and the metric's
    params, and nothing else — the parser refuses any other key (it was
    lenient until a misspelled bound scored as no bound at all)."""
    params = _param_properties(config_cls, mode_name)
    node: dict[str, Any] = {
        "type": "object",
        "title": f"{mode_name} mode config",
        "properties": {**_leaf_common_properties(), **(params or {})},
    }
    if params is not None:
        node["additionalProperties"] = False
    return node


def _override_def_name(mode_name: str) -> str:
    return f"override_{mode_name.replace('-', '_')}"


def _override_def(config_clss: list[tuple[str, type]], title: str) -> dict:
    """A flat ``comparison.variable_overrides`` entry scored by one of
    ``config_clss`` (more than one for an entry with no ``mode``): ``mode``
    and those metrics' params, and nothing else."""
    props: dict[str, Any] = {"mode": {"type": "string", "enum": sorted(metric_names())}}
    closed = True
    for name, config_cls in config_clss:
        params = _param_properties(config_cls, name)
        closed = closed and params is not None
        props.update(params or {})
    node: dict[str, Any] = {"type": "object", "title": title, "properties": props}
    if closed:
        node["additionalProperties"] = False
    return node


def _override_defs(modes: list[tuple[str, type]]) -> dict[str, dict]:
    """``override`` — one entry of ``variable_overrides`` — and the def per
    mode it selects by its ``mode`` key. With no ``mode`` the entry is
    scored by the default metric, or by ``points`` under
    ``--default-points`` (``registry.unnamed_metrics``): it may carry
    what either reads."""
    by_name = dict(modes)
    defs = {
        _override_def_name(name): _override_def([(name, cls)], f"{name} override")
        for name, cls in modes
    }
    defs["override_without_mode"] = _override_def(
        [(name, by_name[name]) for name in unnamed_metrics() if name in by_name],
        "override with no mode",
    )
    defs["override"] = {
        "type": "object",
        "title": "Flat per-variable override",
        "allOf": [
            {
                "if": {"required": ["mode"]},
                "else": {"$ref": "#/$defs/override_without_mode"},
            },
            *(
                {
                    "if": {
                        "properties": {"mode": {"const": name}},
                        "required": ["mode"],
                    },
                    "then": {"$ref": f"#/$defs/{_override_def_name(name)}"},
                }
                for name, _ in modes
            ),
        ],
    }
    return defs


def _combinator_kind_rules() -> list[dict]:
    """Each key only some kinds read (``k``, ``weights``, ...) is forbidden
    on every other kind — the parser's own table, so the two agree."""
    keys = sorted({key for kind in COMBINATOR_KIND_KEYS.values() for key in kind})
    return [
        {
            "if": {
                "properties": {
                    "combinator": {
                        "enum": sorted(
                            kind
                            for kind, takes in COMBINATOR_KIND_KEYS.items()
                            if key in takes
                        )
                    }
                },
                "required": ["combinator"],
            },
            "else": {"properties": {key: False}},
        }
        for key in keys
    ]


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
        # review 2026-07-06 finding 72: make the mode $defs real — the
        # ``metric`` discriminator selects the matching mode definition, so
        # validators type-check mode knobs and editors get autocomplete.
        # Each of those defs is closed (``additionalProperties: false``):
        # that is where a leaf's unknown key is refused.
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
        "allOf": _combinator_kind_rules(),
    }

    test_entry = {
        "type": "object",
        "required": ["model"],
        # The entry's keys, and its simulation and comparison blocks': the
        # one table the spec parser checks an entry against.
        "properties": copy.deepcopy(spec_shape.ENTRY),
        "additionalProperties": False,
    }

    return {
        "$schema": _JSON_SCHEMA_URI,
        "title": "DSTF test_spec.json",
        "type": "object",
        "required": ["tests"],
        # The root's own keys — `tests`, and the `$schema` that points an
        # editor here — and no other: the parser refuses one (`spec_shape`).
        "properties": copy.deepcopy(spec_shape.ROOT),
        "additionalProperties": False,
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
            **_override_defs(modes),
        },
    }


def build_config_schema() -> dict:
    """The JSON-Schema document of ``testing.json``: every key of
    ``config_shape.TESTING_JSON`` with its type, default and description,
    and under ``backends`` each registered backend's own settings table
    (``Backend.settings``) for the entries named after it.

    As strict as ``Config``: a key that is in neither table is refused
    there, and is ``additionalProperties: false`` here.
    """
    from ..backends import backend_class, backend_names, source_types

    properties = schema_properties(TESTING_JSON)
    # What `Config` holds the value to: one a registered backend runs.
    properties["source_type"]["enum"] = source_types()
    blocks: dict[str, Any] = {}
    names = backend_names()
    for name in names:
        cls = backend_class(name)
        if cls is None:
            continue
        # An entry is its backend's by how its name STARTS: "Dymola 2025" —
        # the LONGEST backend name it starts with (`backend_family`), so
        # the pattern of `Acme` lets by what starts with `AcmePlus`.
        longer = [other[len(name) :] for other in names if other.startswith(name)]
        beyond = "|".join(re.escape(rest) for rest in longer if rest)
        blocks[f"^{re.escape(name)}" + (f"(?!{beyond})" if beyond else "")] = {
            "type": "object",
            "title": f"{name} settings",
            "properties": schema_properties(cls.settings),
            "additionalProperties": False,
        }
    properties["backends"].update(
        {"patternProperties": blocks, "additionalProperties": False}
    )
    return {
        "$schema": _JSON_SCHEMA_URI,
        "title": "DSTF testing.json",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
