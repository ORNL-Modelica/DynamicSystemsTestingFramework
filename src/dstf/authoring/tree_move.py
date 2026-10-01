"""Moving a subtree of a raw metrics tree (U4): the whole new tree, or the
one sentence saying why not. UI-free and pure: the Edit tree capability
stages the tree `plan_move` returns as ONE ``add /metrics`` op, and lists
every place a node can go from `move_destinations`.

The owner's rulings it implements: a move that empties a combinator
removes it, up the tree (D2); a ``k-of-n`` whose ``k`` exceeds its
criteria lowers ``k``, a weight leaves a ``weighted`` with its child and a
node moved into one gets weight 1 — each said in the move's notes (D3); a move that puts under an ``or`` or
a ``k-of-n`` a child that can never fail is refused (D4, by D142's reason:
it would pass its parent with nothing else passing). Only a pairing the
move MAKES is refused — a tree as it is stays as it is.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from ..comparison.validator import BaselineRoleLookup
from .tree_pointer import node_or_none

ROOT = "/metrics"

# V5's sentences, in the order they are checked (a move that changes
# nothing is None between INTO_ITSELF and INTO_WARN), the validator's last.
ROOT_REFUSAL = "The root holds the whole tree: move what is in it instead."
INTO_ITSELF = "A criterion group can't move into itself."
INTO_WARN = (
    "A warning holds exactly one criterion or group: drop onto it to wrap "
    "both in `and`."
)
WARN_UNDER = (
    "A warning passes whatever it scores: under or / k-of-n it would pass its "
    "parent with nothing else passing."
)
#: Not the tree's refusal but the drag's: one that started on rows rebuilt
#: before its drop (`TreeCard.dropped`), whose paths name another tree.
TREE_CHANGED = "The tree changed while you dragged: try again."

#: The place a node already is, as `move_destinations` lists it.
WHERE_IT_IS = "where it is"

#: The combinators a child that can never fail may not newly sit under.
_NO_FREE_PASS_UNDER = ("or", "k-of-n")


@dataclass(frozen=True)
class Dest:
    """Where a node goes. ``at``: into the combinator at ``path``, before
    the child at ``index`` as the tree is NOW, before the move (``None``:
    at its end). ``onto``: onto the node at ``path`` — a criterion is
    wrapped with the moved node in an ``and`` (it first), a combinator
    takes it at its end."""

    kind: Literal["at", "onto"]
    path: str
    index: int | None = None


@dataclass(frozen=True)
class Moved:
    tree: dict  # the whole new tree: what `add /metrics` stages
    path: str  # where the moved node is in it
    notes: tuple[str, ...]  # what else changed: "k 3 → 2", ...


class MoveRefused(ValueError):
    """``str()`` is the one sentence the row shows (V5)."""


def validator_for(role_of: BaselineRoleLookup) -> Callable[[dict], None]:
    """What a move's new tree must pass: the parser, then the D66 roles —
    raising `MoveRefused` with the text `Session.stage`'s refusal shows (the
    parser's message without its path; the validator's joined)."""
    from ..comparison.tree_spec import MetricSpecError, parse_metric_tree
    from ..comparison.validator import validate_tree

    def validate(raw: dict) -> None:
        try:
            spec = parse_metric_tree(raw)
        except MetricSpecError as e:
            raise MoveRefused(e.message) from e
        errors = validate_tree(spec, role_of)
        if errors:
            raise MoveRefused(" ".join(e.message for e in errors))

    return validate


def _parent(path: str) -> tuple[str, int]:
    parent, index = path.rsplit("/children/", 1)
    return parent, int(index)


def _inside(path: str, src: str) -> bool:
    return path == src or path.startswith(src + "/")


def _index_of(kids: list, node: dict) -> int:
    return next(i for i, k in enumerate(kids) if k is node)


def _nodes(tree: dict) -> Iterator[dict]:
    yield tree
    for child in tree.get("children") or []:
        yield from _nodes(child)


def _path_of(tree: dict, node: dict, path: str = ROOT) -> str | None:
    if tree is node:
        return path
    for i, child in enumerate(tree.get("children") or []):
        found = _path_of(child, node, f"{path}/children/{i}")
        if found is not None:
            return found
    return None


def always_passes(node: dict) -> bool:
    """Can this node never fail, whatever its criteria score? A ``warn``
    cannot; a combinator can by its children (an ``and`` all, an ``or``
    any, a ``k-of-n`` at least k of them); a ``weighted`` sum and a
    criterion can always fail."""
    kind = node.get("combinator")
    kids = node.get("children") or []
    if kind == "warn":
        return True
    if kind == "and":
        return bool(kids) and all(always_passes(c) for c in kids)
    if kind == "or":
        return any(always_passes(c) for c in kids)
    if kind == "k-of-n":
        k = node.get("k")
        return isinstance(k, int) and sum(always_passes(c) for c in kids) >= k
    return False


def _free_passes(tree: dict) -> Iterator[tuple[dict, dict]]:
    """Every ``(parent, child)`` of ``tree`` with an ``or`` / ``k-of-n``
    parent and a child that can never fail."""
    for node in _nodes(tree):
        if node.get("combinator") in _NO_FREE_PASS_UNDER:
            for child in node.get("children") or []:
                if always_passes(child):
                    yield node, child


def _same_place(tree: dict, src: str, dest: Dest) -> bool:
    """Is ``dest`` where the node at ``src`` already is — before itself,
    before the child after it, or the end when it is last?"""
    parent, i = _parent(src)
    if dest.path != parent:
        return False
    holder = node_or_none(tree, parent)
    assert holder is not None  # the parent of a node that exists
    n = len(holder["children"])
    index = dest.index if dest.kind == "at" else None
    if index is None or index >= n:
        return i == n - 1
    return index in (i, i + 1)


def plan_move(
    tree: dict,
    src: str,
    dest: Dest,
    *,
    validate: Callable[[dict], None] | None = None,
) -> Moved | None:
    """The whole new tree with the node at ``src`` moved to ``dest``, or
    None when it would stay where it is. `MoveRefused` says why a move
    cannot be made; ``validate`` (`validator_for`) is checked last."""
    if src == ROOT:
        raise MoveRefused(ROOT_REFUSAL)
    if _inside(dest.path, src):
        raise MoveRefused(INTO_ITSELF)
    if node_or_none(tree, src) is None:
        raise ValueError(f"{src} names no node")
    target = node_or_none(tree, dest.path)
    if target is None:
        raise ValueError(f"{dest.path} names no node")
    wrap = dest.kind == "onto" and "combinator" not in target
    if not wrap and "combinator" not in target:
        raise ValueError(f"{dest.path} is a criterion: nothing goes in it")
    if not wrap and _same_place(tree, src, dest):
        return None
    if not wrap and target["combinator"] == "warn":
        raise MoveRefused(INTO_WARN)

    new = copy.deepcopy(tree)
    # Each new node beside the one it was copied from, so a pairing D4
    # finds is compared with the old tree by reference. The new node is
    # held in the value too: a pruned dict freed mid-move would free its
    # id() for the wrap's new `and`.
    origin = {id(n): (n, o) for o, n in zip(_nodes(tree), _nodes(new))}
    notes: list[str] = []
    # Everything the move names, resolved BEFORE anything shifts: the dicts
    # themselves are the identities (a removal renumbers a list, never
    # replaces the dicts in it).
    moved = node_or_none(new, src)
    old_parent_path, old_index = _parent(src)
    old_parent = node_or_none(new, old_parent_path)
    into = node_or_none(new, dest.path)
    assert moved is not None and old_parent is not None and into is not None
    chain = [old_parent]
    path = old_parent_path
    while path != ROOT:
        path = _parent(path)[0]
        up = node_or_none(new, path)
        assert up is not None
        chain.append(up)
    holder = node_or_none(new, _parent(dest.path)[0]) if wrap else None
    if wrap:
        assert holder is not None  # a criterion at the root holds no src
        anchor = None
    else:
        kids = into["children"]
        index = dest.index if dest.kind == "at" else None
        anchor = kids[index] if index is not None and index < len(kids) else None

    # Out: the node leaves its parent; its weight goes with it — said,
    # unless it only moves among the same weighted's children.
    del old_parent["children"][old_index]
    weight = _drop_weight(old_parent, old_index)
    if weight is not None and into is not old_parent:
        notes.append(_went(weight))
    # In, BEFORE any repair: the destination then holds the node, so the
    # prune below never removes it nor anything holding it, and a place
    # before a combinator the prune removes is where that combinator was.
    if holder is not None:
        at = _index_of(holder["children"], into)
        holder["children"][at] = {"combinator": "and", "children": [into, moved]}
    else:
        kids = into["children"]
        at = len(kids) if anchor is None else _index_of(kids, anchor)
        kids.insert(at, moved)
        if into.get("combinator") == "weighted" and isinstance(
            into.get("weights"), list
        ):
            if into is old_parent and weight is not None:
                # A reorder among one weighted's children: the weight
                # travels with its child, and nothing else changed.
                into["weights"].insert(at, weight)
            else:
                into["weights"].insert(at, 1)
                notes.append("weight 1 given")
    # The source's side, repaired upward: an emptied combinator goes (D2)
    # — the root never empties, it holds the destination — then every
    # k-of-n that lost a child lowers k to its criteria (D3).
    for node, up in zip(chain, chain[1:]):
        if node["children"]:
            break
        i = _index_of(up["children"], node)
        del up["children"][i]
        notes.append(f"the empty {node['combinator']} was removed")
        pruned = _drop_weight(up, i)
        if pruned is not None:
            notes.append(_went(pruned))
    for node in chain:
        k, n = node.get("k"), len(node["children"])
        if node.get("combinator") == "k-of-n" and n and isinstance(k, int) and k > n:
            node["k"] = n
            notes.append(f"k {k} → {n}")

    if not wrap and new == tree:
        # Two equal siblings trading places: the tree is as it was, and a
        # pending edit that changes nothing is no edit.
        return None

    # D4, by D142's reason: a child that can never fail under an `or` or
    # a `k-of-n` is refused where the move made that pairing; one the old
    # tree already had is left as it is.
    for parent, child in _free_passes(new):
        old_parent_of = origin.get(id(parent))
        old_child_of = origin.get(id(child))
        was = (
            old_parent_of is not None
            and old_child_of is not None
            and old_parent_of[0] is parent
            and old_child_of[0] is child
            and any(c is old_child_of[1] for c in old_parent_of[1]["children"])
            and always_passes(old_child_of[1])
        )
        if not was:
            raise MoveRefused(WARN_UNDER)
    if validate is not None:
        validate(new)
    where = _path_of(new, moved)
    assert where is not None
    return Moved(tree=new, path=where, notes=tuple(notes))


def _drop_weight(node: dict, index: int) -> float | None:
    """A ``weighted`` node's weight at ``index``, taken out with its child."""
    if node.get("combinator") != "weighted":
        return None
    weights = node.get("weights")
    if not isinstance(weights, list) or index >= len(weights):
        return None
    return weights.pop(index)


def _went(weight: object) -> str:
    """The note for a weight that left its ``weighted`` with its child."""
    text = format(weight, "g") if isinstance(weight, (int, float)) else str(weight)
    return f"weight {text} went with it"


def _view(node: dict, names: Mapping[str, str]) -> dict:
    """The raw node as `tree_text` reads a `tree_view` node: a criterion's
    label is what the page reads for its variable."""
    if "combinator" not in node:
        return {"kind": "leaf", "label": names.get(node.get("variable", "")), **node}
    return {
        **node,
        "kind": "combinator",
        "children": [_view(c, names) for c in node.get("children") or []],
    }


def move_destinations(
    tree: dict,
    src: str,
    *,
    validate: Callable[[dict], None] | None = None,
    names: Mapping[str, str] | None = None,
) -> list[tuple[str, Dest, str | None]]:
    """Every place the node at ``src`` can go, in tree order, as ``(label,
    dest, reason)``: a combinator's positions between its children's rows
    ("at 1" … "at the end"), a criterion's "wrap both in and". ``names`` is
    each variable's text as the page reads it (`SessionView.labels`).

    The places beside the node read as ONE row, "… — where it is" (reason
    None; the caller greys it: `plan_move` gives None). The node's own row
    is not listed; the places in its own subtree are, with their refusal."""
    from .tree_text import node_text

    names = names or {}
    rows: list[tuple[str, Dest, str | None]] = []
    here = False

    def one(text: str, where: str, dest: Dest) -> None:
        nonlocal here
        try:
            planned = plan_move(tree, src, dest, validate=validate)
        except MoveRefused as e:
            rows.append((f"{text} — {where}", dest, str(e)))
            return
        if planned is None:
            if not here:
                here = True
                rows.append((f"{text} — {WHERE_IT_IS}", dest, None))
            return
        rows.append((f"{text} — {where}", dest, None))

    def walk(node: dict, path: str) -> None:
        text = node_text(_view(node, names))
        if path == ROOT:
            text += " (root)"
        if "combinator" not in node:
            if path != src:
                one(text, "wrap both in and", Dest("onto", path))
            return
        kids = node.get("children") or []
        for i, child in enumerate(kids):
            one(text, f"at {i + 1}", Dest("at", path, i))
            walk(child, f"{path}/children/{i}")
        one(text, "at the end", Dest("at", path, None))

    walk(tree, ROOT)
    return rows


#: A drag's zones on a row (D1 as amended by the owner): its top half is
#: before it, its bottom half onto it — no "after": every pixel line means
#: one place. To put a node last at an outer level, drop onto that outer
#: combinator. The root has only "onto": nothing sits beside it.
ZONES = ("before", "onto")


def zone_dest(path: str, zone: str) -> Dest | None:
    """The destination a drop in ``zone`` of ``path``'s row means: before
    it among its siblings, or onto it (a criterion: wrap both in ``and``;
    a combinator: its end). None for a zone the row has not got."""
    if zone == "onto":
        return Dest("onto", path)
    if zone == "before" and path != ROOT:
        parent, index = _parent(path)
        return Dest("at", parent, index)
    return None


def zone_map(
    tree: dict, src: str, *, validate: Callable[[dict], None] | None = None
) -> dict[str, dict[str, str | None]]:
    """``{row path: {zone: reason}}`` over every row of ``tree`` for a drag
    of the node at ``src``: ``None`` allowed, ``""`` where it already is,
    else the sentence that refuses it. The page asks once per drag and
    draws every zone from the answer, with no round trip while dragging:
    it grows with the square of the tree (134 nodes ~0.2 s)."""
    out: dict[str, dict[str, str | None]] = {}

    def walk(node: dict, path: str) -> None:
        zones: dict[str, str | None] = {}
        for zone in ZONES:
            dest = zone_dest(path, zone)
            if dest is None:
                continue
            try:
                planned = plan_move(tree, src, dest, validate=validate)
            except MoveRefused as e:
                zones[zone] = str(e)
                continue
            zones[zone] = "" if planned is None else None
        out[path] = zones
        for i, child in enumerate(node.get("children") or []):
            walk(child, f"{path}/children/{i}")

    walk(tree, ROOT)
    return out
