"""The command dispatch table and the process entry points. The command
tree itself — every command, its flags and its help — is ``cli.parser``."""

import sys

from ..config import ConfigError
from .author_cmd import cmd_author
from .baseline_cmds import (
    cmd_companion_add,
    cmd_companion_freeze,
    cmd_companion_list,
    cmd_companion_remove,
    cmd_soft_check_add,
    cmd_soft_check_list,
    cmd_soft_check_remove,
)
from .diag_cmds import (
    cmd_baseline_export,
    cmd_check,
    cmd_discover,
    cmd_manifest_cleanup,
    cmd_manifest_dump,
    cmd_manifest_show,
)
from .parser import build_arg_parser, command_path
from .run_cmds import cmd_compare, cmd_run
from .spec_cmds import cmd_spec_add, cmd_spec_schema, cmd_spec_update

# Command → handler dispatch table, keyed by the command as it is typed
# (`cli.parser.command_path`). Lives at module scope so tests can assert the
# registered commands without re-running `main()`. Update both the tree (in
# `cli.parser`) and this dict when adding a command — the two are held equal
# by `test_argparse_subcommands_match_dispatch_table`.
_COMMANDS = {
    "discover": cmd_discover,
    "run": cmd_run,
    "compare": cmd_compare,
    "author": cmd_author,
    "check": cmd_check,
    "spec add": cmd_spec_add,
    "spec update": cmd_spec_update,
    "spec schema": cmd_spec_schema,
    "baseline export": cmd_baseline_export,
    "soft-check add": cmd_soft_check_add,
    "soft-check list": cmd_soft_check_list,
    "soft-check remove": cmd_soft_check_remove,
    "companion add": cmd_companion_add,
    "companion list": cmd_companion_list,
    "companion freeze": cmd_companion_freeze,
    "companion remove": cmd_companion_remove,
    "manifest show": cmd_manifest_show,
    "manifest dump": cmd_manifest_dump,
    "manifest cleanup": cmd_manifest_cleanup,
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
        # Before dispatch so every command sees the same registries — a
        # plug-in backend must be resolvable by `discover`, `run` and
        # `spec schema` alike. A broken plug-in raises ConfigError and
        # lands on the clean exit-2 path below.
        from ..plugins import load_plugins

        load_plugins()
        return _COMMANDS[command_path(args)](args)
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
