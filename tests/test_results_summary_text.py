"""`dstf summary`'s words (``reporting.results_summary_text``): the matrix
and the lists as Markdown for a pull request, and the same facts as JSON."""

import json
from pathlib import Path

from dstf.reporting.results_summary import Cell, NotPassing, Summary
from dstf.reporting.results_summary_text import as_json, as_markdown

TOTALS = {"total": 3, "PASS": 1, "FAIL": 1, "SIM_FAIL": 1, "NO_REF": 0, "NOT RUN": 0}


def _cell(
    label, folder, rows, *, passed=1, total=3, not_new=None, note="", totals=None
):
    return Cell(
        label=label,
        folder=folder,
        path=Path(f"/runs/{label}/verdicts.json"),
        passed=passed,
        total=total,
        totals=totals or TOTALS,
        not_passing=rows,
        not_new=not_new,
        base_note=note,
    )


def _summary(cells, *, base="base", folders=None):
    labels = list(dict.fromkeys(c.label for c in cells))
    return Summary(
        labels=labels,
        folders=folders or sorted({c.folder for c in cells}),
        cells={(c.label, c.folder): c for c in cells},
        base=base,
    )


def _table(text, start):
    """The rows of the first table at or after the line ``start``."""
    lines = text.splitlines()
    i = lines.index(start)
    rows = []
    for line in lines[i:]:
        if not line.startswith("| "):
            break
        rows.append([c.strip() for c in line.strip("|").split(" | ")])
    return rows


BASE = _cell(
    "base",
    "Python/linux",
    [NotPassing("Lib.pkg.Broken", "SIM_FAIL", "RuntimeError: broken on purpose")],
    passed=2,
    totals={"total": 3, "PASS": 2, "FAIL": 0, "SIM_FAIL": 1, "NO_REF": 0, "NOT RUN": 0},
)
BRANCH = _cell(
    "branch",
    "Python/linux",
    [
        NotPassing("Lib.pkg.Broken", "SIM_FAIL", "RuntimeError: x", "SIM_FAIL"),
        NotPassing("Lib.pkg.Ramp", "FAIL", "x: NRMSE 0.058 vs 0.01 → FAIL", "PASS"),
    ],
    not_new=1,
)


def test_the_matrix_has_a_row_per_backend_os_and_a_column_per_label():
    other = _cell(
        "branch",
        "Dymola/windows",
        [],
        passed=3,
        total=3,
        totals={"total": 3, "PASS": 3},
    )
    text = as_markdown(_summary([BASE, BRANCH, other]))
    assert _table(text, "| Backend/os | base | branch |") == [
        ["Backend/os", "base", "branch"],
        ["---", "---", "---"],
        ["Dymola/windows", "–", "3/3"],
        ["Python/linux", "2/3 (1 SIM_FAIL)", "1/3 (1 FAIL, 1 SIM_FAIL) · 1 not new"],
    ]


def test_a_list_s_summary_line_is_in_the_owner_s_words_and_opens_on_a_new_failure():
    text = as_markdown(_summary([BASE, BRANCH]))
    assert (
        "<details open>\n<summary>Python/linux, branch: 2 not passing, "
        "1 also fail on base</summary>" in text
    )
    # the base's own list holds nothing new: folded
    assert "<details>\n<summary>Python/linux, base: 1 not passing</summary>" in text


def test_a_list_with_nothing_new_is_folded():
    old_only = _cell(
        "branch",
        "Python/linux",
        [NotPassing("Lib.pkg.Broken", "SIM_FAIL", "", "SIM_FAIL")],
        not_new=1,
    )
    text = as_markdown(_summary([BASE, old_only]))
    assert (
        "<details>\n<summary>Python/linux, branch: 1 not passing, "
        "1 also fail on base</summary>" in text
    )


def test_a_list_row_names_the_test_its_status_the_base_s_and_what_failed():
    text = as_markdown(_summary([BASE, BRANCH]))
    assert _table(text, "| Test | Status | base | What failed |")[2:] == [
        [
            '<code title="Lib.pkg.Ramp">Ramp</code>',
            "FAIL",
            "PASS",
            "x: NRMSE 0.058 vs 0.01 → FAIL",
        ],
        [
            '<code title="Lib.pkg.Broken">Broken</code>',
            "SIM_FAIL",
            "SIM_FAIL",
            "RuntimeError: x",
        ],
    ]


def test_with_no_base_run_for_the_backend_os_the_line_says_why():
    alone = _cell(
        "branch",
        "OpenModelica/linux",
        [NotPassing("Lib.A", "FAIL", "y: → FAIL")],
        note="no base run for OpenModelica/linux",
    )
    text = as_markdown(_summary([BASE, alone]))
    assert (
        "<details open>\n<summary>OpenModelica/linux, branch: 1 not passing; "
        "no base run for OpenModelica/linux</summary>" in text
    )
    assert _table(text, "| Test | Status | What failed |")[2] == [
        '<code title="Lib.A">A</code>',
        "FAIL",
        "y: → FAIL",
    ]
    rows = _table(text, "| Backend/os | base | branch |")
    assert rows[2] == ["OpenModelica/linux", "–", "1/3 (1 FAIL, 1 SIM_FAIL)"]


def test_without_a_base_every_list_is_open_and_no_cell_counts_not_new():
    alone = _cell("head", "Python/linux", [NotPassing("Lib.A", "FAIL", "")])
    text = as_markdown(_summary([alone], base=None))
    assert (
        "<details open>\n<summary>Python/linux, head: 1 not passing</summary>" in text
    )
    assert "not new" not in text


def test_a_pipe_or_markup_in_what_failed_keeps_the_row():
    odd = _cell(
        "head",
        "Python/linux",
        [NotPassing("Lib.A|B", "FAIL", "a|b <tag> & more")],
    )
    text = as_markdown(_summary([odd], base=None))
    assert (
        '| <code title="Lib.A\\|B">A\\|B</code> | FAIL | a\\|b &lt;tag&gt; &amp; more |'
        in text
    )


def test_every_run_passing_lists_nothing():
    clean = _cell(
        "head", "Python/linux", [], passed=3, total=3, totals={"total": 3, "PASS": 3}
    )
    text = as_markdown(_summary([clean], base=None))
    assert "<details" not in text
    assert "| Python/linux | 3/3 |" in text


def test_json_holds_every_cell_and_row():
    data = json.loads(as_json(_summary([BASE, BRANCH])))
    assert (data["labels"], data["base"], data["folders"]) == (
        ["base", "branch"],
        "base",
        ["Python/linux"],
    )
    cells = {(c["label"], c["folder"]): c for c in data["cells"]}
    branch = cells[("branch", "Python/linux")]
    assert (branch["backend"], branch["os"]) == ("Python", "linux")
    assert (branch["passed"], branch["total"], branch["not_new"]) == (1, 3, 1)
    assert branch["totals"] == TOTALS
    assert branch["file"] == "/runs/branch/verdicts.json"
    assert branch["not_passing"][1] == {
        "model_id": "Lib.pkg.Ramp",
        "status": "FAIL",
        "reason": "x: NRMSE 0.058 vs 0.01 → FAIL",
        "base": "PASS",
    }
    assert cells[("base", "Python/linux")]["not_new"] is None
