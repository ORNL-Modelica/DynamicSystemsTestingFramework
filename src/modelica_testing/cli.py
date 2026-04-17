"""Command-line interface for the Modelica testing system."""

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Optional

from .config import Config
from .discovery.test_registry import TestModel, discover_tests


def _resolve_filter_patterns(spec: str) -> list[str]:
    """Resolve a --filter argument into a list of glob patterns.

    Accepts:
      - "Foo.Bar.*"           — single glob
      - "Foo.A,Foo.B,Foo.C"   — comma-separated globs
      - "@path/to/file.txt"   — file with one pattern per line; '#' starts a comment
    """
    if spec.startswith("@"):
        path = Path(spec[1:])
        if not path.exists():
            raise FileNotFoundError(f"Filter file not found: {path}")
        patterns = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                patterns.append(line)
        return patterns
    return [p.strip() for p in spec.split(",") if p.strip()]


def _filter_tests(
    tests: list[TestModel],
    pattern: Optional[str] = None,
    package: Optional[str] = None,
) -> list[TestModel]:
    """Filter tests by glob/list/file on model_id, plus optional package prefix."""
    filtered = tests
    if package:
        filtered = [t for t in filtered if t.model_id.startswith(package)]
    if pattern:
        patterns = _resolve_filter_patterns(pattern)
        filtered = [
            t for t in filtered
            if any(fnmatch.fnmatch(t.model_id, p) for p in patterns)
        ]
    return filtered


def _find_orphan_manifest_entries(config, all_tests: list) -> dict[str, str]:
    """Return {test_key: model_id} for manifest entries whose model_id is no
    longer in the discovered tests. These usually indicate renamed/removed
    models — they don't break correctness (compare_all skips them) but their
    work dirs and report dirs accumulate on disk.
    """
    manifest_path = config.work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    from .simulators import BatchManifest
    manifest = BatchManifest.load(manifest_path)
    discovered = {t.model_id for t in all_tests}
    return {
        tk: entry["model_id"]
        for tk, entry in manifest.manifest.items()
        if entry["model_id"] not in discovered
    }


def _notify_orphans(config, all_tests: list) -> None:
    """One-line notice if orphan manifest entries exist."""
    orphans = _find_orphan_manifest_entries(config, all_tests)
    if orphans:
        print(
            f"Note: {len(orphans)} orphan manifest entries (models no longer in "
            f"discovery). Run 'manifest cleanup --orphans' to list/prune."
        )


def _write_id_mapping(store, config) -> None:
    """Write a ref ID → model ID mapping file to the work directory."""
    active = store.index.active_tests()
    if not active:
        return
    import json
    mapping_path = config.work_dir / "reference_manifest.json"
    config.work_dir.mkdir(parents=True, exist_ok=True)
    mapping = {f"ref_{tid}": active[tid] for tid in sorted(active)}
    mapping_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and list all test models."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests found.")
        return 1

    # Print summary table
    print(f"{'Model ID':<90} {'Vars':>4}  {'StopTime':>10}  {'Method':<10}  {'Source':<10}")
    print("-" * 130)
    for t in tests:
        stop = f"{t.stop_time:g}"
        print(f"{t.model_id:<90} {t.n_vars:>4}  {stop:>10}  {t.method:<10}  {t.source:<10}")

    print(f"\nTotal: {len(tests)} tests")

    total_vars = sum(t.n_vars for t in tests)
    print(f"  Total tracked variables: {total_vars}")

    by_source = {}
    for t in tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  Source '{source}': {count}")

    return 0


def _get_runner(config, persistent: bool = False):
    """Get the appropriate simulator runner for the configured simulator.

    When *persistent* is True and the backend is Dymola, swap to the
    persistent-worker runner that drives Dymola via its Python interface.
    """
    from .simulators import get_runner
    runner = get_runner(config)
    if persistent and config.simulator_backend == "Dymola":
        from .simulators.dymola.persistent_runner import PersistentDymolaRunner
        runner = PersistentDymolaRunner(config)
    return runner


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    _write_id_mapping(store, config)

    runner = _get_runner(config, persistent=not getattr(args, "batch", False))

    # --rerun selects tests by status from prior comparisons (no new sim yet).
    # Default category: failed. Implies --merge so the report covers the full suite.
    if getattr(args, "rerun", None) is not None:
        from .comparison.comparator import compare_all
        rerun_filter = args.rerun or "failed"
        rerun_categories = _parse_review_filter(rerun_filter)
        prior_results = runner.read_last_results(all_tests)
        if not prior_results:
            print("--rerun requires prior results in the work directory. Run a full pass first.")
            return 1
        prior_comps = compare_all(
            all_tests, prior_results, store, config.tolerance, config.final_only,
        )
        rerun_models = {c.model_id for c in prior_comps if _should_review(c, rerun_categories)}
        tests = [t for t in tests if t.model_id in rerun_models]
        args.merge = True
        print(f"--rerun {rerun_filter}: selected {len(tests)} of {len(prior_comps)} tests")

    if not tests:
        print("No tests matched the filter.")
        return 1

    print(f"Running {len(tests)} tests...")
    # Pre-populate model_id → "ref_NNNN" map so the live dashboard can link
    # to the correct per-test report directory (matches generate_report_suite naming).
    for test in tests:
        ref_id = store.index.get_id(test.model_id)
        if ref_id:
            runner.ref_id_map[test.model_id] = f"ref_{ref_id}"
    manifests = runner.run_tests(tests)

    # Enrich batch manifest with ref IDs now that store is available
    for m in manifests:
        m.enrich_ref_ids(store.index)

    # 4.B.4 — cross-backend chains. Tests whose recognizer set
    # requested_baselines to include a known chain target (today only
    # "dymola-via-fmpy") get a second baseline produced by chaining backends.
    # MetricTree leaves can then score against it via "against": "<chain-name>".
    _run_cross_backend_chains(tests, runner, config, store)

    # --merge expands the read/compare/report scope to every test that has
    # results on disk (per the persistent batch manifest), not just what was
    # rerun this invocation. Lets you incrementally rerun a subset and still
    # get a full report covering the whole suite.
    if getattr(args, "merge", False):
        merged_model_ids = {
            entry["model_id"]
            for m in manifests for entry in m.manifest.values()
        }
        scope_tests = [t for t in all_tests if t.model_id in merged_model_ids]
    else:
        scope_tests = tests

    results = runner.read_results(manifests, scope_tests)

    if args.accept:
        stored = store.accept_results(scope_tests, results)
        print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
        return 0
    elif args.interactive is not None:
        from .comparison.comparator import compare_all

        comparisons = compare_all(scope_tests, results, store, config.tolerance, config.final_only)
        return _interactive_review(
            scope_tests, results, comparisons, store, config,
            review_filter=args.interactive,
        )
    else:
        from .comparison.comparator import compare_all

        comparisons = compare_all(scope_tests, results, store, config.tolerance, config.final_only)

        if args.report:
            return _generate_report_suite(comparisons, results, scope_tests, store, config)

        return _output_report(comparisons, args)


def _run_cross_backend_chains(tests, runner, config, store) -> None:
    """For each test with ``requested_baselines`` listing a known chain target,
    invoke the chain and store the resulting named baseline (4.B.4).

    Today only ``dymola-via-fmpy`` is recognized. Failures are logged + skipped
    (don't abort the run); chains are best-effort enrichment of baselines.
    Requires Windows + Dymola (with FMI export) for the export step — the chain
    silently no-ops on platforms that don't support it (logs a warning).
    """
    from .simulators.cross_backend import (
        CROSS_BACKEND_BASELINE_NAME,
        produce_dymola_via_fmpy_baseline,
    )

    chain_tests = [
        t for t in tests
        if CROSS_BACKEND_BASELINE_NAME in (t.requested_baselines or [])
    ]
    if not chain_tests:
        return

    print(
        f"[experimental] Cross-backend chain '{CROSS_BACKEND_BASELINE_NAME}' is "
        f"scoped to autonomous FMU-exportable tests only (no external inputs, "
        f"no python-driver tests); end-to-end validation on real Dymola pending. "
        f"See D65."
    )
    print(f"Running cross-backend chains for {len(chain_tests)} test(s)...")
    n_ok = 0
    for t in chain_tests:
        ok = produce_dymola_via_fmpy_baseline(t, runner, config, store)
        if ok:
            n_ok += 1
            print(f"  {t.model_id}: '{CROSS_BACKEND_BASELINE_NAME}' baseline written")
        else:
            print(f"  {t.model_id}: chain skipped (see logs)")
    print(f"Cross-backend chains: {n_ok}/{len(chain_tests)} succeeded")


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare last run results against stored references."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from .comparison.comparator import compare_all
    from .storage.reference_store import ReferenceStore

    runner = _get_runner(config)
    store = ReferenceStore(config)
    _write_id_mapping(store, config)
    results = runner.read_last_results(tests)
    comparisons = compare_all(tests, results, store, config.tolerance, config.final_only)

    if getattr(args, "report", False):
        return _generate_report_suite(comparisons, results, tests, store, config)

    return _output_report(comparisons, args)


def cmd_export(args: argparse.Namespace) -> int:
    """Export reference data."""
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)

    if args.format == "csv":
        out = args.output or (config.work_dir / "references.csv")
        store.export_csv(out)
        print(f"Exported to {out}")
    else:
        out = args.output or (config.work_dir / "references.json")
        store.export_json(out)
        print(f"Exported to {out}")

    return 0



def cmd_check_dymola(args: argparse.Namespace) -> int:
    """Diagnose discovery of Dymola's Python interface."""
    from .simulators.dymola.interface_loader import describe_dymola_interface

    override = Path(args.dymola_interface) if getattr(args, "dymola_interface", None) else None
    info = describe_dymola_interface(override)

    print("Dymola Python interface diagnostic")
    print("==================================")
    print(f"  Archive found:   {info['archive'] or '(none)'}")
    if info['format']:
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


def cmd_manifest(args: argparse.Namespace) -> int:
    """Show and manage test references."""
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "show":
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
            print(f"\nSkipped:")
            for tid in sorted(skipped):
                print(f"{tid:>6}  {skipped[tid]['model_id']}")

        if obsolete and args.show_obsolete:
            print(f"\nObsolete:")
            for tid in sorted(obsolete):
                print(f"{tid:>6}  {obsolete[tid]['model_id']}")

        return 0

    elif action == "dump":
        _write_id_mapping(store, config)
        mapping_path = config.work_dir / "reference_manifest.json"
        print(f"Written to {mapping_path}")
        return 0

    elif action == "cleanup":
        if getattr(args, "orphans", False):
            return _cleanup_orphans(config, apply=getattr(args, "apply", False))
        removed = store.cleanup_obsolete()
        if removed:
            print(f"Removed {removed} obsolete reference files.")
        else:
            print("No obsolete references to clean up.")
        return 0

    return 1


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
        from .storage.reference_store import ReferenceStore, RefIndex
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
    from .simulators import BatchManifest
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
        from .storage.reference_store import ReferenceStore
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



def cmd_add(args: argparse.Namespace) -> int:
    """Add a test to test_spec.json."""
    config = _build_config(args)
    model_id = args.model_id
    variables = args.variables or []

    from .discovery.spec_parser import add_to_test_spec

    # Determine spec file location
    spec_path = config.test_spec_file
    if spec_path is None:
        spec_path = config.reference_root / "test_spec.json"

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
            choice = input(f"  {model_id} already exists in spec. Overwrite? [y/n] > ").strip().lower()
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
    """Update comparison tolerances in test_spec.json from a JSON file."""
    import json as json_mod
    from .discovery.spec_parser import update_test_comparison

    config = _build_config(args)
    spec_path = config.test_spec_file
    if spec_path is None:
        spec_path = config.reference_root / "test_spec.json"

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

    update_test_comparison(spec_path, update_data)
    print(f"Updated comparison settings for {model_id}")
    print(f"  Spec file: {spec_path}")
    return 0


def _get_spec_path(config) -> Path:
    """Get the test_spec.json path, using config or default location."""
    if config.test_spec_file is not None:
        return config.test_spec_file
    return config.reference_root / "test_spec.json"


_VALID_REVIEW_FILTERS = {"all", "failed", "no-baseline", "warnings", "sim-failed", "passed"}


def _parse_review_filter(filter_str: str) -> set[str]:
    """Parse and validate the review filter string."""
    filters = {f.strip() for f in filter_str.split(",") if f.strip()}
    invalid = filters - _VALID_REVIEW_FILTERS
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid review filter(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(_VALID_REVIEW_FILTERS - {'all'}))}"
        )
    if "all" in filters:
        return {"all"}
    return filters


def _should_review(comp, filters: set[str]) -> bool:
    """Check if a comparison matches the active review filters."""
    if "all" in filters:
        return True
    if "sim-failed" in filters and not comp.sim_success:
        return True
    if "no-baseline" in filters and not comp.has_reference:
        return True
    if "failed" in filters and comp.sim_success and not comp.passed and comp.has_reference:
        return True
    if "warnings" in filters and comp.warnings:
        return True
    if "passed" in filters and comp.sim_success and comp.passed and not comp.warnings:
        return True
    return False


def _interactive_review(
    tests: list[TestModel],
    results: dict,
    comparisons: list,
    store,
    config=None,
    review_filter: str = "all",
) -> int:
    """Interactively review each test and accept/reject results."""
    from .discovery.spec_parser import update_test_variables
    from .simulators import resolve_variable_patterns
    from .simulators.dymola.mat_reader import read_dymola_mat

    filters = _parse_review_filter(review_filter)

    test_lookup = {t.model_id: t for t in tests}
    n_accepted = 0
    n_skipped = 0
    n_auto_skipped = 0
    n_total = len(comparisons)

    spec_path = _get_spec_path(config) if config else None

    # ANSI colors
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    filter_desc = f" (filter: {review_filter})" if "all" not in filters else ""
    print(f"\nInteractive review: {n_total} tests{filter_desc}\n")

    for idx, comp in enumerate(comparisons, 1):
        if not _should_review(comp, filters):
            n_auto_skipped += 1
            continue
        model_id = comp.model_id

        # Status line
        if not comp.sim_success:
            status = f"{RED}SIM_FAIL{RESET}"
        elif not comp.has_reference:
            status = f"{YELLOW}NO BASELINE{RESET}"
        elif comp.passed:
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"

        warn = ""
        if comp.warnings:
            warn = f" {YELLOW}[{len(comp.warnings)} warning(s)]{RESET}"

        if comp.test_id:
            id_tag = f"[{comp.test_id}] "
        elif not comp.has_reference:
            id_tag = "[new] "
        else:
            id_tag = ""
        print(f"[{idx}/{n_total}] {BOLD}{id_tag}{model_id}{RESET}")
        print(f"  Status: {status}{warn}")

        if comp.error_message and not comp.sim_success:
            print(f"  Error: {comp.error_message}")

        # Brief variable summary
        if comp.variables:
            n_vars_pass = sum(1 for v in comp.variables if v.passed)
            n_vars_total = len(comp.variables)
            if comp.passed:
                print(f"  Variables: {n_vars_pass}/{n_vars_total} passed")
            else:
                print(f"  Variables: {n_vars_pass}/{n_vars_total} passed, {n_vars_total - n_vars_pass} failed")

        # Can't accept if simulation failed
        if not comp.sim_success:
            print(f"  Skipping (simulation failed)\n")
            n_skipped += 1
            continue

        # Prompt
        result = results.get(model_id)
        has_result = result is not None and result.success
        test = test_lookup.get(model_id)
        added_patterns = []  # Track patterns added via [v] this session

        while True:
            if has_result:
                prompt = "  [a]ccept  [v]ariables  [s]kip  [d]etail  [p]lot  [q]uit > "
            else:
                prompt = "  [s]kip  [d]etail  [p]lot  [q]uit > "
            try:
                choice = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return 1

            if choice == "a" and has_result:
                # If variables were added, re-read the .mat to resolve them
                if added_patterns and test and config:
                    test.variable_patterns = list(set(
                        test.variable_patterns + added_patterns
                    ))
                    test_key = _find_test_key(model_id, config)
                    if test_key:
                        runner = _get_runner(config)
                        result = runner.read_result(test, test_key, None)

                if test and result and store.store_reference(test, result):
                    n_vars = len(result.variables) if result.variables else 0
                    print(f"  {GREEN}Accepted ({n_vars} variables).{RESET}\n")
                    n_accepted += 1
                else:
                    print(f"  {RED}Failed to store.{RESET}\n")
                    n_skipped += 1
                break

            elif choice == "v" and has_result:
                try:
                    raw = input("  Variable patterns (comma-separated): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return 1
                if raw:
                    new_patterns = [p.strip() for p in raw.split(",") if p.strip()]
                    added_patterns.extend(new_patterns)
                    # Save to test_spec.json
                    if spec_path:
                        update_test_variables(spec_path, model_id, new_patterns)
                        print(f"  Added {len(new_patterns)} pattern(s) to {spec_path.name}")
                    else:
                        print(f"  Added {len(new_patterns)} pattern(s) (in-memory only, no spec file configured)")
                    # Show what would match from the .mat
                    if config:
                        test_key = _find_test_key(model_id, config)
                        if test_key:
                            test_dir = config.work_dir / test_key
                            mat_path = test_dir / "dsres.mat"
                            if mat_path.exists():
                                mat_data = read_dymola_mat(mat_path)
                                if mat_data:
                                    available = list(mat_data.keys())
                                    matched = resolve_variable_patterns(new_patterns, available)
                                    print(f"  Matched {len(matched)} variables: {', '.join(matched[:10])}")
                                    if len(matched) > 10:
                                        print(f"    ... and {len(matched) - 10} more")

            elif choice == "s":
                print(f"  Skipped.\n")
                n_skipped += 1
                break

            elif choice == "d":
                _print_detail(comp)

            elif choice == "p":
                if config:
                    _generate_and_open_plots(model_id, comp, result, store, config, test)
                else:
                    print("  No config available for plot generation.")

            elif choice == "q":
                auto = f", {n_auto_skipped} auto-skipped" if n_auto_skipped else ""
                print(f"\nStopped. Accepted {n_accepted}, skipped {n_skipped}{auto}.")
                return 0 if n_accepted > 0 else 1

            else:
                options = "a/v/s/d/p/q" if has_result else "s/d/p/q"
                print(f"  Invalid choice. Enter {options}.")

    auto = f", {n_auto_skipped} auto-skipped" if n_auto_skipped else ""
    print(f"\nDone. Accepted {n_accepted}, skipped {n_skipped}{auto}.")
    return 0


def _generate_and_open_plots(model_id, comp, result, store, config, test=None) -> None:
    """Generate comparison plots and open in browser."""
    from .reporting.plot_comparison import generate_comparison_plots, open_in_browser

    test_key = _find_test_key(model_id, config)
    if test_key:
        plot_dir = config.work_dir / test_key / "plots"
    else:
        plot_dir = config.work_dir / "plots" / model_id.replace(".", "_")

    ref_data = store.get_reference(model_id)

    test_dir = config.work_dir / test_key if test_key else None

    spec_path = _get_spec_path(config) if config else None

    # Resolve reference file path for clickable link in report
    ref_file = None
    test_id = store.index.get_id(model_id)
    if test_id:
        from .storage.reference_store import RefIndex
        ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

    from .simulators import get_runner_class
    try:
        artifact_files = tuple(get_runner_class(config).artifact_files)
    except ValueError:
        artifact_files = ()

    html_path = generate_comparison_plots(
        model_id=model_id,
        ref_data=ref_data,
        result=result,
        comparisons=comp.variables,
        plot_dir=plot_dir,
        test_dir=test_dir,
        test_model=test,
        spec_path=spec_path,
        ref_file=ref_file,
        warnings=comp.warnings,
        metric_tree=comp.metric_tree,
        artifact_files=artifact_files,
    )

    if html_path:
        print(f"  Plots saved to {plot_dir}")
        open_in_browser(html_path)
    else:
        print("  Plot generation failed (matplotlib not installed?)")


def _find_test_key(model_id: str, config) -> Optional[str]:
    """Find the test_NNNN key for a model from the batch manifest."""
    from .simulators import BatchManifest
    manifest_paths = sorted(config.work_dir.glob("batch_manifest.json"))
    for mp in manifest_paths:
        bm = BatchManifest.load(mp)
        for tk, entry in bm.manifest.items():
            if entry["model_id"] == model_id:
                return tk
    return None


def _print_detail(comp) -> None:
    """Print detailed variable comparison for interactive review."""
    if comp.error_message:
        print(f"  Error: {comp.error_message}")

    for var in comp.variables:
        name = var.name or f"x[{var.index}]"
        status = "\033[92mPASS\033[0m" if var.passed else "\033[91mFAIL\033[0m"
        print(f"    {status}  {name}")
        if var.is_constant:
            print(f"           RMSE: {var.rmse:.6e} (constant signal)")
        else:
            print(f"           NRMSE: {var.nrmse:.6e} (range: {var.signal_range:.4e})")
        print(f"           Max abs error: {var.max_abs_error:.6e} at t={var.max_abs_error_time:g}")
        print(f"           Final: ref={var.reference_final:.6e}  act={var.actual_final:.6e}")

    if comp.warnings:
        print(f"    Structural warnings:")
        for w in comp.warnings:
            print(f"      {w.field}: {w.reference_value} -> {w.current_value}")
    print()


def _generate_report_suite(comparisons, results, tests, store, config) -> int:
    """Generate per-test HTML reports and an index page."""
    from .reporting.plot_comparison import generate_report_suite, open_in_browser

    index_path = generate_report_suite(comparisons, results, tests, store, config)
    print(f"Report suite written to {index_path.parent}")
    open_in_browser(index_path)

    n_failed = sum(1 for c in comparisons if not c.passed)
    return 1 if n_failed else 0


def _output_report(comparisons: list, args: argparse.Namespace) -> int:
    """Route comparisons to the selected report format."""
    fmt = getattr(args, "report_format", "console")
    if fmt == "junit":
        from .reporting.junit_report import generate_junit_report
        out = Path(getattr(args, "output", None) or "test-results.xml")
        generate_junit_report(comparisons, out)
        print(f"JUnit report written to {out}")
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    elif fmt == "html":
        from .reporting.html_report import generate_html_report
        out = Path(getattr(args, "output", None) or "test-report.html")
        generate_html_report(comparisons, out)
        print(f"HTML report written to {out}")
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    else:
        from .reporting.console_report import print_report
        return print_report(comparisons)


def _build_config(args: argparse.Namespace) -> Config:
    """Build Config from parsed CLI arguments."""
    kwargs = {}
    if args.source_path:
        kwargs["source_path"] = Path(args.source_path)
    if hasattr(args, "config") and args.config:
        kwargs["config_file"] = Path(args.config)
    if hasattr(args, "reference_root") and args.reference_root:
        kwargs["reference_root"] = Path(args.reference_root)
    if hasattr(args, "simulator") and args.simulator:
        kwargs["simulator"] = args.simulator
    if hasattr(args, "simulator_path") and args.simulator_path:
        kwargs["simulator_path"] = args.simulator_path
    if hasattr(args, "show_ide") and args.show_ide:
        kwargs["show_ide"] = True
    if hasattr(args, "work_dir") and args.work_dir:
        kwargs["work_dir"] = Path(args.work_dir)
    if hasattr(args, "parallel") and args.parallel:
        kwargs["parallel"] = args.parallel
    if hasattr(args, "batch_size") and args.batch_size is not None:
        kwargs["batch_size"] = args.batch_size
    if hasattr(args, "dymola_interface") and args.dymola_interface:
        kwargs["dymola_interface_path"] = Path(args.dymola_interface)
    if hasattr(args, "tolerance") and args.tolerance:
        kwargs["tolerance"] = args.tolerance
    if hasattr(args, "final_only") and args.final_only:
        kwargs["final_only"] = args.final_only
    if hasattr(args, "timeout") and args.timeout:
        kwargs["timeout"] = args.timeout
    if hasattr(args, "test_spec") and args.test_spec:
        kwargs["test_spec_file"] = Path(args.test_spec)
    return Config(**kwargs)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modelica-testing",
        description="Modelica Library Regression Testing System",
    )
    parser.add_argument(
        "--source-path", type=str, default=None,
        help="Path to the source for the library under test (Modelica package dir containing package.mo, FMU dir, ...). Default: auto-detect from cwd.",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to testing.json config file (default: look near package directory)",
    )
    parser.add_argument(
        "--reference-root", type=str, default=None,
        help="Path to reference results root (default: <library>/Resources/ReferenceResults)",
    )
    parser.add_argument(
        "--test-spec", type=str, default=None,
        help="Path to test_spec.json (external test definitions)",
    )
    parser.add_argument(
        "--dymola-interface", type=str, default=None,
        help="Path to Dymola's Python interface archive (dymola.egg or dymola-*.whl) "
             "or the directory containing it. Overrides auto-discovery.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    p_discover = subparsers.add_parser(
        "discover", help="Discover and list all test models"
    )
    p_discover.add_argument("--filter", type=str, help="Glob, comma-separated list, or @file (one pattern per line) — matches against model_id")
    p_discover.add_argument("--package", type=str, help="Filter by package prefix")

    # run
    p_run = subparsers.add_parser("run", help="Run tests in Dymola")
    p_run.add_argument("--filter", type=str, help="Glob, comma-separated list, or @file (one pattern per line) — matches against model_id")
    p_run.add_argument("--package", type=str, help="Filter by package prefix")
    p_run.add_argument(
        "-i", "--interactive", nargs="?", const="all", default=None,
        metavar="FILTER",
        help="Interactive review. Optional filter: failed, no-baseline, "
             "warnings, sim-failed, passed (comma-separated). Default: all.",
    )
    p_run.add_argument(
        "--accept", action="store_true",
        help="Accept results as new baseline references",
    )
    p_run.add_argument("--simulator", type=str, help="Simulator name (e.g., 'Dymola 2025')")
    p_run.add_argument("--simulator-path", type=str, help="Absolute path to simulator executable (overrides config)")
    p_run.add_argument(
        "--show-ide", action="store_true",
        help="Show Dymola GUI instead of running headless",
    )
    p_run.add_argument("--work-dir", type=str, help="Working directory for output")
    p_run.add_argument("--parallel", type=int, help="Number of parallel Dymola instances")
    p_run.add_argument("--batch-size", type=int, dest="batch_size",
                       help="(--batch only) Tests per Dymola session (default: all-per-worker). Small values (3-5) give better load balancing and crash isolation but reload the library more often.")
    p_run.add_argument("--batch", action="store_true",
                       help="Use the legacy batched .mos runner instead of the default persistent-worker mode. Falls back to this automatically if the Dymola Python interface can't be loaded.")
    p_run.add_argument("--tolerance", type=float, help="Override comparison tolerance")
    p_run.add_argument("--final-only", action="store_true", help="Compare only final values")
    p_run.add_argument(
        "--timeout", type=int, default=None,
        help="Per-test timeout in seconds (default: 60)",
    )
    p_run.add_argument(
        "--report-format", choices=["console", "junit", "html"],
        default="console", help="Output format for test report",
    )
    p_run.add_argument(
        "--report", action="store_true",
        help="Generate HTML report suite with index page and per-test reports",
    )
    p_run.add_argument(
        "--merge", action="store_true",
        help="When used with --filter, expand the read/compare/report scope to "
             "include all tests in the persistent batch manifest (not just the "
             "filtered subset). Lets you rerun a few tests but still see a full "
             "report covering the whole suite.",
    )
    p_run.add_argument(
        "--rerun", nargs="?", const="failed", default=None, metavar="CATEGORIES",
        help="Rerun tests selected by status from the prior run. Categories: "
             "failed, no-baseline, warnings, sim-failed, passed (comma-separated). "
             "Default: failed. Implies --merge.",
    )

    # compare
    p_compare = subparsers.add_parser(
        "compare", help="Compare last results against references"
    )
    p_compare.add_argument("--filter", type=str, help="Glob, comma-separated list, or @file (one pattern per line) — matches against model_id")
    p_compare.add_argument("--package", type=str, help="Filter by package prefix")
    p_compare.add_argument("--tolerance", type=float, help="Override comparison tolerance")
    p_compare.add_argument("--final-only", action="store_true", help="Compare only final values")
    p_compare.add_argument(
        "--report-format", choices=["console", "junit", "html"],
        default="console",
    )
    p_compare.add_argument(
        "--report", action="store_true",
        help="Generate HTML report suite with index page and per-test reports",
    )

    # export
    p_export = subparsers.add_parser("export", help="Export reference data")
    p_export.add_argument(
        "--format", choices=["json", "csv"], default="json",
        help="Export format",
    )
    p_export.add_argument("--output", type=str, help="Output file path")
    p_export.add_argument("--filter", type=str, help="Glob, comma-separated list, or @file (one pattern per line) — matches against model_id")
    p_export.add_argument("--package", type=str, help="Filter by package prefix")

    # manifest
    p_manifest = subparsers.add_parser(
        "manifest", help="Manage the test manifest"
    )
    p_manifest.add_argument(
        "action", choices=["show", "dump", "cleanup"],
        help="'show': display all references and their status. "
             "'dump': write ref ID to model ID mapping to work directory. "
             "'cleanup': remove reference files with status 'obsolete'.",
    )
    p_manifest.add_argument(
        "--show-obsolete", action="store_true",
        help="Also show obsolete entries (show only)",
    )
    p_manifest.add_argument(
        "--orphans", action="store_true",
        help="cleanup: target orphan batch_manifest entries (models no longer "
             "in discovery) instead of obsolete reference files. Lists by "
             "default; pass --apply to actually remove.",
    )
    p_manifest.add_argument(
        "--apply", action="store_true",
        help="cleanup --orphans: actually delete orphan dirs and manifest entries (default is dry-run).",
    )

    # add
    p_add = subparsers.add_parser(
        "add", help="Add a test to test_spec.json"
    )
    p_add.add_argument(
        "model_id", type=str,
        help="Fully qualified Modelica model path (e.g., 'MyLib.Examples.Test')",
    )
    p_add.add_argument(
        "--variables", nargs="*", default=None,
        help="Variable patterns to track (e.g., 'pipe.T*' 'pump.m_flow'). "
             "Omit for simulate-only.",
    )

    # spec-update
    p_spec_update = subparsers.add_parser(
        "spec-update", help="Update comparison tolerances in test_spec.json from a JSON file"
    )
    p_spec_update.add_argument(
        "json_file", type=str,
        help="Path to JSON file with tolerance settings (e.g., from interactive report export)",
    )

    # check-dymola — diagnostic for the Dymola Python interface loader
    subparsers.add_parser(
        "check-dymola",
        help="Locate and load Dymola's Python interface; report what was found",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "discover": cmd_discover,
        "run": cmd_run,
        "compare": cmd_compare,
        "export": cmd_export,
        "manifest": cmd_manifest,
        "add": cmd_add,
        "spec-update": cmd_spec_update,
        "check-dymola": cmd_check_dymola,
    }

    return commands[args.command](args)


def main_entry() -> None:
    """Entry point for the console_scripts wrapper."""
    sys.exit(main())
