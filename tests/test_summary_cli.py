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


@pytest.fixture
def runs(tmp_path):
    base = _run_dir(tmp_path / "base", {"Lib.A": "PASS", "Lib.B": "FAIL"})
    head = _run_dir(tmp_path / "head", {"Lib.A": "FAIL", "Lib.B": "FAIL"})
    return base, head


def test_markdown_is_printed(runs, capsys):
    base, head = runs
    assert main(["summary", f"base={base}", f"head={head}", "--base", "base"]) == 0
    out = capsys.readouterr().out
    assert "| Python/linux | 1/2 (1 FAIL) | 0/2 (2 FAIL) · 1 not new |" in out
    assert (
        "<summary>Python/linux, head: 2 not passing, 1 also fail on base</summary>"
        in out
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
