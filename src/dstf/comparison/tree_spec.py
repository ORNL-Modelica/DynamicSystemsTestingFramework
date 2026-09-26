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

from .registry import DEFAULT_METRIC, combinator_names, metric, metric_names


class MetricSpecError(ValueError):
    """Raised on malformed MetricTree spec input: ``path`` says where,
    ``message`` what is wrong. ``str()`` is ``"path: message"`` for the CLI,
    as a validator's ``ValidationError`` reads; the tool shows ``message``
    alone, beside the row it came from."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}" if path else message)


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
    window_start: float | None = None
    window_end: float | None = None


@dataclass
class CombinatorSpec:
    """A combinator node: and/or/k-of-n/warn/weighted over child specs.

    ``k`` is required iff ``combinator == "k-of-n"``. ``warn`` requires
    exactly one child; the others require at least one. ``weighted`` (4.E)
    requires ``weights`` (list parallel to children), ``threshold``, and an
    optional ``direction`` (``"less"`` default, or ``"greater"``).
    """

    combinator: str
    children: list[LeafSpec | CombinatorSpec]
    k: int = 0  # only meaningful for k-of-n
    # Weighted-only fields (empty/zero for other combinators).
    weights: list[float] = field(default_factory=list)
    threshold: float = 0.0
    direction: str = "less"

    def __post_init__(self):
        if self.combinator == "k-of-n" and self.k <= 0:
            raise MetricSpecError(
                "", f"k-of-n combinator requires positive 'k'; got {self.k}"
            )
        if self.combinator == "weighted":
            if len(self.weights) != len(self.children):
                raise MetricSpecError(
                    "",
                    f"weighted combinator: weights ({len(self.weights)}) "
                    f"must match children ({len(self.children)})",
                )
            if self.direction not in ("less", "greater"):
                raise MetricSpecError(
                    "",
                    f"weighted combinator: direction must be 'less' or 'greater', "
                    f"got {self.direction!r}",
                )


SpecNode = LeafSpec | CombinatorSpec


def collect_leaf_paths(spec: SpecNode, *, root: str = "/metrics") -> list[str]:
    """Return RFC 6901 JSON-Pointer paths for each leaf in tree order.

    Order matches :func:`tree_eval.collect_leaf_variables` (pre-order walk)
    and the paths the evaluator stamps on results. Paths are entry-relative
    — an RFC 6902 patch op uses them directly (the whitelist in
    :mod:`patch_apply` covers ``/metrics``).

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


def resolves_a_variable(leaf: LeafSpec) -> bool:
    """Does this leaf score a variable of the RESULT?

    False for a stored-scalar leaf — a metric that accepts ``quantity``
    given a string one reads its numbers from the run's statistics or
    diagnostics, and its ``variable`` is free text naming that leaf, not a
    series anything resolves. Treated as a variable it registered
    ``CPUtime`` for the expression soft-check, which then compared a
    baseline's recorded expression against a quantity nothing measured.
    """
    desc = metric(leaf.metric)
    return not (
        desc is not None
        and desc.accepts_quantity
        and isinstance(leaf.params.get("quantity"), str)
    )


def collect_variables_by_baseline(spec: SpecNode) -> dict[str, list[str]]:
    """``{baseline name: the variables its leaves score}``, in tree order.

    Unique on both sides, and ``"primary"`` is a key whenever a leaf takes
    the default. The comparator reads it to run the expression soft-check
    on every baseline a tree actually uses — once per PAIR, not per leaf
    and not over every variable of the result: a baseline records the
    expressions of the variables it holds, and a variable no leaf scores
    against it is not being compared with it at all. A leaf that resolves
    no variable (:func:`resolves_a_variable`) contributes none.
    """
    out: dict[str, list[str]] = {}
    for leaf, _ in _walk_leaves_with_paths(spec, "/metrics"):
        if not resolves_a_variable(leaf):
            continue
        names = out.setdefault(leaf.against, [])
        if leaf.variable not in names:
            names.append(leaf.variable)
    return out


def leaves_for_variable(
    spec: SpecNode,
    variable: str,
    *,
    root: str = "/metrics",
) -> list[tuple[LeafSpec, str]]:
    """Return ``(leaf, json_pointer)`` pairs for every leaf targeting ``variable``.

    Pre-order walk; same ordering as :func:`collect_leaf_paths`. Used by
    the per-variable plot section to render only the leaves whose subtree
    membership touches that variable.
    """
    return [
        (leaf, path)
        for leaf, path in _walk_leaves_with_paths(spec, root)
        if leaf.variable == variable
    ]


def _walk_leaves_with_paths(
    node: SpecNode,
    path: str,
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
    evaluation_by_path: dict[str, dict] | None = None,
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
    node: SpecNode,
    path: str,
    eval_by_path: dict[str, dict],
) -> dict:
    view: dict = {"path": path}
    if isinstance(node, LeafSpec):
        view.update(
            {
                "kind": "leaf",
                "metric": node.metric,
                "variable": node.variable,
                "params": dict(node.params),
                "against": node.against,
                "window": _window_view(node),
                "children": [],
            }
        )
    else:
        view.update(
            {
                "kind": "combinator",
                "combinator": node.combinator,
                "children": [
                    _spec_to_view_recursive(c, f"{path}/children/{i}", eval_by_path)
                    for i, c in enumerate(node.children)
                ],
            }
        )
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


def spec_to_raw(node: SpecNode) -> dict:
    """Serialize a ``SpecNode`` back to the raw ``metrics`` JSON shape.

    Inverse of :func:`parse_metric_tree` — the output round-trips through
    it. Added for review 2026-07-06 finding 41: ``cmd_spec_update`` uses
    this to MATERIALIZE the implicit tree (from
    :func:`synthesize_implicit_tree`) into a flat-override test entry
    before applying reporter-emitted per-leaf patch ops like
    ``/metrics/children/0/tolerance`` — without it, no scalar edit on a
    flat-override test could round-trip.
    """
    if isinstance(node, LeafSpec):
        out: dict = {"metric": node.metric, "variable": node.variable}
        out.update(node.params)
        if node.against != "primary":
            out["against"] = node.against
        window = _window_view(node)
        if window:
            out["window"] = window
        return out
    raw: dict = {
        "combinator": node.combinator,
        "children": [spec_to_raw(c) for c in node.children],
    }
    if node.combinator == "k-of-n":
        raw["k"] = node.k
    if node.combinator == "weighted":
        raw["weights"] = list(node.weights)
        raw["threshold"] = node.threshold
        if node.direction != "less":
            raw["direction"] = node.direction
    return raw


def synthesize_implicit_tree(
    variables: list[str],
    *,
    variable_overrides: dict[str, dict] | None = None,
    base_tolerance: float | None = None,
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
        # The override-dict ``mode`` value and the spec ``metric``
        # discriminator are the same identifier — no translation table, just
        # a registry membership check. Absent or unrecognized falls back to
        # the default metric, matching the flat-override scoring path.
        mode_key = ov.pop("mode", None)
        metric_name = mode_key if mode_key and metric(mode_key) else DEFAULT_METRIC
        if base_tolerance is not None and "tolerance" not in ov:
            ov["tolerance"] = float(base_tolerance)
        leaves.append(LeafSpec(metric=metric_name, variable=var, params=ov))
    return CombinatorSpec(combinator="and", children=list(leaves))


def parse_metric_tree(raw: dict, _path: str = "metrics") -> SpecNode:
    """Parse a MetricTree spec dict into the typed form.

    Raises ``MetricSpecError`` with a path-bearing message on any structural
    or semantic problem (unknown combinator, missing field, wrong type).
    """
    if not isinstance(raw, dict):
        raise MetricSpecError(_path, f"expected an object, got {type(raw).__name__}")

    has_combinator = "combinator" in raw
    has_metric = "metric" in raw
    if has_combinator and has_metric:
        raise MetricSpecError(
            _path, "node has both 'combinator' and 'metric' keys; pick one"
        )
    if not has_combinator and not has_metric:
        raise MetricSpecError(
            _path,
            "node must have either 'combinator' (internal) or 'metric' (leaf)",
        )

    if has_combinator:
        return _parse_combinator(raw, _path)
    return _parse_leaf(raw, _path)


def _parse_combinator(raw: dict, path: str) -> CombinatorSpec:
    name = raw["combinator"]
    if not isinstance(name, str) or name not in combinator_names():
        raise MetricSpecError(
            f"{path}.combinator",
            f"unknown combinator {name!r}; valid: {sorted(combinator_names())}",
        )

    children_raw = raw.get("children")
    if not isinstance(children_raw, list) or not children_raw:
        raise MetricSpecError(
            f"{path}.children", "combinator must have a non-empty 'children' list"
        )

    if name == "warn" and len(children_raw) != 1:
        raise MetricSpecError(
            f"{path}.children",
            f"'warn' combinator requires exactly 1 child, got {len(children_raw)}",
        )

    children = [
        parse_metric_tree(child, f"{path}.children[{i}]")
        for i, child in enumerate(children_raw)
    ]

    k = 0
    if name == "k-of-n":
        if "k" not in raw:
            raise MetricSpecError(f"{path}.k", "'k-of-n' combinator requires 'k'")
        if not isinstance(raw["k"], int) or raw["k"] <= 0:
            raise MetricSpecError(
                f"{path}.k", f"must be a positive integer, got {raw['k']!r}"
            )
        k = raw["k"]
        if k > len(children):
            left = "criterion is" if len(children) == 1 else "criteria are"
            raise MetricSpecError(
                f"{path}.k",
                f"k is {k} but only {len(children)} {left} left under this k-of-n.",
            )

    weights: list[float] = []
    threshold = 0.0
    direction = "less"
    if name == "weighted":
        weights_raw = raw.get("weights")
        if not isinstance(weights_raw, list):
            raise MetricSpecError(
                f"{path}.weights", "'weighted' combinator requires a list of weights"
            )
        if len(weights_raw) != len(children):
            n_w, n_c = len(weights_raw), len(children)
            raise MetricSpecError(
                f"{path}.weights",
                f"{n_w} weight{'' if n_w == 1 else 's'} for {n_c} "
                f"{'criterion' if n_c == 1 else 'criteria'}: a weighted node "
                f"takes one weight per criterion.",
            )
        try:
            weights = [float(w) for w in weights_raw]
        except (TypeError, ValueError) as exc:
            raise MetricSpecError(
                f"{path}.weights", f"all weights must be numeric ({exc})"
            ) from exc
        if "threshold" not in raw:
            raise MetricSpecError(
                f"{path}.threshold", "'weighted' combinator requires 'threshold'"
            )
        try:
            threshold = float(raw["threshold"])
        except (TypeError, ValueError) as exc:
            raise MetricSpecError(
                f"{path}.threshold", f"must be numeric, got {raw['threshold']!r}"
            ) from exc
        direction = raw.get("direction", "less")
        if direction not in ("less", "greater"):
            raise MetricSpecError(
                f"{path}.direction", f"must be 'less' or 'greater', got {direction!r}"
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
    metric_name = raw["metric"]
    if not isinstance(metric_name, str) or metric_name not in metric_names():
        raise MetricSpecError(
            f"{path}.metric",
            f"unknown metric {metric_name!r}; valid: {sorted(metric_names())}",
        )

    variable = raw.get("variable")
    quantity = raw.get("quantity")
    desc = metric(metric_name)
    if (
        variable is None
        and desc is not None
        and desc.accepts_quantity
        and isinstance(quantity, str)
        and quantity
    ):
        # For a metric that declares ``accepts_quantity``, a string quantity
        # names a stored scalar (statistics path / diagnostics summary), so
        # the leaf has no tracked series to name — the path doubles as the
        # display label. An explicit ``variable`` alongside it stays as the
        # label the user chose. Every other metric scores a trajectory and
        # still requires ``variable``.
        variable = quantity
    if not isinstance(variable, str) or not variable:
        raise MetricSpecError(f"{path}.variable", "required string field")

    # ``against`` picks which named baseline the leaf scores against.
    # Defaults to "primary"; must be a non-empty string if present.
    against = raw.get("against", "primary")
    if not isinstance(against, str) or not against:
        raise MetricSpecError(
            f"{path}.against", f"must be a non-empty string, got {against!r}"
        )

    # ``window`` scopes the leaf to a sub-interval [start, end] (idea #46).
    # Both endpoints optional; open-ended on either side supported. The
    # comparator slices before calling mode.compare so every metric
    # composes uniformly with the window.
    window_start: float | None = None
    window_end: float | None = None
    window_raw = raw.get("window")
    if window_raw is not None:
        if not isinstance(window_raw, dict):
            raise MetricSpecError(
                f"{path}.window",
                f"expected an object with 'start'/'end', "
                f"got {type(window_raw).__name__}",
            )
        if "start" in window_raw:
            window_start = float(window_raw["start"])
        if "end" in window_raw:
            window_end = float(window_raw["end"])
        if (
            window_start is not None
            and window_end is not None
            and window_end <= window_start
        ):
            raise MetricSpecError(
                f"{path}.window",
                f"The window must end after it starts: it starts at {window_start} "
                f"and ends at {window_end}.",
            )

    # Everything else is metric-specific params (tolerance, tube_rel, ...).
    params = {
        k: v
        for k, v in raw.items()
        if k not in {"metric", "variable", "against", "window"}
    }
    return LeafSpec(
        metric=metric_name,
        variable=variable,
        params=params,
        against=against,
        window_start=window_start,
        window_end=window_end,
    )
