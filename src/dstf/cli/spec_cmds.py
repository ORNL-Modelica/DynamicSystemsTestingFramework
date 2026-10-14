"""The `spec` group — commands that write or describe `test_spec.json`:
`spec add`, `spec update`, and `spec schema` (which also describes
`testing.json`)."""

import argparse
import sys
from pathlib import Path

from ..config import CONFIG_FILENAME
from .common import _build_config

#: `spec schema <file>`: the two files a schema is written for, by the name
#: each is asked for. The first is the default.
SPEC_FILE = "test_spec.json"
SCHEMAS = {"test_spec": SPEC_FILE, "testing": CONFIG_FILENAME}


def cmd_spec_add(args: argparse.Namespace) -> int:
    """Add a test to test_spec.json."""
    config = _build_config(args)
    model_id = args.model_id
    variables = args.variables if args.variables is not None else []

    from ..discovery.spec_parser import add_to_test_spec

    # Determine spec file location
    spec_path = config.spec_path

    added = add_to_test_spec(spec_path, model_id, variables)
    if added:
        config.write_default_file()
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
            config.write_default_file()
            print(f"Updated: {model_id}")
            return 0
        else:
            print("Skipped.")
            return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    """Update test_spec.json from an RFC 6902 patch envelope (6.4.3).

    The envelope is the only accepted input shape: the multi-model form the
    authoring tool exports, or the single-model form older files carry.
    """
    import json as json_mod

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

    from ..discovery.patch_apply import PatchError
    from ..discovery.patch_envelope import parse_envelope
    from ..discovery.spec_update import apply_validated_patches

    groups = parse_envelope(update_data, label=str(json_file))
    requested = [(g.model_id, g.ops) for g in groups]
    if not requested:
        print(f"Nothing to apply: {json_file} carries no patches")
        return 0
    try:
        applied = apply_validated_patches(spec_path, requested, config)
    except PatchError as e:
        print(f"Patch failed: {e}")
        return 1
    config.write_default_file()
    _print_applied(applied, requested, spec_path)
    return 0


def _print_applied(applied, requested, spec_path) -> None:
    """One block per model: the materialization note when the applied ops
    outgrew the requested ones, then the requested ops."""
    for (model_id, full), (_, ops) in zip(applied, requested):
        if len(full) > len(ops):
            # The implicit tree is an `and` of one leaf per criterion the
            # flat path scored: counted as what every other surface calls
            # them, and in the number's own noun (it read `(1 leaves)`).
            n = len(full[0]["value"]["children"])
            print(
                f"Note: materialized implicit metrics tree for {model_id} "
                f"({n} {'criterion' if n == 1 else 'criteria'}) — entry had no "
                "'metrics' block"
            )
        print(f"Applied {len(ops)} op(s) to {model_id} → {spec_path}")
        for op in ops:
            print(f"  {op.get('op'):<8} {op.get('path'):<60} {op.get('value', '')}")


def cmd_spec_schema(args: argparse.Namespace) -> int:
    """Emit a JSON-Schema document describing ``test_spec.json`` (6.4.5),
    or — ``spec schema testing`` — the project's ``testing.json``."""
    import json as json_mod

    from ..reporting.schema_export import build_config_schema, build_schema

    if SCHEMAS[args.schema] == CONFIG_FILENAME:
        schema = build_config_schema()
    else:
        schema = build_schema()
    text = json_mod.dumps(schema, indent=2) + "\n"
    out = args.output
    if out is not None:
        # LF on every platform — the schema gets committed.
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote schema to {out}")
    else:
        sys.stdout.write(text)
    return 0
