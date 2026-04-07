"""Command-line interface for the Modelica testing system."""

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Optional

from .config import Config
from .discovery.test_registry import TestModel, discover_tests, generate_mos_file


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
    print(f"{'Model ID':<90} {'Vars':>4}  {'StopTime':>10}  {'Method':<10}  {'In MOS':>6}")
    print("-" * 130)
    for t in tests:
        stop = f"{t.stop_time:g}"
        in_mos = "yes" if t.in_mos else "no"
        print(f"{t.model_id:<90} {t.n_vars:>4}  {stop:>10}  {t.method:<10}  {in_mos:>6}")

    print(f"\nTotal: {len(tests)} tests")

    # Count stats
    with_mos = sum(1 for t in tests if t.in_mos)
    print(f"  In runAll_Dymola.mos: {with_mos}")
    print(f"  Not in mos file: {len(tests) - with_mos}")

    total_vars = sum(t.n_vars for t in tests)
    print(f"  Total tracked variables: {total_vars}")

    # Regenerate runAll_Dymola.mos if requested
    if args.regenerate_mos:
        mos_path = config.mos_file
        # Only include tests that were in the original mos file or new tests
        mos_tests = [t for t in tests if t.in_mos]
        generate_mos_file(mos_tests, mos_path)
        print(f"\nRegenerated {mos_path} with {len(mos_tests)} tests")

    return 0


def _get_runner(config):
    """Get the appropriate simulator runner for the configured simulator."""
    if config.simulator == "Dymola":
        from .simulators.dymola import DymolaRunner
        return DymolaRunner(config)
    else:
        raise ValueError(
            f"Unsupported simulator: {config.simulator}. "
            f"Supported: {', '.join(('Dymola',))}"
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
    if args.library_path:
        kwargs["library_root"] = Path(args.library_path)
    if hasattr(args, "config") and args.config:
        kwargs["config_file"] = Path(args.config)
    if hasattr(args, "reference_root") and args.reference_root:
        kwargs["reference_root"] = Path(args.reference_root)
    if hasattr(args, "dymola_path") and args.dymola_path:
        kwargs["dymola_path"] = args.dymola_path
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
    return Config(**kwargs)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modelica-testing",
        description="Modelica Library Regression Testing System",
    )
    parser.add_argument(
        "--library-path", type=str, default=None,
        help="Path to the Modelica library root (default: auto-detect from cwd)",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to testing.json config file (default: look in library root)",
    )
    parser.add_argument(
        "--reference-root", type=str, default=None,
        help="Path to reference results root (default: <library>/Resources/ReferenceResults)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    p_discover = subparsers.add_parser(
        "discover", help="Discover and list all test models"
    )
    p_discover.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_discover.add_argument("--package", type=str, help="Filter by package prefix")
    p_discover.add_argument(
        "--regenerate-mos", action="store_true",
        help="Regenerate runAll_Dymola.mos from discovered tests",
    )

    # Shared arg for subcommands that need reference results
    def _add_ref_arg(p):
        p.add_argument(
            "--reference-root", type=str, default=None,
            help="Path to reference results root",
        )

    # run
    p_run = subparsers.add_parser("run", help="Run tests in Dymola")
    p_run.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_run.add_argument("--package", type=str, help="Filter by package prefix")
    p_run.add_argument(
        "--accept", action="store_true",
        help="Accept results as new baseline references",
    )
    p_run.add_argument("--dymola-path", type=str, help="Path to dymola executable")
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
    _add_ref_arg(p_run)

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
    _add_ref_arg(p_compare)

    # export
    p_export = subparsers.add_parser("export", help="Export reference data")
    p_export.add_argument(
        "--format", choices=["json", "csv"], default="json",
        help="Export format",
    )
    p_export.add_argument("--output", type=str, help="Output file path")
    p_export.add_argument("--filter", type=str, help="Glob pattern for model_id")
    p_export.add_argument("--package", type=str, help="Filter by package prefix")
    _add_ref_arg(p_export)

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
    _add_ref_arg(p_migrate)

    # convert
    p_convert = subparsers.add_parser(
        "convert", help="Convert reference files between old and new formats"
    )
    p_convert.add_argument(
        "direction", choices=["to-manifest", "from-manifest"],
        help="'to-manifest': old abbreviated names -> ref_NNNN.json + manifest. "
             "'from-manifest': ref_NNNN.json -> human-readable names + index.json",
    )
    _add_ref_arg(p_convert)

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
        "convert": cmd_convert,
    }

    return commands[args.command](args)


def main_entry() -> None:
    """Entry point for the console_scripts wrapper."""
    sys.exit(main())
