"""Command-line interface for the Modelica testing system."""

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Optional

from .config import Config
from .discovery.test_registry import TestModel, discover_tests


def _filter_tests(
    tests: list[TestModel],
    pattern: Optional[str] = None,
    package: Optional[str] = None,
) -> list[TestModel]:
    """Filter tests by glob pattern on model_id or package prefix."""
    filtered = tests
    if package:
        filtered = [t for t in filtered if t.model_id.startswith(package)]
    if pattern:
        filtered = [t for t in filtered if fnmatch.fnmatch(t.model_id, pattern)]
    return filtered


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


def _get_runner(config):
    """Get the appropriate simulator runner for the configured simulator."""
    backend = config.simulator_backend
    if backend == "Dymola":
        from .simulators.dymola import DymolaRunner
        return DymolaRunner(config)
    else:
        raise ValueError(
            f"Unsupported simulator backend: {backend} (from '{config.simulator}'). "
            f"Supported: Dymola"
        )


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests matched the filter.")
        return 1

    print(f"Running {len(tests)} tests...")

    from .storage.reference_store import ReferenceStore

    runner = _get_runner(config)
    manifests = runner.run_tests(tests)
    results = runner.read_results(manifests, tests)

    if args.accept:
        store = ReferenceStore(config)
        stored = store.accept_results(tests, results)
        print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
        return 0
    elif args.interactive:
        from .comparison.comparator import compare_all

        store = ReferenceStore(config)
        comparisons = compare_all(tests, results, store, config)
        return _interactive_review(tests, results, comparisons, store, config)
    else:
        from .comparison.comparator import compare_all

        store = ReferenceStore(config)
        comparisons = compare_all(tests, results, store, config)
        return _output_report(comparisons, args)


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare last run results against stored references."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    from .comparison.comparator import compare_all
    from .storage.reference_store import ReferenceStore

    runner = _get_runner(config)
    store = ReferenceStore(config)
    results = runner.read_last_results(tests)
    comparisons = compare_all(tests, results, store, config)
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


def cmd_migrate(args: argparse.Namespace) -> int:
    """Migrate old buildingspy .txt references to JSON format."""
    config = _build_config(args)
    source = Path(args.source)

    if not source.exists():
        print(f"Source directory not found: {source}")
        return 1

    # Optionally enrich with test model info from discovery
    test_lookup = None
    if not args.skip_discovery:
        tests = discover_tests(config)
        test_lookup = {t.model_id: t for t in tests}

    from .storage.migrate import migrate_buildingspy_references

    print(f"Migrating from: {source}")
    print(f"Writing to:     {config.reference_dir}")
    migrated = migrate_buildingspy_references(source, config, test_lookup)
    print(f"\nMigrated {migrated} reference files")

    if migrated == 0:
        return 1

    # Verify migration unless --no-verify
    if not args.no_verify:
        print()
        from .tools.verify_migration import verify_migration

        output_dir = Path(args.verify_output) if args.verify_output else (config.work_dir / "migration_verify")
        verify_migration(
            source_dir=source,
            json_dir=config.reference_dir,
            output_dir=output_dir,
            plot=not args.no_plots,
        )

    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    """Manage the test manifest."""
    config = _build_config(args)
    from .storage.reference_store import TestManifest, ReferenceStore

    manifest = TestManifest(config.manifest_file)
    action = args.action

    if action == "show":
        if not manifest.exists():
            print(f"No manifest found at {config.manifest_file}")
            return 1

        active = manifest.active_tests()
        data = manifest._load()
        obsolete = {
            tid: entry["model_id"]
            for tid, entry in data["tests"].items()
            if entry.get("status") == "obsolete"
        }

        print(f"Manifest: {config.manifest_file}")
        print(f"Active: {len(active)}  Obsolete: {len(obsolete)}\n")

        if active:
            print(f"{'ID':>6}  {'Model ID'}")
            print("-" * 80)
            for tid in sorted(active):
                print(f"{tid:>6}  {active[tid]}")

        if obsolete and args.show_obsolete:
            print(f"\nObsolete:")
            for tid in sorted(obsolete):
                print(f"{tid:>6}  {obsolete[tid]}")

        return 0

    elif action == "cleanup":
        store = ReferenceStore(config)
        removed = store.cleanup_obsolete()
        if removed:
            print(f"Removed {removed} obsolete reference files.")
        else:
            print("No obsolete references to clean up.")
        return 0

    elif action == "rebuild":
        tests = discover_tests(config)
        tests = _filter_tests(tests, getattr(args, "filter", None), getattr(args, "package", None))

        if not tests:
            print("No tests found.")
            return 1

        # Register all discovered tests in a fresh manifest
        manifest = TestManifest(config.manifest_file)
        for test in tests:
            manifest.register(test.model_id)

        active = manifest.active_tests()
        print(f"Rebuilt manifest with {len(active)} tests at {config.manifest_file}")
        return 0

    return 1


def cmd_convert(args: argparse.Namespace) -> int:
    """Convert reference files between old (abbreviated) and new (manifest) formats."""
    config = _build_config(args)

    from .storage.convert import convert_to_manifest, convert_from_manifest

    ref_dir = config.reference_dir
    manifest_path = config.manifest_file

    if args.direction == "to-manifest":
        print(f"Converting references in {ref_dir} to manifest format...")
        index_path = ref_dir / "index.json" if (ref_dir / "index.json").exists() else None
        converted = convert_to_manifest(ref_dir, manifest_path, index_path)
        print(f"\nConverted {converted} files. Manifest: {manifest_path}")
        return 0 if converted > 0 else 1

    elif args.direction == "from-manifest":
        print(f"Converting references in {ref_dir} from manifest to readable names...")
        converted = convert_from_manifest(ref_dir, manifest_path, config.library_name)
        print(f"\nConverted {converted} files.")
        return 0 if converted > 0 else 1

    return 1


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


def _get_spec_path(config) -> Path:
    """Get the test_spec.json path, using config or default location."""
    if config.test_spec_file is not None:
        return config.test_spec_file
    return config.reference_root / "test_spec.json"


def _interactive_review(
    tests: list[TestModel],
    results: dict,
    comparisons: list,
    store,
    config=None,
) -> int:
    """Interactively review each test and accept/reject results."""
    from .discovery.spec_parser import update_test_variables
    from .simulators import resolve_variable_patterns
    from .simulators.dymola.mat_reader import read_dymola_mat

    test_lookup = {t.model_id: t for t in tests}
    n_accepted = 0
    n_skipped = 0
    n_total = len(comparisons)

    spec_path = _get_spec_path(config) if config else None

    # ANSI colors
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"\nInteractive review: {n_total} tests\n")

    for idx, comp in enumerate(comparisons, 1):
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

        print(f"[{idx}/{n_total}] {BOLD}{model_id}{RESET}")
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
                            mat_path = test_dir / f"{test_key}.mat"
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
                    _generate_and_open_plots(model_id, comp, result, store, config)
                else:
                    print("  No config available for plot generation.")

            elif choice == "q":
                print(f"\nStopped. Accepted {n_accepted}, skipped {n_skipped + (n_total - idx)}.")
                return 0 if n_accepted > 0 else 1

            else:
                options = "a/v/s/d/p/q" if has_result else "s/d/p/q"
                print(f"  Invalid choice. Enter {options}.")

    print(f"\nDone. Accepted {n_accepted}, skipped {n_skipped}.")
    return 0


def _generate_and_open_plots(model_id, comp, result, store, config) -> None:
    """Generate comparison plots and open in browser."""
    from .reporting.plot_comparison import generate_comparison_plots, open_in_browser

    test_key = _find_test_key(model_id, config)
    if test_key:
        plot_dir = config.work_dir / test_key / "plots"
    else:
        plot_dir = config.work_dir / "plots" / model_id.replace(".", "_")

    ref_data = store.get_reference(model_id)

    html_path = generate_comparison_plots(
        model_id=model_id,
        ref_data=ref_data,
        result=result,
        comparisons=comp.variables,
        plot_dir=plot_dir,
    )

    if html_path:
        print(f"  Plots saved to {plot_dir}")
        open_in_browser(html_path)
    else:
        print("  Plot generation failed (matplotlib not installed?)")


def _find_test_key(model_id: str, config) -> Optional[str]:
    """Find the test_NNNN key for a model from the batch manifest."""
    from .simulators import BatchManifest
    manifest_paths = sorted(config.work_dir.glob("batch_*_manifest.json"))
    for mp in manifest_paths:
        bm = BatchManifest.load(mp)
        for tk, mid in bm.manifest.items():
            if mid == model_id:
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
    if args.package_path:
        kwargs["package_path"] = Path(args.package_path)
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
        "--package-path", type=str, default=None,
        help="Path to the Modelica package directory containing package.mo (default: auto-detect from cwd)",
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

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    p_discover = subparsers.add_parser(
        "discover", help="Discover and list all test models"
    )
    p_discover.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_discover.add_argument("--package", type=str, help="Filter by package prefix")

    # run
    p_run = subparsers.add_parser("run", help="Run tests in Dymola")
    p_run.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_run.add_argument("--package", type=str, help="Filter by package prefix")
    p_run.add_argument(
        "-i", "--interactive", action="store_true",
        help="Interactively review and accept/reject each test result",
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
    p_run.add_argument("--tolerance", type=float, help="Override comparison tolerance")
    p_run.add_argument("--final-only", action="store_true", help="Compare only final values")
    p_run.add_argument(
        "--timeout", type=int, default=None,
        help="Per-test timeout in seconds (default: 600)",
    )
    p_run.add_argument(
        "--report-format", choices=["console", "junit", "html"],
        default="console", help="Output format for test report",
    )
    #_run)

    # compare
    p_compare = subparsers.add_parser(
        "compare", help="Compare last results against references"
    )
    p_compare.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_compare.add_argument("--package", type=str, help="Filter by package prefix")
    p_compare.add_argument("--tolerance", type=float, help="Override comparison tolerance")
    p_compare.add_argument("--final-only", action="store_true", help="Compare only final values")
    p_compare.add_argument(
        "--report-format", choices=["console", "junit", "html"],
        default="console",
    )
    #_compare)

    # export
    p_export = subparsers.add_parser("export", help="Export reference data")
    p_export.add_argument(
        "--format", choices=["json", "csv"], default="json",
        help="Export format",
    )
    p_export.add_argument("--output", type=str, help="Output file path")
    p_export.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_export.add_argument("--package", type=str, help="Filter by package prefix")
    #_export)

    # migrate
    p_migrate = subparsers.add_parser(
        "migrate", help="Migrate old buildingspy .txt references to JSON format"
    )
    p_migrate.add_argument(
        "source", type=str,
        help="Path to directory containing buildingspy .txt reference files",
    )
    p_migrate.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip test discovery (faster, but less metadata in output)",
    )
    p_migrate.add_argument(
        "--no-verify", action="store_true",
        help="Skip post-migration verification",
    )
    p_migrate.add_argument(
        "--no-plots", action="store_true",
        help="Skip plot generation during verification",
    )
    p_migrate.add_argument(
        "--verify-output", type=str, default=None,
        help="Output directory for verification results (default: <work_dir>/migration_verify)",
    )
    #_migrate)

    # manifest
    p_manifest = subparsers.add_parser(
        "manifest", help="Manage the test manifest"
    )
    p_manifest.add_argument(
        "action", choices=["show", "cleanup", "rebuild"],
        help="'show': display manifest contents. "
             "'cleanup': remove obsolete reference files. "
             "'rebuild': regenerate manifest from discovered tests.",
    )
    p_manifest.add_argument("--filter", type=str, help="Glob pattern for model_id (rebuild only)")
    p_manifest.add_argument("--package", type=str, help="Filter by package prefix (rebuild only)")
    p_manifest.add_argument(
        "--show-obsolete", action="store_true",
        help="Also show obsolete entries (show only)",
    )
    #_manifest)

    # convert
    p_convert = subparsers.add_parser(
        "convert", help="Convert reference files between old and new formats"
    )
    p_convert.add_argument(
        "direction", choices=["to-manifest", "from-manifest"],
        help="'to-manifest': old abbreviated names -> ref_NNNN.json + manifest. "
             "'from-manifest': ref_NNNN.json -> human-readable names + index.json",
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

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "discover": cmd_discover,
        "run": cmd_run,
        "compare": cmd_compare,
        "export": cmd_export,
        "migrate": cmd_migrate,
        "manifest": cmd_manifest,
        "convert": cmd_convert,
        "add": cmd_add,
    }

    return commands[args.command](args)


def main_entry() -> None:
    """Entry point for the console_scripts wrapper."""
    sys.exit(main())
