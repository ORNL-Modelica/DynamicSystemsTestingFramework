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
from typing import Optional, Union

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

    ``window_start`` / ``window_end`` (idea #46, Phase 6.1.1) scope the
    leaf to a sub-interval of the trajectory. Both optional; when set, the
    leaf evaluates only against samples with ``window_start <= t <= window_end``.
    Uniform across every metric — mode configs stay untouched, slicing
    happens in :func:`tree_eval._evaluate_leaf` before ``mode.compare``.
    """

    metric: str
    variable: str
    params: dict = field(default_factory=dict)
    against: str = "primary"
    window_start: Optional[float] = None
    window_end: Optional[float] = None


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


def collect_leaf_paths(spec: SpecNode, *, root: str = "/metrics") -> list[str]:
    """Return RFC 6901 JSON-Pointer paths for each leaf in tree order.

    Order matches :func:`tree_eval.collect_leaf_variables` (pre-order walk)
    so callers can zip the two lists element-wise. Paths are entry-relative
    — the reporter's ``buildPatchData`` uses them directly in RFC 6902
    patch ops (the whitelist in :mod:`patch_apply` covers ``/metrics``).

    Example: a two-leaf AND under the root yields
    ``["/metrics/children/0", "/metrics/children/1"]``; a single-leaf root
    yields ``["/metrics"]``.
    """
    out: list[str] = []
    _collect_leaf_paths(spec, root, out)
    return out


def _collect_leaf_paths(node: SpecNode, path: str, out: list[str]) -> None:
    if isinstance(node, LeafSpec):
        out.append(path)
        return
    for i, child in enumerate(node.children):
        _collect_leaf_paths(child, f"{path}/children/{i}", out)


def collect_variables(spec: SpecNode) -> list[str]:
    """Return unique variable names referenced by the tree, in tree order.

    Drives the reporter's per-variable plot grouping: one plot per unique
    variable, with the variable's leaves rendered as the plot's interactive
    nodes. First-occurrence ordering matches the JSON spec layout users see.
    """
    seen: set[str] = set()
    out: list[str] = []
    for leaf, _ in _walk_leaves_with_paths(spec, "/metrics"):
        if leaf.variable not in seen:
            seen.add(leaf.variable)
            out.append(leaf.variable)
    return out


def leaves_for_variable(
    spec: SpecNode, variable: str, *, root: str = "/metrics",
) -> list[tuple[LeafSpec, str]]:
    """Return ``(leaf, json_pointer)`` pairs for every leaf targeting ``variable``.

    Pre-order walk; same ordering as :func:`collect_leaf_paths`. Used by
    the per-variable plot section to render only the leaves whose subtree
    membership touches that variable.
    """
    return [
        (leaf, path) for leaf, path in _walk_leaves_with_paths(spec, root)
        if leaf.variable == variable
    ]


def _walk_leaves_with_paths(
    node: SpecNode, path: str,
):
    if isinstance(node, LeafSpec):
        yield node, path
        return
    for i, child in enumerate(node.children):
        yield from _walk_leaves_with_paths(child, f"{path}/children/{i}")


def spec_to_view(
    node: SpecNode,
    *,
    root: str = "/metrics",
    evaluation_by_path: Optional[dict[str, dict]] = None,
) -> dict:
    """Serialize a ``SpecNode`` to a JSON-safe dict with paths + evaluation.

    The reporter's Stage-2 recursive UI component consumes this as its
    single source of truth. Each node carries:

      * ``kind``       — "leaf" | "combinator"
      * ``path``       — RFC 6901 JSON-Pointer (e.g. ``/metrics/children/0``)
      * ``combinator`` / ``k`` / ``weights`` / ``threshold`` / ``direction``
        — combinator-only fields
      * ``metric`` / ``variable`` / ``params`` / ``against`` / ``window``
        — leaf-only fields
      * ``children``   — recursive list (empty for leaves)
      * ``passed`` / ``score`` / ``label`` — merged from ``evaluation_by_path``
        when supplied; omitted otherwise

    ``evaluation_by_path`` is expected to be a flat dict keyed by JSON-Pointer,
    produced by :func:`flatten_evaluation`. Missing keys are tolerated — a
    spec view without an evaluation renders as read-only structure.
    """
    eval_by_path = evaluation_by_path or {}
    return _spec_to_view_recursive(node, root, eval_by_path)


def _spec_to_view_recursive(
    node: SpecNode, path: str, eval_by_path: dict[str, dict],
) -> dict:
    view: dict = {"path": path}
    if isinstance(node, LeafSpec):
        view.update({
            "kind": "leaf",
            "metric": node.metric,
            "variable": node.variable,
            "params": dict(node.params),
            "against": node.against,
            "window": _window_view(node),
            "children": [],
        })
    else:
        view.update({
            "kind": "combinator",
            "combinator": node.combinator,
            "children": [
                _spec_to_view_recursive(c, f"{path}/children/{i}", eval_by_path)
                for i, c in enumerate(node.children)
            ],
        })
        if node.combinator == "k-of-n":
            view["k"] = node.k
        if node.combinator == "weighted":
            view["weights"] = list(node.weights)
            view["threshold"] = node.threshold
            view["direction"] = node.direction

    eval_entry = eval_by_path.get(path)
    if eval_entry:
        for key in ("passed", "score", "label"):
            if key in eval_entry:
                view[key] = eval_entry[key]
    return view


def _window_view(leaf: LeafSpec) -> dict:
    """Return the leaf's window as a JSON-ready dict (``{}`` when unset)."""
    out: dict = {}
    if leaf.window_start is not None:
        out["start"] = float(leaf.window_start)
    if leaf.window_end is not None:
        out["end"] = float(leaf.window_end)
    return out


def synthesize_implicit_tree(
    variables: list[str],
    *,
    variable_overrides: Optional[dict[str, dict]] = None,
    base_tolerance: Optional[float] = None,
) -> SpecNode:
    """Build a render-only ``SpecNode`` for tests that don't author a tree.

    The flat-override path (no ``metrics`` block in test_spec.json) scores
    via the implicit AND of per-variable comparisons. The reporter's
    recursive UI works against ``SpecNode`` trees, so we synthesize one
    here. The synthesized tree is **render-only** — it's not written back
    to the spec until the user makes a structural edit (Stage 4 / option
    ii: no silent spec rewrites).

    Each variable gets one leaf in the order given. The leaf metric is
    derived from ``variable_overrides[var].mode`` (defaults to ``"nrmse"``);
    other override keys flow into the leaf's ``params`` so the controls
    pre-fill correctly. Always wraps in an ``and`` combinator — matches
    :func:`metric_tree.implicit_and_tree`'s shape so evaluation results
    can be merged into the view by JSON-Pointer path without special-
    casing the singleton.
    """
    overrides = variable_overrides or {}
    leaves: list[LeafSpec] = []
    for var in variables:
        ov = dict(overrides.get(var, {}))
        mode_key = ov.pop("mode", None)
        metric = _MODE_TO_METRIC.get(mode_key, "nrmse")
        if base_tolerance is not None and "tolerance" not in ov:
            ov["tolerance"] = float(base_tolerance)
        leaves.append(LeafSpec(metric=metric, variable=var, params=ov))
    return CombinatorSpec(combinator="and", children=list(leaves))


# Inverse of tree_eval._METRIC_TO_MODE_KEY — translates the override-dict
# ``mode`` value back into the spec ``metric`` discriminator. Kept here
# (not in tree_eval) because synthesis is a tree_spec responsibility.
_MODE_TO_METRIC = {
    None: "nrmse",
    "nrmse": "nrmse",
    "tube": "tube",
    "final_only": "final-only",
    "range": "range",
    "event-timing": "event-timing",
    "dominant-frequency": "dominant-frequency",
}


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

    # ``window`` scopes the leaf to a sub-interval [start, end] (idea #46).
    # Both endpoints optional; open-ended on either side supported. The
    # comparator slices before calling mode.compare so every metric
    # composes uniformly with the window.
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    window_raw = raw.get("window")
    if window_raw is not None:
        if not isinstance(window_raw, dict):
            raise MetricSpecError(
                f"{path}.window: expected an object with 'start'/'end', "
                f"got {type(window_raw).__name__}"
            )
        if "start" in window_raw:
            window_start = float(window_raw["start"])
        if "end" in window_raw:
            window_end = float(window_raw["end"])
        if (window_start is not None and window_end is not None
                and window_end <= window_start):
            raise MetricSpecError(
                f"{path}.window: end ({window_end}) must be > start ({window_start})"
            )

    # Everything else is metric-specific params (tolerance, tube_rel, ...).
    params = {
        k: v for k, v in raw.items()
        if k not in {"metric", "variable", "against", "window"}
    }
    return LeafSpec(
        metric=metric, variable=variable, params=params, against=against,
        window_start=window_start, window_end=window_end,
    )
