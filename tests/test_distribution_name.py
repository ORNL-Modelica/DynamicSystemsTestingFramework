"""The distribution name is not the import name, and drift between them is silent.

You ``import dstf`` but you install ``dynamic-systems-testing-framework``: the
short name was already taken on PyPI by an unrelated project, found while
verifying the README's install instructions.

That split has one dangerous property. ``importlib.metadata.version()`` with a
stale name raises ``PackageNotFoundError``, which both call sites catch and turn
into a fallback string — so a rename that misses a lookup does not fail, it just
starts writing ``unknown`` as the version into every result file's provenance.
These tests make that drift loud.

The subtle part is the skip: "the declared name does not resolve" is BOTH the
drift state and the ordinary not-installed state, and skipping on the second
silently covers the first. So an unresolvable name is only a skip when no
install of this project can be found at all; if one exists under a different
name, that is drift and it fails.
"""

from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path

import pytest
import tomllib

import dstf
from dstf.backends.run_metadata import _dstf_version

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _declared_name() -> str:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["name"]


def _installs_of_this_project() -> list[str]:
    """Distribution names whose install points at this source tree.

    ``direct_url.json`` is what an editable install leaves behind, and it is the
    only reliable link back to the checkout — an editable distribution's file
    list names a ``.pth`` shim, not ``dstf/__init__.py``.
    """
    found = []
    for dist in distributions():
        raw = dist.read_text("direct_url.json")
        if raw and str(_REPO_ROOT) in raw.replace("\\", "/").replace("file://", ""):
            name = dist.metadata["Name"]
            if name:
                found.append(name)
    return found


def test_dist_name_constant_matches_pyproject():
    """The constant the code looks up is the name the package is published under."""
    assert dstf._DIST_NAME == _declared_name()


def test_metadata_resolves_under_the_declared_name():
    """The installed distribution answers to the name the code asks for.

    Failing here with an install present is the drift this file exists to catch.
    """
    declared = _declared_name()
    try:
        assert version(declared)
        return
    except PackageNotFoundError:
        pass

    installed = _installs_of_this_project()
    if not installed:
        pytest.skip("this project is not installed in the current environment")
    pytest.fail(
        f"pyproject declares {declared!r}, but this source tree is installed as "
        f"{installed!r}. Version lookups fall back to 'unknown' rather than "
        f"failing, so provenance would silently degrade. Re-run `uv sync`."
    )


def test_version_lookups_agree_and_are_not_the_fallback():
    """Both call sites resolve for real; the fallbacks are what a miss looks like."""
    if not _installs_of_this_project():
        pytest.skip("this project is not installed in the current environment")
    assert dstf.__version__ != "0.0.0+unknown"
    assert _dstf_version() != "unknown"
    assert _dstf_version() == dstf.__version__
