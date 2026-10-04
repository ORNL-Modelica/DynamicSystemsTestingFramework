"""Applying a validated patch set to ``test_spec.json``.

The one read-modify-write both ``dstf spec-update`` and the authoring
Session's ``save`` go through: materialize the implicit tree where a
per-leaf op needs one, apply each model's ops to its entry, refuse a base
tolerance the patch made no number, run the D66 validator on every touched
tree, then write once. Nothing is written
unless every group passes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern
from ..config import Config
from ..storage.ref_index import _atomic_write_text
from .patch_apply import PatchError, apply_patch_to_document, load_spec_document

if TYPE_CHECKING:
    from ..storage.reference_store import ReferenceStore


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


def unknown_override_modes(entry: dict) -> dict[str, str]:
    """``{variable: mode}`` for every flat override naming a ``mode`` no
    metric is registered under. The flat path fails such a variable loudly
    (a typo must not change the algorithm); ``synthesize_implicit_tree``
    would fall back to nrmse, so a tree must not be written for it."""
    from ..comparison.registry import metric

    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    return {
        var: str(ov["mode"])
        for var, ov in overrides.items()
        if isinstance(ov, dict) and ov.get("mode") and metric(str(ov["mode"])) is None
    }


def implicit_tree_op(entry: dict, *, variables: Sequence[str] | None = None) -> dict:
    """The ``add /metrics`` op that materializes the implicit tree a
    flat-override entry scores with: one leaf per tracked variable, params
    from ``comparison.variable_overrides``, wrapped in ``and`` (see
    ``synthesize_implicit_tree``). ``variables`` replaces the entry's own
    list: the authoring Session passes the names the flat path actually
    scored, which for a test tracked by its ``.mo`` annotation are not in
    the spec at all (``spec-update``, with no result in hand, can only use
    the entry's). Raises :class:`PatchError` when there are no variables to
    build leaves from."""
    from ..comparison.tree_spec import spec_to_raw, synthesize_implicit_tree

    variables = (
        list(variables) if variables is not None else entry.get("variables") or []
    )
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
    bad_modes = unknown_override_modes(entry)
    if bad_modes:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: {bad_modes!r} name a mode no metric is "
            "registered under; the flat path fails them, a tree would score nrmse"
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
    if not needs_materialization(entry, ops):
        return ops
    assert entry is not None
    return [implicit_tree_op(entry), *ops]


def has_metrics(entry: dict | None) -> bool:
    """Whether ``entry`` has a ``metrics`` tree of its own. ``"metrics":
    null`` is none: spec_parser scores such an entry by the flat path, so
    every "has it a tree" question — here and in the authoring Session —
    asks this, and none tests the key."""
    return entry is not None and entry.get("metrics") is not None


def needs_materialization(entry: dict | None, patch: Sequence[dict]) -> bool:
    """Some op edits under ``/metrics/`` and neither the entry nor the patch
    itself supplies the tree (a missing entry is left to ``apply_patch``)."""
    if entry is None or has_metrics(entry):
        return False
    edits_under = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in patch
    )
    supplies_tree = any(
        isinstance(op, dict) and op.get("path") == "/metrics" for op in patch
    )
    return edits_under and not supplies_tree


def validate_entry_metric_tree(
    data: dict, model_id: str, config: Config, store: ReferenceStore | None = None
) -> None:
    """When the patched entry has a ``metrics`` tree, run the D66 validator
    against the reference store's baseline roles. Skipped when the entry has
    no tree (implicit AND). ``store`` saves the caller that already holds one
    (the Session) a rescan of the whole partition per call."""
    entry = spec_entry(data, model_id)
    if entry is None or not has_metrics(entry):
        return

    from ..comparison.tree_spec import MetricSpecError, parse_metric_tree
    from ..comparison.validator import role_lookup_from_store, validate_tree
    from ..storage.reference_store import ReferenceStore

    try:
        tree = parse_metric_tree(entry["metrics"])
    except MetricSpecError as e:
        # The reason drops the path ("metrics.window: The window must end
        # after ..."): the tool shows the reason beside the row it names.
        raise PatchError(
            f"patched metric tree is invalid: {e}", reason=e.message
        ) from e
    except Exception as e:
        raise PatchError(f"patched metric tree is invalid: {e}", reason=str(e)) from e

    if store is None:
        store = ReferenceStore(config)
    lookup = role_lookup_from_store(store, model_id)
    errors = validate_tree(tree, lookup)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise PatchError(
            f"validator rejected the patched tree: {msg}",
            # Each message is a sentence that ends in its own full stop.
            reason=" ".join(e.message for e in errors),
        )


def _base_tolerance(entry: dict | None) -> object:
    """The entry's ``comparison.tolerance`` as written, or ``None``."""
    comparison = (entry or {}).get("comparison")
    return comparison.get("tolerance") if isinstance(comparison, dict) else None


def _same_base(before: object, after: object) -> bool:
    """Whether the patch left the base as it was: the same JSON text, keys
    sorted. Not ``==``: Python's ``True == 1`` at any depth (``[1] → [true]``
    is a change), and a NaN is not equal to itself (writing the NaN already
    there is none) — as text, a bool is ``true`` and a NaN is ``NaN``."""

    def text(value: object) -> str:
        return json.dumps(value, sort_keys=True, allow_nan=True)

    return text(before) == text(after)


def validate_entry_base_tolerance(data: dict, model_id: str, before: object) -> None:
    """The patched entry's base tolerance is ONE finite number, or unset —
    when the patch changed it from ``before``. The comparison refuses a test
    whose base is anything else; a patch must not write one. One already
    there is the comparison's to refuse, not this patch's: refusing it here
    would leave no patch able to edit the entry, or remove it."""
    from ..comparison.sides import base_tolerance_refused, is_base_tolerance

    after = _base_tolerance(spec_entry(data, model_id))
    if after is None or is_base_tolerance(after) or _same_base(before, after):
        return
    text = base_tolerance_refused("test_spec.json comparison.tolerance", after)
    raise PatchError(text, reason=text)


def validate_entry_plots(data: dict, model_id: str) -> None:
    """The patched entry's ``plots`` extras, when it has the key, parse."""
    from .plots import PlotsSpecError, parse_plots

    entry = spec_entry(data, model_id)
    if entry is None or "plots" not in entry:
        return
    try:
        parse_plots(entry["plots"], path=f"tests[{model_id}].plots")
    except PlotsSpecError as e:
        raise PatchError(f"patched plots are invalid: {e}", reason=str(e)) from e


def apply_validated_patches(
    spec_path: Path,
    groups: Sequence[tuple[str, Sequence[dict]]],
    config: Config,
    store: ReferenceStore | None = None,
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
        before = _base_tolerance(spec_entry(data, model_id))
        apply_patch_to_document(data, model_id, full)
        validate_entry_base_tolerance(data, model_id, before)
        validate_entry_metric_tree(data, model_id, config, store)
        validate_entry_plots(data, model_id)
        applied.append((model_id, full))
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")
    return applied
