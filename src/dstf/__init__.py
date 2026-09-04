"""Dynamic Systems Testing Framework — regression & unit testing."""

from typing import TYPE_CHECKING

# The DISTRIBUTION name, which is not the import name: you `import dstf`,
# but you install `dynamic-systems-testing-framework`. They differ because
# the short name was already taken on PyPI by an unrelated project. Every
# importlib.metadata lookup must use this constant — a stale literal does not
# raise, it silently yields the unknown-version fallback below, so
# tests/test_distribution_name.py pins this to pyproject.toml's [project] name.
_DIST_NAME = "dynamic-systems-testing-framework"

# Version source-of-truth lives in pyproject.toml; importlib.metadata
# reads the installed package's metadata so `dstf --version` and any
# downstream tooling stay in sync without manual duplication.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version(_DIST_NAME)
except Exception:  # noqa: BLE001 — fall back during editable / unbuilt installs
    __version__ = "0.0.0+unknown"

if TYPE_CHECKING:
    from .plugins import load_plugins

__all__ = ["__version__", "load_plugins"]


def __getattr__(name: str):
    """Lazily expose :func:`dstf.plugins.load_plugins` at package level.

    `import dstf` must stay near-free — the CLI imports it before argparse
    has even run. Module-level `from .plugins import load_plugins` would
    drag in the plug-in machinery on every import, so it resolves on first
    attribute access instead (PEP 562).
    """
    if name == "load_plugins":
        from .plugins import load_plugins

        return load_plugins
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
