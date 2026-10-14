"""`dstf author`: the authoring tool (§4.7 "Launch")."""

import argparse
import importlib
from pathlib import Path

from ..config import ConfigError

#: Every module the ``[author]`` extra installs, checked before serving:
#: the Variables drawer's `regex` is reached only when a Test page is (per
#: request), so a venv synced before it joined the extra served the Suite
#: page and failed every Test page. The extra's distributions are these
#: names (`test_author_checks_every_package_of_the_extra`).
_AUTHOR_MODULES = ("nicegui", "regex")

#: The port the tool is served on when ``--port`` names none.
DEFAULT_PORT = 8080


def cmd_author(args: argparse.Namespace) -> int:
    """Open the authoring shell on this project and serve until Quit."""
    # The Session builds the Config itself, from the file it will save
    # beside: `dstf author` opens the project a testing.json names, and
    # looks for none.
    # --source-path, --reference-root and --test-spec are not offered (the
    # tool edits and saves the spec testing.json names; an override here
    # would save to a different file than it shows), so the parser has
    # already refused them.
    if args.config is None:
        raise ConfigError("dstf author needs --config <testing.json>")
    try:
        for module in _AUTHOR_MODULES:
            importlib.import_module(module)
        from ..authoring.shell import run_app
    except ImportError as e:
        # The tool needs NiceGUI and regex, the optional extra — say how to
        # get it as one clean "Config error: ..." line, never a traceback.
        raise ConfigError(
            "the authoring tool needs the [author] extra: "
            "uv sync --extra dev --extra author (or pip install "
            f"'dynamic-systems-testing-framework[author]') — {e}"
        ) from e
    run_app(
        Path(args.config),
        filter=args.filter,
        package=args.package,
        port=args.port,
        work_dir=Path(args.work_dir) if args.work_dir is not None else None,
        backend=args.backend,
        show=not args.no_browser,
    )
    return 0
