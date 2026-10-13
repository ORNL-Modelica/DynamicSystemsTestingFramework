"""Argument-parser construction, the subcommand dispatch table, and the
process entry points."""

import argparse
import sys

from ..config import ConfigError
from .author_cmd import cmd_author
from .baseline_cmds import cmd_companion, cmd_import_baseline, cmd_soft_check
from .common import _parse_review_filter
from .diag_cmds import (
    cmd_check,
    cmd_discover,
    cmd_export,
    cmd_manifest,
)
from .run_cmds import cmd_compare, cmd_run
from .spec_cmds import (
    SCHEMA_FILES,
    SPEC_FILE,
    cmd_add,
    cmd_export_schema,
    cmd_spec_update,
)


def _review_filter_arg(value: str) -> str:
    """argparse ``type=`` callback for ``-i`` / ``--rerun`` category lists.

    review 2026-07-06 finding 58: validate at parse time so a typo like
    ``-i sim_failed`` aborts in under a second with the valid-categories
    message (argparse exit code 2) — not after the full simulation run.
    """
    _parse_review_filter(value)  # raises argparse.ArgumentTypeError on typos
    return value


class _BackendNames:
    """``check``'s choices: the registered backends' names, asked for when
    an argument is checked against them and not when the parser is built —
    a plug-in registers its backend as the plug-ins load, which `--help`
    and `--version` must not wait for (nor fail on)."""

    def _names(self) -> list[str]:
        from ..backends import backend_names
        from ..plugins import load_plugins

        load_plugins()
        return backend_names()

    def __contains__(self, name: object) -> bool:
        return name in self._names()

    def __iter__(self):
        return iter(self._names())


def _add_global_flags(parser: argparse.ArgumentParser, *, subcommand: bool) -> None:
    """Attach the config-locating flags every subcommand needs.

    Called twice for each flag: once on the top-level parser (so
    ``dstf --config X run`` works) and once on a parent parser that every
    subparser inherits (so ``dstf run --config X`` works too). Both
    positions must work — the flag-before-subcommand-only form was the
    single biggest first-contact stumble.

    *subcommand* switches the default to :data:`argparse.SUPPRESS`, and that
    is load-bearing: ``_SubParsersAction`` parses the subcommand's arguments
    into a fresh namespace and then copies **every** attribute of it onto the
    top-level namespace. A plain ``default=None`` on the subparser copy would
    therefore erase a value given before the subcommand. SUPPRESS leaves the
    attribute unset when the flag isn't passed there, so the top-level value
    survives; the top-level copy (``subcommand=False``) is what guarantees
    the attribute exists at all.
    """
    default = argparse.SUPPRESS if subcommand else None
    parser.add_argument(
        "--source-path",
        type=str,
        default=default,
        help="Path to the source for the library under test (Modelica package dir containing package.mo, FMU dir, ...). Default: testing.json's source_path; without one, a Modelica package found from the cwd.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=default,
        help="Path to testing.json (default: the one in the library's parent directory, the library, --reference-root or the cwd). "
        "When none is found, `discover` and `check` read the project on defaults; every other command writes a default one beside the library (in --reference-root when given).",
    )
    parser.add_argument(
        "--reference-root",
        type=str,
        default=default,
        help="Path to reference results root (default: testing.json's reference_root; without one, testing.json's own directory "
        "when that is named ReferenceResults, else Resources/ReferenceResults beside the library)",
    )
    parser.add_argument(
        "--test-spec",
        type=str,
        default=default,
        help="Path to test_spec.json (external test definitions)",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI parser. Pulled out of :func:`main` so
    tests can introspect subcommands and flags without dispatching, and
    so :func:`main` stays a thin parse-and-dispatch shell.
    """
    from .. import __version__ as _dstf_version

    parser = argparse.ArgumentParser(
        prog="dstf",
        description="Dynamic Systems Testing Framework — regression & unit testing for time-dependent system behavior",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {_dstf_version}",
        help="Print the installed dstf version and exit.",
    )
    _add_global_flags(parser, subcommand=False)

    # Inherited by every subparser so the global flags are accepted after the
    # subcommand as well as before it.
    global_parent = argparse.ArgumentParser(add_help=False)
    _add_global_flags(global_parent, subcommand=True)

    # Parent parsers for flag groups shared across subcommands. argparse
    # merges these via parents=[...] on add_parser; each subcommand inherits
    # only the groups that apply, keeping --help output identical to the
    # pre-refactor inline form while eliminating duplication.
    filter_parent = argparse.ArgumentParser(add_help=False)
    filter_parent.add_argument(
        "--filter",
        type=str,
        help="Glob, comma-separated list, or @file (one pattern per line) — "
        "matches against model_id. Commas inside [] character classes are "
        "part of the class, not separators (e.g. 'Test[A,B]*' is one pattern).",
    )
    filter_parent.add_argument("--package", type=str, help="Filter by package prefix")

    compare_parent = argparse.ArgumentParser(add_help=False)
    compare_parent.add_argument(
        "--tolerance", type=float, help="Override comparison tolerance"
    )
    compare_parent.add_argument(
        "--default-points",
        action="store_true",
        help="Use points mode (with empty points list) as the default for variables without an explicit ``mode`` override.",
    )

    report_parent = argparse.ArgumentParser(add_help=False)
    report_parent.add_argument(
        "--report-format",
        choices=["console", "junit", "html"],
        default="console",
        help="Output format for test report",
    )
    report_parent.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Destination for --report-format junit/html "
        "(default: test-results.xml / test-report.html in the CWD). "
        "Ignored by the console format.",
    )
    report_parent.add_argument(
        "--report",
        action="store_true",
        help="Render per-test interactive deep-dive reports under "
        "<work_dir>/reports/<test>/. The unified <work_dir>/dashboard.html "
        "always renders regardless of this flag — it just doesn't link to "
        "per-test pages without --report.",
    )
    report_parent.add_argument(
        "--no-browser",
        action="store_true",
        help="Open no browser tab on the dashboard or a report page; the "
        "file:// link is still printed.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    subparsers.add_parser(
        "discover",
        parents=[global_parent, filter_parent],
        help="Discover and list all test models",
    )

    # run
    p_run = subparsers.add_parser(
        "run",
        parents=[global_parent, filter_parent, compare_parent, report_parent],
        help="Run tests (simulate via the configured backend + compare against references)",
    )
    # review 2026-07-06 finding 61: review-then-accept (-i) and
    # accept-everything (--accept) are mutually exclusive intents — make
    # combining them a hard argparse error instead of silently letting
    # --accept win and skipping the review the user asked for.
    accept_or_review = p_run.add_mutually_exclusive_group()
    accept_or_review.add_argument(
        "-i",
        "--interactive",
        nargs="?",
        const="all",
        default=None,
        metavar="FILTER",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="Interactive review. Optional filter: failed, no-baseline, "
        "warnings, sim-failed, passed (comma-separated). Default: all.",
    )
    accept_or_review.add_argument(
        "--accept",
        action="store_true",
        help="Accept results as new baseline references",
    )
    p_run.add_argument("--backend", type=str, help="Backend name (e.g., 'Dymola 2025')")
    p_run.add_argument(
        "--backend-path",
        type=str,
        help="Absolute path to the backend's executable (overrides config)",
    )
    p_run.add_argument("--work-dir", type=str, help="Working directory for output")
    p_run.add_argument(
        "--parallel",
        type=int,
        help="Number of concurrent SIMULATION workers (one simulator process each, where the backend drives one). "
        "Note: on `compare` the same flag means report-rendering threads.",
    )
    p_run.add_argument(
        "--batch",
        action="store_true",
        help="Use the legacy batched script backend (Dymola .mos / OpenModelica subprocess) instead of the default persistent-worker mode. A backend whose persistent preflight fails (Dymola DymolaInterface or OMPython missing) aborts with a message pointing here — there is no silent fallback.",
    )
    p_run.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-test timeout in seconds (default: 60)",
    )
    p_run.add_argument(
        "--merge",
        action="store_true",
        help="When used with --filter, expand the read/compare/report scope to "
        "include all tests in the persistent batch manifest (not just the "
        "filtered subset). Lets you rerun a few tests but still see a full "
        "report covering the whole suite.",
    )
    p_run.add_argument(
        "--rerun",
        nargs="?",
        const="failed",
        default=None,
        metavar="CATEGORIES",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="Rerun tests selected by status from the prior run. Categories: "
        "failed, no-baseline, warnings, sim-failed, passed (comma-separated). "
        "Default: failed. Implies --merge.",
    )

    # compare
    p_compare = subparsers.add_parser(
        "compare",
        parents=[global_parent, filter_parent, compare_parent, report_parent],
        help="Compare last results against references",
    )
    p_compare.add_argument(
        "--backend",
        type=str,
        help="Backend name (e.g., 'Dymola 2025'). Affects which "
        "<reference_root>/<Backend>/<os>/ partition is read.",
    )
    p_compare.add_argument(
        "--backend-path",
        type=str,
        help="Absolute path to the backend's executable (overrides config). "
        "`compare` starts no simulator: this matters only to a backend that "
        "will not load without its executable (Julia, Python).",
    )
    p_compare.add_argument(
        "--work-dir", type=str, help="Working directory containing prior run outputs"
    )
    p_compare.add_argument(
        "--parallel",
        type=int,
        help="Threads for --report RENDERING (Plotly serialization + JSON sidecar dump). "
        "Comparison itself is fast; this is the post-comparison report suite. "
        "Note: on `run` the same flag means simulation workers. Default: 1.",
    )
    p_compare.add_argument(
        "--accept",
        action="store_true",
        help="Accept the last run's on-disk results as new baselines WITHOUT "
        "re-simulating. Skips tests the run recorded as failed/timed_out.",
    )

    # author — the authoring tool (NiceGUI shell over the Session)
    p_author = subparsers.add_parser(
        "author",
        parents=[global_parent, filter_parent],
        help="Open the authoring tool on this project",
    )
    p_author.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to serve the shell on (default: 8080). It binds 127.0.0.1 only.",
    )
    p_author.add_argument(
        "--no-browser",
        action="store_true",
        help="Serve without opening a browser tab (headless launch).",
    )
    p_author.add_argument(
        "--work-dir", type=str, help="Working directory containing prior run outputs"
    )
    p_author.add_argument(
        "--backend",
        type=str,
        help="Backend name (e.g., 'Dymola 2025'). Affects which "
        "<reference_root>/<Backend>/<os>/ partition is read.",
    )

    # export
    p_export = subparsers.add_parser(
        "export",
        parents=[global_parent],
        help="Export reference data",
    )
    p_export.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Export format",
    )
    p_export.add_argument("--output", type=str, help="Output file path")

    # manifest
    p_manifest = subparsers.add_parser(
        "manifest", parents=[global_parent], help="Manage the test manifest"
    )
    p_manifest.add_argument(
        "action",
        choices=["show", "dump", "cleanup"],
        help="'show': display all references and their status. "
        "'dump': write ref ID to model ID mapping to work directory. "
        "'cleanup': remove reference files with status 'obsolete' "
        "(dry-run by default; pass --apply to delete).",
    )
    p_manifest.add_argument(
        "--show-obsolete",
        action="store_true",
        help="Also show obsolete entries (show only)",
    )
    p_manifest.add_argument(
        "--orphans",
        action="store_true",
        help="cleanup: target orphan batch_manifest entries (models no longer "
        "in discovery) instead of obsolete reference files.",
    )
    p_manifest.add_argument(
        "--apply",
        action="store_true",
        help="cleanup: actually delete. EVERY cleanup path is a dry-run "
        "without this flag — it lists what it would remove and exits.",
    )

    # add
    p_add = subparsers.add_parser(
        "add", parents=[global_parent], help="Add a test to test_spec.json"
    )
    p_add.add_argument(
        "model_id",
        type=str,
        help="Fully qualified Modelica model path (e.g., 'MyLib.Examples.Test')",
    )
    p_add.add_argument(
        "--variables",
        nargs="*",
        default=None,
        help="Variable patterns to track (e.g., 'pipe.T*' 'pump.m_flow'). "
        "Omit for simulate-only.",
    )

    # spec-update
    p_spec_update = subparsers.add_parser(
        "spec-update",
        parents=[global_parent],
        help="Apply a JSON-Patch envelope to test_spec.json",
    )
    p_spec_update.add_argument(
        "json_file",
        type=str,
        help="Path to the patch envelope (e.g. spec_patch.json from dstf author's Export)",
    )

    # companion — manage plot-only overlays (D66 baseline-role split)
    p_companion = subparsers.add_parser(
        "companion",
        parents=[global_parent],
        help="Manage plot-only companion references (external data shown as overlays)",
    )
    p_companion.add_argument(
        "action",
        choices=["add", "list", "freeze", "remove"],
        help="'add': register an external file pointer. 'list': show companions. "
        "'freeze': copy external data into ref storage. 'remove': deregister.",
    )
    p_companion.add_argument(
        "model_id",
        nargs="?",
        help="Model ID (required for add/freeze/remove; optional for list)",
    )
    p_companion.add_argument(
        "name", nargs="?", help="Companion name (required for add/freeze/remove)"
    )
    p_companion.add_argument(
        "path", nargs="?", help="Path to data file (required for add)"
    )
    p_companion.add_argument(
        "--format",
        choices=["csv", "json"],
        default=None,
        help="Data format (auto-detected from extension if omitted)",
    )
    p_companion.add_argument(
        "--map",
        dest="variable_map",
        action="append",
        metavar="TEST_VAR=COMPANION_VAR",
        default=None,
        help="'add' only: which of the companion's variables draws on which "
        "of the test's. Repeat per variable. Both names are checked against "
        "the stored baseline and the companion file. Two test variables "
        "may name one companion variable (one curve, drawn twice). "
        "Without any --map the reporter falls back on matching spellings.",
    )

    # soft-check — manage advisory cross-check baselines (D66)
    p_soft = subparsers.add_parser(
        "soft-check",
        parents=[global_parent],
        help="Manage soft_check baselines (advisory cross-checks; warn-only scoring)",
    )
    p_soft.add_argument(
        "action",
        choices=["list", "remove"],
        help="'list': show soft_checks. 'remove': deregister. "
        "Note: there is no `soft-check add` — by design, soft_checks are "
        "added only via `import-baseline` (D66 baseline-role split: "
        "soft_checks come from another regression system's primary, not "
        "from local user input).",
    )
    p_soft.add_argument("model_id", nargs="?")
    p_soft.add_argument("name", nargs="?")

    # import-baseline — import another system's primary as a soft_check (D66)
    p_import = subparsers.add_parser(
        "import-baseline",
        parents=[global_parent],
        help="Import another regression system's primary as a soft_check",
    )
    p_import.add_argument(
        "model_id", type=str, help="Local model ID to attach the soft_check to"
    )
    p_import.add_argument(
        "name",
        type=str,
        help="Soft_check name (how MetricTree leaves will reference it)",
    )
    p_import.add_argument(
        # The dest must NOT be "source_path": that name is also the global
        # --source-path option, and _build_config would treat the ref JSON
        # as the library root (find_package_dir then iterdir()s a file —
        # NotADirectoryError; found live during W-6, D103). metavar keeps
        # the user-facing name.
        "import_source",
        metavar="source_path",
        type=str,
        help="Path to source reference JSON (flat ref file shape)",
    )

    # export-schema — emit JSON-Schema for test_spec.json (6.4.5) or testing.json
    p_schema = subparsers.add_parser(
        "export-schema",
        parents=[global_parent],
        help="Emit a JSON-Schema describing the test_spec.json shape "
        "(or, with --file testing.json, the testing.json shape)",
    )
    p_schema.add_argument(
        "--file",
        choices=SCHEMA_FILES,
        default=SPEC_FILE,
        help="Which file's schema to emit (default: test_spec.json). "
        "testing.json's lists every key with its type, default and "
        "description, and each backend's own settings.",
    )
    p_schema.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write schema to this file instead of stdout",
    )

    # check — where each backend is on this machine, and whether the one
    # the project runs with is ready
    p_check = subparsers.add_parser(
        "check",
        parents=[global_parent],
        help="Say which backends are installed, where each was found and by "
        "which rule, and whether the one this project runs with is ready",
    )
    p_check.add_argument(
        # Not "backend": that is `--backend`'s name, which names the backend
        # a project RUNS with (`_build_config`).
        "check_backend",
        nargs="?",
        default=None,
        choices=_BackendNames(),
        metavar="backend",
        help="Diagnose this backend alone (a registered backend's name: "
        "Dymola, OpenModelica, FMPy, Julia, Python, or a plug-in's). "
        "Default: list every backend, then diagnose the one the project selects.",
    )

    return parser


# Subcommand → handler dispatch table. Lives at module scope so tests can
# assert the registered commands without re-running `main()`. Update both
# the parser (in :func:`build_arg_parser`) and this dict when adding a
# subcommand — symmetry is checked by :data:`_COMMANDS_PARSER_PARITY` test.
_COMMANDS = {
    "discover": cmd_discover,
    "run": cmd_run,
    "compare": cmd_compare,
    "author": cmd_author,
    "export": cmd_export,
    "manifest": cmd_manifest,
    "add": cmd_add,
    "spec-update": cmd_spec_update,
    "companion": cmd_companion,
    "soft-check": cmd_soft_check,
    "import-baseline": cmd_import_baseline,
    "export-schema": cmd_export_schema,
    "check": cmd_check,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    try:
        # Inside the `try`: `check <backend>` loads the plug-ins to know the
        # backends' names, and a broken plug-in is this config error too.
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return 1
        # Before dispatch so every subcommand sees the same registries —
        # a plug-in backend must be resolvable by `discover`, `run` and
        # `export-schema` alike. A broken plug-in raises ConfigError and
        # lands on the clean exit-2 path below.
        from ..plugins import load_plugins

        load_plugins()
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        # review 2026-07-06 leftover A: a testing.json problem (malformed,
        # unreadable, missing explicit --config) is a user error — print one
        # clean line and exit 2 (argparse's usage-error code), never a
        # traceback.
        print(f"Config error: {exc}", file=sys.stderr)
        return 2


def main_entry() -> None:
    """Entry point for the console_scripts wrapper."""
    sys.exit(main())
