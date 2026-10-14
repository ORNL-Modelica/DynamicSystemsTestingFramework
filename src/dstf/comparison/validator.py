"""Metric-tree validator enforcing D66 baseline-role rules.

The comparator's MetricTree distinguishes three baseline roles (D66):
**primary** = regression anchor; **soft_check** = advisory cross-check;
**companion** = plot-only overlay. This module enforces that trees
respect those semantics.

The rules split by what they read. The FILE's rules read the tree alone,
so a tree that breaks one is wrong in every ``<Backend>/<os>`` folder:

1. Every tree has at least one criterion against ``primary`` outside
   ``warn`` — a real regression anchor.
2. A criterion outside ``warn`` names ``primary``: a soft check sits under
   a ``warn``, a companion is only drawn, and any other name is neither.
3. A ``weighted`` node sums scores of one direction.

The FOLDER's rules read which names this folder stores, and so hold in
one folder and not in another, where the spec is the same:

4. A criterion names no companion, under ``warn`` or not.
5. A criterion names a stored baseline — the primary or a soft check.

A criterion outside ``warn`` naming a stored soft check breaks rule 2, and
is said in rule 2's sentence: one rule, one sentence, on every surface.

:func:`validate_tree` with no lookup checks the file's rules; that is the
check a test is refused by where it is scored (``comparator.broken_roles``),
since a soft check is imported into ONE folder and the same spec is scored
in all of them. With a lookup (``spec update`` and the tool, which write
the file and hold the store) it checks all five.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from .tree_spec import CombinatorSpec, LeafSpec, SpecNode


class BaselineRole(Enum):
    PRIMARY = "primary"
    SOFT_CHECK = "soft_check"
    COMPANION = "companion"


@dataclass
class ValidationError:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


BaselineRoleLookup = Callable[[str], BaselineRole | None]


def validate_tree(
    tree: SpecNode,
    role_of: BaselineRoleLookup | None = None,
    _path: str = "metrics",
) -> list[ValidationError]:
    """Return a list of validation errors for ``tree`` against D66 role rules.

    Args:
        tree: Parsed MetricTree spec (root node).
        role_of: callable returning :class:`BaselineRole` for a known
            baseline name, or ``None`` for an unknown name. Typical
            caller builds this from a :class:`ReferenceStore` query.
            Omitted, only the rules the file decides by itself are checked
            (the module's 1-3): no name but ``primary`` is known, and none
            is held against the tree for being unknown.
    """
    errors: list[ValidationError] = []
    _walk(tree, role_of, _path, in_warn=False, errors=errors)

    # Tree-level rule: at least one primary leaf outside warn.
    if not _has_primary_outside_warn(tree, role_of or _primary_only):
        errors.append(
            ValidationError(
                _path,
                "Every tree needs at least one criterion scored against the "
                "primary baseline outside a 'warn': a tree of warnings alone "
                "can never fail.",
            )
        )
    return errors


def _primary_only(name: str) -> BaselineRole | None:
    return BaselineRole.PRIMARY if name == "primary" else None


def _walk(
    node: SpecNode,
    role_of: BaselineRoleLookup | None,
    path: str,
    in_warn: bool,
    errors: list[ValidationError],
) -> None:
    if isinstance(node, LeafSpec):
        _check_leaf(node, role_of, path, in_warn, errors)
        return
    if isinstance(node, CombinatorSpec):
        if node.combinator == "weighted":
            _check_weighted_directions(node, path, errors)
        child_in_warn = in_warn or node.combinator == "warn"
        for i, child in enumerate(node.children):
            _walk(child, role_of, f"{path}.children[{i}]", child_in_warn, errors)


def _check_weighted_directions(
    node: CombinatorSpec,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Reject weighted nodes whose leaf children score in opposite directions.

    ``weighted`` compares one summed score against one threshold with one
    comparison direction, so summing a lower-is-better score (NRMSE-like
    error) with a higher-is-better one (tube's in-band fraction) is
    meaningless — the sum can move toward the threshold for opposite
    reasons. The metric registry's ``score_direction`` is the authority
    (D101).
    """
    from .registry import metric as registry_metric

    directions: dict[str, list[str]] = {}
    for child in node.children:
        if not isinstance(child, LeafSpec):
            continue
        desc = registry_metric(child.metric)
        if desc is None:
            continue  # unknown metric fails loudly at parse/eval, not here
        directions.setdefault(desc.score_direction, []).append(child.metric)
    if len(directions) > 1:
        parts = "; ".join(
            f"{d}: {', '.join(sorted(set(ms)))}" for d, ms in sorted(directions.items())
        )
        errors.append(
            ValidationError(
                path,
                f"A 'weighted' node cannot mix lower-is-better and "
                f"higher-is-better scores ({parts}); split them into separate "
                f"weighted nodes.",
            )
        )


def _check_leaf(
    leaf: LeafSpec,
    role_of: BaselineRoleLookup | None,
    path: str,
    in_warn: bool,
    errors: list[ValidationError],
) -> None:
    # The file alone: outside a warn only the primary is scored, which every
    # folder holds — whatever the name is in this folder, a stored soft
    # check, a companion or nothing: ONE rule, said in one sentence where
    # the tree is scored and where it is written. Under a warn, any name
    # may be a soft check of SOME folder; one this folder lacks is a
    # criterion not scored.
    if not in_warn and leaf.against != "primary":
        errors.append(
            ValidationError(
                path,
                f"This criterion scores against {leaf.against!r} outside a "
                f"'warn', where only the primary baseline is scored: a soft "
                f"check sits under a 'warn', and a companion is only drawn.",
            )
        )
        return
    if role_of is None:
        return
    role = role_of(leaf.against)
    if role is None:
        errors.append(
            ValidationError(
                path,
                f"This criterion scores against {leaf.against!r}, which is not "
                f"a baseline of this test (the primary or a soft check); check "
                f"the name.",
            )
        )
        return
    if role is BaselineRole.COMPANION:
        errors.append(
            ValidationError(
                path,
                f"This criterion scores against the companion {leaf.against!r}, "
                f"and companions are only drawn; import it as a soft check "
                f"(dstf soft-check add) to score against it.",
            )
        )


def _has_primary_outside_warn(
    node: SpecNode,
    role_of: BaselineRoleLookup,
    in_warn: bool = False,
) -> bool:
    if isinstance(node, LeafSpec):
        if in_warn:
            return False
        return role_of(node.against) is BaselineRole.PRIMARY
    if isinstance(node, CombinatorSpec):
        child_in_warn = in_warn or node.combinator == "warn"
        return any(
            _has_primary_outside_warn(c, role_of, child_in_warn) for c in node.children
        )
    return False


# ---------------------------------------------------------------------------
# Convenience role-lookup factories
# ---------------------------------------------------------------------------


def role_lookup_from_store(store, model_id: str) -> BaselineRoleLookup:
    """Build a role-lookup for ``store``'s view of ``model_id``.

    Primary is always ``"primary"``. Soft_checks come from
    :meth:`ReferenceStore.get_soft_checks`. Companions from
    :meth:`ReferenceStore.get_companions`.
    """
    soft_check_names = set(store.get_soft_checks(model_id).keys())
    companion_names = set(store.get_companions(model_id).keys())

    def lookup(name: str) -> BaselineRole | None:
        if name == "primary":
            return BaselineRole.PRIMARY
        if name in soft_check_names:
            return BaselineRole.SOFT_CHECK
        if name in companion_names:
            return BaselineRole.COMPANION
        return None

    return lookup


def role_lookup_from_names(
    soft_checks: Sequence[str] = (),
    companions: Sequence[str] = (),
) -> BaselineRoleLookup:
    """Build a role-lookup from explicit name lists (testing-friendly)."""
    sc = set(soft_checks)
    co = set(companions)

    def lookup(name: str) -> BaselineRole | None:
        if name == "primary":
            return BaselineRole.PRIMARY
        if name in sc:
            return BaselineRole.SOFT_CHECK
        if name in co:
            return BaselineRole.COMPANION
        return None

    return lookup
