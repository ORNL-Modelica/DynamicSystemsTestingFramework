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

VALID_COMBINATORS = frozenset({"and", "or", "k-of-n", "warn", "weighted"})
VALID_METRICS = frozenset({
    "nrmse", "tube", "final-only", "range",
    "event-timing", "dominant-frequency",
})


class MetricSpecError(ValueError):
    """Raised on malformed MetricTree spec input. Message includes the path."""


@dataclass
class LeafSpec:
    """A single per-variable metric evaluation.

    ``params`` carries metric-specific knobs (tolerance, tube_rel, tube_points,
    tube_width_mode, ...). The shape mirrors the existing
    ``variable_overrides`` payload in test_spec.json so the same tube/NRMSE
    parameter names work in both contexts.

    ``against`` selects which named baseline the leaf scores against (Phase
    4.A.2). Defaults to ``"primary"`` — the baseline stored at the flat top
    level of the reference file. Non-primary names (``"experiment"``,
    ``"analytical"``, ...) reference entries under the reference's
    ``baselines`` map. Unknown names are rejected at evaluation time.
    """

    metric: str
    variable: str
    params: dict = field(default_factory=dict)
    against: str = "primary"


@dataclass
class CombinatorSpec:
    """A combinator node: and/or/k-of-n/warn/weighted over child specs.

    ``k`` is required iff ``combinator == "k-of-n"``. ``warn`` requires
    exactly one child; the others require at least one. ``weighted`` (4.E)
    requires ``weights`` (list parallel to children), ``threshold``, and an
    optional ``direction`` (``"less"`` default, or ``"greater"``).
    """

    combinator: str
    children: list[Union["LeafSpec", "CombinatorSpec"]]
    k: int = 0  # only meaningful for k-of-n
    # Weighted-only fields (empty/zero for other combinators).
    weights: list[float] = field(default_factory=list)
    threshold: float = 0.0
    direction: str = "less"

    def __post_init__(self):
        if self.combinator == "k-of-n" and self.k <= 0:
            raise MetricSpecError(
                f"k-of-n combinator requires positive 'k'; got {self.k}"
            )
        if self.combinator == "weighted":
            if len(self.weights) != len(self.children):
                raise MetricSpecError(
                    f"weighted combinator: weights ({len(self.weights)}) "
                    f"must match children ({len(self.children)})"
                )
            if self.direction not in ("less", "greater"):
                raise MetricSpecError(
                    f"weighted combinator: direction must be 'less' or 'greater', "
                    f"got {self.direction!r}"
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

    weights: list[float] = []
    threshold = 0.0
    direction = "less"
    if name == "weighted":
        weights_raw = raw.get("weights")
        if not isinstance(weights_raw, list):
            raise MetricSpecError(
                f"{path}.weights: 'weighted' combinator requires a list of weights"
            )
        if len(weights_raw) != len(children):
            raise MetricSpecError(
                f"{path}.weights: length ({len(weights_raw)}) must match "
                f"children ({len(children)})"
            )
        try:
            weights = [float(w) for w in weights_raw]
        except (TypeError, ValueError) as exc:
            raise MetricSpecError(
                f"{path}.weights: all weights must be numeric ({exc})"
            )
        if "threshold" not in raw:
            raise MetricSpecError(
                f"{path}.threshold: 'weighted' combinator requires 'threshold'"
            )
        try:
            threshold = float(raw["threshold"])
        except (TypeError, ValueError):
            raise MetricSpecError(
                f"{path}.threshold: must be numeric, got {raw['threshold']!r}"
            )
        direction = raw.get("direction", "less")
        if direction not in ("less", "greater"):
            raise MetricSpecError(
                f"{path}.direction: must be 'less' or 'greater', got {direction!r}"
            )

    return CombinatorSpec(
        combinator=name,
        children=children,
        k=k,
        weights=weights,
        threshold=threshold,
        direction=direction,
    )


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

    # ``against`` picks which named baseline the leaf scores against.
    # Defaults to "primary"; must be a non-empty string if present.
    against = raw.get("against", "primary")
    if not isinstance(against, str) or not against:
        raise MetricSpecError(
            f"{path}.against: must be a non-empty string, got {against!r}"
        )

    # Everything else is metric-specific params (tolerance, tube_rel, ...).
    params = {
        k: v for k, v in raw.items()
        if k not in {"metric", "variable", "against"}
    }
    return LeafSpec(metric=metric, variable=variable, params=params, against=against)
