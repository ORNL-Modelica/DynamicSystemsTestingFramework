"""`dstf diff`: every variable of two runs of one tool, as Markdown or JSON."""

import argparse
import sys
from pathlib import Path

from .common import NotLabelled, labelled


def cmd_diff(args: argparse.Namespace) -> int:
    """Print (or write to --output) what differs between two runs, every
    variable of each test's result file, as Markdown or JSON. The runs are
    only read; no testing.json and no library is."""
    from ..reporting.baseline_diff_text import GITHUB_LIMIT
    from ..reporting.run_diff import run_diff
    from ..reporting.run_diff_text import as_json, as_markdown, detailed
    from ..storage.baseline_diff import DiffError

    try:
        diff = run_diff(
            [labelled(a) for a in args.runs],
            tests=args.test,
            folders=args.folder,
            near_zero=args.near_zero,
        )
        details = detailed(diff, args.details) if args.details is not None else set()
    except (NotLabelled, DiffError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        text = as_json(diff)
    else:
        text = as_markdown(diff, details=details)
        if len(text) > GITHUB_LIMIT:
            print(
                f"Note: this Markdown is {len(text):,} characters; a GitHub "
                f"pull-request body or comment holds {GITHUB_LIMIT:,}.",
                file=sys.stderr,
            )
    if args.output is None:
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0
