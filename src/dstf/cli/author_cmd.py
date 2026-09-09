"""`dstf author`: the authoring tool (§4.7 "Launch")."""

import argparse

from ..config import ConfigError
from .common import _build_config


def cmd_author(args: argparse.Namespace) -> int:
    """Open the authoring shell on this project and serve until Quit."""
    try:
        from ..authoring.app import run_app
    except ImportError as e:
        # The shell needs NiceGUI, which is an optional extra — say how to
        # get it as one clean "Config error: ..." line, never a traceback.
        raise ConfigError(
            "the authoring tool needs the [author] extra: "
            "uv sync --extra dev --extra author (or pip install "
            f"'dynamic-systems-testing-framework[author]') — {e}"
        ) from e
    config = _build_config(args)
    if config.config_file is None:
        raise ConfigError("dstf author needs --config <testing.json>")
    run_app(
        config.config_file,
        filter=args.filter,
        package=args.package,
        port=args.port,
        work_dir=config.work_dir if getattr(args, "work_dir", None) else None,
        backend=args.backend if getattr(args, "backend", None) else None,
        show=not args.no_browser,
    )
    return 0
