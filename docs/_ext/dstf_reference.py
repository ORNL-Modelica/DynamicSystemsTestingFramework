"""The reference pages, rendered from the code.

Every flag, key, default, comparison mode and backend setting on the
reference pages comes from the table the code itself reads it by: the
command tree (``dstf.cli.build_arg_parser``), ``testing.json``'s keys and a
recognizer entry's (``dstf.config_shape``), each registered backend's
``settings``, the ``test_spec.json`` tables (``dstf.discovery.spec_shape``)
and the registered modes and combinators. Nothing on those pages restates
one by hand, so a key added to a table is on its page by construction.

Two layers. :mod:`dstf_reference_rows` turns the code's tables into plain
rows (the tests call it); the directives here turn those rows into docutils
nodes:

- ``dstf-keys <table>``: one table of keys — name, type, default,
  description;
- ``dstf-backend-settings``: one such table per registered backend;
- ``dstf-modes``: each comparison mode, its description and its fields;
- ``dstf-combinators``: each kind of group, and its description;
- ``dstf-cli``: every command of the command line;
- ``dstf-schema-url <file>``: where the site publishes a file's schema.

The two JSON schemas are written into the built site at ``schemas/`` when
the build ends.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from docutils import nodes
from docutils.parsers.rst import Directive
from dstf_reference_rows import (
    SCHEMA_FILES,
    Row,
    backend_tables,
    combinator_rows,
    command_nodes,
    key_rows,
    mode_sections,
    schema_documents,
    tables,
)

# ---------------------------------------------------------------------------
# Text to nodes
# ---------------------------------------------------------------------------

_CODE = re.compile(r"`([^`]+)`")


def code(text: str) -> nodes.literal:
    """``text`` as inline code. A short one is not broken across lines —
    a browser breaks ``--help`` after its first hyphen."""
    return nodes.literal(text, text, classes=["dstf-nowrap"] if len(text) <= 32 else [])


def inline(text: str) -> list[nodes.Node]:
    """``text`` as inline nodes: a span in single backticks is code, the
    rest plain text — never parsed as markup."""
    out: list[nodes.Node] = []
    pos = 0
    for match in _CODE.finditer(text):
        if match.start() > pos:
            out.append(nodes.Text(text[pos : match.start()]))
        out.append(code(match.group(1)))
        pos = match.end()
    if pos < len(text):
        out.append(nodes.Text(text[pos:]))
    return out


def paragraphs(text: str) -> list[nodes.Node]:
    """``text``'s blank-line-separated paragraphs, each a paragraph node."""
    return [
        nodes.paragraph("", "", *inline(" ".join(p.split())))
        for p in text.strip().split("\n\n")
        if p.strip()
    ]


#: How a column's cells are shown, by the column's header: as code, and
#: with which class — on a narrow screen a table's cells stack, and a
#: classed cell is led by its column's name (``_static/dstf.css``).
_COLUMNS = {
    "Key": ("code", "dstf-name"),
    "Option": ("code", "dstf-name"),
    "Column": ("code", "dstf-name"),
    "Combinator": ("code", "dstf-name"),
    "Type": ("text", "dstf-type"),
    "Default": ("code", "dstf-default"),
}


def table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> nodes.table:
    """A table node, its cells shown by their column (:data:`_COLUMNS`;
    any other column is text)."""
    tbl = nodes.table(classes=["dstf-reference"])
    group = nodes.tgroup(cols=len(header))
    tbl += group
    for _ in header:
        group += nodes.colspec(colwidth=1)
    head = nodes.thead()
    group += head
    row = nodes.row()
    for title in header:
        row += nodes.entry("", nodes.paragraph("", title))
    head += row
    body = nodes.tbody()
    group += body
    for cells in rows:
        row = nodes.row()
        for title, cell in zip(header, cells):
            shown, cls = _COLUMNS.get(title, ("text", ""))
            if shown == "code" and cell != "—":
                content = nodes.paragraph("", "", code(cell))
            else:
                content = nodes.paragraph("", "", *inline(cell))
            classes = [cls] if cls else []
            if cell == "—":
                classes.append("dstf-empty")
            row += nodes.entry("", content, classes=classes)
        body += row
    return tbl


def key_table(table_rows: list[Row]) -> nodes.table:
    return table(
        ("Key", "Type", "Default", "Description"),
        [(r.name, r.type, r.default, r.description) for r in table_rows],
    )


def _section(
    directive: Directive, title: str, anchor: str, *children: nodes.Node
) -> nodes.section:
    section = nodes.section(ids=[nodes.make_id(anchor)])
    section += nodes.title(title, "", code(title))
    for child in children:
        section += child
    directive.state.document.note_implicit_target(section)
    return section


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------


class KeysDirective(Directive):
    """``dstf-keys <table>``: one key table."""

    required_arguments = 1

    def run(self) -> list[nodes.Node]:
        name = self.arguments[0]
        known = tables()
        if name not in known:
            raise self.error(f"no key table {name!r}; there are {sorted(known)}")
        return [key_table(key_rows(known[name]))]


class BackendSettingsDirective(Directive):
    """``dstf-backend-settings``: one key table per registered backend."""

    def run(self) -> list[nodes.Node]:
        out: list[nodes.Node] = []
        for name, settings in backend_tables().items():
            body: list[nodes.Node] = (
                [key_table(key_rows(settings))]
                if settings
                else [nodes.paragraph("", "This backend takes no settings.")]
            )
            out.append(_section(self, name, f"backend-{name}", *body))
        return out


class ModesDirective(Directive):
    """``dstf-modes``: every comparison mode."""

    def run(self) -> list[nodes.Node]:
        out: list[nodes.Node] = []
        for mode in mode_sections():
            body: list[nodes.Node] = [*paragraphs(mode.description)]
            if mode.fields:
                body.append(key_table(mode.fields))
            for name, rows in mode.columns.items():
                body.append(
                    nodes.paragraph(
                        "",
                        "",
                        nodes.Text("A row of "),
                        nodes.literal(name, name),
                        nodes.Text(":"),
                    )
                )
                body.append(
                    table(
                        ("Column", "Type", "Description"),
                        [(r.name, r.type, r.description) for r in rows],
                    )
                )
            out.append(_section(self, mode.name, f"mode-{mode.name}", *body))
        return out


class CombinatorsDirective(Directive):
    """``dstf-combinators``: every kind of group, with its description."""

    def run(self) -> list[nodes.Node]:
        return [table(("Combinator", "When it passes"), list(combinator_rows()))]


class CliDirective(Directive):
    """``dstf-cli``: every node of the command tree, a section each."""

    def run(self) -> list[nodes.Node]:
        out: list[nodes.Node] = []
        for command in command_nodes():
            title = f"dstf {command.path}".strip()
            body: list[nodes.Node] = [*paragraphs(command.description)]
            body.append(nodes.literal_block(command.usage, command.usage))
            if command.options:
                body.append(
                    table(
                        ("Option", "Description", "Default"),
                        [(o.names, o.help, o.default) for o in command.options],
                    )
                )
            if command.examples:
                body.append(nodes.literal_block(command.examples, command.examples))
            anchor = "cli-" + (command.path.replace(" ", "-") or "dstf")
            out.append(_section(self, title, anchor, *body))
        return out


class SchemaUrlDirective(Directive):
    """``dstf-schema-url <file>``: the URL of a file's published schema —
    absolute where the build knows the site's address (Read the Docs says
    it), else the path under the site's root."""

    required_arguments = 1

    def run(self) -> list[nodes.Node]:
        path = SCHEMA_FILES[self.arguments[0]]
        base = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
        env = self.state.document.settings.env
        depth = env.docname.count("/")
        href = f"{base.rstrip('/')}/{path}" if base else "../" * depth + path
        text = href if base else f"<this site>/{path}"
        return [
            nodes.paragraph(
                "",
                "",
                nodes.reference(text, "", code(text), refuri=href),
            )
        ]


def _write_schemas(app: Any, exception: Exception | None) -> None:
    if exception is not None or app.builder.format != "html":
        return
    for path, build in schema_documents().items():
        target = Path(app.outdir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")


def setup(app: Any) -> dict[str, Any]:
    app.add_directive("dstf-keys", KeysDirective)
    app.add_directive("dstf-backend-settings", BackendSettingsDirective)
    app.add_directive("dstf-modes", ModesDirective)
    app.add_directive("dstf-combinators", CombinatorsDirective)
    app.add_directive("dstf-cli", CliDirective)
    app.add_directive("dstf-schema-url", SchemaUrlDirective)
    app.connect("build-finished", _write_schemas)
    app.add_css_file("dstf.css")
    return {"parallel_read_safe": True, "parallel_write_safe": True}
