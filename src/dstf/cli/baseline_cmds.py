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


class _MapError(Exception):
    """A ``--map`` the user must fix before anything is registered."""


def _parse_variable_map(raw: list[str] | None) -> dict[str, str]:
    """``["x=sensor_a", ...]`` → ``{"x": "sensor_a"}``.

    Two test variables MAY name one companion variable — that draws the
    one curve on both plots. One test variable named twice is a typo: the
    second would silently win, so it is refused instead.
    """
    mapping: dict[str, str] = {}
    for item in raw or []:
        test_var, sep, companion_var = item.partition("=")
        if not sep or not test_var.strip() or not companion_var.strip():
            raise _MapError(
                f"--map {item!r} is not TEST_VAR=COMPANION_VAR — one test "
                f"variable name, '=', one companion variable name"
            )
        test_var, companion_var = test_var.strip(), companion_var.strip()
        if test_var in mapping:
            raise _MapError(
                f"--map names the test variable {test_var!r} twice "
                f"({mapping[test_var]!r} and {companion_var!r}); a variable "
                f"draws one companion curve"
            )
        mapping[test_var] = companion_var
    return mapping


def _check_variable_map(
    store,
    model_id: str,
    path: Path,
    fmt: str,
    mapping: dict[str, str],
) -> None:
    """Refuse a mapping naming something neither file has.

    A wrong name draws nothing and says nothing, months later, in a
    report — so both sides are checked here, where both files are open:
    the left against the test's stored baseline, the right against the
    companion's own data. A path that does not resolve is left to
    ``add_companion``, which reports it as the registration error it is.
    """
    if not mapping:
        return
    from ..reporting.overlay_loader import companion_variable_names

    reference = store.get_reference(model_id) or {}
    known = {
        entry.get("name")
        for entry in reference.get("variables") or []
        if isinstance(entry, dict)
    }
    unknown = sorted(v for v in mapping if v not in known)
    if unknown:
        raise _MapError(
            f"--map names {', '.join(repr(v) for v in unknown)}, which the "
            f"baseline of {model_id} does not hold. Its variables are: "
            f"{', '.join(sorted(n for n in known if n))}"
        )

    if not path.exists():
        return
    try:
        companion_vars = set(companion_variable_names(path, fmt))
    except Exception as e:
        raise _MapError(f"cannot read {path} to check --map against it: {e}") from e
    missing = sorted(set(mapping.values()) - companion_vars)
    if missing:
        raise _MapError(
            f"--map names {', '.join(repr(v) for v in missing)}, which "
            f"{path} does not hold. Its variables are: "
            f"{', '.join(sorted(companion_vars))}"
        )


def _print_companions(companions: dict) -> None:
    """One line per companion, plus one per mapped variable under it."""
    for name, co in companions.items():
        extra = co.path if co.kind == "external" else co.data_file
        print(f"  {name:<30} kind={co.kind:<8} format={co.format:<5} {extra}")
        for test_var, companion_var in co.variables.items():
            print(f"  {'':<30} {test_var} -> {companion_var}")


def cmd_companion(args: argparse.Namespace) -> int:
    """Manage plot-only companion references (D66).

    Companions are experimental / analytical / rig data shown as plot
    overlays but never scored against. Four actions:
      add <model> <name> <path> [--format csv|json] [--map TEST=COMPANION]
      list [<model>]
      freeze <model> <name>
      remove <model> <name>

    ``--map`` is repeatable and records which of the companion's variables
    draws on which of the test's — the answer the report reads instead of
    guessing from spellings. Two test variables may name one companion
    variable; one test variable may not be named twice.
    """
    _require_positionals(args, "companion", _COMPANION_REQUIRED)
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore
    from ..storage.roles import infer_companion_format

    store = ReferenceStore(config)
    action = args.action

    if action == "add":
        path = Path(args.path)
        fmt = getattr(args, "format", None) or infer_companion_format(str(path))
        try:
            mapping = _parse_variable_map(getattr(args, "variable_map", None))
            _check_variable_map(store, args.model_id, path, fmt, mapping)
            ok = store.add_companion(
                args.model_id,
                args.name,
                path=path,
                format=getattr(args, "format", None),
                variables=mapping,
            )
        except (_MapError, ValueError, FileNotFoundError) as e:
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
            _print_companions(companions)
            return 0
        # All models
        total = 0
        for _tid, entry in sorted(store.index.all_tests().items()):
            if entry["status"] == "obsolete":
                continue
            companions = store.get_companions(entry["model_id"])
            if companions:
                print(f"{entry['model_id']}:")
                _print_companions(companions)
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
