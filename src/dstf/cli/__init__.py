"""Command-line interface for the Dynamic Systems Testing Framework.

The package's public face is the two entry points: ``main`` (used by the
docs and by tests that drive the whole CLI) and ``main_entry`` (the
``[project.scripts]`` console-script target and what ``python -m dstf``
calls). Everything else lives in the command modules and is imported from
its own home — there is no compatibility re-export layer here.
"""

from .main import main, main_entry

__all__ = ["main", "main_entry"]
