"""Query and diagnostic commands: discover, export, manifest, check-*."""

import argparse
from pathlib import Path

from ..discovery.test_registry import discover_tests
from .common import (
    _build_config,
    _filter_tests,
    _find_orphan_manifest_entries,
    _write_id_mapping,
)


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and list all test models."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests found.")
        return 1

    # Print summary table
    print(
        f"{'Model ID':<90} {'Vars':>4}  {'StopTime':>10}  {'Method':<10}  {'Source':<10}"
    )
    print("-" * 130)
    for t in tests:
        stop = f"{t.stop_time:g}"
        print(
            f"{t.model_id:<90} {t.n_vars:>4}  {stop:>10}  {t.method:<10}  {t.source:<10}"
        )

    print(f"\nTotal: {len(tests)} tests")

    total_vars = sum(t.n_vars for t in tests)
    print(f"  Total tracked variables: {total_vars}")

    by_source = {}
    for t in tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  Source '{source}': {count}")

    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export reference data."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)

    # review 2026-07-06 finding 60: --output arrives as a string — coerce to
    # Path (the ReferenceStore export APIs call .parent on it).
    if args.format == "csv":
        out = Path(args.output) if args.output else (config.work_dir / "references.csv")
        store.export_csv(out)
        print(f"Exported to {out}")
    else:
        out = (
            Path(args.output) if args.output else (config.work_dir / "references.json")
        )
        store.export_json(out)
        print(f"Exported to {out}")

    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    """Show and manage test references."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "show":
        all_tests = store.index.all_tests()
        active = {tid: e for tid, e in all_tests.items() if e["status"] != "obsolete"}
        obsolete = {tid: e for tid, e in all_tests.items() if e["status"] == "obsolete"}
        skipped = {tid: e for tid, e in all_tests.items() if e["status"] == "skip"}

        print(f"References: {config.reference_dir}")
        print(
            f"Active: {len(active)}  Skip: {len(skipped)}  Obsolete: {len(obsolete)}\n"
        )

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

    elif action == "dump":
        _write_id_mapping(store, config)
        mapping_path = config.work_dir / "reference_manifest.json"
        print(f"Written to {mapping_path}")
        return 0

    elif action == "cleanup":
        # Dry-run is the universal default for destructive paths: `cleanup`
        # lists what it would remove and exits 0 unless --apply is given.
        # The --orphans path always worked this way; the obsolete-reference
        # path used to delete on sight.
        apply = getattr(args, "apply", False)
        if getattr(args, "orphans", False):
            return _cleanup_orphans(config, apply=apply)
        return _cleanup_obsolete(store, apply=apply)

    return 1


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


def cmd_check_dymola(args: argparse.Namespace) -> int:
    """Diagnose discovery of Dymola's Python interface."""
    from ..backends.dymola.interface_loader import describe_dymola_interface

    override = (
        Path(args.dymola_interface) if getattr(args, "dymola_interface", None) else None
    )
    info = describe_dymola_interface(override)

    print("Dymola Python interface diagnostic")
    print("==================================")
    print(f"  Archive found:   {info['archive'] or '(none)'}")
    if info["format"]:
        print(f"  Format:          .{info['format']}")
    print(f"  sys.path entry:  {info['sys_path_entry'] or '(none)'}")
    print(f"  import ok:       {info['import_ok']}")
    if info.get("dymola_interface_module"):
        print(f"  Module:          {info['dymola_interface_module']}")
    if info["error"]:
        print(f"  Error:           {info['error']}")
    print()
    print("Searched roots:")
    for r in info["search_roots"]:
        print(f"  - {r}")
    if info["discovered_dirs"]:
        print("Discovered install dirs (newest first):")
        for d in info["discovered_dirs"]:
            print(f"  - {d}")

    if info["import_ok"]:
        print("\nOK — ready to use the Dymola Python interface.")
        return 0
    print("\nFAIL — fix one of:")
    print("  1. Pass --dymola-interface <path>")
    print("  2. Set DYMOLA_INTERFACE_PATH env var")
    print("  3. Add 'dymola_interface_path' to testing.json")
    return 1


def cmd_check_openmodelica(args: argparse.Namespace) -> int:
    """Diagnose OMPython availability + the `omc` binary."""
    import shutil as _shutil

    from ..backends.openmodelica.session_loader import describe_om_session

    info = describe_om_session()
    omc_path = _shutil.which("omc")

    print("OpenModelica diagnostic")
    print("=======================")
    print(f"  OMPython installed: {info['ompython_installed']}")
    print(f"  OMPython version:   {info['version'] or '(unknown)'}")
    print(f"  Import ok:          {info['import_ok']}")
    if info["error"]:
        print(f"  Error:              {info['error']}")
    print(f"  omc binary:         {omc_path or '(not on PATH)'}")

    ok = info["import_ok"] and bool(omc_path)
    if ok:
        print("\nOK — persistent-worker OpenModelica is ready.")
        return 0
    print("\nFAIL — fix one of:")
    if not info["import_ok"]:
        print('  - Install OMPython:   uv pip install -e ".[om]"')
    if not omc_path:
        print("  - Install OpenModelica and ensure `omc` is on PATH")
    print(
        "Or run with --batch to use the omc-script fallback (still needs omc on PATH)."
    )
    return 1


def cmd_check_julia(args: argparse.Namespace) -> int:
    """Diagnose the `julia` binary on PATH."""
    import shutil as _shutil
    import subprocess as _subprocess

    julia_path = _shutil.which("julia")
    print("Julia diagnostic")
    print("================")
    print(f"  julia binary: {julia_path or '(not on PATH)'}")
    if not julia_path:
        print(
            "\nFAIL — install Julia from https://julialang.org/downloads/ "
            "(or via `juliaup`)."
        )
        return 1
    try:
        out = _subprocess.run(
            [julia_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print(f"  version:      {out.stdout.strip() or '(no output)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  version probe failed: {exc}")
        return 1
    print("\nOK — julia is invocable.")
    print(
        "Note: first run on a project pays a multi-minute MTK / OrdinaryDiffEq "
        "JIT compile (subsequent runs hit the cache)."
    )
    return 0
