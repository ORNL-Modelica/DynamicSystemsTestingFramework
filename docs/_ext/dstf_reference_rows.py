"""The reference pages' content as plain rows, from the code's own tables.

What :mod:`dstf_reference` renders, without docutils: these functions read
the command tree, the key tables, the registered backends, modes and
combinators — and no file — and start nothing, so a test calls them as the
build does. Each walks its table itself: a key, a flag or a field added to
the code is on its page by construction.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# The tables, by the name a page gives them
# ---------------------------------------------------------------------------


def tables() -> dict[str, Mapping[str, Any]]:
    """Every key table a ``dstf-keys`` directive may render, by its name."""
    from dstf.config_shape import RECOGNIZER, TESTING_JSON
    from dstf.discovery import spec_shape

    return {
        "testing": TESTING_JSON,
        "recognizer": RECOGNIZER,
        "spec-root": spec_shape.ROOT,
        "spec-entry": spec_shape.ENTRY,
        "spec-simulation": spec_shape.SIMULATION,
        "spec-comparison": spec_shape.COMPARISON,
        "spec-override": spec_shape.OVERRIDE,
        "spec-every-criterion": spec_shape.EVERY_METRIC,
        "spec-plot": spec_shape.PLOT,
        "spec-leaf": spec_shape.LEAF,
        "spec-window": spec_shape.WINDOW,
        "spec-combinator": spec_shape.COMBINATOR,
    }


def backend_tables() -> dict[str, Mapping[str, Any]]:
    """Each registered backend's settings table, by the backend's name."""
    from dstf.backends import backend_class, backend_names

    out: dict[str, Mapping[str, Any]] = {}
    for name in backend_names():
        cls = backend_class(name)
        if cls is not None:
            out[name] = cls.settings
    return out


#: What the page calls a ``$ref`` of the published schema.
_REFS = {
    "#/$defs/tree_node": "criteria tree",
    "#/$defs/test_entry": "test entry",
    "#/$defs/override": "override",
}


def type_text(schema: Mapping[str, Any]) -> str:
    """A key's type in words, from its JSON-Schema fragment."""
    if "$ref" in schema:
        return _REFS.get(schema["$ref"], "object")
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(choice) for choice in schema["enum"])
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return " or ".join(type_text(alt) for alt in schema[key])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " or ".join(type_text({**schema, "type": k}) for k in kind)
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, Mapping) and items:
            inner = type_text(items)
            return f"list of {inner}"
        return "list"
    if kind == "integer":
        minimum = schema.get("minimum")
        return f"integer ≥ {minimum}" if minimum is not None else "integer"
    if kind == "number":
        if "exclusiveMinimum" in schema:
            return f"number > {schema['exclusiveMinimum']}"
        return "number"
    if isinstance(kind, str):
        return kind
    return "any"


def default_text(default: Any) -> str:
    """A key's default as the page prints it: JSON, or a dash for none."""
    return "—" if default is None else json.dumps(default)


@dataclass(frozen=True)
class Row:
    """One row of a key table."""

    name: str
    type: str
    default: str
    description: str


def key_rows(table: Mapping[str, Any]) -> list[Row]:
    """A key table's rows, in its own order: every key, nothing else."""
    return [
        Row(key, type_text(s.schema), default_text(s.default), s.description)
        for key, s in table.items()
    ]


# ---------------------------------------------------------------------------
# Comparison modes and combinators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeSection:
    """One comparison mode as its page section shows it."""

    name: str
    description: str
    fields: list[Row]
    #: ``{field: rows of its columns}`` for a field that is a table of rows.
    columns: dict[str, list[Row]]


def _field_type(f: Any, row_tables: Collection[str]) -> str:
    """A mode field's type in words. ``row_tables`` are the fields that are
    tables of rows (they declare ``columns``)."""
    kind = {"float": "number", "int": "integer", "bool": "boolean", "str": "string"}
    if f.type == "enum":
        text = " | ".join(json.dumps(c) for c in f.choices or [])
    elif f.type == "sides":
        text = "number or {lower, upper}"
    elif f.type == "passthrough":
        text = "list of rows" if f.name in row_tables else "any"
    else:
        text = kind.get(f.type, f.type)
    return f"{text} or null" if f.optional else text


def _column_type(kind: Any) -> str:
    if isinstance(kind, tuple):
        return " | ".join(json.dumps(c) for c in kind)
    optional = kind.endswith("?")
    base = {
        "float": "number",
        "sides": "number or {lower, upper}",
    }.get(kind.rstrip("?"), kind.rstrip("?"))
    return f"{base} or null" if optional else base


def mode_sections() -> list[ModeSection]:
    """Every registered comparison mode, in registration order: its
    description, its fields as rows, and the columns of each field that is
    a table of rows."""
    from dstf.comparison.mode_schema import column_help, derive_schema
    from dstf.comparison.registry import metrics

    out: list[ModeSection] = []
    for desc in metrics():
        if not dataclasses.is_dataclass(desc.config_cls):
            out.append(ModeSection(desc.name, desc.description, [], {}))
            continue
        meta = {f.name: f.metadata for f in dataclasses.fields(desc.config_cls)}
        row_tables = {name for name, m in meta.items() if m.get("columns")}
        fields = [
            Row(
                f.name,
                _field_type(f, row_tables),
                default_text(f.default),
                f.help or "",
            )
            for f in derive_schema(desc.config_cls, mode=desc.name).fields
        ]
        helps = column_help(desc.config_cls)
        columns = {
            name: [
                Row(
                    column[0],
                    _column_type(column[1]),
                    "—",
                    helps.get(name, {}).get(column[0], ""),
                )
                for column in m["columns"]
            ]
            for name, m in meta.items()
            if m.get("columns")
        }
        out.append(ModeSection(desc.name, desc.description, fields, columns))
    return out


def combinator_rows() -> list[tuple[str, str]]:
    """Every registered kind of group: its name and its description."""
    from dstf.comparison.registry import combinators

    return [(c.name, c.description) for c in combinators()]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    """One flag or positional of a command, as its table shows it."""

    names: str
    help: str
    default: str


@dataclass(frozen=True)
class Command:
    """One node of the command tree: the top level, a group or a command."""

    path: str
    summary: str
    description: str
    usage: str
    options: list[Option]
    examples: str
    group: bool


def _option(action: Any) -> Option | None:
    import argparse

    if isinstance(action, argparse._SubParsersAction):
        return None
    if action.help == argparse.SUPPRESS:
        return None
    if action.option_strings:
        names = ", ".join(action.option_strings)
        if action.nargs != 0:
            names += f" {action.metavar or action.dest.upper()}"
    else:
        names = action.metavar or action.dest
    if action.choices is not None and not action.option_strings:
        names = f"{names} {{{','.join(map(str, action.choices))}}}"
    default = action.default
    shown = (
        "—"
        if default in (None, argparse.SUPPRESS, False) or action.nargs == 0
        else json.dumps(default)
        if not isinstance(default, str)
        else default
    )
    text = " ".join((action.help or "").split())
    if action.choices is not None and action.option_strings:
        text = f"One of {', '.join(map(str, action.choices))}. {text}"
    return Option(names, text, shown)


def command_nodes() -> list[Command]:
    """Every node of the command tree — the top level first, then each
    group and command in the order ``dstf --help`` lists them — with its
    description, usage, options and examples as ``--help`` prints them."""
    from dstf.cli import build_arg_parser

    root = build_arg_parser()
    out: list[Command] = []
    for path, node in root.nodes.items():
        out.append(
            Command(
                path=path,
                summary=root.summaries.get(path, ""),
                description=node.description or "",
                usage=node.format_usage().strip(),
                options=[o for a in node._actions if (o := _option(a)) is not None],
                examples=(node.epilog or "").strip(),
                group=getattr(node, "is_group", False),
            )
        )
    return out


#: Where the built site holds each file's JSON schema, under its root.
SCHEMA_FILES = {
    "testing": "schemas/testing.schema.json",
    "test-spec": "schemas/test_spec.schema.json",
}


def schema_documents() -> dict[str, Callable[[], dict]]:
    """Each published schema's builder, by its file in the site."""
    from dstf.reporting.schema_export import build_config_schema, build_schema

    return {
        SCHEMA_FILES["testing"]: build_config_schema,
        SCHEMA_FILES["test-spec"]: build_schema,
    }
