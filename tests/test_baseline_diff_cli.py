"""`dstf baseline diff` from the command line: where the reference root is,
what it prints or writes, and each refusal as one sentence and exit 2."""

import json

import pytest

from conftest import RESAVED_OLD, GitRepo, resaved_single
from dstf.cli.main import main


def _ref(model_id, values):
    return json.dumps(
        {
            "model_id": model_id,
            "simulation": {"stop_time": 2.0},
            "time": [0.0, 1.0, 2.0],
            "variables": [
                {"index": 1, "name": "x", "expression": "", "values": values}
            ],
        }
    )


@pytest.fixture
def lib(tmp_path):
    """A repository whose ReferenceResults changed Lib.A between two commits."""
    repo = GitRepo(tmp_path / "lib")
    folder = repo.top / "ReferenceResults" / "Dymola" / "linux"
    folder.mkdir(parents=True)
    (folder / "ref_a.json").write_text(_ref("Lib.A", [0, 5, 10]))
    repo.one = repo.commit("one")
    (folder / "ref_a.json").write_text(_ref("Lib.A", [0, 6, 10]))
    repo.two = repo.commit("two")
    return repo


def test_markdown_is_printed(lib, capsys):
    root = lib.top / "ReferenceResults"
    code = main(["baseline", "diff", lib.one, lib.two, "--reference-root", str(root)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith(
        f"**1 test has new baselines** (`{lib.one[:7]}` → `{lib.two[:7]}`)"
    )
    assert "| `A` | 10 % |" in out


def test_json_is_written_to_the_output_file(lib, tmp_path, capsys):
    root = lib.top / "ReferenceResults"
    out = tmp_path / "diff.json"
    code = main(
        ["baseline", "diff", lib.one, lib.two, "--reference-root", str(root)]
        + ["--format", "json", "-o", str(out)]
    )
    assert code == 0
    assert capsys.readouterr().out == f"Wrote {out}\n"
    data = json.loads(out.read_text(encoding="utf-8"))
    (test,) = data["tests"]
    change = test["baselines"]["Dymola/linux"]["variables"][0]["change"]
    assert (change["largest"], change["at"]) == (pytest.approx(10.0), 1.0)


def test_without_rev2_the_files_on_disk_are_compared(lib, capsys):
    (lib.top / "ReferenceResults" / "Dymola" / "linux" / "ref_a.json").write_text(
        _ref("Lib.A", [0, 6, 12])
    )
    root = lib.top / "ReferenceResults"
    assert main(["baseline", "diff", lib.two, "--reference-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        f"**1 test has new baselines** (`{lib.two[:7]}` → files on disk)"
    )
    assert "| `A` | 20 % |" in out


def test_a_testing_json_in_the_reference_root_names_it(lib, capsys):
    root = lib.top / "ReferenceResults"
    (root / "testing.json").write_text('{"source_path": "../NoLibraryHere"}')
    code = main(["baseline", "diff", lib.one, lib.two, "--config", str(root)])
    assert code == 0, capsys.readouterr().err
    assert "| `A` | 10 % |" in capsys.readouterr().out


def test_a_testing_json_s_reference_root_is_read_from_its_directory(lib, capsys):
    (lib.top / "testing.json").write_text('{"reference_root": "ReferenceResults"}')
    code = main(["baseline", "diff", lib.one, lib.two, "--config", str(lib.top)])
    assert code == 0, capsys.readouterr().err
    assert "| `A` | 10 % |" in capsys.readouterr().out


def test_without_a_flag_the_testing_json_here_is_read(lib, monkeypatch, capsys):
    (lib.top / "testing.json").write_text('{"reference_root": "ReferenceResults"}')
    monkeypatch.chdir(lib.top)
    assert main(["baseline", "diff", lib.one, lib.two]) == 0
    assert "| `A` | 10 % |" in capsys.readouterr().out


def _refused(argv, capsys) -> str:
    code = main(argv)
    captured = capsys.readouterr()
    assert code == 2, captured.out
    assert "Traceback" not in captured.err
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1, captured.err
    return lines[0]


def test_a_testing_json_that_says_nothing_of_the_reference_root_is_refused(lib, capsys):
    (lib.top / "testing.json").write_text('{"source_path": "Lib"}')
    said = _refused(["baseline", "diff", lib.one, "--config", str(lib.top)], capsys)
    assert said == (
        f"Error: {lib.top / 'testing.json'} names no reference_root and is not in a "
        "ReferenceResults folder: pass --reference-root DIR"
    )


def test_no_reference_root_and_no_testing_json_is_refused(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    said = _refused(["baseline", "diff", "HEAD"], capsys)
    assert said == (
        "Error: no reference root: pass --reference-root DIR, or --config "
        "naming a testing.json"
    )


def test_an_unknown_revision_is_refused(lib, capsys):
    root = lib.top / "ReferenceResults"
    said = _refused(["baseline", "diff", "nope", "--reference-root", str(root)], capsys)
    assert said == f"Error: unknown revision 'nope' in {lib.top}"


def test_a_folder_in_no_repository_is_refused(tmp_path, capsys):
    said = _refused(
        ["baseline", "diff", "HEAD", "--reference-root", str(tmp_path)], capsys
    )
    assert said == f"Error: not a git repository: {tmp_path}"


def test_against_a_folder_with_no_baselines_is_refused(lib, capsys):
    root = lib.top / "ReferenceResults"
    said = _refused(
        ["baseline", "diff", lib.one, lib.two, "--reference-root", str(root)]
        + ["--against", "OpenModelica/linux"],
        capsys,
    )
    assert said == (
        "Error: --against OpenModelica/linux: no baselines in OpenModelica/linux "
        f"at {lib.two[:7]} (it holds Dymola/linux)"
    )


def _diff_argv(lib, *more):
    root = lib.top / "ReferenceResults"
    return ["baseline", "diff", lib.one, lib.two, "--reference-root", str(root), *more]


def test_by_default_only_the_overview_is_printed(lib, capsys):
    assert main(_diff_argv(lib)) == 0
    out = capsys.readouterr().out
    assert "| `A` | 10 % |" in out
    assert "<details>" not in out


def test_details_with_no_name_folds_out_every_test(lib, capsys):
    assert main(_diff_argv(lib, "--details")) == 0
    out = capsys.readouterr().out
    assert out.count("<details>") == 1
    assert "<summary><code>A</code> — 10 %</summary>" in out


def test_details_names_a_test_by_its_short_name_or_model_id(lib, capsys):
    for name in ("A", "Lib.A"):
        assert main(_diff_argv(lib, "--details", name)) == 0
        assert capsys.readouterr().out.count("<details>") == 1


def test_details_naming_no_listed_test_is_refused(lib, capsys):
    said = _refused(_diff_argv(lib, "--details", "Nope"), capsys)
    assert said == (
        "Error: --details: no test this diff lists is named Nope "
        "(a short name such as A, or a full model id)"
    )


def test_markdown_over_what_a_pull_request_holds_is_noted_on_stderr(tmp_path, capsys):
    """A GitHub pull-request body holds 65,536 characters: past that one
    note goes to stderr, and the Markdown itself is printed whole."""
    repo = GitRepo(tmp_path / "big")
    folder = repo.top / "ReferenceResults" / "Dymola" / "linux"
    folder.mkdir(parents=True)
    names = [f"Lib.M{i}_" + "x" * 3000 for i in range(25)]
    for i, name in enumerate(names):
        (folder / f"ref_{i}.json").write_text(_ref(name, [0, 5, 10]))
    one = repo.commit("one")
    for i, name in enumerate(names):
        (folder / f"ref_{i}.json").write_text(_ref(name, [0, 6, 10]))
    two = repo.commit("two")
    root = repo.top / "ReferenceResults"
    assert main(["baseline", "diff", one, two, "--reference-root", str(root)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"**{len(names)} tests have new baselines**")
    assert len(captured.out) > 65_536
    assert captured.err == (
        f"Note: this Markdown is {len(captured.out):,} characters; a GitHub "
        "pull-request body or comment holds 65,536.\n"
    )


def test_markdown_within_what_a_pull_request_holds_notes_nothing(lib, capsys):
    assert main(_diff_argv(lib, "--details")) == 0
    assert capsys.readouterr().err == ""


def test_a_baseline_saved_again_at_single_precision_reads_0_percent(tmp_path, capsys):
    """A real baseline accepted again at single precision, its numbers
    kept: the Markdown's cell reads 0 %, the JSON keeps each difference's
    true size and says it is within the saved digits."""
    repo = GitRepo(tmp_path / "lib")
    file = repo.top / "ReferenceResults" / "Dymola" / "windows" / "ref_a.json"
    file.parent.mkdir(parents=True)
    old, new = resaved_single()
    file.write_text(json.dumps(old))
    one = repo.commit("one")
    file.write_text(json.dumps(new))
    two = repo.commit("two")
    argv = ["baseline", "diff", one, two]
    argv += ["--reference-root", str(repo.top / "ReferenceResults")]
    assert main(argv) == 0
    assert "| `A` | 0 % |" in capsys.readouterr().out.splitlines()
    out = tmp_path / "diff.json"
    assert main([*argv, "--format", "json", "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    (test,) = data["tests"]
    variables = test["baselines"]["Dymola/windows"]["variables"]
    assert [v["name"] for v in variables] == list(RESAVED_OLD)
    x1 = variables[0]["change"]
    assert (x1["within_saved_digits"], x1["scale"]) == (True, "value")
    assert x1["largest"] == pytest.approx(3.747e-6, rel=1e-3)
    assert all(v["change"]["within_saved_digits"] for v in variables)


def test_details_refuses_a_value_holding_an_equals_sign(lib, capsys):
    """No test name holds "=": such a value is refused when the command
    line is read, as it is by `plot` and `diff`, whose runs it would be."""
    with pytest.raises(SystemExit) as e:
        main(_diff_argv(lib, "--details", "A", "a=1"))
    assert e.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        "dstf baseline diff: error: argument --details: a=1 is not a test name "
        "(no name holds '=')"
    )
