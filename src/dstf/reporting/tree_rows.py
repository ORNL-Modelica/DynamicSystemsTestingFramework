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


def names_a_series(leaf: dict, diagnostics: Collection[str]) -> bool:
    """Is this leaf of a `tree_view` a plot's criterion? Not one on the
    Statistics card (`on_the_card_view`; ``diagnostics``, the names the
    test lists): a stored-scalar leaf's ``variable`` is free text naming
    the leaf, and a plot of a variable it happens to spell showed that
    leaf as its criterion; a listed name's number is read from its
    summary, and the report, which has no card, lists it in its metric
    tree alone — as it always listed a statistic's."""
    from ..comparison.tree_spec import on_the_card_view

    return not on_the_card_view(leaf, diagnostics)


def card_paths(view: dict | None, diagnostics: Collection[str]) -> set[str]:
    """The paths of the leaves of a SCORED tree's view that are on the
    Statistics card (`on_the_card_view`; ``diagnostics``, the names the test
    lists): drawn on no plot. Both tiers' plots skip exactly these."""
    from ..comparison.tree_spec import on_the_card_view

    return {
        str(leaf.get("path"))
        for leaf in leaves_of(view)
        if on_the_card_view(leaf, diagnostics)
    }


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


def criteria_rows(
    view: dict | None, subject: Collection[str], diagnostics: Collection[str]
) -> list[dict]:
    """A plot's criteria: the leaves of ``view`` on a member of
    ``subject``, in TREE order, each with ``crumbs`` — its ancestors'
    kinds, the root's first (``and › k-of-n ›``). The tree's structure is
    read off a row, not off the order of the rows: a family's rows are
    where the tree has them. A card leaf is no row of any plot
    (:func:`names_a_series`; ``diagnostics``, the names the test lists).
    Both tiers list a plot's criteria by this."""
    wanted = set(subject)
    kinds = ancestor_kinds(view)
    return [
        {
            "leaf": leaf,
            "variable": leaf.get("variable"),
            "crumbs": crumb_text(kinds.get(str(leaf.get("path")), ())),
        }
        for leaf in leaves_of(view)
        if leaf.get("variable") in wanted and names_a_series(leaf, diagnostics)
    ]
