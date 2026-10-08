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
    rows: Sequence[dict],
    *,
    unit_tests: Collection[str] = (),
    pinned: Collection[str] = (),
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
    A name in ``unit_tests`` never folds (D140: a UnitTests element is a
    test quantity of its own).

    ``pinned`` are the member paths of a fold whose editor is OPEN: they
    are kept together and apart from every other leaf, whatever they equal
    — a value typed into that editor passes through others on its way
    (`0.05` is `0.0` first), and one of those equal to another group's
    would otherwise merge the two and carry the other group along with
    the next keystroke."""
    apart = set(unit_tests)
    held = set(pinned)
    #: (family, parent, criterion, member rows) in order of first appearance
    groups: list[tuple[str, str | None, dict | None, list[dict]]] = []
    group_of: dict[str, int] = {}
    for row in rows:
        leaf = row["leaf"]
        name, path = leaf.get("variable"), str(leaf["path"])
        fam = None if not name or name in apart else family_of(str(name))
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


def family_in(
    names: Sequence[str], variable: str, *, unit_tests: Collection[str] = ()
) -> Family | None:
    """The family ``variable`` is an element of among ``names`` (every name
    of the result file, `Session.variable_names`), or ``None``: it names no
    element of an array, it is a UnitTests element (D140), or no other
    element of its array is there — one variable is no family, and gets no
    Indices field."""
    apart = set(unit_tests)
    if variable in apart:
        return None
    fam = family_of(variable)
    if fam is None:
        return None
    members: dict[int, str] = {fam[1]: variable}
    for name in names:
        other = None if name in apart else family_of(name)
        if other is not None and other[0] == fam[0]:
            members.setdefault(other[1], name)
    if len(members) < 2:
        return None
    return Family(fam[0], dict(sorted(members.items())))


# -- an edit of a fold, as per-leaf ops and as one -----------------------------


def as_one(model_id: str, tree: dict, ops: Sequence[PatchOp]) -> PatchOp:
    """``ops`` — per-leaf edits of ``tree``, the test's raw tree as it is
    now — as ONE ``add /metrics`` of the tree they give: what a gesture on
    a whole fold stages, as a move stages its one whole tree (D144). One
    pending edit, not one per element; a second in a row replaces the
    first (`Session.stage` folds an add onto an add of one member), so a
    value typed into a folded field is one edit, not a keystroke log; and
    a flat test materializes nothing first. Applied by the spec's own
    patch applier, so the tree is exactly what the ops would have left."""
    data = {"tests": [{"model": model_id, "metrics": copy.deepcopy(tree)}]}
    apply_patch_to_document(data, model_id, [op.raw() for op in ops])
    return PatchOp(model_id, "add", ROOT, data["tests"][0]["metrics"])


@dataclass(frozen=True)
class Reindexed:
    """A fold's members after an Indices edit: the per-leaf ``ops`` that
    make it so (adds, then removes) and the members' ``paths`` in the tree
    they leave, in tree order."""

    ops: tuple[PatchOp, ...]
    paths: tuple[str, ...]


def reindex(
    model_id: str, tree: dict, paths: Sequence[str], names: Sequence[str]
) -> Reindexed:
    """The fold whose members are at ``paths`` of ``tree`` (tree order, all
    children of one node), made to hold exactly the elements ``names``: a
    member whose variable is not among them is removed; each name no member
    has is added with the fold's criterion — the first member's leaf, its
    variable changed — right after the fold's LAST member, in the order
    given. The adds come first and the removes last-to-first, so every
    op's path is the one it has when it is applied. ``ValueError`` when
    ``paths`` do not name leaves under one node of ``tree``."""
    leaves = [node_or_none(tree, path) for path in paths]
    parents = {parent_of(path) for path in paths}
    if (
        not paths
        or len(parents) != 1
        or None in parents
        or any(leaf is None or "metric" not in leaf for leaf in leaves)
    ):
        raise ValueError("these criteria are no longer one folded row")
    (parent,) = parents
    index = {path: int(path.rsplit("/", 1)[1]) for path in paths}
    variable = {path: leaf["variable"] for path, leaf in zip(paths, leaves) if leaf}
    wanted = list(dict.fromkeys(names))
    have = set(variable.values())
    added = [name for name in wanted if name not in have]
    removed = [path for path in paths if variable[path] not in wanted]
    last = max(index.values())
    template = leaves[0]
    assert template is not None  # checked above
    ops = [
        PatchOp(
            model_id,
            "add",
            f"{parent}/children/{last + 1 + k}",
            {**copy.deepcopy(template), "variable": name},
        )
        for k, name in enumerate(added)
    ]
    ops += [
        PatchOp(model_id, "remove", path)
        for path in sorted(removed, key=index.__getitem__, reverse=True)
    ]
    gone = sorted(index[path] for path in removed)
    kept = [
        index[path] - sum(1 for g in gone if g < index[path])
        for path in paths
        if path not in removed
    ]
    new = [last + 1 + k - len(gone) for k in range(len(added))]
    return Reindexed(
        tuple(ops), tuple(f"{parent}/children/{i}" for i in sorted([*kept, *new]))
    )
