"""Command-line interface for the Dynamic Systems Testing Framework.

The package's public face is the two entry points — ``main`` (used by the
docs and by tests that drive the whole CLI) and ``main_entry`` (the
``[project.scripts]`` console-script target and what ``python -m dstf``
calls) — and ``build_arg_parser``, the command tree as an ``argparse``
parser: every command, flag and help text, built without loading a plug-in
or reading a file (the reference pages are generated from it). Everything
else lives in the command modules and is imported from its own home — there
is no compatibility re-export layer here.
"""

from .main import main, main_entry
from .parser import build_arg_parser

__all__ = ["build_arg_parser", "main", "main_entry"]
