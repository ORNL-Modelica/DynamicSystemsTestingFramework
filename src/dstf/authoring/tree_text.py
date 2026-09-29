"""What a tree node READS as. UI-free, so the Test page's rows and the
Edit tree capability's form titles say the same thing: a form's title
names its node the way the node's row does. Every function takes a
`tree_view` node (``kind``, ``label``, ``path``), never a raw spec node."""

from __future__ import annotations

from ..reporting.tree_rows import leaves_of


def nodes_of(view: dict | None) -> list[dict]:
    """Every node of a `tree_view`, combinators included, in tree order."""
    if view is None:
        return []
    return [view] + [
        node for child in view.get("children") or [] for node in nodes_of(child)
    ]


def leaf_name(leaf: dict) -> str:
    """``metric · label`` — what a criterion's row READS. The label is the
    expression the model wrote for the variable (`nrmse · pipe.T[1]`), which
    the tree view carries beside the `variable` a patch op targets; a
    variable with no expression reads as its name, as it always did."""
    return f"{leaf.get('metric')} · {leaf.get('label') or leaf.get('variable')}"


def node_text(node: dict) -> str:
    if node.get("kind") == "leaf":
        return leaf_name(node)
    n = len(leaves_of(node))
    text = f"{node.get('combinator')} · {n} {'criterion' if n == 1 else 'criteria'}"
    if node.get("combinator") == "k-of-n" and node.get("k") is not None:
        text += f" · k={node['k']}"
    if node.get("combinator") == "weighted" and node.get("threshold") is not None:
        text += f" · threshold {node['threshold']:g}"
    return text


def count_text(node: dict) -> str:
    """A combinator's row text while its kind is read from the dropdown
    beside it: `node_text` without the kind — ``3 criteria``,
    ``k=2 · 3 criteria``, ``threshold 0.5 · 3 criteria``."""
    n = len(leaves_of(node))
    parts = [f"{n} {'criterion' if n == 1 else 'criteria'}"]
    if node.get("combinator") == "k-of-n" and node.get("k") is not None:
        parts.insert(0, f"k={node['k']}")
    if node.get("combinator") == "weighted" and node.get("threshold") is not None:
        parts.insert(0, f"threshold {node['threshold']:g}")
    return " · ".join(parts)


def parent_kinds(view: dict | None) -> dict[str, str | None]:
    """Each node's path -> its parent's combinator (None for the root):
    what a leaf's `+` reads, since the parent takes the child it adds."""
    kinds: dict[str, str | None] = {}
    for node in nodes_of(view):
        kinds.setdefault(str(node.get("path")), None)
        for child in node.get("children") or []:
            kinds[str(child.get("path"))] = node.get("combinator")
    return kinds
