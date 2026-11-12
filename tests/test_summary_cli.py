"""`dstf summary` from the command line: what it prints or writes, and each
refusal as one sentence and exit 2."""

import json

import pytest

from dstf.cli.main import main


def _run_dir(folder, statuses, *, backend="Python", os_="linux", metadata=True):
    folder.mkdir(parents=True, exist_ok=True)
    tests = [
        {"model_id": m, "status": s, "message": None, "warnings": [], "tree": None}
        for m, s in statuses.items()
    ]
    totals = {"total": len(tests)}
    for s in ("PASS", "FAIL", "SIM_FAIL", "NO_REF", "NOT RUN"):
        totals[s] = sum(t["status"] == s for t in tests)
    doc = {
        "metadata": {"backend": backend, "backend_name": backend, "os": os_}
        if metadata
        else None,
        "totals": totals,
        "tests": tests,
    }
    (folder / "verdicts.json").write_text(json.dumps(doc), encoding="utf-8")
    return folder


def _list_of(lines, label):
    """A label's folded list, from its ``<details>`` line to its end."""
    (summary,) = [
        i
        for i, line in enumerate(lines)
        if line.startswith(f"<summary>Python/linux, {label}:")
    ]
    end = lines.index("</details>", summary)
    return lines[summary - 1 : end + 1]


@pytest.fixture
def runs(tmp_path):
    base = _run_dir(tmp_path / "base", {"Lib.A": "PASS", "Lib.B": "FAIL"})
    head = _run_dir(tmp_path / "head", {"Lib.A": "FAIL", "Lib.B": "FAIL"})
    return base, head


def test_markdown_is_printed(runs, capsys):
    base, head = runs
    assert main(["summary", f"base={base}", f"head={head}", "--base", "base"]) == 0
    out = capsys.readouterr().out
    assert "| Python/linux | 1/2 (1 FAIL) | 0/2 (2 FAIL; 1 also fails on base) |" in out
    assert (
        "<summary>Python/linux, head: 2 not passing, 1 also fails on base</summary>"
        in out
    )


def test_a_failure_where_the_base_had_no_baseline_is_new_and_open(tmp_path, capsys):
    """The base said NO_REF: it did not fail the test, so the branch's
    SIM_FAIL is new, its list open."""
    main_ = _run_dir(tmp_path / "main", {"L.C": "NO_REF", "L.P": "PASS"})
    branch = _run_dir(tmp_path / "branch", {"L.C": "SIM_FAIL", "L.P": "PASS"})
    argv = ["summary", f"main={main_}", f"branch={branch}", "--base", "main"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert (
        "| Python/linux | 1/2 (1 no baseline) | 1/2 (1 SIM_FAIL; 0 also fail on main) |"
        in out
    )
    assert (
        "<details open>\n<summary>Python/linux, branch: 1 not passing, "
        "0 also fail on main</summary>" in out
    )


def test_no_baseline_on_both_sides_is_no_failure(tmp_path, capsys):
    """L.C has no baseline on main nor on the branch: no failure of the
    branch, counted on its own, and listed apart from what failed."""
    main_ = _run_dir(tmp_path / "main", {"L.C": "NO_REF", "L.P": "PASS"})
    branch = _run_dir(tmp_path / "branch", {"L.C": "NO_REF", "L.P": "PASS"})
    argv = ["summary", f"main={main_}", f"branch={branch}", "--base", "main"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[2] == (
        "| Python/linux | 1/2 (1 no baseline) | 1/2 (1 no baseline; 1 also has none "
        "on main) |"
    )
    assert lines[4] == (
        'Passed / run. "also fails on main" = the test fails on main too (or ends '
        'there the same way). "no baseline" = NO_REF: nothing stored to score '
        "against, so not a failure."
    )
    branch_list = _list_of(lines, "branch")
    assert branch_list == [
        "<details>",
        "<summary>Python/linux, branch: 1 no baseline, 1 also has none on "
        "main</summary>",
        "",
        'No baseline, nothing scored: <code title="L.C">C</code> (main: NO_REF).',
        "",
        "</details>",
    ]
    assert "What failed" not in out


def test_no_baseline_on_the_branch_only_is_not_a_failure_the_base_shares(
    tmp_path, capsys
):
    """Main failed L.C; the branch runs it and has no baseline for it. That is
    no failure that also fails on main: only L.D, a SIM_FAIL on both, is."""
    main_ = _run_dir(tmp_path / "main", {"L.C": "SIM_FAIL", "L.D": "SIM_FAIL"})
    branch = _run_dir(tmp_path / "branch", {"L.C": "NO_REF", "L.D": "SIM_FAIL"})
    argv = ["summary", f"main={main_}", f"branch={branch}", "--base", "main"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[2] == (
        "| Python/linux | 0/2 (2 SIM_FAIL) | 0/2 (1 SIM_FAIL, 1 no baseline; "
        "1 also fails on main) |"
    )
    branch_list = _list_of(lines, "branch")
    assert branch_list == [
        "<details>",
        "<summary>Python/linux, branch: 1 not passing, 1 also fails on main; "
        "1 no baseline</summary>",
        "",
        "| Test | Status | main | What failed |",
        "| --- | --- | --- | --- |",
        '| <code title="L.D">D</code> | SIM_FAIL | SIM_FAIL |  |',
        "",
        'No baseline, nothing scored: <code title="L.C">C</code> (main: SIM_FAIL).',
        "",
        "</details>",
    ]
    out_file = tmp_path / "summary.json"
    assert main([*argv, "--format", "json", "-o", str(out_file)]) == 0
    cells = json.loads(out_file.read_text(encoding="utf-8"))["cells"]
    branch_cell = [c for c in cells if c["label"] == "branch"][0]
    assert (branch_cell["not_new"], branch_cell["none_on_base"]) == (1, 0)


def test_a_base_run_cut_short_does_not_fold_the_branch_s_failures(tmp_path, capsys):
    """The base never ran L.S and L.P (NOT RUN, NO_REF): the branch's FAILs
    there are new; only L.C, a SIM_FAIL on both, is not."""
    main_ = _run_dir(
        tmp_path / "main", {"L.C": "SIM_FAIL", "L.P": "NO_REF", "L.S": "NOT RUN"}
    )
    branch = _run_dir(
        tmp_path / "branch", {"L.C": "SIM_FAIL", "L.P": "FAIL", "L.S": "FAIL"}
    )
    argv = ["summary", f"main={main_}", f"branch={branch}", "--base", "main"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "0/3 (2 FAIL, 1 SIM_FAIL; 1 also fails on main) |" in out
    assert (
        "<details open>\n<summary>Python/linux, branch: 3 not passing, "
        "1 also fails on main</summary>" in out
    )


def test_json_is_written_to_the_output_file(runs, tmp_path, capsys):
    base, head = runs
    out = tmp_path / "summary.json"
    code = main(
        ["summary", f"base={base}", f"head={head}", "--base", "base"]
        + ["--format", "json", "-o", str(out)]
    )
    assert code == 0
    assert capsys.readouterr().out == f"Wrote {out}\n"
    data = json.loads(out.read_text(encoding="utf-8"))
    head_cell = [c for c in data["cells"] if c["label"] == "head"][0]
    assert (head_cell["passed"], head_cell["not_new"]) == (0, 1)


def _refused(argv, capsys) -> str:
    code = main(argv)
    captured = capsys.readouterr()
    assert code == 2, captured.out
    assert "Traceback" not in captured.err
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1, captured.err
    return lines[0]


def test_an_argument_that_is_not_label_equals_dir_is_refused(runs, capsys):
    base, _ = runs
    said = _refused(["summary", str(base)], capsys)
    assert said == f"Error: {base}: not LABEL=DIR (main=testing_output/main)"


def test_a_dir_with_no_verdicts_is_refused(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    said = _refused(["summary", f"head={tmp_path / 'empty'}"], capsys)
    assert said.startswith(f"Error: head={tmp_path / 'empty'}: no verdicts.json in it")


def test_a_base_naming_no_label_is_refused(runs, capsys):
    base, head = runs
    said = _refused(["summary", f"a={base}", f"b={head}", "--base", "main"], capsys)
    assert said == "Error: --base main: no such label (the labels are a, b)"
