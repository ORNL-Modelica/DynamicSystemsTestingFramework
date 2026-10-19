"""From a comparison and a trajectory to a VariableView — the assembly the
report's figure build and the authoring Session's ``view`` share."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from ..comparison.types import VariableComparison
from .view import VariableView, build_view

LeafPair = tuple[str, VariableComparison]


def leaves_by_variable(metric_tree, card: Collection[str]) -> dict[str, list[LeafPair]]:
    """Every scored leaf a plot draws, keyed by the variable it scores, as
    the ``(leaf_path, comparison)`` pairs ``build_view`` takes. Not the
    leaves at ``card`` — the paths of the leaves on the Statistics card
    (`on_the_card`: a statistic, a listed name's number): numbers on the
    card, curves on plots, so a card leaf's marks are drawn on no plot.
    Empty for ``None``."""
    from ..comparison.tree_eval import leaf_comparisons_by_path

    out: dict[str, list[LeafPair]] = {}
    if metric_tree is None:
        return out
    skip = set(card)
    for path, vc in leaf_comparisons_by_path(metric_tree).items():
        if path not in skip:
            out.setdefault(vc.name, []).append((path, vc))
    return out


def view_from_trajectory(
    name: str, trajectory: dict, leaves: Sequence[LeafPair]
) -> VariableView:
    """The view for one variable from a report-shaped trajectory entry
    (``act_time`` / ``act_values`` / ``ref_time`` / ``ref_values`` /
    ``text`` / ``overlays``, any of them absent).

    The view's label is the trajectory's ``text`` — what the test's page
    reads for the variable — not its ``label``: the plain label belongs to
    the surfaces that show the muted name beside it, and a figure's legend
    and hover show no name."""
    return build_view(
        name,
        label=trajectory.get("text") or trajectory.get("label") or name,
        act_time=trajectory.get("act_time"),
        act_values=trajectory.get("act_values"),
        ref_time=trajectory.get("ref_time"),
        ref_values=trajectory.get("ref_values"),
        overlays=trajectory.get("overlays") or [],
        leaves=leaves,
    )
