"""`dstf summary`: several runs' verdicts side by side."""

import argparse
import sys
from pathlib import Path


def _labelled(arg: str) -> tuple[str, Path]:
    """``LABEL=DIR`` as its two parts."""
    from ..reporting.results_summary import SummaryError

    label, sep, folder = arg.partition("=")
    if not (sep and label and folder):
        raise SummaryError(f"{arg}: not LABEL=DIR (main=testing_output/main)")
    return label, Path(folder)


def cmd_summary(args: argparse.Namespace) -> int:
    """Print (or write to --output) the pass/run matrix over the runs, as
    Markdown or JSON."""
    from ..reporting.results_summary import SummaryError, read_summary
    from ..reporting.results_summary_text import as_json, as_markdown

    try:
        summary = read_summary([_labelled(a) for a in args.runs], base=args.base)
    except SummaryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    text = as_json(summary) if args.format == "json" else as_markdown(summary)
    if args.output is None:
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0
