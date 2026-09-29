"""A plot's criteria rows, read off a `tree_view`: the ONE walk both tiers
list a plot's criteria by — the report's plot sections and the tool's plot
cards. Here, not under `authoring/`, because the report draws from it too
and the report never imports the tool; the tool imports this. Every
function takes a `tree_view` node (``kind``, ``path``, ``combinator``,
``children``), never a raw spec node."""

from __future__ import annotations

from collections.abc import Collection


def leaves_of(view: dict | None) -> list[dict]:
    """The leaf nodes of a `tree_view`, in tree order."""
    if view is None:
        return []
    if view.get("kind") == "leaf":
        return [view]
    return [leaf for child in view.get("children") or [] for leaf in leaves_of(child)]


def names_a_series(leaf: dict) -> bool:
    """Does this leaf of a `tree_view` score a SERIES of the result?
    `resolves_a_variable` over the view's dict: a stored-scalar leaf's
    ``variable`` is free text naming the leaf, and a plot of a variable it
    happens to spell showed that leaf as its criterion."""
    from ..comparison.tree_spec import reads_a_statistic

    return not reads_a_statistic(str(leaf.get("metric")), leaf.get("params") or {})


def ancestor_kinds(view: dict | None) -> dict[str, tuple[str, ...]]:
    """Each node's path -> its ancestors' combinators, the root's first:
    where in the tree a criteria row's leaf sits, which its row is led by
    (``and › k-of-n ›``). A plot's rows are its subject's leaves, so the
    tree's structure is read off the row, not off the order of the rows."""
    out: dict[str, tuple[str, ...]] = {}

    def walk(node: dict, above: tuple[str, ...]) -> None:
        out[str(node.get("path"))] = above
        kind = node.get("combinator")
        for child in node.get("children") or []:
            walk(child, above + ((str(kind),) if kind else ()))

    if view is not None:
        walk(view, ())
    return out


def crumb_text(kinds: tuple[str, ...]) -> str:
    """``and › k-of-n ›`` — a row's breadcrumb; empty for a root leaf."""
    return " ".join(f"{kind} ›" for kind in kinds)


def criteria_rows(view: dict | None, subject: Collection[str]) -> list[dict]:
    """A plot's criteria: the leaves of ``view`` on a member of
    ``subject``, in TREE order, each with ``crumbs`` — its ancestors'
    kinds, the root's first (``and › k-of-n ›``). The tree's structure is
    read off a row, not off the order of the rows: a family's rows are
    where the tree has them. A stored-scalar leaf is no row of any plot
    (:func:`names_a_series`): its ``variable`` names the leaf, not a
    series. Both tiers list a plot's criteria by this."""
    wanted = set(subject)
    kinds = ancestor_kinds(view)
    return [
        {
            "leaf": leaf,
            "variable": leaf.get("variable"),
            "crumbs": crumb_text(kinds.get(str(leaf.get("path")), ())),
        }
        for leaf in leaves_of(view)
        if leaf.get("variable") in wanted and names_a_series(leaf)
    ]
