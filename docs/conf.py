"""Sphinx configuration of the documentation site.

The reference pages are generated from the code by the local extension in
``_ext/`` (``dstf_reference``), which imports ``dstf``: importing it starts
no simulator and reads no project file. The working notes beside the site's
pages in ``docs/`` stay plain files of the repository and are left out of
the build (``exclude_patterns``).
"""

from __future__ import annotations

import sys
from importlib.metadata import version as _version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "_ext"))

project = "Dynamic Systems Testing Framework"
author = "Scott Greenwood"
copyright = "2026 ORNL-Modelica"
# The installed distribution's version: the one the reference describes.
release = _version("dynamic-systems-testing-framework")
version = ".".join(release.split(".")[:2])

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "dstf_reference",
]

source_suffix = {".md": "markdown"}
# A flag stays --apply and a JSON string stays "modelica": no typographic
# dashes or quotes in text that is copied into a terminal or a file.
smartquotes = False
root_doc = "index"
myst_enable_extensions = ["colon_fence"]
myst_heading_anchors = 3

#: Contributor material: plain files of the repository, not pages of the
#: site. usage.md and extensibility.md are replaced by guides.
exclude_patterns = [
    "_build",
    "decisions.md",
    "ideas.md",
    "vision.md",
    "architecture.md",
    "patterns.md",
    "constraints.md",
    "releasing.md",
    "usage.md",
    "extensibility.md",
    "qa",
    "design",
    "superpowers",
]

html_theme = "furo"
html_title = f"DSTF {release}"
html_static_path = ["_static"]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_class_signature = "separated"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
}

nitpicky = True
#: Types the API pages name and do not document: each internal, with why a
#: plug-in does not need it.
nitpick_ignore = [
    # What run_tests returns: the run's record on disk, which the framework
    # reads back; a plug-in returns what the default or _prepare_run made.
    ("py:class", "dstf.storage.batch_manifest.BatchManifest"),
    ("py:class", "BatchManifest"),
    # The parsed group a combinator's build reads its keys from.
    ("py:class", "CombinatorSpec"),
    # The authoring tool's own types, in descriptor fields a plug-in mode
    # leaves at their defaults.
    ("py:class", "ScoreSummary"),
    ("py:class", "HandleEdit"),
    ("py:class", "SeedData"),
    ("py:class", "RowPlace"),
]
