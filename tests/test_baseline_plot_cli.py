"""`dstf baseline plot` from the command line, without a browser: each
refusal is one sentence and exit 2, and none writes anything. The images
themselves are `test_baseline_plot_playwright.py`'s."""

import json
import sys
import types

import pytest

from conftest import GitRepo, resaved_single
from dstf.cli.main import main
from dstf.reporting.baseline_gallery import GALLERY, MARKER


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


def _lib(tmp_path, variables=1, unchanged=0):
    """A repository whose ReferenceResults changed Lib.A between two commits:
    ``variables`` that moved, and ``unchanged`` ones that did not."""
    repo = GitRepo(tmp_path / "lib")
    folder = repo.top / "ReferenceResults" / "Dymola" / "linux"
    folder.mkdir(parents=True)
    names = [f"x{i}" for i in range(variables)]
    same = [(f"same{i}", [1, 2, 3]) for i in range(unchanged)]
    (folder / "ref_a.json").write_text(
        _ref("Lib.A", [(n, [0, 5, 10]) for n in names] + same)
    )
    repo.one = repo.commit("one")
    (folder / "ref_a.json").write_text(
        _ref("Lib.A", [(n, [0, 6, 10]) for n in names] + same)
    )
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


def test_over_300_images_in_the_folders_named_is_refused_too(tmp_path, capsys):
    lib = _lib(tmp_path, variables=301)
    out = tmp_path / "out"
    assert _plot(lib, out, "--folder", "Dymola/linux") == 2
    assert (
        capsys.readouterr()
        .err.strip()
        .startswith("Error: 301 images to draw, over 300:")
    )
    assert not out.exists()


def test_the_300_counted_are_the_images_drawn_not_the_variables(
    tmp_path, capsys, monkeypatch
):
    """Only a variable that moved is drawn: 5 that moved beside 400 that
    did not are 5 images, past the guard to the browser (none here)."""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    lib = _lib(tmp_path, variables=5, unchanged=400)
    assert _plot(lib, tmp_path / "out") == 2
    assert "drawing images needs Playwright" in capsys.readouterr().err


def test_the_help_names_every_scale_the_panel_may_read(capsys):
    """A constant reads its change as % of its value, a series of zeros as
    the difference itself: the help said only "% of range"."""
    with pytest.raises(SystemExit):
        main(["baseline", "plot", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "% of value" in text
    assert "the absolute difference" in text


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


def _fake_playwright(monkeypatch, stop_at=None):
    """Playwright faked at its edge: a browser whose screenshot is a few
    bytes, and is interrupted (Ctrl-C) at the ``stop_at``-th."""
    shots = []

    class Page:
        def route(self, *a):
            pass

        def set_content(self, *a):
            pass

        def add_script_tag(self, **k):
            pass

        def set_viewport_size(self, *a):
            pass

        def evaluate(self, *a):
            pass

        def locator(self, *a):
            return self

        def screenshot(self, **k):
            shots.append(1)
            if len(shots) == stop_at:
                raise KeyboardInterrupt
            return b"\x89PNG fake"

    class Browser:
        def new_page(self):
            return Page()

        def close(self):
            pass

    class Running:
        chromium = types.SimpleNamespace(launch=lambda: Browser())

        def start(self):
            return self

        def stop(self):
            pass

    fake = types.ModuleType("playwright.sync_api")
    fake.Error = type("Error", (Exception,), {})
    fake.sync_playwright = Running
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)


def _files(out):
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def test_a_run_interrupted_mid_render_leaves_a_directory_the_next_run_may_write(
    tmp_path, monkeypatch, capsys
):
    """Interrupted after its first image, a run had removed its old marker
    and written no new one: the rerun refused the images as not its own."""
    lib = _lib(tmp_path, variables=3)
    out = tmp_path / "out"
    planned = [f"A/Dymola-linux/0{i}-x{i - 1}.png" for i in (1, 2, 3)]
    _fake_playwright(monkeypatch, stop_at=2)
    with pytest.raises(KeyboardInterrupt):
        _plot(lib, out)
    assert _files(out) == sorted([MARKER, planned[0]])
    listed = json.loads((out / MARKER).read_text())["files"]
    assert sorted(listed) == sorted([*planned, GALLERY])

    _fake_playwright(monkeypatch)
    assert _plot(lib, out) == 0
    assert _files(out) == sorted([MARKER, GALLERY, *planned])


def test_a_baseline_saved_again_at_single_precision_draws_nothing(tmp_path, capsys):
    """A real baseline accepted again at single precision, its numbers
    kept: no image (no browser is started), and the page names its
    variables as the rounding of the saved digits."""
    repo = GitRepo(tmp_path / "lib")
    file = repo.top / "ReferenceResults" / "Dymola" / "windows" / "ref_a.json"
    file.parent.mkdir(parents=True)
    old, new = resaved_single()
    file.write_text(json.dumps(old))
    one = repo.commit("one")
    file.write_text(json.dumps(new))
    two = repo.commit("two")
    out = tmp_path / "out"
    code = main(
        ["baseline", "plot", one, two, "-o", str(out)]
        + ["--reference-root", str(repo.top / "ReferenceResults")]
    )
    assert code == 0
    assert capsys.readouterr().out.startswith(
        f"Wrote 0 images, 1 table and {out / GALLERY} ("
    )
    assert sorted(p.name for p in out.rglob("*")) == [MARKER, GALLERY]
    page = (out / GALLERY).read_text(encoding="utf-8")
    # constants: in the value table, the rounding of the saved digits 0 %
    assert (
        "<tr><td><code>unitTests.x[3]</code></td><td>615.5131</td><td>615.5131</td>"
        "<td>0 %</td></tr>"
    ) in page.splitlines()
    assert "rounding of the saved digits only" not in page
