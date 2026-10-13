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


def cmd_author(args: argparse.Namespace) -> int:
    """Open the authoring shell on this project and serve until Quit."""
    # The Session builds the Config itself, from the file it will save
    # beside. Nothing is resolved before these checks: a full Config
    # resolution can WRITE a default testing.json next to a package.mo.
    if not getattr(args, "config", None):
        raise ConfigError("dstf author needs --config <testing.json>")
    dropped = [
        flag
        for attr, flag in (
            ("source_path", "--source-path"),
            ("reference_root", "--reference-root"),
            ("test_spec", "--test-spec"),
        )
        if getattr(args, attr, None)
    ]
    if dropped:
        raise ConfigError(
            f"dstf author reads {', '.join(dropped)} from testing.json; the tool "
            "edits and saves the spec that file names, so an override here "
            "would save to a different file than it shows"
        )
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
        work_dir=Path(args.work_dir) if getattr(args, "work_dir", None) else None,
        backend=args.backend if getattr(args, "backend", None) else None,
        show=not args.no_browser,
    )
    return 0
