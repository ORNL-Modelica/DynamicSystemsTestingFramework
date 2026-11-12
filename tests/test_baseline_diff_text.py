"""`baseline diff`'s words (``reporting.baseline_diff_text``): the Markdown a
reviewer pastes into a pull request, and the same facts as strict JSON."""

import json
import math
from pathlib import Path

import pytest

from dstf.reporting.baseline_diff_text import (
    LIMITS,
    as_json,
    as_markdown,
    detailed,
    percent,
)
from dstf.reporting.stats_rows import statistics_rows
from dstf.storage.baseline import Sampling
from dstf.storage.baseline_diff import (
    BaselineDiff,
    Diff,
    DiffError,
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
        (0.0, "0 %"),
        (2.3e-4, "2.3e-04 %"),
        (1.7654e-6, "1.8e-06 %"),
        (0.0088, "0.0088 %"),
        (0.002, "0.0020 %"),
        (0.19045, "0.19 %"),
        (1.0, "1.0 %"),
        (1.75051, "1.8 %"),
        (9.94, "9.9 %"),
        (9.96, "10 %"),
        (23.4, "23 %"),
        (157.2, "157 %"),
    ],
)
def test_a_percent_has_two_significant_digits_under_10_and_is_whole_above(value, text):
    assert percent(value) == text


def _change(largest, at=1.0, scale="range", **more):
    return SeriesChange(largest=largest, scale=scale, at=at, compared=3, **more)


def _var(name, largest, *, label=None, status="changed", **more):
    change = _change(largest, **more) if status == "changed" else None
    return VariableDiff(name, label or name, status, change)


def _diff(
    tests,
    *,
    folders=("Dymola/linux", "OpenModelica/linux"),
    new=NEW,
    against=None,
    metadata_only=2,
):
    return Diff(
        root=Path("refs"),
        old=OLD,
        new=new,
        against=against,
        folders=list(folders),
        tests=tests,
        unchanged=7,
        metadata_only=metadata_only,
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


def _md(diff):
    """The Markdown with every test's folded section."""
    return as_markdown(diff, details=[t.model_id for t in diff.tests])


def _overview(text):
    """The overview table's rows, as lists of cells."""
    lines = text.split("\n\n<details>", 1)[0].splitlines()
    rows = [line for line in lines if line.startswith("| ")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows]


def test_the_headline_counts_the_listed_tests_new_and_removed_apart():
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
        "**1 test changed, 1 new, 1 removed** (`aaaaaaa` → `bbbbbbb`). "
        "7 baseline files unchanged, 2 of them only in metadata."
    )


def test_the_headline_of_changed_tests_only_says_they_have_new_baselines():
    diff = _diff(
        [
            TestDiff("Lib.A", {"Dymola/linux": _changed("Dymola/linux", _var("x", 1))}),
            TestDiff("Lib.B", {"Dymola/linux": _changed("Dymola/linux", _var("x", 2))}),
        ],
        metadata_only=0,
    )
    assert as_markdown(diff).splitlines()[0] == (
        "**2 tests have new baselines** (`aaaaaaa` → `bbbbbbb`). 7 baseline files unchanged."
    )


def test_against_the_files_on_disk_the_headline_says_so():
    diff = _diff([], new=None)
    assert as_markdown(diff).splitlines()[0] == (
        "**No baseline changed** (`aaaaaaa` → files on disk). "
        "7 baseline files unchanged, 2 of them only in metadata."
    )


def test_the_revisions_subjects_head_the_details_and_only_them():
    diff = _two_tests()
    assert "the old one" not in as_markdown(diff)
    text = as_markdown(diff, details=["Lib.other.B"])
    said = text.index("`aaaaaaa` the old one → `bbbbbbb` the new one")
    assert text.index("Only stored points are compared.") < said
    assert said < text.index("<details>")
    on_disk = _diff(_two_tests().tests, folders=["Dymola/linux"], new=None)
    assert "`aaaaaaa` the old one → the files on disk" in as_markdown(
        on_disk, details=["Lib.other.B"]
    )


def test_nothing_changed_says_so_and_prints_no_table():
    text = as_markdown(_diff([]))
    assert text.startswith("**No baseline changed**")
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
        ["Test", "Change, Dymola/linux", "Change, OpenModelica/linux"],
        ["---", "---", "---"],
        ["`Big`", "30 %", "0.010 % (+1 variable, settings)"],
        ["`Mid`", "3.0 %", "="],
        ["`Small`", "0.50 %", ""],
        ["`New`", "new", ""],
    ]


def test_a_cell_s_marks_join_in_one_parenthesis():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux",
                        _var("x", 0.0, non_finite=1, non_finite_at=2.0),
                        _var("y", 1.0),
                        _var("a", None, status="new"),
                        _var("b", None, status="new"),
                        _var("c", None, status="removed"),
                    )
                },
            ),
            TestDiff(
                "Lib.B",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux",
                        _var("y", 0.5),
                        _var("c", None, status="removed"),
                        _var("d", None, status="removed"),
                    )
                },
            ),
        ],
        folders=["Dymola/linux"],
    )
    assert _overview(as_markdown(diff))[2:] == [
        ["`A`", "1.0 % (+2 variables, −1 variable, not finite)"],
        ["`B`", "0.50 % (−2 variables)"],
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
    text = _md(diff)
    assert _overview(text)[2:] == [["`NaN`", "0 % (not finite)"], ["`Big`", "90 %"]]
    assert "| `x` | 0 %; 2 points not finite from 1.5 s |" in text


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
    text = _md(diff)
    assert _overview(text)[0][-1] == "Gap to Dymola/linux"
    assert _overview(text)[2][-1] == "0.67 % (OpenModelica/linux)"
    assert (
        "| Variable | Change, Dymola/linux | Gap, Dymola/windows to Dymola/linux "
        "| Gap, OpenModelica/linux to Dymola/linux |" in text
    )
    assert "| `x` | 1.0 % (1 s) | 0.0010 % (1 s) | 0.67 % (99.8 s) |" in text
    assert "| `w` |  |  | not in Dymola/linux |" in text


def test_the_steps_column_reads_a_true_minus_and_a_dash_where_none():
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
    assert rows[0][-1] == "Solver steps"
    assert [r[-1] for r in rows[2:]] == ["−25 %", "–"]


def test_no_steps_anywhere_has_no_steps_column():
    diff = _diff(
        [TestDiff("Lib.A", {"Dymola/linux": _changed("Dymola/linux", _var("x", 5.0))})]
    )
    text = as_markdown(diff)
    assert _overview(text)[0] == [
        "Test",
        "Change, Dymola/linux",
        "Change, OpenModelica/linux",
    ]
    assert "Solver steps" not in text


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
    text = _md(diff)
    detail = text.split("<details>", 1)[1]
    assert detail.startswith(
        "\n<summary><code>A</code> — 0.19 %</summary>\n\n`Lib.pkg.A`\n"
    )
    assert "| Variable | Change, Dymola/linux |" in detail
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
    text = _md(diff)
    assert _overview(text)[2] == ["`P`", "two baselines", "cannot be read"]
    assert (
        "- **Dymola/linux**: two baselines at aaaaaaa: ref_1.json, ref_2.json" in text
    )
    assert (
        "- **OpenModelica/linux** `ref_3.json`: cannot be read at aaaaaaa (variable 1 has no name)"
        in text
    )


KEY_CHANGE = (
    "Change = the biggest move of a tracked variable, old → new, as % of its range."
)
KEY_END = "Only stored points are compared. = unchanged; blank = no baseline."


def test_one_key_line_under_the_table_names_only_the_columns_it_has():
    plain = as_markdown(_two_tests())
    assert plain.rstrip().splitlines()[-1] == f"{KEY_CHANGE} {KEY_END}"
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 1.0), steps=(10.0, 12.0)
                    )
                },
                against={"OpenModelica/linux": [_var("x", 0.1)]},
            )
        ],
        against="Dymola/linux",
    )
    lines = as_markdown(diff).rstrip().splitlines()
    assert lines[-3].startswith("| `A` |")
    assert lines[-2:] == [
        "",
        f"{KEY_CHANGE} Gap = the same, between tools or OSes, after the change. "
        f"Solver steps: Dymola/linux, new vs old. {KEY_END}",
    ]
    assert "_" not in lines[-1]


KEY_ZERO = (
    "0 % = the file changed but no tracked value moved beyond the digits it was "
    "saved with."
)


def test_a_change_of_exactly_zero_reads_0_percent_and_the_key_line_says_it():
    """A bare 0 beside = read alike, and the key line did not say it."""
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 0.0))},
            ),
            TestDiff(
                "Lib.B",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 2.0))},
            ),
        ],
        folders=["Dymola/linux"],
    )
    text = as_markdown(diff)
    assert _overview(text)[2:] == [["`B`", "2.0 %"], ["`A`", "0 %"]]
    assert text.rstrip().splitlines()[-1] == f"{KEY_CHANGE} {KEY_ZERO} {KEY_END}"


def test_a_gap_of_exactly_zero_reads_0_percent_with_its_folder():
    """A zero gap is the tools agreeing, not a file that changed: the key
    line's 0 % sentence is for a Change cell only."""
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"A/linux": _changed("A/linux", _var("x", 1.0))},
                against={"B/linux": [_var("x", 0.0)], "C/linux": [_var("x", 0.0)]},
            )
        ],
        folders=["A/linux", "B/linux", "C/linux"],
        against="A/linux",
    )
    text = as_markdown(diff)
    assert _overview(text)[2][-1] == "0 % (B/linux)"
    assert KEY_ZERO not in text


def test_the_steps_cell_of_an_unchanged_count_reads_plus_minus_0_percent():
    """``Solver steps 0`` read as zero steps."""
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 5.0), steps=(200.0, 200.0)
                    )
                },
            )
        ],
        folders=["Dymola/linux"],
    )
    text = as_markdown(diff)
    assert _overview(text)[2] == ["`A`", "5.0 %", "±0 %"]
    assert KEY_ZERO not in text


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
    text = _md(diff)
    assert "  - Solver effort, old → new: Translation constants 5 → 6" in text
    assert (
        "  - Unchanged: Accepted steps 10; States 4; 2 translation statistics" in text
    )


def test_a_change_of_nothing_reads_0_percent_with_no_time_or_scale():
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
    assert "| `x` | 0 % |" in _md(diff)


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
    assert as_markdown(diff).splitlines()[0] == (
        "**1 test not compared** (`aaaaaaa` → `bbbbbbb`). "
        "7 baseline files unchanged, 2 of them only in metadata."
    )


# --- the overview's cells ---------------------------------------------------------


def test_the_gap_cell_is_the_largest_change_and_says_a_non_finite_point():
    """The folder is chosen by its largest change, as a cell's is; a
    non-finite point anywhere is said beside it, never chosen over a
    larger number (a NaN beside 0.1 % hid a 600 % change)."""
    diff = _diff(
        [
            TestDiff(
                "Lib.T",
                {"A/linux": _changed("A/linux", _var("x", 1.0))},
                against={
                    "B/linux": [
                        _var("x", 0.1),
                        _var("y", 0.0, non_finite=1, non_finite_at=2.0),
                    ],
                    "C/linux": [_var("x", 600.0), _var("y", 0.0)],
                },
            )
        ],
        folders=["A/linux", "B/linux", "C/linux"],
        against="A/linux",
    )
    assert _overview(as_markdown(diff))[2][-1] == "600 % (C/linux, not finite)"


def test_the_gap_cell_ranks_any_percent_above_an_absolute_difference():
    """A 50 absolute difference (a change from 0) is no larger than a 5 %
    one: the folder is chosen as a cell's change is."""
    diff = _diff(
        [
            TestDiff(
                "Lib.T",
                {"A/linux": _changed("A/linux", _var("x", 1.0))},
                against={
                    "B/linux": [_var("y", 50.0, scale="absolute")],
                    "C/linux": [_var("y", 5.0)],
                },
            )
        ],
        folders=["A/linux", "B/linux", "C/linux"],
        against="A/linux",
    )
    assert _overview(as_markdown(diff))[2][-1] == "5.0 % (C/linux)"


def test_a_cell_where_nothing_was_compared_reads_a_dash_not_0():
    """An all-NaN newer side, or an empty time: no point compared is no
    change of 0."""
    nothing = SeriesChange(largest=None, scale="absolute", at=None, non_finite=3)
    empty = SeriesChange(
        largest=None, scale="absolute", at=None, note="no time in the old baseline"
    )
    diff = _diff(
        [
            TestDiff(
                "Lib.N",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", VariableDiff("x", "x", "changed", nothing)
                    ),
                    "OpenModelica/linux": _changed(
                        "OpenModelica/linux", VariableDiff("x", "x", "changed", empty)
                    ),
                },
            )
        ]
    )
    assert _overview(as_markdown(diff))[2] == ["`N`", "– (not finite)", "–"]


def test_a_test_new_in_one_folder_but_held_in_another_is_not_a_new_test():
    """A test is new only where no folder held it at the older revision
    (removed likewise); the folder that gained it still reads `new`, and
    a folder whose baseline did not change reads `=`."""
    diff = _diff(
        [
            TestDiff(
                "Lib.E",
                {"OpenModelica/linux": BaselineDiff("OpenModelica/linux", "new")},
                unchanged=["Dymola/linux"],
            ),
            TestDiff(
                "Lib.G",
                {"OpenModelica/linux": BaselineDiff("OpenModelica/linux", "removed")},
                unchanged=["Dymola/linux"],
            ),
            TestDiff(
                "Lib.F",
                {"OpenModelica/linux": BaselineDiff("OpenModelica/linux", "new")},
            ),
        ]
    )
    text = as_markdown(diff)
    assert text.splitlines()[0].startswith("**2 tests changed, 1 new** (")
    assert _overview(text)[2:] == [
        ["`E`", "=", "new"],
        ["`F`", "", "new"],
        ["`G`", "=", "removed"],
    ]


def test_the_steps_column_reads_equal_where_that_baseline_is_unchanged():
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
                unchanged=["Dymola/linux"],
            ),
            TestDiff(
                "Lib.C",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("x", 0.5), steps=(0.0, 12.0)
                    )
                },
            ),
        ]
    )
    rows = _overview(as_markdown(diff))
    assert [r[-1] for r in rows[2:]] == ["−25 %", "=", "–"]


def test_a_pipe_or_a_backtick_in_a_name_keeps_the_table_s_columns():
    diff = _diff(
        [
            TestDiff(
                "Lib.A|B",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _var("a|b", 2.0), _var("c`d", 1.0)
                    )
                },
            )
        ],
        folders=["Dymola/linux"],
    )
    text = _md(diff)
    assert "| `A\\|B` | 2.0 % |" in text
    assert "| `a\\|b` | 2.0 % (1 s) |" in text
    assert "| `` c`d `` | 1.0 % (1 s) |" in text


def test_a_sampling_first_recorded_is_said_in_the_detail_and_not_marked():
    first = BaselineDiff(
        "Dymola/linux",
        "changed",
        "ref_a.json",
        "ref_a.json",
        variables=[_var("x", 1.0)],
        sampling_first=Sampling(500, 900, 300, "single"),
        samples=[SettingChange("sampling", "stored_points", 250, 300)],
    )
    diff = _diff([TestDiff("Lib.A", {"Dymola/linux": first})], folders=["Dymola/linux"])
    text = _md(diff)
    assert _overview(text)[2] == ["`A`", "1.0 %"]
    assert (
        "- **Dymola/linux** `ref_a.json`: settings unchanged; "
        "sampling first recorded (500, single)" in text
    )
    assert "  - Samples, old → new: stored_points 250 → 300" in text


# --- which tests are folded out ----------------------------------------------------


def _two_tests():
    return _diff(
        [
            TestDiff(
                "Lib.pkg.A", {"Dymola/linux": _changed("Dymola/linux", _var("x", 2.0))}
            ),
            TestDiff(
                "Lib.other.B",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 1.0))},
            ),
        ],
        folders=["Dymola/linux"],
    )


def test_by_default_the_markdown_is_the_overview_and_the_key():
    text = as_markdown(_two_tests())
    assert "<details>" not in text
    assert "| `A` | 2.0 % |" in text
    assert text.rstrip().endswith(KEY_END)


def test_details_with_no_name_folds_out_every_test():
    diff = _two_tests()
    text = as_markdown(diff, details=detailed(diff, []))
    assert text.count("<details>") == 2


def test_details_by_short_name_or_model_id_folds_out_those_only():
    diff = _two_tests()
    assert detailed(diff, ["B"]) == ["Lib.other.B"]
    assert detailed(diff, ["Lib.pkg.A", "B"]) == ["Lib.pkg.A", "Lib.other.B"]
    text = as_markdown(diff, details=detailed(diff, ["B"]))
    assert text.count("<details>") == 1
    assert "<summary><code>B</code>" in text


def test_details_naming_no_listed_test_is_refused_with_its_name():
    with pytest.raises(DiffError) as got:
        detailed(_two_tests(), ["B", "Nope"])
    assert str(got.value) == (
        "--details: no test this diff lists is named Nope "
        "(a short name such as A, or a full model id)"
    )


def _zero_cell_with(*others):
    """One test whose Dymola/linux cell has x at 0 % beside ``others``."""
    return _diff(
        [
            TestDiff(
                "Lib.A",
                {"Dymola/linux": _changed("Dymola/linux", _var("x", 0.0), *others)},
            )
        ],
        folders=["Dymola/linux"],
    )


def test_a_move_on_the_absolute_scale_outranks_a_zero_percent():
    """y went 0 → 0.5 (no range to take a percent of): the cell names that
    move, not x's 0 %, and the key line does not say nothing moved."""
    text = as_markdown(_zero_cell_with(_var("y", 0.5, scale="absolute")))
    assert _overview(text)[2] == ["`A`", "0.5 absolute"]
    assert KEY_ZERO not in text


def test_a_value_that_turned_not_finite_is_no_unmoved_file():
    text = as_markdown(_zero_cell_with(_var("y", 0.0, non_finite=1, non_finite_at=2.0)))
    assert KEY_ZERO not in text


def test_old_points_outside_the_new_span_are_no_unmoved_file():
    text = as_markdown(_zero_cell_with(_var("y", 0.0, outside=5)))
    assert KEY_ZERO not in text


def test_old_points_of_an_event_new_stores_otherwise_are_said_and_no_unmoved_file():
    """The middle samples of an event the new run stores another number of
    times are not compared: the detail counts them, and a 0 % beside them
    is no file whose values did not move."""
    diff = _zero_cell_with(_var("y", 0.0, uneven=2))
    assert KEY_ZERO not in as_markdown(diff)
    text = as_markdown(diff, details=["Lib.A"])
    assert (
        "| `y` | 0 %; 2 old points inside an event the new run stores another "
        "number of times |"
    ) in text


def _rounding(name, largest):
    """A change within the digits the coarser file was saved with."""
    return _var(name, largest, scale="value", within_saved_digits=True)


def test_a_change_within_the_saved_digits_reads_0_percent_and_ranks_as_none():
    """A file accepted again at another precision moved by its rounding
    only: its cell reads 0 %, it sorts below a real move of a smaller
    number, and the key line says what 0 % means."""
    added = [_var(f"n{i}", 0.0, status="new") for i in range(6)]
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {
                    "Dymola/linux": _changed(
                        "Dymola/linux", _rounding("x", 3.7e-6), *added
                    )
                },
            ),
            TestDiff(
                "Lib.B", {"Dymola/linux": _changed("Dymola/linux", _var("y", 1e-6))}
            ),
        ],
        folders=["Dymola/linux"],
    )
    text = as_markdown(diff)
    assert _overview(text)[2:] == [["`B`", "1.0e-06 %"], ["`A`", "0 % (+6 variables)"]]
    assert text.rstrip().splitlines()[-1] == f"{KEY_CHANGE} {KEY_ZERO} {KEY_END}"


def test_the_detail_names_a_rounding_with_its_true_size():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"Dymola/linux": _changed("Dymola/linux", _rounding("x", 3.7e-6))},
            )
        ],
        folders=["Dymola/linux"],
    )
    text = as_markdown(diff, details=["Lib.A"])
    assert "<summary><code>A</code> — 0 %</summary>" in text.splitlines()
    assert "| `x` | 0 % (rounding: 3.7e-06 % of value) |" in text.splitlines()


def test_a_gap_within_the_saved_digits_reads_0_percent():
    diff = _diff(
        [
            TestDiff(
                "Lib.A",
                {"A/linux": _changed("A/linux", _var("x", 1.0))},
                against={"B/linux": [_rounding("x", 5e-6)]},
            )
        ],
        folders=["A/linux", "B/linux"],
        against="A/linux",
    )
    assert _overview(as_markdown(diff))[2] == ["`A`", "1.0 %", "", "0 %"]


def test_a_renamed_variable_says_so_in_the_detail_and_only_it_in_json():
    renamed = VariableDiff(
        "unitTests.x[1]",
        "PHS.p",
        "changed",
        _change(3.9, at=2.0),
        renamed=("PHS.p", "unitTests.x[1]"),
    )
    kept = _var("y", 1.0)
    base = BaselineDiff("Dymola/linux", "changed", variables=[renamed, kept])
    diff = _diff([TestDiff("Lib.A", {"Dymola/linux": base})])
    text = as_markdown(diff, details=["Lib.A"])
    assert "| `PHS.p` | 3.9 % (2 s); renamed PHS.p → unitTests.x[1] |" in text
    assert "| `y` | 1.0 % (1 s) |" in text
    data = json.loads(as_json(diff))
    got = data["tests"][0]["baselines"]["Dymola/linux"]["variables"]
    assert got[0]["renamed"] == ["PHS.p", "unitTests.x[1]"]
    # a variable paired by its name keeps the JSON it had
    assert "renamed" not in got[1]
