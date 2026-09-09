"""Applying a validated patch set to ``test_spec.json``.

The one read-modify-write both ``dstf spec-update`` and the authoring
Session's ``save`` go through: materialize the implicit tree where a
per-leaf op needs one, apply each model's ops to its entry, run the D66
validator on every touched tree, then write once. Nothing is written
unless every group passes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from ..backends.types import is_glob_pattern
from ..config import Config
from ..storage.ref_index import _atomic_write_text
from .patch_apply import PatchError, apply_patch_to_document, load_spec_document


def spec_entry(data: dict, model_id: str) -> dict | None:
    """The first entry for ``model_id`` — first wins, as ``apply_patch`` and
    discovery agree — or ``None``."""
    for e in data.get("tests", []):
        if isinstance(e, dict) and e.get("model") == model_id:
            return e
    return None


def is_literal_variable(pattern: str) -> bool:
    """A tracked-variable entry that names one variable. The resolver's rule:
    ``*`` and ``?`` are wildcards, a Modelica array index ``[1]`` is not."""
    return not is_glob_pattern(pattern)


def implicit_tree_op(entry: dict) -> dict:
    """The ``add /metrics`` op that materializes the implicit tree a
    flat-override entry scores with: one leaf per tracked variable, params
    from ``comparison.variable_overrides``, wrapped in ``and`` (see
    ``synthesize_implicit_tree``). Raises :class:`PatchError` when the entry
    declares no variables to build leaves from."""
    from ..comparison.tree_spec import spec_to_raw, synthesize_implicit_tree

    variables = entry.get("variables") or []
    if not variables:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: the entry declares no tracked variables "
            "to build leaves from"
        )
    globs = [v for v in variables if not is_literal_variable(v)]
    if globs:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: {globs!r} are patterns, and a leaf per "
            "pattern would score a sentinel where the flat path scored the "
            "variables the result resolved"
        )
    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    tree = synthesize_implicit_tree(variables, variable_overrides=overrides)
    return {"op": "add", "path": "/metrics", "value": spec_to_raw(tree)}


def materialize_implicit_metrics(
    entry: dict | None, patch: Sequence[dict]
) -> list[dict]:
    """``patch`` with :func:`implicit_tree_op` prepended when some op edits
    under ``/metrics/`` and neither the entry nor the patch itself supplies
    the tree.

    review 2026-07-06 finding 41: per-leaf ops target
    ``/metrics/children/N/...``, but a flat-override entry has no ``metrics``
    block and ``patch_apply`` refuses to auto-create an array parent. A
    missing entry is left to ``apply_patch`` (which creates it and then
    reports the real failure)."""
    ops = list(patch)
    if entry is None or "metrics" in entry:
        return ops
    edits_under = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in ops
    )
    supplies_tree = any(
        isinstance(op, dict) and op.get("path") == "/metrics" for op in ops
    )
    if not edits_under or supplies_tree:
        return ops
    return [implicit_tree_op(entry), *ops]


def validate_entry_metric_tree(data: dict, model_id: str, config: Config) -> None:
    """When the patched entry has a ``metrics`` tree, run the D66 validator
    against the reference store's baseline roles. Skipped when the entry has
    no tree (implicit AND)."""
    entry = spec_entry(data, model_id)
    if entry is None or "metrics" not in entry:
        return

    from ..comparison.tree_spec import parse_metric_tree
    from ..comparison.validator import role_lookup_from_store, validate_tree
    from ..storage.reference_store import ReferenceStore

    try:
        tree = parse_metric_tree(entry["metrics"])
    except Exception as e:
        raise PatchError(f"patched metric tree is invalid: {e}") from e

    store = ReferenceStore(config)
    lookup = role_lookup_from_store(store, model_id)
    errors = validate_tree(tree, lookup)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise PatchError(f"validator rejected the patched tree: {msg}")


def apply_validated_patches(
    spec_path: Path,
    groups: Sequence[tuple[str, Sequence[dict]]],
    config: Config,
) -> list[tuple[str, list[dict]]]:
    """Apply every group's ops to its model's entry in one read-modify-write.

    Per group: materialize, apply, validate. The file is written once,
    atomically and LF (D122), only after every group passed. Returns the ops
    actually applied per group — materialization included — so a caller can
    say what it did."""
    if not groups:
        return []  # nothing to apply; the file is not rewritten
    data = load_spec_document(spec_path)
    applied: list[tuple[str, list[dict]]] = []
    for model_id, ops in groups:
        full = materialize_implicit_metrics(spec_entry(data, model_id), ops)
        apply_patch_to_document(data, model_id, full)
        validate_entry_metric_tree(data, model_id, config)
        applied.append((model_id, full))
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")
    return applied
