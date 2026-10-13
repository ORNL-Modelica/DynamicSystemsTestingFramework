"""MetricTree: composition of metric results with Boolean / weighted combinators.

See docs/vision.md "Metric composition" and docs/extensibility.md §6.

A spec with no ``metrics`` tree collapses to a degenerate flat-AND over
per-variable ``VariableComparison`` results (``implicit_and_tree``), which is
exactly the implicit semantics. An explicit ``metrics`` tree in
``test_spec.json`` reaches the and / or / k-of-n / warn / weighted
combinators below.

Design contract:

* Combinators are pure functions of child ``MetricResult``s. No I/O, no mutation.
* A combinator's output ``diagnostics`` must preserve enough of its children's
  diagnostics that a report can show which branch failed and why.
* Leaves wrap a ``VariableComparison``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

from .comparator import VariableComparison
from .sides import as_written

# ---------------------------------------------------------------------------
# Uniform metric result (combinator output shape)
# ---------------------------------------------------------------------------


@dataclass
class MetricResult:
    """Uniform shape produced by both leaves and combinators.

    ``VariableComparison`` is a richer, reporting-friendly leaf result; this
    is the narrow type combinators pass around. A leaf is adapted to a
    ``MetricResult`` on the way in via :func:`leaf_from_variable`.

    Fields:
      passed      — overall pass/fail after combinator logic.
      score       — numeric score; interpretation is combinator-dependent
                    (min NRMSE for AND, max NRMSE for OR, etc.). Not normalized
                    in general. A value of ``None`` means "no meaningful score"
                    (e.g. after a ``warn`` wrapper).
      label       — short human-readable tag for reports (e.g. a variable name
                    or a combinator name + children count).
      diagnostics — open-ended structured extras. Reports render these.
      children    — empty for leaves; populated for combinator nodes so the
                    tree shape is reconstructible.
    """

    passed: bool
    score: float | None
    label: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    children: list[MetricResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Combinator protocol
# ---------------------------------------------------------------------------


class Combinator(ABC):
    """Strategy interface for combining child metric results into one."""

    #: Short identifier used in config / diagnostics.
    name: str = ""

    @abstractmethod
    def combine(self, children: list[MetricResult]) -> MetricResult: ...


class AndCombinator(Combinator):
    """All children must pass. Score is ``min`` over the children's scores.

    Matches the implicit behavior of a spec with no ``metrics`` tree: a test
    passes iff every variable's metric passes.

    ``min`` is only "the worst child" for lower-is-better metrics. Children
    can mix directions (the registry's ``score_direction`` is ``"lower"``
    for NRMSE-like scores, ``"higher"`` for tube coverage), and this node
    does not consult it — ``passed`` is a pure Boolean AND and is always
    right; the reported score is a summary number, not a ranking.
    """

    name = "and"

    def combine(self, children: list[MetricResult]) -> MetricResult:
        if not children:
            # Vacuously true. Score undefined; pick None rather than lie.
            return MetricResult(passed=True, score=None, label="and[0]", children=[])
        passed = all(c.passed for c in children)
        scores = [c.score for c in children if c.score is not None]
        score = min(scores) if scores else None
        return MetricResult(
            passed=passed,
            score=score,
            label=f"and[{len(children)}]",
            diagnostics={"n_failed": sum(1 for c in children if not c.passed)},
            children=list(children),
        )


class OrCombinator(Combinator):
    """At least one child must pass. Score is ``max`` over the children's scores.

    Same caveat as :class:`AndCombinator`: ``max`` is only "the best child"
    for higher-is-better metrics, and this node does not consult the
    registry's ``score_direction``. ``passed`` (a Boolean OR) is unaffected.
    """

    name = "or"

    def combine(self, children: list[MetricResult]) -> MetricResult:
        if not children:
            # Vacuously false for OR (no children = no passing child).
            return MetricResult(passed=False, score=None, label="or[0]", children=[])
        passed = any(c.passed for c in children)
        scores = [c.score for c in children if c.score is not None]
        score = max(scores) if scores else None
        return MetricResult(
            passed=passed,
            score=score,
            label=f"or[{len(children)}]",
            diagnostics={"n_passed": sum(1 for c in children if c.passed)},
            children=list(children),
        )


class KOfNCombinator(Combinator):
    """At least K of N children must pass."""

    name = "k-of-n"

    def __init__(self, k: int):
        if k < 0:
            raise ValueError("k must be non-negative")
        self.k = k

    def combine(self, children: list[MetricResult]) -> MetricResult:
        n_passed = sum(1 for c in children if c.passed)
        passed = n_passed >= self.k
        scores = [c.score for c in children if c.score is not None]
        # Heuristic: score = K-th best child score (or worst if K > len).
        score: float | None
        if not scores:
            score = None
        else:
            scores_sorted = sorted(scores, reverse=True)
            idx = min(self.k - 1, len(scores_sorted) - 1) if self.k > 0 else 0
            score = scores_sorted[idx] if scores_sorted else None
        return MetricResult(
            passed=passed,
            score=score,
            label=f"k-of-n[{self.k}/{len(children)}]",
            diagnostics={"k": self.k, "n": len(children), "n_passed": n_passed},
            children=list(children),
        )


class WeightedCombinator(Combinator):
    """Weighted sum of child scores against a threshold (4.E).

    Pass condition is direction-aware:
      - ``direction="less"`` (default — NRMSE-like; lower is better):
        ``sum(w_i * score_i) < threshold``.
      - ``direction="greater"`` (tube-like; higher is better):
        ``sum(w_i * score_i) > threshold``.

    All children must produce a numeric score; if any child has ``score=None``,
    the weighted node fails (with a diagnostic explaining why) since the
    aggregate is undefined.
    """

    name = "weighted"

    def __init__(self, weights: list[float], threshold: float, direction: str = "less"):
        if direction not in ("less", "greater"):
            raise ValueError(
                f"weighted: direction must be 'less' or 'greater', got {direction!r}"
            )
        if not weights:
            raise ValueError("weighted: requires at least one weight")
        self.weights = list(weights)
        self.threshold = float(threshold)
        self.direction = direction

    def combine(self, children: list[MetricResult]) -> MetricResult:
        if len(children) != len(self.weights):
            raise ValueError(
                f"weighted: weights ({len(self.weights)}) must match "
                f"children ({len(children)})"
            )
        if any(c.score is None for c in children):
            return MetricResult(
                passed=False,
                score=None,
                label=f"weighted[{len(children)}]",
                diagnostics={
                    "reason": "child has no numeric score; weighted sum undefined",
                    "weights": list(self.weights),
                    "threshold": self.threshold,
                    "direction": self.direction,
                },
                children=list(children),
            )
        # Every child has a numeric score — the guard above returned otherwise.
        weighted_sum = sum(
            w * cast(float, c.score) for w, c in zip(self.weights, children)
        )
        passed = (
            weighted_sum < self.threshold
            if self.direction == "less"
            else weighted_sum > self.threshold
        )
        return MetricResult(
            passed=passed,
            score=weighted_sum,
            label=f"weighted[{len(children)}]",
            diagnostics={
                "weighted_sum": weighted_sum,
                "threshold": self.threshold,
                "direction": self.direction,
                "weights": list(self.weights),
            },
            children=list(children),
        )


class WarnCombinator(Combinator):
    """Single-child wrapper that always passes the parent but surfaces the
    child's diagnostics as warnings.

    Used when an additional baseline comparison is informational (e.g. an
    experimental-data overlay) and should not gate pass/fail. A ``warn`` node
    always reports ``passed=True``; the report renders the child's failure
    (if any) as a warning rather than a failure.
    """

    name = "warn"

    def combine(self, children: list[MetricResult]) -> MetricResult:
        if len(children) != 1:
            raise ValueError(
                f"warn combinator expects exactly 1 child, got {len(children)}"
            )
        child = children[0]
        # Surface child's failure (if any) as a warning in diagnostics; parent
        # passes regardless. Score is intentionally None — a warn branch does
        # not contribute to a meaningful aggregate score.
        warned = not child.passed
        return MetricResult(
            passed=True,
            score=None,
            label="warn",
            diagnostics={"warned": warned, "child_label": child.label},
            children=[child],
        )


# ---------------------------------------------------------------------------
# Leaf adapter + implicit-tree construction
# ---------------------------------------------------------------------------


def leaf_from_variable(vc: VariableComparison) -> MetricResult:
    """Adapt a per-variable ``VariableComparison`` to a leaf ``MetricResult``.

    The numeric ``score`` is chosen per mode:
      * NRMSE    — ``vc.nrmse``                   (lower is better; worst = min)
      * tube     — ``vc.tube_points_inside``      (higher is better; uses OR if
                                                   composed with another tube node)
      * final    — ``vc.nrmse`` (same semantics)

    Callers that need the raw ``VariableComparison`` can recover it from
    ``result.diagnostics['variable']``.
    """
    if vc.mode == "tube" and vc.tube_points_inside is not None:
        score = vc.tube_points_inside
    else:
        score = vc.nrmse
    diagnostics = {
        "mode": vc.mode,
        "tolerance": as_written(vc.tolerance_used),
        "max_abs_error": vc.max_abs_error,
        "variable": vc,
    }
    if vc.against is not None:
        # The baseline the criterion read; none where it reads none.
        diagnostics["against"] = vc.against
    return MetricResult(
        passed=vc.passed,
        score=score,
        label=vc.name,
        diagnostics=diagnostics,
        children=[],
    )


def stamp_leaf_path(leaf: MetricResult, path: str) -> None:
    """Address a leaf result by its RFC 6902 path — the same path a patch op uses.

    Prefixes the stashed ``VariableComparison``'s geometry ids with the path
    so a figure handle and a spec edit share one name. Idempotent: a mode
    emits local ids, and a stamped id starts with ``/``.
    """
    leaf.diagnostics["leaf_path"] = path
    vc = leaf.diagnostics.get("variable")
    if vc is not None:
        vc.geometry = tuple(
            p if p.id.startswith("/") else p.prefixed(path) for p in vc.geometry
        )


def implicit_and_tree(variables: list[VariableComparison]) -> MetricResult:
    """Build the implicit flat-AND tree from a list of per-variable comparisons.

    This is the degenerate MetricTree that matches the current pass/fail
    semantics exactly: a test passes iff every variable passes. Leaves are
    stamped ``/metrics/children/<i>`` — the paths
    :func:`~.tree_spec.synthesize_implicit_tree` produces for the same list,
    so the report can look leaves up by path on both evaluation routes.
    """
    leaves = []
    for i, vc in enumerate(variables):
        leaf = leaf_from_variable(vc)
        stamp_leaf_path(leaf, f"/metrics/children/{i}")
        leaves.append(leaf)
    return AndCombinator().combine(leaves)
