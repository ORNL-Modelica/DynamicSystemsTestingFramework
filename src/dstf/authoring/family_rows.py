"""A criterion over an array family as data (D152): which of a plot's
criteria rows fold into one, which elements an Indices text names, and the
per-leaf ops an edit of the fold is — the shell draws the rows
(`shell/family_fold.py`) and the builtins stage the ops, and neither knows
the rules, as the Variables drawer draws `variable_rows`.

Nothing here is stored: `test_spec.json` holds one literal leaf per element
(the brief's ruling §3.10), and the fold is derived from the tree every
time it is drawn. The rows come from the report's walk
(`reporting.tree_rows.criteria_rows`), which the report goes on rendering
one per leaf — the fold is the tool's alone."""

from __future__ import annotations

import copy
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from ..discovery.patch_apply import apply_patch_to_document
from ..figures.families import family_label, family_of, parse_slice, slice_text
from .spec_document import PatchOp, json_equal
from .tree_pointer import node_or_none

ROOT = "/metrics"

#: What a folded row's key ends with. The key is its first member's path
#: and this — never a JSON pointer — so the Test page's one open editor
#: `(plot index, key)` names a fold or a criterion and cannot mistake them.
_FOLD = "#fold"


def fold_key(path: str) -> str:
    """The key of the fold whose first member (tree order) is at ``path``."""
    return path + _FOLD


def is_fold(key: str) -> bool:
    return key.endswith(_FOLD)


def leaf_of(key: str) -> str:
    """A fold's first member's path; a criterion's own path as it is."""
    return key[: -len(_FOLD)] if is_fold(key) else key


def parent_of(path: str) -> str | None:
    """The path of the combinator ``path``'s node is a child of."""
    return path.rsplit("/children/", 1)[0] if "/children/" in path else None


def criterion_of(leaf: Mapping) -> dict:
    """What a leaf of a `tree_view` IS apart from its variable: its metric,
    its parameters, the baseline it is scored against and its window. Two
    leaves with one of these are one criterion on two variables."""
    return {
        "metric": leaf.get("metric"),
        "params": leaf.get("params") or {},
        "against": leaf.get("against") or "primary",
        "window": leaf.get("window") or {},
    }


@dataclass(frozen=True)
class Fold:
    """One folded criteria row: ``rows`` are its members' rows
    (`criteria_rows`' dicts), in tree order — two or more leaves of one
    array ``family`` (its key, ``pipe.T[]``), children of one node, that
    are one criterion (:func:`criterion_of`)."""

    family: str
    rows: tuple[dict, ...]

    @property
    def leaves(self) -> list[dict]:
        return [row["leaf"] for row in self.rows]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(str(leaf["path"]) for leaf in self.leaves)

    @property
    def variables(self) -> list[str]:
        return [str(leaf["variable"]) for leaf in self.leaves]

    @property
    def key(self) -> str:
        return fold_key(self.paths[0])

    @property
    def crumbs(self) -> str:
        """One parent, so one breadcrumb: the first member's."""
        return str(self.rows[0]["crumbs"])

    @property
    def label(self) -> str:
        """``pipe.T[1:6,8:20]``: the family with its folded indices."""
        return family_label(self.family, self.variables)

    @property
    def name(self) -> str:
        """``tube · pipe.T[1:20]`` — what the row READS, as a criterion's
        row reads ``metric · variable`` (`tree_text.leaf_name`)."""
        return f"{self.leaves[0].get('metric')} · {self.label}"


def fold_rows(
    rows: Sequence[dict], *, pinned: Collection[str] = ()
) -> list[dict | Fold]:
    """A plot's criteria ``rows`` (`criteria_rows`) with each array
    family's identical criteria folded into one :class:`Fold`, placed where
    its first member's row was; every other row as it came, in its order.

    Leaves fold when they are elements of ONE family by result-file name
    (`figures.families.family_of`), children of ONE node, and ONE criterion
    (:func:`criterion_of`, compared as JSON — `json_equal` — so ``1`` is
    ``1.0``) — and are two or more: a group of one is a row. Two groups of
    one family are two folds. A second identical leaf on a variable the
    group already holds stays its own row: a fold names each element once.
    The rows are ONE plot's, so no UnitTests element is among them with
    another: each is a plot of its own (D140, `derive_plots`).

    ``pinned`` are the member paths of a fold whose editor is OPEN: they
    are kept together and apart from every other leaf, whatever they equal
    — a value typed into that editor passes through others on its way
    (`0.05` is `0.0` first), and one of those equal to another group's
    would otherwise merge the two and carry the other group along with
    the next keystroke."""
    held = set(pinned)
    #: (family, parent, criterion, member rows) in order of first appearance
    groups: list[tuple[str, str | None, dict | None, list[dict]]] = []
    group_of: dict[str, int] = {}
    for row in rows:
        leaf = row["leaf"]
        name, path = leaf.get("variable"), str(leaf["path"])
        fam = family_of(str(name)) if name else None
        if fam is None:
            continue
        parent = parent_of(path)
        # The pinned group is the open fold's, whatever its leaves equal.
        criterion = None if path in held else criterion_of(leaf)
        found = next(
            (
                i
                for i, (family, node, held_criterion, _) in enumerate(groups)
                if family == fam[0]
                and node == parent
                and (
                    (criterion is None and held_criterion is None)
                    or (
                        criterion is not None
                        and held_criterion is not None
                        and json_equal(criterion, held_criterion)
                    )
                )
            ),
            None,
        )
        if found is None:
            groups.append((fam[0], parent, criterion, [row]))
            group_of[path] = len(groups) - 1
        elif name not in {m["leaf"].get("variable") for m in groups[found][3]}:
            groups[found][3].append(row)
            group_of[path] = found
    out: list[dict | Fold] = []
    for row in rows:
        at = group_of.get(str(row["leaf"]["path"]))
        if at is None or len(groups[at][3]) < 2:
            out.append(row)
        elif groups[at][3][0] is row:
            out.append(Fold(groups[at][0], tuple(groups[at][3])))
    return out


def tally(statuses: Sequence[str]) -> str:
    """A folded row's count, ``18 pass · 2 fail`` — its members by verdict
    (`tree_card.leaf_status`'s words), an advisory and an unscored count
    when either is non-zero, as the tree card's closed line tallies."""
    parts = [f"{statuses.count('pass')} pass"]
    for status, word in (
        ("fail", "fail"),
        ("warn", "advisory"),
        ("queued", "unscored"),
    ):
        if statuses.count(status):
            parts.append(f"{statuses.count(status)} {word}")
    return " · ".join(parts)


# -- the elements an Indices field names ------------------------------------------


@dataclass(frozen=True)
class Family:
    """An array family as the last run's result file has it: its key
    (``pipe.T[]``) and its elements' names by index."""

    key: str
    names: Mapping[int, str]

    @property
    def label(self) -> str:
        """``pipe.T[1:20]`` — the family and every element it has."""
        return family_label(self.key, list(self.names.values()))

    @property
    def text(self) -> str:
        """Every element, as an Indices field holds them: ``1:20``."""
        return slice_text(list(self.names))

    def chosen(self, text: str) -> list[str]:
        """The names the Indices ``text`` chooses, by index ascending.
        ``ValueError`` with the sentence to show: `parse_slice`'s for a
        text that is no slice, and one naming the family's size for an
        index it does not have."""
        indices = parse_slice(text)
        missing = [i for i in indices if i not in self.names]
        if missing:
            n = len(self.names)
            what = (
                f"{missing[0]} is not one of them"
                if len(missing) == 1
                else f"{slice_text(missing)} are not among them"
            )
            raise ValueError(
                f"{self.label} has {n} element{'' if n == 1 else 's'}; {what}."
            )
        return [self.names[i] for i in indices]

    def indices_of(self, names: Collection[str]) -> str:
        """``names`` — elements of this family — as an Indices text."""
        wanted = set(names)
        return slice_text([i for i, name in self.names.items() if name in wanted])


def family_in(names: Sequence[str], variable: str) -> Family | None:
    """The family ``variable`` is an element of among ``names`` (every name
    of the result file, `Session.variable_names`), or ``None``: it names no
    element of an array, or no other element of its array is there — one
    variable is no family, and gets no Indices field. By name alone: that
    a UnitTests element is no element of a family (D140) is its caller's
    to say, which knows the test (`edit_tree._family`)."""
    fam = family_of(variable)
    if fam is None:
        return None
    members: dict[int, str] = {fam[1]: variable}
    for name in names:
        other = family_of(name)
        if other is not None and other[0] == fam[0]:
            members.setdefault(other[1], name)
    if len(members) < 2:
        return None
    return Family(fam[0], dict(sorted(members.items())))


# -- an edit of a fold, as per-leaf ops and as one -----------------------------


NOT_A_FOLD = "these criteria are no longer one folded row"


def elements_text(names: Sequence[str]) -> str:
    """Elements of one array as a person reads them: ``pipe.T[1:5:16]``."""
    fam = family_of(names[0]) if names else None
    return family_label(fam[0], list(names)) if fam is not None else ", ".join(names)


def as_one(
    model_id: str,
    tree: dict,
    ops: Sequence[PatchOp],
    notes: Sequence[tuple[str, str]] = (),
) -> PatchOp:
    """``ops`` — per-leaf edits of ``tree``, the test's raw tree as it is
    now — as ONE ``add /metrics`` of the tree they give: what a gesture on
    a whole fold stages, as a move stages its one whole tree (D144). One
    pending edit, not one per element; a second in a row replaces the
    first (`Session.stage` folds an add onto an add of one member), so a
    value typed into a folded field is one edit, not a keystroke log.
    Applied by the spec's own patch applier, so the tree is exactly what
    the ops would have left — every node they do not name as it was.

    Marked as an edit of the tree (`PatchOp.edits_tree`): a flat entry's
    implicit tree is written out before it, as before any per-leaf op, and
    the preview shows what the gesture changed, not a whole tree added.
    ``notes`` say what the gesture did (`PatchOp.notes`)."""
    data = {"tests": [{"model": model_id, "metrics": copy.deepcopy(tree)}]}
    apply_patch_to_document(data, model_id, [op.raw() for op in ops])
    return PatchOp(
        model_id,
        "add",
        ROOT,
        data["tests"][0]["metrics"],
        edits_tree=True,
        notes=tuple(notes),
    )


def _shown(value: object) -> str:
    """A leaf's value in a note: a number as a person types it, a word as
    it is, anything else (a pair, a table) as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return json.dumps(value)
    return f"{value:g}"


def edit_notes(
    old: Mapping, new: Mapping, variables: Sequence[str]
) -> list[tuple[str, str]]:
    """What a folded field's change did, one sentence per field it moved —
    ``tolerance → 0.05 on 20 criteria of pipe.T[1:20]`` — from the members'
    leaf before (``old``) and after (``new``), their variables apart. A
    mode switched says that and nothing more: every field went with it.
    Each sentence's key is its field and array, so a value typed over
    replaces its line (`PatchOp.notes`)."""
    what = f"on {len(variables)} criteria of {elements_text(variables)}"
    fam = family_of(variables[0])
    array = fam[0] if fam is not None else variables[0]

    def note(field: str, value: object, there: bool) -> tuple[str, str]:
        said = f"{field} → {_shown(value)}" if there else f"{field} cleared"
        return f"{field}:{array}", f"{said} {what}"

    if old.get("metric") != new.get("metric"):
        return [note("mode", str(new.get("metric")), True)]
    flat_old, flat_new = _fields(old), _fields(new)
    return [
        note(key, flat_new.get(key), key in flat_new)
        for key in sorted({*flat_old, *flat_new})
        if not (
            key in flat_old
            and key in flat_new
            and json_equal(flat_old[key], flat_new[key])
        )
    ]


def _fields(leaf: Mapping) -> dict:
    """A raw leaf as the fields an editor shows: its parameters, the two
    ends of its window and a baseline that is not the primary one."""
    out = {
        key: value
        for key, value in leaf.items()
        if key not in ("metric", "variable", "window", "against")
    }
    for end, value in (leaf.get("window") or {}).items():
        out[f"window {end}"] = value
    if leaf.get("against") not in (None, "primary"):
        out["against"] = leaf["against"]
    return out


@dataclass(frozen=True)
class Reindexed:
    """A fold's members after an Indices edit: the per-leaf ``ops`` that
    make it so (removes, then adds), the members' ``paths`` in the tree
    they leave, in tree order, and the elements that came and went."""

    ops: tuple[PatchOp, ...]
    paths: tuple[str, ...]
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def notes(self) -> list[tuple[str, str]]:
        """What the edit did, for the preview (`PatchOp.notes`): ``Indices:
        removed pipe.T[20]``. Each sentence is its own key: two Indices
        edits are two lines."""
        said = [
            f"Indices: {verb} {elements_text(names)}"
            for verb, names in (("removed", self.removed), ("added", self.added))
            if names
        ]
        return [(text, text) for text in said]


def _own_leaf(child: Mapping) -> Mapping | None:
    """The criterion ``child`` IS — a leaf — or holds as a warning of its
    own (a ``warn`` around one leaf); None for any other group."""
    if "metric" in child:
        return child
    kids = child.get("children") or []
    if child.get("combinator") == "warn" and len(kids) == 1 and "metric" in kids[0]:
        return kids[0]
    return None


def _one_criterion(a: Mapping, b: Mapping) -> bool:
    """Whether two raw leaves are one criterion apart from their variable
    (`criterion_of`'s rule, over the spec's own shape: no baseline named is
    the primary one, no window is the whole run)."""
    return json_equal(
        {**_fields(a), "metric": a.get("metric")},
        {**_fields(b), "metric": b.get("metric")},
    )


def reindex(
    model_id: str, tree: dict, paths: Sequence[str], names: Sequence[str]
) -> Reindexed:
    """The fold whose members are at ``paths`` of ``tree`` (tree order, all
    children of one node), made to hold exactly the elements ``names``: a
    member whose variable is not among them is removed; each name no member
    has is added with the fold's criterion — the first member's leaf, its
    variable changed.

    The group's leaves stay in INDEX order: a new element goes right after
    the member with the next smaller index — before the first, when it is
    smaller than all; where the group was, when no member is left — so an
    Indices text put back puts the tree back, and is no edit. Members the
    tree holds out of index order are not moved.

    An element that already has the fold's very criterion as a leaf under
    that node gets no second one: that leaf is taken as a member. One with
    a criterion of the fold's METRIC that differs — or sits in a ``warn``
    of its own — is not given another beside it: ``ValueError`` naming it,
    and nothing is planned. So is ``paths`` that do not name leaves under
    one node (`NOT_A_FOLD`).

    The removes come first, last-to-first, then the adds by index, so
    every op's path is the one it has when it is applied."""
    leaves = [node_or_none(tree, path) for path in paths]
    parents = {parent_of(path) for path in paths}
    if (
        not paths
        or len(parents) != 1
        or None in parents
        or any(leaf is None or "metric" not in leaf for leaf in leaves)
    ):
        raise ValueError(NOT_A_FOLD)
    (parent,) = parents
    siblings = (node_or_none(tree, str(parent)) or {}).get("children") or []
    template = leaves[0]
    assert template is not None  # checked above
    #: each member's place among its parent's children -> its variable
    member = {
        int(path.rsplit("/", 1)[1]): str(leaf["variable"])
        for path, leaf in zip(paths, leaves)
        if leaf
    }
    wanted = list(dict.fromkeys(names))
    taken = []
    for name in [n for n in wanted if n not in member.values()]:
        for at, child in enumerate(siblings):
            own = None if at in member else _own_leaf(child)
            if (
                own is None
                or own.get("variable") != name
                or own.get("metric") != template.get("metric")
            ):
                continue
            if own is child and _one_criterion(own, template):
                member[at] = name  # it has this very criterion already
            else:
                taken.append(name)
            break
    if taken:
        metric = template.get("metric")
        # A plain list: these are indices to take OUT of the text typed.
        indices = ",".join(
            str(fam[1]) for n in taken if (fam := family_of(n)) is not None
        )
        raise ValueError(
            f"{taken[0]} already has its own {metric} criterion under this group — "
            f"edit or remove that one, or leave {indices} out"
            if len(taken) == 1
            else f"{', '.join(taken[:-1])} and {taken[-1]} already have their own "
            f"{metric} criteria under this group — edit or remove those, or leave "
            f"{indices} out"
        )

    def index_of(name: str) -> float:
        fam = family_of(name)
        return fam[1] if fam is not None else float("inf")

    #: The parent's children as the ops leave them: a child that stands by
    #: its old place, a new leaf by its variable.
    order: list[int | str] = list(range(len(siblings)))
    members: dict[int | str, str] = {at: name for at, name in member.items()}
    where = min(member)  # the group's first place, for a fold left empty
    removed = [at for at, name in member.items() if name not in wanted]
    ops = []
    for at in sorted(removed, reverse=True):
        ops.append(PatchOp(model_id, "remove", f"{parent}/children/{at}"))
        order.remove(at)
        del members[at]
    added = sorted((n for n in wanted if n not in member.values()), key=index_of)
    for name in added:
        placed = [
            (index_of(members[token]), k)
            for k, token in enumerate(order)
            if token in members
        ]
        below = [(i, k) for i, k in placed if i < index_of(name)]
        if below:
            at = max(below)[1] + 1
        elif placed:
            at = min(placed)[1]
        else:
            at = sum(1 for token in order if isinstance(token, int) and token < where)
        ops.append(
            PatchOp(
                model_id,
                "add",
                f"{parent}/children/{at}",
                {**copy.deepcopy(template), "variable": name},
            )
        )
        order.insert(at, name)
        members[name] = name
    return Reindexed(
        tuple(ops),
        tuple(
            f"{parent}/children/{k}"
            for k, token in enumerate(order)
            if token in members
        ),
        tuple(added),
        tuple(member[at] for at in sorted(removed)),
    )
