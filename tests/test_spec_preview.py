"""The pending drawer's preview: a test's entry before and after its staged
edits, as `json.dumps(indent=2)` writes it (spec_preview)."""

from __future__ import annotations

import copy
import json

from dstf.authoring.spec_preview import Line, entry_lines, spans
from dstf.backends.types import VariableText

M = "TRANSFORM.HeatExchangers.Examples.OilWater_CTHX"


def _entry() -> dict:
    """A three-level tree (entry → metrics → children), TRANSFORM-shaped;
    its LAST leaf carries a tolerance as its last key."""
    return {
        "model": M,
        "comparison": {"tolerance": 0.05},
        "metrics": {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "unitTests.x[2]"},
                {"metric": "nrmse", "variable": "unitTests.x[1]", "tolerance": 0.01},
            ],
        },
    }


def _kinds(lines):
    return [(line.kind, line.text.strip()) for line in lines]


def test_a_tolerance_change_is_one_del_and_one_add_with_context():
    before = _entry()
    after = copy.deepcopy(before)
    after["metrics"]["children"][1]["tolerance"] = 0.005
    lines = entry_lines(before, after)
    changed = [(k, t) for k, t in _kinds(lines) if k in ("del", "add")]
    assert changed == [("del", '"tolerance": 0.01'), ("add", '"tolerance": 0.005')]
    i = [line.kind for line in lines].index("del")
    assert lines[i + 1].kind == "add"
    assert [line.kind for line in lines[:i]] == ["same"] * len(lines[:i])
    assert [line.kind for line in lines[i + 2 :]] == ["same"] * len(lines[i + 2 :])
    assert 1 <= len(lines[:i]) <= 3 and 1 <= len(lines[i + 2 :]) <= 3
    # The text is the dumped line itself, indentation kept.
    assert lines[i].text == '        "tolerance": 0.01'


def test_appending_a_child_after_the_last_one_reads_as_difflib_aligns_it():
    """The board drew `- }` / `+ },`; difflib aligns the old leaf's `}` with
    the NEW leaf's closing `}` (same text, same indent), so the changed
    leaf's close reads `+ },`, the new block follows as added lines, and
    the old `}` stays a context line after them — the shape `git diff`
    prints too."""
    before = _entry()
    after = copy.deepcopy(before)
    after["metrics"]["children"][1]["tolerance"] = 0.005
    after["metrics"]["children"].append(
        {"metric": "range", "variable": "unitTests.x[2]", "min_value": 0.0}
    )
    lines = entry_lines(before, after)
    changed = [(k, t) for k, t in _kinds(lines) if k in ("del", "add")]
    assert changed == [
        ("del", '"tolerance": 0.01'),
        ("add", '"tolerance": 0.005'),
        ("add", "},"),
        ("add", "{"),
        ("add", '"metric": "range",'),
        ("add", '"variable": "unitTests.x[2]",'),
        ("add", '"min_value": 0.0'),
    ]
    last_add = max(i for i, line in enumerate(lines) if line.kind == "add")
    assert lines[last_add + 1] == Line("same", "      }")


def test_two_far_apart_changes_are_two_groups_with_a_gap():
    before = _entry()
    before["metrics"]["children"] += [
        {"metric": "nrmse", "variable": f"unitTests.x[{i}]"} for i in range(3, 12)
    ]
    after = copy.deepcopy(before)
    after["comparison"]["tolerance"] = 0.04
    after["metrics"]["children"][-1]["tolerance"] = 0.2
    lines = entry_lines(before, after)
    assert [line for line in lines if line.kind == "gap"] == [Line("gap", "…")]
    gap = lines.index(Line("gap", "…"))
    # Between the two groups: each group's changes on its own side.
    assert {line.kind for line in lines[:gap]} >= {"del", "add"}
    assert {line.kind for line in lines[gap + 1 :]} >= {"add"}


def test_a_new_entry_is_all_added_and_equal_entries_are_nothing():
    lines = entry_lines(None, _entry())
    assert lines and {line.kind for line in lines} == {"add"}
    assert [line.text for line in lines] == json.dumps(_entry(), indent=2).splitlines()
    assert entry_lines(_entry(), _entry()) == []
    assert {line.kind for line in entry_lines(_entry(), None)} == {"del"}


def test_autojunk_off_keeps_a_long_repetitive_entry_minimal():
    """difflib's autojunk drops every line seen in more than 1% of a
    >200-line sequence from its match seeds: in a tree whose criteria
    repeat their variables every line is such a line, and two tightened
    tolerances 40 leaves apart read as ~200 replaced lines with it on."""
    children = [
        {
            "metric": ("nrmse", "range", "tube")[i % 3],
            "variable": f"unitTests.x[{1 + i % 4}]",
            "tolerance": 0.01,
        }
        for i in range(60)
    ]
    before = {"model": M, "metrics": {"combinator": "and", "children": children}}
    assert len(json.dumps(before, indent=2).splitlines()) > 300
    after = copy.deepcopy(before)
    after["metrics"]["children"][10]["tolerance"] = 0.005
    after["metrics"]["children"][50]["tolerance"] = 0.002
    lines = entry_lines(before, after)
    changed = [(k, t) for k, t in _kinds(lines) if k in ("del", "add")]
    assert changed == [
        ("del", '"tolerance": 0.01'),
        ("add", '"tolerance": 0.005'),
        ("del", '"tolerance": 0.01'),
        ("add", '"tolerance": 0.002'),
    ]


TEXTS = {
    "unitTests.x[1]": VariableText(label="pulse.y", text="pulse.y"),
    "x": VariableText(label="x", text="x"),
    "'a.b'.c": VariableText(label="der('a.b'.c)", text="der('a.b'.c)"),
    'q"uote': VariableText(label="quoted", text="quoted"),
}


def test_a_variable_reads_as_its_label_with_the_name_muted():
    assert spans('"variable": "unitTests.x[1]",', TEXTS) == [
        ('"variable": ', False),
        ("pulse.y", False),
        (" unitTests.x[1]", True),
        (",", False),
    ]


def test_spans_on_quoted_modelica_names_and_escaped_quotes():
    assert spans(json.dumps({"variable": "'a.b'.c"})[1:-1], TEXTS) == [
        ('"variable": ', False),
        ("der('a.b'.c)", False),
        (" 'a.b'.c", True),
    ]
    line = json.dumps({"variable": 'q"uote'})[1:-1]  # "variable": "q\"uote"
    assert line == '"variable": "q\\"uote"'
    assert spans(line, TEXTS) == [
        ('"variable": ', False),
        ("quoted", False),
        (' q"uote', True),
    ]


def test_a_line_with_no_variable_is_given_back_whole():
    for line in (
        '"variable": "x",',  # label == name: not rewritten
        '"tolerance": 0.005',
        '"note": "a \\"quoted\\" [1] thing",',
        "},",
        "",
    ):
        assert "".join(text for text, _ in spans(line, TEXTS)) == line
        assert not any(muted for _, muted in spans(line, TEXTS))
