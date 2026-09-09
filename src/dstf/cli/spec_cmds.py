"""Commands that write or describe `test_spec.json`: add, spec-update,
export-schema."""

import argparse
import sys
from pathlib import Path

from ..config import ConfigError
from .common import _build_config


def cmd_add(args: argparse.Namespace) -> int:
    """Add a test to test_spec.json."""
    config = _build_config(args)
    model_id = args.model_id
    variables = args.variables or []

    from ..discovery.spec_parser import add_to_test_spec

    # Determine spec file location
    spec_path = config.spec_path

    added = add_to_test_spec(spec_path, model_id, variables)
    if added:
        var_desc = ", ".join(variables) if variables else "(simulate only)"
        print(f"Added: {model_id}")
        print(f"  Variables: {var_desc}")
        print(f"  Spec file: {spec_path}")
        return 0
    else:
        # Already exists — prompt to overwrite
        try:
            choice = (
                input(f"  {model_id} already exists in spec. Overwrite? [y/n] > ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if choice == "y":
            add_to_test_spec(spec_path, model_id, variables, overwrite=True)
            print(f"Updated: {model_id}")
            return 0
        else:
            print("Skipped.")
            return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    """Update test_spec.json from an RFC 6902 patch envelope (6.4.3).

    The envelope is the only accepted input shape — it is what the reporter's
    export panel emits.
    """
    import json as json_mod

    from ..discovery.patch_apply import PatchError

    config = _build_config(args)
    spec_path = config.spec_path

    json_file = Path(args.json_file)
    if not json_file.exists():
        print(f"File not found: {json_file}")
        return 1

    try:
        update_data = json_mod.loads(json_file.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError) as e:
        print(f"Failed to read {json_file}: {e}")
        return 1

    model_id = update_data.get("model")
    if not model_id:
        print("JSON must contain a 'model' field")
        return 1

    patch = update_data.get("patch")
    if not isinstance(patch, list):
        raise ConfigError(
            f"{json_file}: not a JSON-Patch envelope — expected "
            '{"model": "<model id>", "patch": [<RFC 6902 op>, ...]}, '
            f"got a 'patch' of type {type(patch).__name__}"
        )

    # Mutate in-memory first, then validate metric tree (6.4.4), then write
    # — no partial state on validation failure.
    try:
        _apply_validated_patch(spec_path, model_id, patch, config)
    except PatchError as e:
        print(f"Patch failed: {e}")
        return 1
    print(f"Applied {len(patch)} op(s) to {model_id} → {spec_path}")
    for op in patch:
        print(f"  {op.get('op'):<8} {op.get('path'):<60} {op.get('value', '')}")
    return 0


def _apply_validated_patch(spec_path, model_id, patch, config):
    """Apply a patch with dry-run + validate → then write. Validator
    enforcement lives in `comparison/validator.py`; we only invoke it
    when the patched entry has a `metrics` tree."""
    from ..discovery.patch_apply import apply_patch

    # Stage 0 (review 2026-07-06 finding 41): per-leaf ops against a
    # flat-override entry need the implicit tree materialized first.
    patch = _materialize_implicit_metrics(spec_path, model_id, patch)
    # Stage 1: dry-run apply in memory to check path/whitelist errors.
    mutated = apply_patch(spec_path, model_id, patch, write=False)
    # Stage 2: validate metric tree (if present).
    _validate_entry_metric_tree(mutated, model_id, config)
    # Stage 3: commit.
    apply_patch(spec_path, model_id, patch, write=True)


def _materialize_implicit_metrics(spec_path, model_id, patch):
    """Prepend an op materializing the implicit metrics tree when needed.

    review 2026-07-06 finding 41: the reporter's per-leaf patch ops target
    ``/metrics/children/N/...``, but tests defined via flat
    ``comparison.variable_overrides`` (the primary documented format) have
    no ``metrics`` block — ``patch_apply`` refuses to auto-create an array
    parent, so no scalar edit on a flat-override test could round-trip.
    Materialize the SAME implicit tree the reporter displayed (one leaf
    per tracked variable, params from ``comparison.variable_overrides``,
    wrapped in an AND — see ``synthesize_implicit_tree``) as a leading
    ``add /metrics`` op, then let the per-leaf ops land on it.
    """
    import json as json_mod

    from ..comparison.tree_spec import spec_to_raw, synthesize_implicit_tree
    from ..discovery.patch_apply import PatchError

    needs_tree = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in patch
    )
    if not needs_tree or not spec_path.exists():
        return patch
    try:
        data = json_mod.loads(spec_path.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError):
        return patch  # let apply_patch surface the real read error
    tests = data.get("tests") if isinstance(data, dict) else None
    entry = next(
        (
            e
            for e in (tests or [])
            if isinstance(e, dict) and e.get("model") == model_id
        ),
        None,
    )
    if entry is None or "metrics" in entry:
        return patch
    variables = entry.get("variables") or []
    if not variables:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for {model_id!r}: "
            "the entry declares no tracked variables to build leaves from"
        )
    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    tree = synthesize_implicit_tree(variables, variable_overrides=overrides)
    print(
        f"Note: materialized implicit metrics tree for {model_id} "
        f"({len(variables)} leaves) — entry had no 'metrics' block"
    )
    return [{"op": "add", "path": "/metrics", "value": spec_to_raw(tree)}, *patch]


def _validate_entry_metric_tree(data, model_id, config):
    """When the patched entry has a `metrics` tree, run the D66 validator.

    Skipped when the entry has no tree (legacy implicit-AND) or when the
    validator's baseline-role lookup can't be built (missing ref store).
    """
    entry = None
    for e in data.get("tests", []):
        if isinstance(e, dict) and e.get("model") == model_id:
            entry = e
            break
    if entry is None or "metrics" not in entry:
        return

    from ..comparison.tree_spec import parse_metric_tree
    from ..comparison.validator import role_lookup_from_store, validate_tree
    from ..discovery.patch_apply import PatchError
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


def cmd_export_schema(args: argparse.Namespace) -> int:
    """Emit a JSON-Schema document describing ``test_spec.json`` (6.4.5)."""
    import json as json_mod

    from ..reporting.schema_export import build_schema

    schema = build_schema()
    text = json_mod.dumps(schema, indent=2) + "\n"
    out = getattr(args, "output", None)
    if out:
        # LF on every platform — the schema gets committed.
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote schema to {out}")
    else:
        sys.stdout.write(text)
    return 0
