"""`baseline diff`'s words (``reporting.baseline_diff_text``): the Markdown a
reviewer pastes into a pull request, and the same facts as strict JSON."""

import json
import math
from pathlib import Path

import pytest

from dstf.reporting.baseline_diff_text import LIMITS, as_json, as_markdown, percent
from dstf.reporting.stats_rows import statistics_rows
from dstf.storage.baseline_diff import (
    BaselineDiff,
    Diff,
    SeriesChange,
    SettingChange,
    TestDiff,
    VariableDiff,
)
from dstf.storage.git_revisions import Commit

OLD = Commit("a" * 40, "aaaaaaa", "the old one")
NEW = Commit("b" * 40, "bbbbbbb", "the new one")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (2.3e-4, "2.3e-04 %"),
        (1.7654e-6, "1.8e-06 %"),
        (0.0088, "0.0088 %"),
        (0.19045, "0.19 %"),
        (1.75051, "1.8 %"),
        (9.94, "9.9 %"),
        (23.4, "23 %"),
        (157.2, "157 %"),
    ],
)
def test_a_percent_has_two_digits_under_10_and_is_whole_above(value, text):
    assert percent(value) == text


def _change(largest, at=1.0, scale="range", **more):
    return SeriesChange(largest=largest, scale=scale, at=at, compared=3, **more)


def _var(name, largest, *, label=None, status="changed", **more):
    change = _change(largest, **more) if status == "changed" else None
    return VariableDiff(name, label or name, status, change)


def _diff(
    tests, *, folders=("Dymola/linux", "OpenModelica/linux"), new=NEW, against=None
):
    return Diff(
        root=Path("refs"),
        old=OLD,
        new=new,
        against=against,
        folders=list(folders),
        tests=tests,
        unchanged=7,
        metadata_only=2,
    )


def _changed(folder, *variables, settings=(), effort=(), steps=None):
    return BaselineDiff(
        folder,
        "changed",
        "ref_a.json",
        "ref_a.json",
        variables=list(variables),
        settings=list(settings),
        effort=list(effort),
        steps=steps,
    )


def _overview(text):
    """The overview table's rows, as lists of cells."""
    lines = text.split("\n\n<details>", 1)[0].splitlines()
    rows = [line for line in lines if line.startswith("| ")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows]


def test_the_first_line_counts_tests_and_each_folder_s_baselines():
    diff = _diff(
        [
            TestDiff(
                "Lib.A", {"Dymola/linux": _changed("Dymola/linux", _var("x", 1.0))}
            ),
            TestDiff(
                "Lib.B",
                {
                    "Dymola/linux": BaselineDiff("Dymola/linux", "new"),
                    "OpenModelica/linux": BaselineDiff("OpenModelica/linux", "new"),
                },
            ),
            TestDiff(
                "Lib.C",
                {"OpenModelica/linux": BaselineDiff("OpenModelica/linux", "removed")},
            ),
        ]
    )
    first = as_markdown(diff).splitlines()[0]
    assert first == (
        "**Baselines changed aaaaaaa..bbbbbbb**: 1 test changed, 1 new, 1 removed "
        "(Dymola/linux: 1 changed, 1 new; OpenModelica/linux: 1 new, 1 removed). "
        "7 baselines unchanged, 2 of them in metadata only."
    )


def test_against_the_files_on_disk_the_line_says_so():
    diff = _diff([], new=None)
    assert as_markdown(diff).startswith(
        "**Baselines changed aaaaaaa..files on disk**: "
    )


def test_the_revisions_subjects_are_named():
    text = as_markdown(_diff([]))
    assert "`aaaaaaa` the old one → `bbbbbbb` the new one" in text


def test_nothing_changed_says_so_and_prints_no_table():
    text = as_markdown(_diff([]))
    assert "No baseline changed." in text
    assert "| Test |" not in text


def test_the_overview_has_a_row_per_test_sorted_by_its_largest_change():
    diff = _diff(
        [
            TestDiff(
                "Lib.Small", {"Dymola/linux": _changed("Dymola/linux", _var("x", 0.5))}
            ),
            TestDiff("Lib.New", {"Dymola/linux": BaselineDiff("Dymola/linux", "new")}),
            TestDiff(
                "Lib.pkg.Big",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 30.0), _var("y", 2.0)
                    ),
                    "OpenModelica/linux": _changed(
                        "OpenModelica/linux",
                        _var("x", 0.01),
                        _var("z", None, status="new"),
                        settings=[
                            SettingChange("simulation", "method", "Dassl", "dassl")
                        ],
                    ),
                },
            ),
            TestDiff(
                "Lib.Mid",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 3.0))},
                unchanged=["OpenModelica/linux"],
            ),
        ]
    )
    assert _overview(as_markdown(diff)) == [
        ["Test", "Dymola/linux", "OpenModelica/linux"],
        ["---", "---", "---"],
        ["`Big`", "30 %", "0.01 % +1 new · settings"],
        ["`Mid`", "3 %", "unchanged"],
        ["`Small`", "0.5 %", ""],
        ["`New`", "new", ""],
    ]


def test_a_change_of_a_constant_says_of_value_and_one_from_zero_absolute():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 1.8e-6, scale="value")
                    )
                },
            ),
            TestDiff(
                "Lib.B",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 0.004, scale="absolute")
                    )
                },
            ),
        ],
        folders=["Dymola/linux"],
    )
    rows = _overview(as_markdown(diff))
    assert rows[2:] == [["`A`", "1.8e-06 % of value"], ["`B`", "0.004 absolute"]]


def test_a_non_finite_difference_sorts_first_and_is_said():
    diff = _diff(
        [
            TestDiff(
                "Lib.Big", {"Dymola/linux": _changed("Dymola/linux", _var("x", 90.0))}
            ),
            TestDiff(
                "Lib.NaN",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 0.0, non_finite=2, non_finite_at=1.5)
                    )
                },
            ),
        ],
        folders=["Dymola/linux"],
    )
    text = as_markdown(diff)
    assert _overview(text)[2:] == [["`NaN`", "0 · not finite"], ["`Big`", "90 %"]]
    assert "| `x` | 0; 2 points not finite from 1.5 s |" in text


def test_against_adds_a_column_with_the_largest_and_where():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 1.0))},
                against={
                    "Dymola/windows": [_var("x", 0.001)],
                    "OpenModelica/linux": [
                        _var("x", 0.67, at=99.8),
                        _var("w", None, status="new"),
                    ],
                },
            )
        ],
        folders=["Dymola/linux", "Dymola/windows", "OpenModelica/linux"],
        against="Dymola/linux",
    )
    text = as_markdown(diff)
    assert _overview(text)[0][-1] == "vs Dymola/linux"
    assert _overview(text)[2][-1] == "0.67 % OpenModelica/linux"
    assert "| `x` | 1 % (1 s) | 0.001 % (1 s) | 0.67 % (99.8 s) |" in text
    assert "| `w` |  |  | not in Dymola/linux |" in text


def test_the_steps_column_names_its_folder_and_reads_a_dash_where_none():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 5.0), steps=(200.0, 150.0)
                    )
                },
            ),
            TestDiff(
                "Lib.B",
                {"OpenModelica/linux": _changed("OpenModelica/linux", _var("x", 1.0))},
            ),
        ]
    )
    rows = _overview(as_markdown(diff))
    assert rows[0][-1] == "steps, Dymola/linux"
    assert [r[-1] for r in rows[2:]] == ["-25 %", "–"]


def test_no_steps_anywhere_has_no_steps_column():
    diff = _diff(
        [TestDiff("Lib.A", {"Dymola/linux": _changed("Dymola/linux", _var("x", 5.0))})]
    )
    assert _overview(as_markdown(diff))[0] == [
        "Test",
        "Dymola/linux",
        "OpenModelica/linux",
    ]


def test_the_detail_lists_each_variable_settings_and_solver_effort():
    rows = statistics_rows(
        {
            "simulation": {"accepted_steps": 150, "states": 4},
            "timing": {"sim_wall": 7.0},
        },
        {
            "simulation": {"accepted_steps": 200, "states": 4},
            "timing": {"sim_wall": 10.0},
        },
        listing=[],
    )
    diff = _diff(
        [
            TestDiff(
                "Lib.pkg.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux",
                        _var("unitTests.x[1]", 0.19, label="pipe.T", at=60.0),
                        _var("gone", None, status="removed"),
                        settings=[
                            SettingChange("simulation", "method", "Dassl", "dassl")
                        ],
                        effort=rows,
                    ),
                    "OpenModelica/linux": BaselineDiff(
                        "OpenModelica/linux", "new", new_file="ref_b.json"
                    ),
                },
            )
        ]
    )
    text = as_markdown(diff)
    detail = text.split("<details>", 1)[1]
    assert detail.startswith(
        "\n<summary><code>A</code> — 0.19 %</summary>\n\n`Lib.pkg.A`\n"
    )
    assert "| Variable | Dymola/linux |" in detail
    assert "| `pipe.T` | 0.19 % (60 s) |" in detail
    assert "| `gone` | removed |" in detail
    assert (
        "- **Dymola/linux** `ref_a.json`: settings: simulation method Dassl → dassl"
        in detail
    )
    assert "  - Solver effort, old → new: Accepted steps 200 → 150" in detail
    assert "  - Unchanged: States 4" in detail
    assert "  - Machine-dependent: Timing sim wall 10 s → 7 s" in detail
    assert "- **OpenModelica/linux** `ref_b.json`: new baseline" in detail


def test_a_baseline_that_cannot_be_compared_says_why():
    diff = _diff(
        [
            TestDiff(
                "Lib.P",
                {
                    "Dymola/linux": BaselineDiff(
                        "Dymola/linux",
                        "two baselines",
                        note="two baselines at aaaaaaa: ref_1.json, ref_2.json",
                    ),
                    "OpenModelica/linux": BaselineDiff(
                        "OpenModelica/linux",
                        "unreadable",
                        "ref_3.json",
                        "ref_3.json",
                        note="cannot be read at aaaaaaa (variable 1 has no name)",
                    ),
                },
            )
        ]
    )
    text = as_markdown(diff)
    assert _overview(text)[2] == ["`P`", "two baselines", "cannot be read"]
    assert (
        "- **Dymola/linux**: two baselines at aaaaaaa: ref_1.json, ref_2.json" in text
    )
    assert (
        "- **OpenModelica/linux** `ref_3.json`: cannot be read at aaaaaaa (variable 1 has no name)"
        in text
    )


def test_the_limits_are_printed_last():
    assert as_markdown(_diff([])).rstrip().endswith(LIMITS)
    assert "stored samples" in LIMITS


# --- JSON -----------------------------------------------------------------------


def test_json_holds_the_facts_and_is_strict():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux",
                        _var("x", math.inf, non_finite=1, non_finite_at=2.0),
                        settings=[
                            SettingChange("simulation", "tolerance", math.nan, 1e-6)
                        ],
                        steps=(200.0, 150.0),
                    )
                },
            )
        ]
    )
    text = as_json(diff)
    assert "NaN" not in text and "Infinity" not in text
    data = json.loads(text)
    assert data["old"] == {
        "sha": "a" * 40,
        "short": "aaaaaaa",
        "subject": "the old one",
    }
    assert data["new"]["short"] == "bbbbbbb"
    assert (data["unchanged"], data["metadata_only"]) == (7, 2)
    assert data["folders"] == ["Dymola/linux", "OpenModelica/linux"]
    base = data["tests"][0]["baselines"]["Dymola/linux"]
    change = base["variables"][0]["change"]
    assert (change["largest"], change["largest_non_finite"]) == (None, "inf")
    assert (change["non_finite"], change["non_finite_at"]) == (1, 2.0)
    setting = base["settings"][0]
    assert (setting["old"], setting["old_non_finite"], setting["new"]) == (
        None,
        "nan",
        1e-6,
    )
    assert base["steps"] == {"old": 200.0, "new": 150.0}
    assert data["limits"] == LIMITS


def test_json_of_the_files_on_disk_has_no_new_revision():
    assert json.loads(as_json(_diff([], new=None)))["new"] is None


def test_unchanged_translation_statistics_are_counted_not_listed():
    """A translation log holds dozens of statistics; listed unchanged, they
    took the Markdown past what a pull-request comment holds (65,536
    characters) over 33 real baselines. A changed one is listed."""
    stats = {
        "translation": {"constants": 5, "alias_variables": 9, "free_parameters": 3},
        "simulation": {"accepted_steps": 10, "states": 4},
    }
    newer = {
        "translation": {"constants": 6, "alias_variables": 9, "free_parameters": 3},
        "simulation": {"accepted_steps": 10, "states": 4},
    }
    rows = statistics_rows(newer, stats, listing=[])
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 1.0), effort=rows)},
            )
        ]
    )
    text = as_markdown(diff)
    assert "  - Solver effort, old → new: Translation constants 5 → 6" in text
    assert (
        "  - Unchanged: Accepted steps 10; States 4; 2 translation statistics" in text
    )


def test_a_change_of_nothing_reads_0_with_no_time_or_scale():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux",
                        _var("x", 0.0, scale="value", at=0.0),
                        _var("y", 1.0),
                    )
                },
            )
        ],
        folders=["Dymola/linux"],
    )
    assert "| `x` | 0 |" in as_markdown(diff)


def test_a_test_that_could_not_be_compared_is_not_counted_as_changed():
    """The rename commit's one row (two baselines at the older side) is no
    changed test."""
    diff = _diff(
        [
            TestDiff(
                "Lib.P",
                {
                    "Dymola/linux": BaselineDiff(
                        "Dymola/linux", "two baselines", note="two"
                    )
                },
                unchanged=["OpenModelica/linux"],
            )
        ]
    )
    first = as_markdown(diff).splitlines()[0]
    assert first.startswith(
        "**Baselines changed aaaaaaa..bbbbbbb**: 0 tests changed, 0 new, 0 removed, "
        "1 not compared (Dymola/linux: 1 two baselines). "
    )
