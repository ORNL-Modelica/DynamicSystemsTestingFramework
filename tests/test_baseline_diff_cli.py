"""`dstf baseline diff` from the command line: where the reference root is,
what it prints or writes, and each refusal as one sentence and exit 2."""

import json

import pytest

from conftest import GitRepo
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
        f"**Baselines changed {lib.one[:7]}..{lib.two[:7]}**: 1 test changed"
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
    assert out.startswith(f"**Baselines changed {lib.two[:7]}..files on disk**")
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
