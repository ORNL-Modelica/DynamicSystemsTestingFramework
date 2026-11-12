"""`dstf plot` from the command line, without a browser (Playwright faked
at its edge): what it writes and says, and each refusal — one sentence,
exit 2, nothing written. The images themselves are
`test_plot_playwright.py`'s."""

import json

import pytest

from conftest import fake_playwright, python_example_copy, write_python_run
from dstf.cli.main import main
from dstf.reporting.baseline_gallery import GALLERY, MARKER

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"
POINTS = "PythonTestingLib.Examples.PointsCheck"
WAVE = {"x": [0.0, 0.8, 0.9, 0.1, -0.7]}


@pytest.fixture
def lib(tmp_path):
    python_example_copy(tmp_path)
    return tmp_path / "lib" / "Resources" / "ReferenceResults" / "testing.json"


def _plot(lib, out, *runs_and_flags):
    return main(
        ["plot", *map(str, runs_and_flags), "-o", str(out), "--config", str(lib)]
    )


def _files(out):
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def test_it_writes_the_images_the_page_and_says_what(
    lib, tmp_path, monkeypatch, capsys
):
    main_run = write_python_run(
        tmp_path / "main",
        {RAMP: {"x": [0, 2, 4, 6, 8]}, CSV: {"x": [1.0] * 5}, POINTS: WAVE},
    )
    fix = write_python_run(
        tmp_path / "fix",
        {RAMP: {"x": [0, 2, 4, 6, 9]}, CSV: {"x": [2.0] * 5}, POINTS: WAVE},
    )
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"main={main_run}", f"fix={fix}") == 0
    said = capsys.readouterr().out.splitlines()
    assert said[0] == "Nothing moved in 1 test: PointsCheck."
    assert said[1].startswith(f"Wrote 1 image, 1 table and {out / GALLERY} (")
    assert _files(out) == sorted([MARKER, GALLERY, "SimpleRamp/Python-linux/01-x.png"])
    marker = json.loads((out / MARKER).read_text())
    assert marker["written_by"] == "dstf plot"
    page = (out / GALLERY).read_text(encoding="utf-8")
    assert (
        "<tr><td><code>x</code></td><td>1</td><td>2</td><td>100 % of value</td></tr>"
        in (page.splitlines())
    )


def test_a_directory_it_did_not_write_is_refused(lib, tmp_path, capsys):
    a = write_python_run(tmp_path / "a", {RAMP: {"x": [0, 2, 4, 6, 8]}})
    b = write_python_run(tmp_path / "b", {RAMP: {"x": [0, 2, 4, 6, 9]}})
    out = tmp_path / "out"
    out.mkdir()
    (out / "mine.txt").write_text("a user's file")
    assert _plot(lib, out, f"a={a}", f"b={b}") == 2
    assert capsys.readouterr().err.strip() == (
        f"Error: {out} holds files plot did not write (mine.txt): name a new or "
        "empty directory"
    )


def test_an_argument_not_label_dir_is_refused(lib, tmp_path, capsys):
    out = tmp_path / "out"
    assert _plot(lib, out, "main", "fix=x") == 2
    assert capsys.readouterr().err.strip() == (
        "Error: main: not LABEL=DIR (main=testing_output/main)"
    )
    assert not out.exists()


def test_a_directory_that_is_no_run_is_refused_and_nothing_written(
    lib, tmp_path, capsys
):
    (tmp_path / "empty").mkdir()
    b = write_python_run(tmp_path / "b", {RAMP: {"x": [0, 2, 4, 6, 9]}})
    out = tmp_path / "out"
    assert _plot(lib, out, f"a={tmp_path / 'empty'}", f"b={b}") == 2
    assert capsys.readouterr().err.strip() == (
        f"Error: a={tmp_path / 'empty'}: not a run's work dir (no "
        "batch_manifest.json in it; `dstf run` writes one)"
    )
    assert not out.exists()


def _wide(lib, tmp_path, n):
    """SimpleRamp tracks every variable, and ``n`` of them moved."""
    spec = lib.parent / "test_spec.json"
    data = json.loads(spec.read_text())
    for entry in data["tests"]:
        if entry["model"] == RAMP:
            entry["variables"] = ["*"]
    spec.write_text(json.dumps(data))
    a = write_python_run(
        tmp_path / "a", {RAMP: {f"x{i}": [0, 1, 2, 3, 4] for i in range(n)}}
    )
    b = write_python_run(
        tmp_path / "b", {RAMP: {f"x{i}": [0, 1, 2, 3, 5] for i in range(n)}}
    )
    return a, b


def test_over_300_images_are_refused_without_test_only(
    lib, tmp_path, monkeypatch, capsys
):
    a, b = _wide(lib, tmp_path, 301)
    out = tmp_path / "out"
    assert _plot(lib, out, f"a={a}", f"b={b}") == 2
    assert capsys.readouterr().err.strip() == (
        "Error: 301 images to draw, over 300: name the tests (--test) or the "
        "folders (--folder) to draw"
    )
    assert not out.exists()
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"a={a}", f"b={b}", "--test", "SimpleRamp") == 0
    assert len(list(out.rglob("*.png"))) == 301


def test_a_named_test_one_run_holds_is_drawn_and_said(
    lib, tmp_path, monkeypatch, capsys
):
    a = write_python_run(
        tmp_path / "a", {RAMP: {"x": [0, 2, 4, 6, 8]}, CSV: {"x": [1.0] * 5}}
    )
    b = write_python_run(tmp_path / "b", {RAMP: {"x": [0, 2, 4, 6, 8]}})
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"a={a}", f"b={b}", "--test", "ConstantCsv") == 0
    said = capsys.readouterr().out.splitlines()
    assert (
        said[0]
        == "ConstantCsv · Python/linux: b: not run. No two runs of it to compare."
    )
    assert said[1].startswith(f"Wrote 0 images, 0 tables and {out / GALLERY} (")


def test_a_backend_and_os_of_one_run_is_said_on_the_console(
    lib, tmp_path, monkeypatch, capsys
):
    a = write_python_run(tmp_path / "a", {RAMP: {"x": [0, 2, 4, 6, 8]}})
    b = write_python_run(tmp_path / "b", {RAMP: {"x": [0, 2, 4, 6, 9]}})
    w = write_python_run(
        tmp_path / "w", {RAMP: {"x": [0, 2, 4, 6, 9]}}, os_name="windows"
    )
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"a={a}", f"b={b}", f"b={w}") == 0
    said = capsys.readouterr().out.splitlines()
    assert said[0] == "Python/windows: one run only (b), not drawn."
