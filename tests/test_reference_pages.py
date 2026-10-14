"""The reference pages of the documentation cannot miss a flag, a key, a
backend setting or a mode field.

They are rendered from the code by ``docs/_ext``: each table the code reads
a file or a command line by is walked by a function of
``dstf_reference_rows``, and each page names the tables it shows by a
directive. So "every key is on its page" is two facts, both tested here
without building the site:

1. by construction — each rendering function yields exactly its table's
   keys, and one added to the code (a fake key, a fake flag) is in what it
   yields with no change to the renderer;
2. by the pages — every table there is is named by a directive on a page
   of the site's table of contents.

And the prose around those directives types no key or flag by hand.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
sys.path.insert(0, str(DOCS / "_ext"))

import dstf_reference_rows as rows  # noqa: E402

from dstf.backends import backend_class, backend_names  # noqa: E402
from dstf.cli import build_arg_parser  # noqa: E402
from dstf.comparison.registry import combinators, metrics  # noqa: E402
from dstf.config_shape import Setting  # noqa: E402

PAGES = sorted((DOCS / "reference").glob("*.md"))
_DIRECTIVE = re.compile(r"^```\{(dstf-[a-z-]+)\}[ \t]*(\S*)", re.MULTILINE)


def _directives() -> dict[tuple[str, str], Path]:
    """Every ``dstf-*`` directive on the reference pages, with its argument."""
    found: dict[tuple[str, str], Path] = {}
    for page in PAGES:
        for match in _DIRECTIVE.finditer(page.read_text(encoding="utf-8")):
            found[(match.group(1), match.group(2))] = page
    return found


# -- 1. by construction ------------------------------------------------------


def test_each_key_table_renders_exactly_its_keys():
    for name, table in rows.tables().items():
        assert [r.name for r in rows.key_rows(table)] == list(table), name


def test_every_registered_backend_has_its_settings_table():
    tables = rows.backend_tables()
    assert list(tables) == backend_names()
    for name, table in tables.items():
        assert table is backend_class(name).settings
        assert [r.name for r in rows.key_rows(table)] == list(table)


def test_every_mode_renders_every_field_and_column():
    sections = {s.name: s for s in rows.mode_sections()}
    assert list(sections) == [d.name for d in metrics()]
    for desc in metrics():
        section = sections[desc.name]
        fields = dataclasses.fields(desc.config_cls)
        assert [r.name for r in section.fields] == [f.name for f in fields]
        assert section.description == desc.description
        for f in fields:
            columns = f.metadata.get("columns")
            if columns:
                assert [r.name for r in section.columns[f.name]] == [
                    c[0] for c in columns
                ]


def test_every_combinator_is_rendered():
    assert [name for name, _ in rows.combinator_rows()] == [
        c.name for c in combinators()
    ]


def test_every_command_and_every_flag_is_rendered():
    parser = build_arg_parser()
    nodes = {c.path: c for c in rows.command_nodes()}
    assert list(nodes) == list(parser.nodes)
    for path, node in parser.nodes.items():
        shown = " ".join(o.names for o in nodes[path].options)
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            for flag in action.option_strings or [action.metavar or action.dest]:
                assert flag in shown, f"dstf {path}: {flag}"


def test_a_key_added_to_a_table_is_rendered(monkeypatch):
    """No list of keys of its own: a key the code gains is on the page."""
    from dstf.config_shape import TESTING_JSON

    monkeypatch.setitem(
        TESTING_JSON,
        "fake_key",
        Setting({"type": "string"}, "x", "A key added by this test."),
    )
    rendered = rows.key_rows(rows.tables()["testing"])
    assert ("fake_key", '"x"') in {(r.name, r.default) for r in rendered}


def test_a_flag_added_to_the_parser_is_rendered(monkeypatch):
    """No list of flags of its own: a flag the tree gains is on the page."""
    import dstf.cli

    real = dstf.cli.build_arg_parser

    def with_a_fake_flag():
        parser = real()
        parser.nodes["run"].add_argument("--fake-flag", help="Added by this test.")
        return parser

    monkeypatch.setattr(dstf.cli, "build_arg_parser", with_a_fake_flag)
    run = next(c for c in rows.command_nodes() if c.path == "run")
    assert any("--fake-flag" in o.names for o in run.options)


# -- 2. by the pages ---------------------------------------------------------


def test_every_table_is_named_by_a_directive():
    found = _directives()
    named = {arg for (kind, arg) in found if kind == "dstf-keys"}
    assert named == set(rows.tables()), sorted(set(rows.tables()) ^ named)
    for kind in ("dstf-backend-settings", "dstf-modes", "dstf-combinators", "dstf-cli"):
        assert (kind, "") in found, kind
    for arg in rows.SCHEMA_FILES:
        assert ("dstf-schema-url", arg) in found, arg


def test_every_reference_page_is_in_the_table_of_contents():
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    for page in PAGES:
        assert f"reference/{page.stem}" in index, page.name


# -- 3. the prose types no key and no flag ------------------------------------


def _names() -> set[str]:
    """Every key, setting, field and flag name the reference pages show."""
    names: set[str] = set()
    for table in [*rows.tables().values(), *rows.backend_tables().values()]:
        names.update(table)
    for section in rows.mode_sections():
        names.update(r.name for r in section.fields)
        for columns in section.columns.values():
            names.update(r.name for r in columns)
    for node in build_arg_parser().nodes.values():
        for action in node._actions:
            names.update(action.option_strings)
    return names


def _prose(page: Path) -> str:
    """A page without its directive blocks: the hand-written text."""
    text = page.read_text(encoding="utf-8")
    return re.sub(r"^```\{.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_prose_types_no_key_or_flag(page):
    names = _names()
    written = set(re.findall(r"`([^`]+)`", _prose(page)))
    written |= set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", _prose(page)))
    # A file name (test_spec.json) is not its key.
    written |= set(re.findall(r"\b([a-z]+_[a-z_]+)\b(?![.\w])", _prose(page)))
    assert not written & names, sorted(written & names)
