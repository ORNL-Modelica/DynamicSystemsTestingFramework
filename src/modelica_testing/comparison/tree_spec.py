"""Declarative spec form for user-authored MetricTrees from test_spec.json.

Parses the JSON shape into typed dataclasses without evaluating anything:

  {
    "combinator": "and",
    "children": [
      {"metric": "nrmse", "variable": "h", "tolerance": 0.01},
      {"metric": "tube",  "variable": "v", "tube_rel": 0.05}
    ]
  }

A spec tree is the static description of *what to score*. Phase 3.3 will
walk it together with simulation results to produce evaluated
``MetricResult`` trees (see ``metric_tree.py``). Phase 3.2 stops at the
parse step so users can write the schema and we can unit-test the validation
without changing pipeline behavior.

Validation is hand-rolled (jsonschema would be heavier than warranted).
Errors carry the JSON path where the problem was found so users can
locate the offending entry quickly in a large spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

VALID_COMBINATORS = frozenset({"and", "or", "k-of-n", "warn"})
VALID_METRICS = frozenset({"nrmse", "tube", "final-only", "range"})


class MetricSpecError(ValueError):
    """Raised on malformed MetricTree spec input. Message includes the path."""


@dataclass
class LeafSpec:
    """A single per-variable metric evaluation.

    ``params`` carries metric-specific knobs (tolerance, tube_rel, tube_points,
    tube_width_mode, ...). The shape mirrors the existing
    ``variable_overrides`` payload in test_spec.json so the same tube/NRMSE
    parameter names work in both contexts.
    """

    metric: str
    variable: str
    params: dict = field(default_factory=dict)


@dataclass
class CombinatorSpec:
    """A combinator node: and/or/k-of-n/warn over child specs.

    ``k`` is required iff ``combinator == "k-of-n"``. ``warn`` requires
    exactly one child; the others require at least one.
    """

    combinator: str
    children: list[Union["LeafSpec", "CombinatorSpec"]]
    k: int = 0  # only meaningful for k-of-n

    def __post_init__(self):
        if self.combinator == "k-of-n" and self.k <= 0:
            raise MetricSpecError(
                f"k-of-n combinator requires positive 'k'; got {self.k}"
            )


SpecNode = Union[LeafSpec, CombinatorSpec]


def parse_metric_tree(raw: dict, _path: str = "metrics") -> SpecNode:
    """Parse a MetricTree spec dict into the typed form.

    Raises ``MetricSpecError`` with a path-bearing message on any structural
    or semantic problem (unknown combinator, missing field, wrong type).
    """
    if not isinstance(raw, dict):
        raise MetricSpecError(
            f"{_path}: expected an object, got {type(raw).__name__}"
        )

    has_combinator = "combinator" in raw
    has_metric = "metric" in raw
    if has_combinator and has_metric:
        raise MetricSpecError(
            f"{_path}: node has both 'combinator' and 'metric' keys; pick one"
        )
    if not has_combinator and not has_metric:
        raise MetricSpecError(
            f"{_path}: node must have either 'combinator' (internal) "
            f"or 'metric' (leaf)"
        )

    if has_combinator:
        return _parse_combinator(raw, _path)
    return _parse_leaf(raw, _path)


def _parse_combinator(raw: dict, path: str) -> CombinatorSpec:
    name = raw["combinator"]
    if not isinstance(name, str) or name not in VALID_COMBINATORS:
        raise MetricSpecError(
            f"{path}.combinator: unknown combinator {name!r}; "
            f"valid: {sorted(VALID_COMBINATORS)}"
        )

    children_raw = raw.get("children")
    if not isinstance(children_raw, list) or not children_raw:
        raise MetricSpecError(
            f"{path}.children: combinator must have a non-empty 'children' list"
        )

    if name == "warn" and len(children_raw) != 1:
        raise MetricSpecError(
            f"{path}.children: 'warn' combinator requires exactly 1 child, "
            f"got {len(children_raw)}"
        )

    children = [
        parse_metric_tree(child, f"{path}.children[{i}]")
        for i, child in enumerate(children_raw)
    ]

    k = 0
    if name == "k-of-n":
        if "k" not in raw:
            raise MetricSpecError(f"{path}.k: 'k-of-n' combinator requires 'k'")
        if not isinstance(raw["k"], int) or raw["k"] <= 0:
            raise MetricSpecError(
                f"{path}.k: must be a positive integer, got {raw['k']!r}"
            )
        k = raw["k"]
        if k > len(children):
            raise MetricSpecError(
                f"{path}.k: k={k} exceeds number of children ({len(children)})"
            )

    return CombinatorSpec(combinator=name, children=children, k=k)


def _parse_leaf(raw: dict, path: str) -> LeafSpec:
    metric = raw["metric"]
    if not isinstance(metric, str) or metric not in VALID_METRICS:
        raise MetricSpecError(
            f"{path}.metric: unknown metric {metric!r}; "
            f"valid: {sorted(VALID_METRICS)}"
        )

    variable = raw.get("variable")
    if not isinstance(variable, str) or not variable:
        raise MetricSpecError(
            f"{path}.variable: required string field"
        )

    # Everything else is metric-specific params (tolerance, tube_rel, ...).
    params = {k: v for k, v in raw.items() if k not in {"metric", "variable"}}
    return LeafSpec(metric=metric, variable=variable, params=params)
