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


def _more_tests(lib, *model_ids):
    """The library's spec gains a test of each model id, run as SimpleRamp
    is."""
    spec = lib.parent / "test_spec.json"
    data = json.loads(spec.read_text())
    (ramp,) = [entry for entry in data["tests"] if entry["model"] == RAMP]
    data["tests"] += [{**ramp, "model": m} for m in model_ids]
    spec.write_text(json.dumps(data))


SAME = {"x": [0.0, 2.0, 4.0, 6.0, 8.0]}


def test_a_short_name_two_tests_share_is_said_as_the_full_model_id(
    lib, tmp_path, monkeypatch, capsys
):
    """Lib.B.Same moved, so only Lib.A.Same is in the nothing-moved line:
    still said in full, for another test the runs hold is a Same too; so is
    Lib.D.Same, whose run failed. Other is the one test of its name."""
    _more_tests(lib, "Lib.A.Same", "Lib.B.Same", "Lib.C.Other", "Lib.D.Same")
    a = write_python_run(
        tmp_path / "a",
        {
            "Lib.A.Same": SAME,
            "Lib.B.Same": SAME,
            "Lib.C.Other": SAME,
            "Lib.D.Same": SAME,
        },
    )
    b = write_python_run(
        tmp_path / "b",
        {
            "Lib.A.Same": SAME,
            "Lib.B.Same": {"x": [0.0, 2.0, 4.0, 6.0, 9.0]},
            "Lib.C.Other": SAME,
            "Lib.D.Same": SAME,
        },
        failed={"Lib.D.Same"},
    )
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"a={a}", f"b={b}") == 0
    said = capsys.readouterr().out.splitlines()
    assert said[:2] == [
        "Lib.D.Same · Python/linux: b: the run failed (Run recorded failed). No two "
        "runs of it to compare.",
        "Nothing moved in 2 tests: Lib.A.Same, Other.",
    ]
    page = (out / GALLERY).read_text(encoding="utf-8").splitlines()
    assert '<p class="said">Nothing moved in 2 tests: Lib.A.Same, Other.</p>' in page


def test_a_test_one_run_holds_is_named_on_the_console_and_the_page(
    lib, tmp_path, monkeypatch, capsys
):
    gone = [f"Lib.Gone.T{i:02d}" for i in range(14)]
    new = ["Lib.New.NewA", "Lib.New.NewB", "Lib.New.NewC"]
    _more_tests(lib, *gone, *new)
    main_run = write_python_run(
        tmp_path / "main",
        {RAMP: SAME, CSV: {"x": [1.0] * 5}, **dict.fromkeys(gone, SAME)},
    )
    fix = write_python_run(
        tmp_path / "fix",
        {RAMP: SAME, CSV: {"x": [1.0] * 5}, **dict.fromkeys(new, SAME)},
    )
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"main={main_run}", f"fix={fix}") == 0
    shown = ", ".join(f"T{i:02d}" for i in range(10))
    only = [
        f"Python/linux: 14 tests only in main, not drawn: {shown} … and 4 more.",
        "Python/linux: 3 tests only in fix, not drawn: NewA, NewB, NewC.",
    ]
    said = capsys.readouterr().out.splitlines()
    assert said[:3] == ["Nothing moved in 2 tests: ConstantCsv, SimpleRamp.", *only]
    assert said[3].startswith(f"Wrote 0 images, 0 tables and {out / GALLERY} (")
    page = (out / GALLERY).read_text(encoding="utf-8").splitlines()
    for line in only:
        assert f'<p class="said">{line}</p>' in page
    assert "<h2>" not in "\n".join(page)


def test_of_three_runs_a_test_one_holds_is_named_and_one_two_hold_is_drawn(
    lib, tmp_path, monkeypatch, capsys
):
    _more_tests(lib, "Lib.Two.Held", "Lib.New.NewA")
    a = write_python_run(tmp_path / "a", {RAMP: SAME, "Lib.Two.Held": SAME})
    b = write_python_run(tmp_path / "b", {RAMP: SAME, "Lib.New.NewA": SAME})
    c = write_python_run(tmp_path / "c", {RAMP: SAME, "Lib.Two.Held": SAME})
    out = tmp_path / "out"
    fake_playwright(monkeypatch)
    assert _plot(lib, out, f"a={a}", f"b={b}", f"c={c}") == 0
    said = capsys.readouterr().out.splitlines()
    assert said[:2] == [
        "Nothing moved in 1 test: SimpleRamp.",
        "Python/linux: 1 test only in b, not drawn: NewA.",
    ]
    page = (out / GALLERY).read_text(encoding="utf-8")
    assert "<h2><code>Held</code> · Python/linux</h2>" in page
    assert '<p class="said">b: not run.</p>' in page
    assert (
        '<p class="said">Python/linux: 1 test only in b, not drawn: NewA.</p>'
    ) in page.splitlines()


@pytest.mark.parametrize(
    ("flag", "value", "what"),
    [
        ("--test", "SimpleRamp", "a test name"),
        ("--folder", "Python/linux", "a backend/os folder"),
    ],
)
def test_a_run_given_after_test_or_folder_is_refused_not_read_as_a_name(
    lib, tmp_path, capsys, flag, value, what
):
    """`plot a=1 --test X b=2`: the flag takes several values, so b=2 was
    read as one more name. A name holds no "=": the value is refused, and
    why a run may have landed there is said, before anything is read or
    written."""
    runs = [
        write_python_run(tmp_path / label, {RAMP: {"x": [0, 2, 4, 6, 8]}})
        for label in "abc"
    ]
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as e:
        _plot(lib, out, f"a={runs[0]}", f"b={runs[1]}", flag, value, f"c={runs[2]}")
    assert e.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        f"dstf plot: error: argument {flag}: c={runs[2]}: {what} holds no '=' "
        f"(a LABEL=DIR after {flag} is read as one of its values)"
    )
    assert not out.exists()
