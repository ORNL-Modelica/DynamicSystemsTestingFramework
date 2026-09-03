"""Baseline-role commands (D66): companion, soft-check, import-baseline."""

import argparse
from pathlib import Path

from ..config import ConfigError
from .common import _build_config

# Positionals each action needs, in CLI order. The parser declares them all
# ``nargs="?"`` because they're per-action — argparse can't express "required
# for add/freeze/remove, optional for list" — so the requirement is enforced
# here and reported as a usage error (exit 2), not a TypeError traceback out
# of the store call.
_COMPANION_REQUIRED = {
    "add": ("model_id", "name", "path"),
    "freeze": ("model_id", "name"),
    "remove": ("model_id", "name"),
    "list": (),
}
_SOFT_CHECK_REQUIRED = {
    "remove": ("model_id", "name"),
    "list": (),
}


def _require_positionals(
    args: argparse.Namespace,
    command: str,
    required: dict[str, tuple[str, ...]],
) -> None:
    """Raise :class:`ConfigError` naming the missing positionals for
    ``<command> <action>``. No-op when everything the action needs is set."""
    names = required.get(args.action, ())
    missing = [n for n in names if getattr(args, n, None) is None]
    if not missing:
        return
    usage = " ".join(f"<{n}>" for n in names)
    raise ConfigError(
        f"{command} {args.action}: missing required argument(s): "
        f"{', '.join(missing)}. Usage: dstf {command} {args.action} {usage}"
    )


def cmd_companion(args: argparse.Namespace) -> int:
    """Manage plot-only companion references (D66).

    Companions are experimental / analytical / rig data shown as plot
    overlays but never scored against. Four actions:
      add <model> <name> <path> [--format csv|json]
      list [<model>]
      freeze <model> <name>
      remove <model> <name>
    """
    _require_positionals(args, "companion", _COMPANION_REQUIRED)
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "add":
        try:
            ok = store.add_companion(
                args.model_id,
                args.name,
                path=Path(args.path),
                format=getattr(args, "format", None),
            )
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}")
            return 1
        print(
            f"Registered companion {args.name!r} → {args.path}"
            if ok
            else f"Failed to register companion {args.name!r}"
        )
        return 0 if ok else 1

    if action == "list":
        model_id = getattr(args, "model_id", None)
        if model_id:
            companions = store.get_companions(model_id)
            if not companions:
                print(f"No companions registered for {model_id}")
                return 0
            print(f"Companions for {model_id}:")
            for name, co in companions.items():
                extra = co.path if co.kind == "external" else co.data_file
                print(f"  {name:<30} kind={co.kind:<8} format={co.format:<5} {extra}")
            return 0
        # All models
        total = 0
        for _tid, entry in sorted(store.index.all_tests().items()):
            if entry["status"] == "obsolete":
                continue
            companions = store.get_companions(entry["model_id"])
            if companions:
                print(f"{entry['model_id']}:")
                for name, co in companions.items():
                    extra = co.path if co.kind == "external" else co.data_file
                    print(
                        f"  {name:<30} kind={co.kind:<8} format={co.format:<5} {extra}"
                    )
                total += len(companions)
        if total == 0:
            print("No companions registered.")
        return 0

    if action == "freeze":
        try:
            ok = store.freeze_companion(args.model_id, args.name)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        if ok:
            print(f"Frozen companion {args.name!r} for {args.model_id}")
        else:
            print(f"Companion {args.name!r} is already frozen or does not exist")
        return 0

    if action == "remove":
        ok = store.remove_companion(args.model_id, args.name)
        if ok:
            print(f"Removed companion {args.name!r} from {args.model_id}")
        else:
            print(f"Companion {args.name!r} not found on {args.model_id}")
        return 0 if ok else 1

    return 1


def cmd_soft_check(args: argparse.Namespace) -> int:
    """Manage soft_check baselines (advisory cross-checks)."""
    _require_positionals(args, "soft-check", _SOFT_CHECK_REQUIRED)
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "list":
        model_id = getattr(args, "model_id", None)
        if model_id:
            checks = store.get_soft_checks(model_id)
            if not checks:
                print(f"No soft_checks registered for {model_id}")
                return 0
            print(f"Soft_checks for {model_id}:")
            for name, bl in checks.items():
                origin = (
                    bl.provenance.get("source") or bl.provenance.get("origin") or "-"
                )
                print(f"  {name:<30} n_vars={len(bl.variables):<4} origin={origin}")
            return 0
        total = 0
        for _tid, entry in sorted(store.index.all_tests().items()):
            if entry["status"] == "obsolete":
                continue
            checks = store.get_soft_checks(entry["model_id"])
            if checks:
                print(f"{entry['model_id']}:")
                for name, bl in checks.items():
                    origin = (
                        bl.provenance.get("source")
                        or bl.provenance.get("origin")
                        or "-"
                    )
                    print(f"  {name:<30} n_vars={len(bl.variables):<4} origin={origin}")
                total += len(checks)
        if total == 0:
            print("No soft_checks registered.")
        return 0

    if action == "remove":
        ok = store.remove_soft_check(args.model_id, args.name)
        if ok:
            print(f"Removed soft_check {args.name!r} from {args.model_id}")
        else:
            print(f"Soft_check {args.name!r} not found on {args.model_id}")
        return 0 if ok else 1

    return 1


def cmd_import_baseline(args: argparse.Namespace) -> int:
    """Import another regression system's primary as a soft_check (D66).

    Reads a reference-shape JSON from ``source_path`` (same schema a
    ``ref_NNNN.json`` file uses at its top level) and stores it under
    the local model's soft_checks with the given name.
    """
    import json as json_mod

    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    source = Path(args.import_source)
    if not source.exists():
        print(f"File not found: {source}")
        return 1
    try:
        data = json_mod.loads(source.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError) as e:
        print(f"Failed to read {source}: {e}")
        return 1

    time = data.get("time") or []
    variables = data.get("variables") or []
    if not time or not variables:
        print("Source file missing 'time' or 'variables' — cannot import")
        return 1

    provenance = dict(data.get("provenance") or {})
    provenance.setdefault("origin", "import-baseline")
    provenance.setdefault("imported_from", str(source))

    try:
        ok = store.add_soft_check(
            args.model_id,
            args.name,
            time=time,
            variables=variables,
            diagnostics=data.get("diagnostics"),
            simulation=data.get("simulation"),
            statistics=data.get("statistics"),
            provenance=provenance,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    if ok:
        print(f"Imported {source} → soft_check {args.name!r} on {args.model_id}")
    return 0 if ok else 1
