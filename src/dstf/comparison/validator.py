"""Metric-tree validator enforcing D66 baseline-role rules.

The comparator's MetricTree distinguishes three baseline roles (D66):
**primary** = regression anchor; **soft_check** = advisory cross-check;
**companion** = plot-only overlay. This module enforces that trees
respect those semantics at spec-load time:

1. Every tree has at least one leaf targeting ``primary`` outside
   ``warn`` — guarantees a real regression anchor.
2. Leaves with ``against: "<soft_check>"`` must sit under a ``warn``
   ancestor — soft_checks never hard-fail.
3. Leaves must never target companion names — companions are not
   scorable, only displayable.
4. Leaves must not target unknown baseline names.

Callers supply a ``BaselineRegistry`` (a mapping of known names to
their role) so validation is context-aware per test.
"""

from __future__ import annotations

from collections.abc import Callable
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
    role_of: BaselineRoleLookup,
    _path: str = "metrics",
) -> list[ValidationError]:
    """Return a list of validation errors for ``tree`` against D66 role rules.

    Args:
        tree: Parsed MetricTree spec (root node).
        role_of: callable returning :class:`BaselineRole` for a known
            baseline name, or ``None`` for an unknown name. Typical
            caller builds this from a :class:`ReferenceStore` query.
    """
    errors: list[ValidationError] = []
    _walk(tree, role_of, _path, in_warn=False, errors=errors)

    # Tree-level rule: at least one primary leaf outside warn.
    if not _has_primary_outside_warn(tree, role_of):
        errors.append(
            ValidationError(
                _path,
                "tree has no leaf targeting 'primary' outside a 'warn' combinator — "
                "every tree must carry at least one hard regression anchor",
            )
        )
    return errors


def _walk(
    node: SpecNode,
    role_of: BaselineRoleLookup,
    path: str,
    in_warn: bool,
    errors: list[ValidationError],
) -> None:
    if isinstance(node, LeafSpec):
        _check_leaf(node, role_of, path, in_warn, errors)
        return
    if isinstance(node, CombinatorSpec):
        child_in_warn = in_warn or node.combinator == "warn"
        for i, child in enumerate(node.children):
            _walk(child, role_of, f"{path}.children[{i}]", child_in_warn, errors)


def _check_leaf(
    leaf: LeafSpec,
    role_of: BaselineRoleLookup,
    path: str,
    in_warn: bool,
    errors: list[ValidationError],
) -> None:
    role = role_of(leaf.against)
    if role is None:
        errors.append(
            ValidationError(
                path,
                f"leaf targets unknown baseline {leaf.against!r}; "
                f"ensure the baseline exists as primary, a registered soft_check, "
                f"or check for a typo",
            )
        )
        return
    if role is BaselineRole.COMPANION:
        errors.append(
            ValidationError(
                path,
                f"leaf targets companion {leaf.against!r}; companions are "
                f"plot-only overlays and cannot be scored against. Use a "
                f"soft_check (via import-baseline) if you need cross-check "
                f"semantics",
            )
        )
        return
    if role is BaselineRole.SOFT_CHECK and not in_warn:
        errors.append(
            ValidationError(
                path,
                f"leaf targets soft_check {leaf.against!r} without a 'warn' "
                f"ancestor; soft_checks are advisory only and must be "
                f"warn-wrapped so they cannot hard-fail the test",
            )
        )
        return


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
    soft_checks: list[str] = (),
    companions: list[str] = (),
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
