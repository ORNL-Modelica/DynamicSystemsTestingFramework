"""`dstf summary`: several runs' verdicts side by side."""

import argparse
import sys
from pathlib import Path

from .common import NotLabelled, labelled


def cmd_summary(args: argparse.Namespace) -> int:
    """Print (or write to --output) the pass/run matrix over the runs, as
    Markdown or JSON."""
    from ..reporting.results_summary import SummaryError, read_summary
    from ..reporting.results_summary_text import as_json, as_markdown

    try:
        summary = read_summary([labelled(a) for a in args.runs], base=args.base)
    except (NotLabelled, SummaryError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    text = as_json(summary) if args.format == "json" else as_markdown(summary)
    if args.output is None:
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0
