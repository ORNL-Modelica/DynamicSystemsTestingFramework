"""`dstf baseline plot` from the command line, without a browser: each
refusal is one sentence and exit 2, and none writes anything. The images
themselves are `test_baseline_plot_playwright.py`'s."""

import json
import sys

from conftest import GitRepo
from dstf.cli.main import main


def _ref(model_id, variables):
    return json.dumps(
        {
            "model_id": model_id,
            "simulation": {"stop_time": 2.0},
            "time": [0.0, 1.0, 2.0],
            "variables": [
                {"index": i, "name": name, "expression": "", "values": values}
                for i, (name, values) in enumerate(variables, start=1)
            ],
        }
    )


def _lib(tmp_path, variables=1):
    """A repository whose ReferenceResults changed Lib.A between two commits."""
    repo = GitRepo(tmp_path / "lib")
    folder = repo.top / "ReferenceResults" / "Dymola" / "linux"
    folder.mkdir(parents=True)
    names = [f"x{i}" for i in range(variables)]
    (folder / "ref_a.json").write_text(_ref("Lib.A", [(n, [0, 5, 10]) for n in names]))
    repo.one = repo.commit("one")
    (folder / "ref_a.json").write_text(_ref("Lib.A", [(n, [0, 6, 10]) for n in names]))
    repo.two = repo.commit("two")
    repo.root = repo.top / "ReferenceResults"
    return repo


def _plot(lib, out, *more):
    return main(
        ["baseline", "plot", lib.one, lib.two, *more, "-o", str(out)]
        + ["--reference-root", str(lib.root)]
    )


def test_one_revision_is_refused(tmp_path, capsys):
    lib = _lib(tmp_path)
    out = tmp_path / "out"
    code = main(
        ["baseline", "plot", lib.one, "-o", str(out), "--reference-root", str(lib.root)]
    )
    assert code == 2
    assert capsys.readouterr().err.strip() == (
        "Error: baseline plot draws two revisions or more; it was given one"
    )
    assert not out.exists()


def test_over_300_images_without_test_is_refused(tmp_path, capsys):
    """A migration commit touches hundreds of tests: hours of images and a
    page too large to send. Named tests are the user's choice."""
    lib = _lib(tmp_path, variables=301)
    out = tmp_path / "out"
    assert _plot(lib, out) == 2
    assert capsys.readouterr().err.strip() == (
        "Error: 301 images to draw, over 300: name the tests (--test) or the "
        "folders (--folder) to draw"
    )
    assert not out.exists()


def test_a_directory_holding_a_user_s_file_is_refused_and_kept(tmp_path, capsys):
    lib = _lib(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    assert _plot(lib, out) == 2
    assert capsys.readouterr().err.strip() == (
        f"Error: {out} holds files baseline plot did not write (notes.txt): "
        "name a new or empty directory"
    )
    assert [p.name for p in out.iterdir()] == ["notes.txt"]


def test_a_test_that_matches_nothing_is_refused(tmp_path, capsys):
    lib = _lib(tmp_path)
    out = tmp_path / "out"
    assert _plot(lib, out, "--test", "Nope") == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("Error: --test: no baseline at these revisions is of a test")
    assert not out.exists()


def test_without_playwright_the_install_is_named_and_nothing_written(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    lib = _lib(tmp_path)
    out = tmp_path / "out"
    assert _plot(lib, out) == 2
    err = capsys.readouterr().err.strip()
    assert err == (
        "Error: drawing images needs Playwright, which is not installed here: "
        "install the browser extra (pip install "
        "'dynamic-systems-testing-framework[browser]'), then run "
        f'"{sys.executable}" -m playwright install chromium'
    )
    assert not out.exists()


def test_without_a_reference_root_it_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main(["baseline", "plot", "HEAD~1", "HEAD", "-o", "out"])
    assert code == 2
    assert "no reference root" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
