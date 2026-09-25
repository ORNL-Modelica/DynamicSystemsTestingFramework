"""A pointer into a raw metrics tree (``/metrics``, ``/metrics/children/0``,
...), resolved to the node it names. UI-free: the builtins read a node
through it to build their ops, and the Test page reads one to tell whether
a form row's node moved and whether a criterion it is asked to reveal
exists. The shell imports no builtin, so the one reader lives here."""

from __future__ import annotations

from typing import Any


def node_at(tree: dict, path: str) -> dict:
    """The raw node at ``path``, a pointer whose first token names the tree
    itself (``/metrics``, ``/metrics/children/0``, ...)."""
    node: Any = tree
    for token in [t for t in path.split("/") if t][1:]:
        # A pointer alternates `children` (a key) and an index (into that
        # list): consume each token as what it is.
        node = node[int(token)] if token.isdigit() else node[token]
    return node


def node_or_none(tree: dict | None, path: str) -> dict | None:
    """The node at ``path``, or ``None`` when the pointer names none of
    ``tree`` — a stale selection, or one naming a raw key (``/metrics/
    combinator`` resolves to a word, which is no node)."""
    if tree is None:
        return None
    try:
        node = node_at(tree, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return node if isinstance(node, dict) else None
