"""Query and diagnostic commands: `discover`, `baseline export`, the
`manifest` group, `check`."""

import argparse
from pathlib import Path

from ..config import Config, ConfigError, NoProject
from ..discovery.test_registry import TestModel, discover_tests
from ..discovery.unit_tests import unit_test_variables
from ..pipeline import write_id_mapping
from .common import (
    _build_config,
    _filter_tests,
    _find_orphan_manifest_entries,
)


def _vars_text(test: TestModel) -> str:
    """What a test tracks, as far as discovery knows it: the count of its
    UnitTests variables, and its ``variables`` entries as PATTERNS — `2`,
    `1 pattern`, `2 + 3 patterns`. A pattern names no variable until it is
    matched against a result; counted as variables they read `0`."""
    declared = len(unit_test_variables(test))
    patterns = len(test.variable_patterns)
    if not patterns:
        return str(declared)
    text = f"{patterns} pattern{'' if patterns == 1 else 's'}"
    return f"{declared} + {text}" if declared else text


def _method_text(test: TestModel, config: Config) -> str:
    """The solver the test names, or `—` where it names none and the
    framework's default would not be true: ``Dassl`` is what a Modelica tool
    is handed, while FMPy maps it to its own solver and a Julia or Python
    source has no solver setting at all."""
    defaulted = test.field_sources.get("method") == "default"
    if defaulted and config.source_type != "modelica":
        return "—"
    return str(test.method)


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and list all test models."""
    # A listing writes nothing: a project with no testing.json is read on
    # defaults, and none is created beside it.
    config = _build_config(args, write_default=False)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests found.")
        return 1

    # Print summary table. Every cell is something true before any run:
    # see `_vars_text` and `_method_text`.
    print(
        f"{'Model ID':<90} {'Vars':>16}  {'StopTime':>10}  {'Method':<10}  {'Source':<10}"
    )
    print("-" * 142)
    for t in tests:
        stop = f"{t.stop_time:g}"
        print(
            f"{t.model_id:<90} {_vars_text(t):>16}  {stop:>10}  "
            f"{_method_text(t, config):<10}  {t.source:<10}"
        )

    print(f"\nTotal: {len(tests)} tests")

    print(f"  UnitTests variables: {sum(len(unit_test_variables(t)) for t in tests)}")
    print(
        f"  Variable patterns: {sum(len(t.variable_patterns) for t in tests)} "
        f"(each is matched against its test's result after the run)"
    )

    by_source = {}
    for t in tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  Source '{source}': {count}")

    return 0


#: `baseline export --format`: each format and the file it is written to
#: in the work dir when no --output names one. The first is the default.
EXPORT_FILES = {"json": "references.json", "csv": "references.csv"}


def cmd_baseline_export(args: argparse.Namespace) -> int:
    """Write every stored baseline of the partition to one file."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    assert config.work_dir is not None  # resolved in Config.__post_init__
    # review 2026-07-06 finding 60: --output arrives as a string — coerce to
    # Path (the ReferenceStore export APIs call .parent on it).
    out = (
        Path(args.output)
        if args.output is not None
        else config.work_dir / EXPORT_FILES[args.format]
    )
    if args.format == "csv":
        store.export_csv(out)
    else:
        store.export_json(out)
    print(f"Exported to {out}")
    return 0


def cmd_manifest_show(args: argparse.Namespace) -> int:
    """List the partition's references and the status of each."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    all_tests = store.index.all_tests()
    active = {tid: e for tid, e in all_tests.items() if e["status"] != "obsolete"}
    obsolete = {tid: e for tid, e in all_tests.items() if e["status"] == "obsolete"}
    skipped = {tid: e for tid, e in all_tests.items() if e["status"] == "skip"}

    print(f"References: {config.reference_dir}")
    print(f"Active: {len(active)}  Skip: {len(skipped)}  Obsolete: {len(obsolete)}\n")

    if active:
        print(f"{'ID':>6}  {'Status':<10}  {'Model ID'}")
        print("-" * 90)
        for tid in sorted(active):
            entry = active[tid]
            print(f"{tid:>6}  {entry['status']:<10}  {entry['model_id']}")

    if skipped:
        print("\nSkipped:")
        for tid in sorted(skipped):
            print(f"{tid:>6}  {skipped[tid]['model_id']}")

    if obsolete and args.show_obsolete:
        print("\nObsolete:")
        for tid in sorted(obsolete):
            print(f"{tid:>6}  {obsolete[tid]['model_id']}")

    return 0


def cmd_manifest_dump(args: argparse.Namespace) -> int:
    """Write the ref ID → model ID mapping to the work directory."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    write_id_mapping(store, config)
    assert config.work_dir is not None  # resolved in Config.__post_init__
    print(f"Written to {config.work_dir / 'reference_manifest.json'}")
    return 0


def cmd_manifest_cleanup(args: argparse.Namespace) -> int:
    """List what a cleanup would remove, or — ``--apply`` — remove it.

    Dry-run is the universal default for destructive paths: ``cleanup``
    lists what it would remove and exits 0 unless ``--apply`` is given. The
    ``--orphans`` path always worked this way; the obsolete-reference path
    used to delete on sight.
    """
    config = _build_config(args)
    if args.orphans:
        return _cleanup_orphans(config, apply=args.apply)
    from ..storage.reference_store import ReferenceStore

    return _cleanup_obsolete(ReferenceStore(config), apply=args.apply)


def _cleanup_obsolete(store, apply: bool) -> int:
    """List (or, with apply=True, delete) reference files marked obsolete."""
    obsolete = store.list_obsolete()
    if not obsolete:
        print("No obsolete references to clean up.")
        return 0

    print(f"Found {len(obsolete)} obsolete reference files:")
    for test_id, model_id, ref_file in obsolete:
        print(f"  {ref_file.name}  {model_id or f'ref_{test_id}'}")

    if not apply:
        print("\nDry run. Re-run with --apply to actually remove.")
        return 0

    removed = store.cleanup_obsolete()
    print(f"\nRemoved {removed} obsolete reference files.")
    return 0


def _cleanup_orphans(config, apply: bool) -> int:
    """List or prune manifest entries (and their on-disk dirs) for models
    no longer in discover_tests. Dry-run by default; pass apply=True to delete.
    """
    all_tests = discover_tests(config)
    orphans = _find_orphan_manifest_entries(config, all_tests)
    if not orphans:
        print("No orphan manifest entries.")
        return 0

    report_dir = config.work_dir / "reports"
    print(f"Found {len(orphans)} orphan manifest entries:")
    for tk, model_id in sorted(orphans.items()):
        work = config.work_dir / tk
        # Try both possible report-dir names
        rep_test = report_dir / tk
        rep_ref = None
        from ..storage.reference_store import ReferenceStore

        store = ReferenceStore(config)
        ref_id = store.index.get_id(model_id)
        if ref_id:
            rep_ref = report_dir / f"ref_{ref_id}"
        bits = []
        if work.exists():
            bits.append(f"work_dir={tk}/")
        if rep_test.exists():
            bits.append(f"reports/{tk}/")
        if rep_ref and rep_ref.exists():
            bits.append(f"reports/ref_{ref_id}/")
        print(f"  {tk}  {model_id}  [{', '.join(bits) or 'manifest only'}]")

    if not apply:
        print("\nDry run. Re-run with --apply to actually remove.")
        return 0

    import shutil

    from ..backends import BatchManifest

    manifest_path = config.work_dir / "batch_manifest.json"
    manifest = BatchManifest.load(manifest_path)
    n_dirs = 0
    for tk, model_id in orphans.items():
        for candidate in [
            config.work_dir / tk,
            report_dir / tk,
        ]:
            if candidate.exists():
                shutil.rmtree(candidate)
                n_dirs += 1
        from ..storage.reference_store import ReferenceStore

        store = ReferenceStore(config)
        ref_id = store.index.get_id(model_id)
        if ref_id:
            rep_ref = report_dir / f"ref_{ref_id}"
            if rep_ref.exists():
                shutil.rmtree(rep_ref)
                n_dirs += 1
        manifest.manifest.pop(tk, None)
    manifest.save()
    print(f"\nRemoved {len(orphans)} manifest entries and {n_dirs} directories.")
    return 0


def _check_entries(config: Config | None) -> list[tuple[str, dict]]:
    """What `dstf check` lists, each with its settings block: the entries
    of the project's `backends` map, in the order written, then every
    registered backend the project has no entry of (the search alone)."""
    from ..backends import backend_family, backend_names

    entries = (
        [(name, dict(block)) for name, block in config.backends.items()]
        if config
        else []
    )
    selected = config.backend_name if config else None
    if selected and selected not in dict(entries) and config is not None:
        entries.append((selected, dict(config.backend_settings)))
    listed = {backend_family(name) for name, _ in entries}
    entries.extend((name, {}) for name in backend_names() if name not in listed)
    return entries


def _check_target(named: str, entries: list[tuple[str, dict]], selected: str) -> str:
    """The one of ``entries`` that `dstf check <named>` diagnoses: the
    entry of that name; else, for a backend's name, its entry the project
    runs with, or its first written. A name that is neither — it starts
    with a backend's, or the parser had refused it — is a config error
    that lists both."""
    from ..backends import backend_family, backend_names

    names = [name for name, _ in entries]
    if named in names:
        return named
    of_backend = [name for name in names if backend_family(name) == named]
    if named in backend_names() and of_backend:
        return selected if selected in of_backend else of_backend[0]
    written = [name for name in names if name not in backend_names()]
    listed = f" ({', '.join(written)})" if written else ""
    raise ConfigError(
        f'check: "{named}" is no entry of backends{listed} and no backend\'s '
        f"name ({', '.join(backend_names())})"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Say where each backend is on this machine and by which rule it was
    found, then diagnose one: the entry or backend named, else the one the
    project selects. Exit 0 iff that one is usable."""
    from ..backends import backend_class, backend_family
    from ..config import _pick_backend
    from ..config_shape import default

    try:
        config: Config | None = _build_config(args, write_default=False)
    except NoProject:
        # No testing.json and no library here: nothing to read. The
        # backends are still where they are.
        config = None

    if config is None:
        project = "no testing.json here: the search alone"
        # As a project with an empty testing.json would be read.
        selected = _pick_backend({}, default("source_type"))
    else:
        project = (
            f"testing.json: {config.config_found}"
            if config.config_found is not None
            else f"no testing.json: {config.library_name} on defaults"
        )
        assert config.backend_name is not None  # resolved in __post_init__
        selected = config.backend_name
    entries = _check_entries(config)
    # An entry of the project's map says its settings; any other is searched for.
    in_config = set(config.backends) if config is not None else set()
    # Every name listed is a backend's or starts with one (`Config`).
    classes = {
        name: cls
        for name, _ in entries
        if (cls := backend_class(backend_family(name))) is not None
    }
    located = {name: classes[name].locate(block) for name, block in entries}

    if args.check_backend is None:
        print(f"Backends ({project})")
        # An entry by its name, with the backend it is of where that is
        # not its name: `Dymola 2025 [Dymola]`.
        labels = {
            name: name
            if backend_family(name) == name
            else f"{name} [{backend_family(name)}]"
            for name, _ in entries
        }
        width = max(len(label) for label in labels.values())
        for name, _ in entries:
            found = located[name]
            if not found.found:
                state = f"not found  {found.parts[0].detail}"
            else:
                first = found.parts[0]
                assert first.rule is not None  # a found part names its rule
                rule = first.rule.said(name if name in in_config else "")
                state = f"found      {first.path}  ({rule})"
            mark = "*" if name == selected else " "
            print(f"  {mark} {labels[name]:<{width}}  {state}")
        print(
            "  * the backend this project runs with"
            if config is not None
            else "  * the backend a Modelica project with no testing.json runs with"
        )
        print()
        target = selected
    else:
        target = _check_target(args.check_backend, entries, selected)

    diagnosis = classes[target].diagnose(
        located[target], entry=target if target in in_config else "", config=config
    )
    print(target)
    for line in diagnosis.lines:
        print(line)
    if diagnosis.usable:
        print(f"\nOK — {target} is ready.")
        return 0
    print(f"\nFAIL — {target} is not ready. Fix one of:")
    for i, fix in enumerate(diagnosis.fixes or _default_fixes(target), start=1):
        print(f"  {i}. {fix}")
    return 1


def _default_fixes(name: str) -> tuple[str, ...]:
    """What to do about a backend that says no more than that a part of it
    was not found."""
    return (
        f'Install {name}, or set "executables" in its block of testing.json\'s '
        '"backends" to where it is',
    )
