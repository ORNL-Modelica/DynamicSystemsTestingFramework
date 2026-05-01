"""Dynamic Systems Testing Framework — regression & unit testing."""

# Version source-of-truth lives in pyproject.toml; importlib.metadata
# reads the installed package's metadata so `dstf --version` and any
# downstream tooling stay in sync without manual duplication.
try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("dstf")
except Exception:  # noqa: BLE001 — fall back during editable / unbuilt installs
    __version__ = "0.0.0+unknown"
