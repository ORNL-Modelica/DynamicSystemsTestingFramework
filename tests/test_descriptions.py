"""Every key, field, mode and combinator a user reads about is described in
the code, in words fit for the reference page and the published schemas.

The reference pages and both JSON schemas are generated from these
descriptions; nothing restates them by hand. So a key without one is a
blank cell on the page and a bare key in an editor, and a description that
names a decision id, holds reStructuredText's double backtick or a TODO is
text no user should read. This walks every table there is — testing.json's
keys, every registered backend's settings, a recognizer entry's keys, the
test_spec.json tables, every registered mode's fields and the columns of
their row tables, and the modes and combinators themselves — and fails on
the first description that is missing or empty, does not end in a full
stop, or holds one of those.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator

import pytest

from dstf.backends import backend_class, backend_names
from dstf.comparison.mode_schema import column_help
from dstf.comparison.modes import QUANTITY_KEYS
from dstf.comparison.registry import combinators, metrics
from dstf.comparison.strict_keys import row_fields
from dstf.config_shape import RECOGNIZER, TESTING_JSON
from dstf.discovery import spec_shape

#: The test_spec.json tables, by the name the page gives them.
SPEC_TABLES = {
    "root": spec_shape.ROOT,
    "entry": spec_shape.ENTRY,
    "simulation": spec_shape.SIMULATION,
    "comparison": spec_shape.COMPARISON,
    "override": spec_shape.OVERRIDE,
    "every leaf and override": spec_shape.EVERY_METRIC,
    "plots entry": spec_shape.PLOT,
    "leaf": spec_shape.LEAF,
    "window": spec_shape.WINDOW,
    "combinator": spec_shape.COMBINATOR,
}


def descriptions() -> Iterator[tuple[str, object]]:
    """``(where, description)`` for everything a user reads about. A
    description that is missing is yielded as ``None``."""
    for key, setting in TESTING_JSON.items():
        yield f"testing.json {key}", setting.description
    for key, setting in RECOGNIZER.items():
        yield f"recognizers[] {key}", setting.description
    for name in backend_names():
        cls = backend_class(name)
        assert cls is not None, name
        for key, setting in cls.settings.items():
            yield f"backends.{name} {key}", setting.description
    for table, keys in SPEC_TABLES.items():
        for key, setting in keys.items():
            yield f"test_spec.json {table} {key}", setting.description
    for side, node in spec_shape.SIDES[1]["properties"].items():
        yield f"tolerance pair {side}", node.get("description")
    for key, text in QUANTITY_KEYS.items():
        yield f"scalar quantity {key}", text
    for desc in metrics():
        yield f"mode {desc.name}", desc.description
        assert dataclasses.is_dataclass(desc.config_cls), desc.name
        helps = column_help(desc.config_cls)
        for f in dataclasses.fields(desc.config_cls):
            yield f"mode {desc.name} field {f.name}", f.metadata.get("help")
        for table, columns in row_fields(desc.name).items():
            for column in sorted(columns):
                yield (
                    f"mode {desc.name} {table} column {column}",
                    helps.get(table, {}).get(column),
                )
    for comb in combinators():
        yield f"combinator {comb.name}", comb.description


_DECISION_ID = re.compile(r"\bD\d+\b")


def problem(text: object) -> str | None:
    """What is wrong with one description, or None."""
    if not isinstance(text, str) or not text.strip():
        return "has no description"
    if not text.rstrip().endswith("."):
        return "does not end in a full stop"
    if _DECISION_ID.search(text):
        return "names a decision id"
    if "``" in text:
        return "holds a double backtick"
    if "TODO" in text:
        return "holds a TODO"
    if "Default:" in text:
        return "restates a default, which its row holds"
    return None


def test_every_description_is_there_and_reads_as_a_sentence():
    wrong = [
        f"{where}: {said}"
        for where, text in descriptions()
        if (said := problem(text)) is not None
    ]
    assert not wrong, "\n".join(wrong)


def test_the_walk_covers_what_it_says():
    """Vacuity guard: the walk reaches every kind of thing it claims to."""
    wheres = [where for where, _ in descriptions()]
    for prefix in (
        "testing.json ",
        "recognizers[] ",
        "backends.Dymola ",
        "test_spec.json leaf ",
        "test_spec.json combinator ",
        "test_spec.json window ",
        "mode tube field tube_points",
        "mode points points column time",
        "combinator weighted",
    ):
        assert any(w.startswith(prefix) for w in wheres), prefix
    assert sum(w.startswith("mode ") and " field " in w for w in wheres) == 20


@pytest.mark.parametrize(
    "text, said",
    [
        (None, "has no description"),
        ("  ", "has no description"),
        ("No stop", "does not end in a full stop"),
        ("See D140.", "names a decision id"),
        ("The ``key``.", "holds a double backtick"),
        ("TODO: say it.", "holds a TODO"),
        ("A key. Default: 3.", "restates a default, which its row holds"),
        ("What it is for, in a sentence.", None),
    ],
)
def test_problem_names_each_condition(text, said):
    assert problem(text) == said


def test_the_schemas_carry_the_descriptions():
    """The exported schemas are built from the same tables: every property
    an object of either describes carries a description — the nodes inside
    an if / else / anyOf clause are conditions on a key the object already
    describes, and are not documentation."""
    from dstf.reporting.schema_export import build_config_schema, build_schema

    def properties(node, path, out):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("properties", "patternProperties"):
                    out.extend((f"{path}/{name}", v) for name, v in value.items())
                properties(value, f"{path}/{key}", out)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                properties(value, f"{path}/{i}", out)
        return out

    for schema in (build_schema(), build_config_schema()):
        nodes = [
            (path, node)
            for path, node in properties(schema, "", [])
            if not re.search(r"/(if|else|anyOf)/", path)
        ]
        assert nodes
        bare = [path for path, node in nodes if not node.get("description")]
        assert not bare, bare


def test_dollar_schema_is_a_key_of_both_files():
    """A user's file may point ``$schema`` at the published schema."""
    assert "$schema" in TESTING_JSON
    assert "$schema" in spec_shape.ROOT
