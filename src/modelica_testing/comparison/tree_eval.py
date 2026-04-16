"""Evaluate a parsed MetricTree spec against simulation + reference data.

Takes a :class:`SpecNode` from :mod:`tree_spec` plus the per-variable
simulation result and reference dicts, and walks the tree to produce an
evaluated :class:`MetricResult` root. Phase 3.3 is the first consumer of
the spec form parsed in Phase 3.2.

Leaves evaluate via the existing :func:`resolve_mode` factory — the spec
is just a declarative envelope around the same per-variable comparison
that :func:`compare_test` already does in the implicit-AND path. Internal
nodes map ``spec.combinator`` to the concrete ``Combinator`` classes in
:mod:`metric_tree`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..simulators import VariableResult
from .comparator import VariableComparison
from .metric_tree import (
    AndCombinator,
    Combinator,
    KOfNCombinator,
    MetricResult,
    OrCombinator,
    WarnCombinator,
    leaf_from_variable,
)
from .modes import resolve_mode
from .tree_spec import CombinatorSpec, LeafSpec, SpecNode

PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class BaselineView:
    """Pre-processed view of one baseline for leaf evaluation.

    Packages the per-variable reference dicts and the shared time vector
    (or ``None`` when each variable carries its own time). Multiple named
    baselines can coexist in the evaluator's input (``{"primary": ...,
    "experiment": ...}``) — Phase 4.A.2 adds a leaf-level ``against``
    field to pick which one a given leaf scores against.
    """

    name: str
    ref_vars_by_name: dict[str, dict]
    shared_ref_time: Optional[np.ndarray] = None

# Spec-level metric names → the discriminator value in the override dict
# consumed by resolve_mode. NRMSE is the default there (no "mode" key).
_METRIC_TO_MODE_KEY = {
    "nrmse": None,
    "tube": "tube",
    "final-only": "final_only",
    "range": "range",
}


def evaluate_spec(
    spec: SpecNode,
    var_results_by_name: dict[str, VariableResult],
    baselines: dict[str, BaselineView],
    base_tolerance: float,
) -> MetricResult:
    """Walk a spec tree and produce an evaluated :class:`MetricResult` root.

    ``base_tolerance`` is used when a leaf's params omit ``tolerance`` (the
    per-test / config / default chain resolved by the caller).

    ``baselines`` maps baseline name (``"primary"``, ``"experiment"``, ...)
    to a :class:`BaselineView`. Today every leaf scores against ``"primary"``
    — a future ``against`` field on :class:`LeafSpec` (Phase 4.A.2) will
    let a leaf pick a different baseline.
    """
    if isinstance(spec, LeafSpec):
        return _evaluate_leaf(spec, var_results_by_name, baselines, base_tolerance)

    children = [
        evaluate_spec(c, var_results_by_name, baselines, base_tolerance)
        for c in spec.children
    ]
    return _build_combinator(spec).combine(children)


def collect_leaf_variables(tree: MetricResult) -> list[VariableComparison]:
    """Return the ``VariableComparison`` stashed on each leaf, in tree order.

    ``leaf_from_variable`` puts the originating ``VariableComparison`` in
    ``diagnostics['variable']``. This helper walks the tree and collects
    them so the reporter (which still takes a flat list) sees the
    variables referenced by the user's tree.
    """
    out: list[VariableComparison] = []
    _walk_leaves(tree, out)
    return out


def to_view(tree: MetricResult) -> dict:
    """Serialize a ``MetricResult`` tree into a JSON-safe nested dict
    for Jinja consumption. Shape is render-oriented — no simulation data,
    no numpy arrays.

    Fields per node:
      * ``kind``      — "leaf" | "combinator"
      * ``passed``    — bool
      * ``label``     — human-readable (variable name for leaves,
                        "and[N]" / "or[N]" / "warn" / "k-of-n[K/N]" for combinators)
      * ``score``     — float or None
      * ``mode``      — metric mode on leaves (nrmse / tube / final_only)
      * ``tolerance`` — on leaves, the tolerance applied
      * ``warned``    — True on ``warn`` nodes whose child failed
      * ``children``  — recursive list (empty for leaves)
    """
    node: dict = {
        "kind": "leaf" if not tree.children else "combinator",
        "passed": bool(tree.passed),
        "label": tree.label,
        "score": tree.score,
        "children": [to_view(c) for c in tree.children],
    }
    diag = tree.diagnostics or {}
    if not tree.children:
        # Leaf diagnostics come from leaf_from_variable + _evaluate_leaf
        if "mode" in diag:
            node["mode"] = diag["mode"]
        if "tolerance" in diag:
            node["tolerance"] = diag["tolerance"]
        if "max_abs_error" in diag:
            node["max_abs_error"] = diag["max_abs_error"]
        if "against" in diag:
            node["against"] = diag["against"]
    else:
        # Combinator diagnostics: n_failed / n_passed / k / n / warned
        for key in ("n_failed", "n_passed", "k", "n", "warned"):
            if key in diag:
                node[key] = diag[key]
    return node


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _walk_leaves(node: MetricResult, out: list[VariableComparison]) -> None:
    if not node.children:
        vc = node.diagnostics.get("variable")
        if vc is not None:
            out.append(vc)
        return
    for child in node.children:
        _walk_leaves(child, out)


def _build_combinator(spec: CombinatorSpec) -> Combinator:
    name = spec.combinator
    if name == "and":
        return AndCombinator()
    if name == "or":
        return OrCombinator()
    if name == "k-of-n":
        return KOfNCombinator(spec.k)
    if name == "warn":
        return WarnCombinator()
    # tree_spec validates on parse; this is just a defensive fallback.
    raise ValueError(f"Unknown combinator: {name!r}")


def _evaluate_leaf(
    leaf: LeafSpec,
    var_results_by_name: dict[str, VariableResult],
    baselines: dict[str, BaselineView],
    base_tolerance: float,
) -> MetricResult:
    var_result = var_results_by_name.get(leaf.variable)
    # Phase 4.A.2: leaf.against picks the named baseline. Missing name →
    # hard fail with a clear label (loud on user-facing reports).
    baseline = baselines.get(leaf.against)
    if baseline is None:
        return leaf_from_variable(_missing_baseline_comparison(leaf))
    ref_var = baseline.ref_vars_by_name.get(leaf.variable)

    if var_result is None or ref_var is None:
        # A leaf referencing a variable that's missing from either side is
        # a hard fail. Build a sentinel VariableComparison so reports can
        # render it the same way they render other failures.
        return leaf_from_variable(_missing_variable_comparison(leaf.variable, var_result))

    if baseline.shared_ref_time is not None:
        ref_time = baseline.shared_ref_time
    else:
        ref_time = np.array(ref_var["time"])
    ref_values = np.array(ref_var["values"])

    override = _leaf_override_dict(leaf)
    tolerance = float(leaf.params.get("tolerance", base_tolerance))
    mode = resolve_mode(override, tolerance, default_final_only=False)

    vc = mode.compare(ref_time, ref_values, var_result.time, var_result.values)
    vc.index = var_result.index
    vc.name = leaf.variable
    vc.tolerance_used = tolerance
    leaf_result = leaf_from_variable(vc)
    # Record which baseline this leaf scored against, so the reporter can
    # show "against=experiment" on non-primary leaves.
    leaf_result.diagnostics["against"] = leaf.against
    return leaf_result


def _leaf_override_dict(leaf: LeafSpec) -> dict:
    """Translate a LeafSpec to the flat override dict resolve_mode expects."""
    override = dict(leaf.params)
    mode_key = _METRIC_TO_MODE_KEY.get(leaf.metric)
    if mode_key is not None:
        override["mode"] = mode_key
    return override


def _missing_baseline_comparison(leaf: LeafSpec) -> VariableComparison:
    """Build a hard-fail sentinel for a leaf referencing an unknown baseline."""
    return VariableComparison(
        index=0,
        name=f"{leaf.variable} (against={leaf.against!r}: baseline not found)",
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
    )


def _missing_variable_comparison(
    name: str,
    var_result: Optional[VariableResult],
) -> VariableComparison:
    actual_final = float("nan")
    index = 0
    if var_result is not None and len(var_result.values) > 0:
        actual_final = float(var_result.values[-1])
        index = var_result.index
    return VariableComparison(
        index=index,
        name=name,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=actual_final,
    )
