"""`dstf author`: the authoring tool (§4.7 "Launch")."""

import argparse
from pathlib import Path

from ..config import ConfigError


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
            ("dymola_interface", "--dymola-interface"),
        )
        if getattr(args, attr, None)
    ]
    if dropped:
        raise ConfigError(
            f"dstf author reads {', '.join(dropped)} from testing.json; the tool "
            "edits and saves the spec that file names, so an override here "
            "would save to a different file than it shows"
        )
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
