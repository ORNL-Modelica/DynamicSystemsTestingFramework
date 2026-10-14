"""The `spec` group — commands that write or describe `test_spec.json`:
`spec add`, `spec update`, and `spec schema` (which also describes
`testing.json`)."""

import argparse
import os
import sys
from pathlib import Path

from ..config import CONFIG_FILENAME
from .common import _build_config

#: `spec schema <file>`: the two files a schema is written for, by the name
#: each is asked for. The first is the default.
SPEC_FILE = "test_spec.json"
SCHEMAS = {"test_spec": SPEC_FILE, "testing": CONFIG_FILENAME}


def _not_added(config, model_id: str, source: str | None) -> str | None:
    """Why ``spec add`` writes no entry for ``model_id``, as one sentence,
    or None. The entry must be one a run can simulate: a Modelica project's
    library holds its class (:func:`~dstf.discovery.mo_parser.find_class`),
    a model — not the library, a package, a function, a record or a
    connector, and not partial — and a test of a backend that runs a file
    (``Backend.source_suffix``) names one of that suffix that is there —
    ``--source``, which no other backend reads."""
    from ..backends import get_backend_class

    # The backend the entry IS, not the entry's name: an entry named
    # "Python 3.12" is the Python backend's.
    suffix = get_backend_class(config).source_suffix
    if suffix is None and source is not None:
        return (
            f"--source does not apply: a test of the {config.backend} backend "
            f"runs no file its entry names; nothing was added."
        )
    if suffix is not None and source is None:
        return (
            f"--source is required: a test of the {config.backend} backend runs "
            f"the {suffix} file its entry names (--source FILE); nothing was "
            f"added."
        )
    if source is not None and suffix is not None:
        if Path(source).is_dir():
            return (
                f"--source {source}: a directory, not a {suffix} file; nothing "
                f"was added."
            )
        if not Path(source).is_file():
            return f"--source {source}: no such file; nothing was added."
        if Path(source).suffix.lower() != suffix.lower():
            return (
                f"--source {source}: a test of the {config.backend} backend runs "
                f"a {suffix} file; nothing was added."
            )
    if config.source_type != "modelica":
        return None
    from ..discovery.mo_parser import find_class

    if model_id == config.library_name:
        return (
            f"{model_id} is the library itself, not a model: name one of its "
            f"models; nothing was added."
        )
    found = find_class(config.library_dir, config.library_name, model_id)
    if found is None:
        return (
            f"{model_id} is no class of the library {config.library_name} "
            f"({config.library_dir}): nothing was added."
        )
    if found.kind == "package":
        return (
            f"{model_id} is a package of the library {config.library_name}, "
            f"which holds models and is none: name one of its models; nothing "
            f"was added."
        )
    if found.kind not in ("model", "block", "class"):
        return (
            f"{model_id} is a {found.kind}, not a model: no run can simulate "
            f"it; nothing was added."
        )
    if found.partial:
        return (
            f"{model_id} is a partial {found.kind}: no run can simulate it; "
            f"nothing was added."
        )
    return None


def cmd_spec_add(args: argparse.Namespace) -> int:
    """Add a test to test_spec.json."""
    config = _build_config(args)
    model_id = args.model_id
    variables = args.variables if args.variables is not None else []

    refused = _not_added(config, model_id, args.source)
    if refused is not None:
        print(refused, file=sys.stderr)
        return 1

    from ..discovery.spec_parser import add_to_test_spec

    # Determine spec file location
    spec_path = config.spec_path
    # `source` is relative to the spec file's directory, as the parser
    # reads it (`spec_parser`), with `/` on every system.
    source = (
        None
        if args.source is None
        else Path(
            os.path.relpath(os.path.abspath(args.source), spec_path.parent)
        ).as_posix()
    )

    added = add_to_test_spec(spec_path, model_id, variables, source=source)
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
            add_to_test_spec(
                spec_path, model_id, variables, overwrite=True, source=source
            )
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

    from ..discovery.patch_apply import PatchError, load_spec_document
    from ..discovery.patch_envelope import parse_envelope
    from ..discovery.spec_update import (
        apply_validated_patches,
        not_stored_notes,
        spec_entry,
    )
    from ..storage.reference_store import ReferenceStore

    groups = parse_envelope(update_data, label=str(json_file))
    requested = [(g.model_id, g.ops) for g in groups]
    if not requested:
        print(f"Nothing to apply: {json_file} carries no patches")
        return 0
    store = ReferenceStore(config)
    try:
        applied = apply_validated_patches(spec_path, requested, config, store)
    except PatchError as e:
        print(f"Patch failed: {e}")
        return 1
    config.write_default_file()
    written = load_spec_document(spec_path)
    _print_applied(
        applied,
        requested,
        spec_path,
        {
            model_id: not_stored_notes(spec_entry(written, model_id), model_id, store)
            for model_id, _ in applied
        },
    )
    return 0


def _print_applied(applied, requested, spec_path, not_stored) -> None:
    """One block per model: the materialization note when the applied ops
    outgrew the requested ones, then the requested ops, then a line per
    baseline its tree names that this folder does not store (``not_stored``,
    by model): written, and not scored here."""
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
        for line in not_stored[model_id]:
            print(line)


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
