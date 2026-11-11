"""Baseline commands: `baseline diff` and `baseline plot`, and the
baseline-role groups (D66) `companion` and `soft-check`, one handler per
action."""

import argparse
import sys
from pathlib import Path

from ..backends.types import variable_name
from ..config import CONFIG_FILENAME, ConfigError, config_file_named, load_config_file
from .common import _build_config

#: What `soft-check add` records as a soft_check's origin when its source
#: file names none.
SOFT_CHECK_ORIGIN = "soft-check add"


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
        # Both in the one spelling of a name (#28): `a[1, 1]` is `a[1,1]`.
        test_var = variable_name(test_var.strip())
        companion_var = variable_name(companion_var.strip())
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
    ``add_companion``, which reports it as the registration error it is,
    and so is a model with no stored baseline at all: there is nothing to
    check the left-hand names against, and "no primary baseline" is the
    error the user needs, not an empty list of the variables one holds.
    """
    if not mapping:
        return
    from ..reporting.overlay_loader import companion_variable_names

    reference = store.get_reference(model_id)
    if reference is None:
        return
    known = set(reference.baseline.names())
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


def _store(args: argparse.Namespace):
    """The reference store of the partition the command line names."""
    from ..storage.reference_store import ReferenceStore

    return ReferenceStore(_build_config(args))


def cmd_companion_add(args: argparse.Namespace) -> int:
    """Register a file as a plot-only companion of a test (D66).

    Companions are experimental / analytical / rig data shown as plot
    overlays but never scored against. ``--map`` is repeatable and records
    which of the companion's variables draws on which of the test's — the
    answer the report reads instead of guessing from spellings. Two test
    variables may name one companion variable; one test variable may not be
    named twice. Only ``add`` writes the record, so only ``add`` has
    ``--map``.
    """
    from ..storage.roles import infer_companion_format

    store = _store(args)
    path = Path(args.path)
    fmt = args.format if args.format is not None else infer_companion_format(str(path))
    try:
        mapping = _parse_variable_map(args.variable_map)
        _check_variable_map(store, args.model_id, path, fmt, mapping)
        ok = store.add_companion(
            args.model_id,
            args.name,
            path=path,
            format=args.format,
            variables=mapping,
        )
    except (_MapError, ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    if ok:
        store.config.write_default_file()
    print(
        f"Registered companion {args.name!r} → {args.path}"
        if ok
        else f"Failed to register companion {args.name!r}"
    )
    return 0 if ok else 1


def cmd_companion_list(args: argparse.Namespace) -> int:
    """List one test's companions, or every test's."""
    store = _store(args)
    if args.model_id is not None:
        companions = store.get_companions(args.model_id)
        if not companions:
            print(f"No companions registered for {args.model_id}")
            return 0
        print(f"Companions for {args.model_id}:")
        _print_companions(companions)
        return 0
    total = 0
    # In model-id order: a baseline's name is a code, whose order means
    # nothing to a reader.
    for model_id in store.list_models():
        companions = store.get_companions(model_id)
        if companions:
            print(f"{model_id}:")
            _print_companions(companions)
            total += len(companions)
    if total == 0:
        print("No companions registered.")
    return 0


def cmd_companion_freeze(args: argparse.Namespace) -> int:
    """Copy an external companion's data into the reference storage."""
    store = _store(args)
    if args.name not in store.get_companions(args.model_id):
        print(f"Companion {args.name!r} not found on {args.model_id}")
        return 1
    try:
        ok = store.freeze_companion(args.model_id, args.name)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    if ok:
        store.config.write_default_file()
        print(f"Frozen companion {args.name!r} for {args.model_id}")
    else:
        print(f"Companion {args.name!r} of {args.model_id} is already frozen")
    return 0


def cmd_companion_remove(args: argparse.Namespace) -> int:
    """Deregister a companion."""
    ok = _store(args).remove_companion(args.model_id, args.name)
    if ok:
        print(f"Removed companion {args.name!r} from {args.model_id}")
    else:
        print(f"Companion {args.name!r} not found on {args.model_id}")
    return 0 if ok else 1


def _print_soft_checks(checks: dict) -> None:
    """One line per soft_check: its name, variable count and origin."""
    for name, bl in checks.items():
        origin = bl.provenance.get("source") or bl.provenance.get("origin") or "-"
        print(f"  {name:<30} n_vars={len(bl.names()):<4} origin={origin}")


def cmd_soft_check_list(args: argparse.Namespace) -> int:
    """List one test's soft_checks (advisory cross-checks), or every test's."""
    store = _store(args)
    if args.model_id is not None:
        checks = store.get_soft_checks(args.model_id)
        if not checks:
            print(f"No soft_checks registered for {args.model_id}")
            return 0
        print(f"Soft_checks for {args.model_id}:")
        _print_soft_checks(checks)
        return 0
    total = 0
    # In model-id order: a baseline's name is a code, whose order means
    # nothing to a reader.
    for model_id in store.list_models():
        checks = store.get_soft_checks(model_id)
        if checks:
            print(f"{model_id}:")
            _print_soft_checks(checks)
            total += len(checks)
    if total == 0:
        print("No soft_checks registered.")
    return 0


def cmd_soft_check_remove(args: argparse.Namespace) -> int:
    """Deregister a soft_check."""
    ok = _store(args).remove_soft_check(args.model_id, args.name)
    if ok:
        print(f"Removed soft_check {args.name!r} from {args.model_id}")
    else:
        print(f"Soft_check {args.name!r} not found on {args.model_id}")
    return 0 if ok else 1


def cmd_soft_check_add(args: argparse.Namespace) -> int:
    """Store another regression system's primary as a soft_check (D66).

    Reads a reference-shape JSON from ``source`` (same schema a
    ``ref_<code>.json`` file uses at its top level) and stores it under
    the local model's soft_checks with the given name.
    """
    import dataclasses
    import json as json_mod

    from ..storage.baseline_codec import (
        UndecodableBaseline,
        decode_series,
        read_body,
        unread_variable_keys,
    )

    store = _store(args)
    source = Path(args.source)
    if not source.exists():
        print(f"File not found: {source}")
        return 1
    # Read by the rule a run reads the soft check by (read_soft_check): a
    # source it would skip is refused here, before anything is written.
    try:
        data = read_body(source)
    except (json_mod.JSONDecodeError, UndecodableBaseline, OSError) as e:
        print(f"Failed to read {source}: {e}")
        return 1

    baseline = decode_series(data, args.name)
    if not baseline.time or not baseline.variables:
        print("Source file missing 'time' or 'variables' — cannot import")
        return 1

    provenance = dict(baseline.provenance)
    provenance.setdefault("origin", SOFT_CHECK_ORIGIN)
    provenance.setdefault("imported_from", str(source))

    try:
        ok = store.add_soft_check(
            args.model_id,
            dataclasses.replace(baseline, provenance=provenance),
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    if ok:
        store.config.write_default_file()
        print(f"Imported {source} → soft_check {args.name!r} on {args.model_id}")
        dropped = unread_variable_keys(data)
        if dropped:
            print(
                "Warning: dropped variable keys nothing in DSTF reads: "
                + "; ".join(
                    f"{key} ({_first_names(names)})" for key, names in dropped.items()
                )
            )
    return 0 if ok else 1


def _first_names(names: list[str], shown: int = 3) -> str:
    """Up to *shown* of *names*, then ``and N more``."""
    out = ", ".join(names[:shown])
    return out + (f" and {len(names) - shown} more" if len(names) > shown else "")


# --- baseline diff -------------------------------------------------------------


def _diff_root(args: argparse.Namespace) -> Path:
    """The reference root `baseline diff` and `baseline plot` read: ``--reference-root``; else
    the ``reference_root`` of the testing.json ``--config`` names (or the one
    in the current directory), relative to its directory, or that directory
    itself when it is named ReferenceResults. The library is never resolved:
    a checkout of the references alone (a pull request's CI) holds none."""
    named = getattr(args, "reference_root", None)
    if named is not None:
        return Path(named)
    config = getattr(args, "config", None)
    if config is not None:
        path = config_file_named(Path(config))
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
    else:
        path = Path.cwd() / CONFIG_FILENAME
        if not path.is_file():
            raise ConfigError(
                "no reference root: pass --reference-root DIR, or --config "
                "naming a testing.json"
            )
    data = load_config_file(path)
    if data.get("reference_root"):
        return path.parent / data["reference_root"]
    if path.parent.name == "ReferenceResults":
        return path.parent
    raise ConfigError(
        f"{path} names no reference_root and is not in a ReferenceResults "
        "folder: pass --reference-root DIR"
    )


def cmd_baseline_diff(args: argparse.Namespace) -> int:
    """Print (or write to --output) which baselines changed between two
    revisions of the reference root, as Markdown or JSON."""
    from ..reporting.baseline_diff_text import (
        GITHUB_LIMIT,
        as_json,
        as_markdown,
        detailed,
    )
    from ..storage.baseline_diff import DiffError, diff_baselines
    from ..storage.git_revisions import GitError

    try:
        root = _diff_root(args)
        diff = diff_baselines(root, args.rev1, args.rev2, against=args.against)
        details = detailed(diff, args.details) if args.details is not None else []
    except (ConfigError, GitError, DiffError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        text = as_json(diff)
    else:
        text = as_markdown(diff, details=details)
        if len(text) > GITHUB_LIMIT:
            print(
                f"Note: this Markdown is {len(text):,} characters; a GitHub "
                f"pull-request body or comment holds {GITHUB_LIMIT:,}.",
                file=sys.stderr,
            )
    if args.output is None:
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0


# --- baseline plot -------------------------------------------------------------

#: The most images `baseline plot` draws without --test: a migration commit
#: touches hundreds of tests.
MAX_IMAGES = 300


def _size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} kB"


def cmd_baseline_plot(args: argparse.Namespace) -> int:
    """Draw the baselines of the tests asked for at each revision: a PNG per
    variable and one gallery.html, into the --output directory. Every check
    runs before the directory is created or a file in it removed."""
    from ..reporting.baseline_gallery import (
        GALLERY,
        OutputRefused,
        clear,
        gallery_html,
        plan_images,
        stale_files,
        write_marker,
    )
    from ..reporting.images import ImagesUnavailable, Renderer
    from ..storage.baseline_diff import DiffError
    from ..storage.baseline_history import baseline_history
    from ..storage.git_revisions import GitError

    out = Path(args.output)
    pngs: dict[str, bytes] = {}
    try:
        if len(args.revisions) < 2:
            raise DiffError(
                "baseline plot draws two revisions or more; it was given one"
            )
        root = _diff_root(args)
        history = baseline_history(
            root, args.revisions, tests=args.test, folders=args.folder
        )
        images = plan_images(history)
        if args.test is None and len(images) > MAX_IMAGES:
            raise DiffError(
                f"{len(images)} images to draw, over {MAX_IMAGES}: name the "
                "tests (--test) or the folders (--folder) to draw"
            )
        stale = stale_files(out)
        if images:
            with Renderer() as renderer:
                clear(out, stale)
                for image in images:
                    png = renderer.png(image.figure)
                    path = out / image.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(png)
                    pngs[image.path] = png
        else:
            clear(out, stale)
    except (ConfigError, GitError, DiffError, OutputRefused, ImagesUnavailable) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    page = out / GALLERY
    page.write_text(gallery_html(history, images, pngs), encoding="utf-8")
    write_marker(out, [*pngs, GALLERY])
    print(
        f"Wrote {len(images)} image{'s' * (len(images) != 1)} and {page} "
        f"({_size(page.stat().st_size)})"
    )
    return 0
