"""`dstf baseline plot` in a real headless Chromium: the images it writes,
the gallery that holds them with nothing fetched, a rewrite of its own
directory, and the figure's layout as plotly.js lays it out (the legend
under the x title, the notes under the legend, the title inside the
image) — measured, never by the dict alone."""

import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from conftest import GitRepo
from dstf.cli.main import main
from dstf.figures.history import HistoryTrace, history_figure
from dstf.reporting.baseline_gallery import GALLERY, MARKER
from dstf.reporting.images import PLOTLY_JS, ImagesUnavailable, Renderer

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

#: Where Playwright's browsers are, read before conftest points
#: XDG_CACHE_HOME (where Playwright looks on Linux) at a per-test folder.
_BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
)


@pytest.fixture(autouse=True)
def _browsers_where_they_are(monkeypatch):
    """The command under test launches its own Chromium inside the test."""
    if Path(_BROWSERS).is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", _BROWSERS)


def _ref(model_id, variables):
    return json.dumps(
        {
            "model_id": model_id,
            "simulation": {"stop_time": 2.0},
            "time": [0.0, 1.0, 2.0],
            "variables": [
                {"index": i, "name": n, "expression": "", "values": v}
                for i, (n, v) in enumerate(variables, start=1)
            ],
        }
    )


@pytest.fixture
def browser():
    """A Chromium of this test's own: the command under test starts its
    own Playwright, which a module-wide one (conftest's) would refuse."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def lib(tmp_path):
    """Lib.A (two variables) and Lib.B (one) changed between two commits."""
    repo = GitRepo(tmp_path / "lib")
    folder = repo.top / "ReferenceResults" / "Dymola" / "linux"
    folder.mkdir(parents=True)
    (folder / "ref_a.json").write_text(
        _ref("Lib.A", [("x", [0, 5, 10]), ("y", [1, 2, 3])])
    )
    (folder / "ref_b.json").write_text(_ref("Lib.B", [("z", [0, 1, 2])]))
    repo.one = repo.commit("one")
    (folder / "ref_a.json").write_text(
        _ref("Lib.A", [("x", [0, 6, 10]), ("y", [1, 2, 4])])
    )
    (folder / "ref_b.json").write_text(_ref("Lib.B", [("z", [0, 1, 3])]))
    repo.two = repo.commit("two")
    repo.root = repo.top / "ReferenceResults"
    return repo


def _size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def _plot(lib, out, *more):
    return main(
        ["baseline", "plot", lib.one, lib.two, *more, "-o", str(out)]
        + ["--reference-root", str(lib.root)]
    )


def test_the_images_the_gallery_and_the_marker_are_written(lib, tmp_path, capsys):
    out = tmp_path / "out"
    assert _plot(lib, out) == 0
    images = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.png"))
    assert images == [
        "A/Dymola-linux/01-x.png",
        "A/Dymola-linux/02-y.png",
        "B/Dymola-linux/01-z.png",
    ]
    for path in images:
        # two revisions and no notes: the sample's 1000 x 640
        assert _size((out / path).read_bytes()) == (1000, 640)
    page = (out / GALLERY).read_text(encoding="utf-8")
    assert page.count('src="data:image/png;base64,') == 3
    marker = json.loads((out / MARKER).read_text())
    assert sorted(marker["files"]) == sorted([*images, GALLERY])
    said = capsys.readouterr().out.strip()
    assert said.startswith(f"Wrote 3 images, 0 tables and {out / GALLERY} (")


def test_a_rewrite_of_its_own_directory_leaves_no_stale_image(lib, tmp_path):
    out = tmp_path / "out"
    assert _plot(lib, out) == 0
    assert _plot(lib, out, "--test", "B") == 0
    left = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert left == sorted([MARKER, GALLERY, "B/Dymola-linux/01-z.png"])
    assert not (out / "A").exists()


def test_the_gallery_shows_every_image_with_the_network_cut(lib, tmp_path):
    out = tmp_path / "out"
    assert _plot(lib, out) == 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        widths, asked = _open_offline(chromium, (out / GALLERY).as_uri())
        chromium.close()
    assert widths == [1000, 1000, 1000]
    assert asked == [(out / GALLERY).as_uri()]


def _open_offline(chromium, url):
    """The page's images as loaded with every request refused, and the
    requests it made."""
    page = chromium.new_page()
    asked = []

    def refuse(route):
        asked.append(route.request.url)
        if route.request.url == url:
            route.continue_()
        else:
            route.abort()

    page.route("**/*", refuse)
    page.goto(url)
    widths = page.evaluate(
        "() => [...document.images].map(i => i.complete && i.naturalWidth)"
    )
    page.close()
    return widths, asked


def _figure(n: int, notes: list[str]) -> dict:
    t = np.linspace(0, 10, 200)
    traces = []
    for i in range(n):
        y = np.sin(t) * (1 + i / 50)
        later = i > 0
        traces.append(
            HistoryTrace(
                name=f"2026-11-{i + 1:02d} abc{i:04d} " + "a subject of 48 " * 3,
                time=t,
                values=y,
                change_time=t if later else None,
                change=(y - np.sin(t)) * 100 if later else None,
                non_finite_at=np.zeros(0) if later else None,
            )
        )
    title = "SteamTurbine_nStages_Test · OpenModelica/linux · " + "cat(1, x[i]) " * 20
    return history_figure(
        title=title,
        y_title="PHS.pressurizer.drum2Phase.p",
        traces=traces,
        unit="range",
        first="2026-04-30 6ad1765",
        notes=notes,
    )


_BOXES = """() => {
  const box = s => { const r = document.querySelector(s).getBoundingClientRect();
                     return {top: r.top, bottom: r.bottom, right: r.right}; };
  const notes = [...document.querySelectorAll('.annotation')].map(
      a => a.getBoundingClientRect());
  return {legend: box('.legend'), xtitle: box('.xtitle'), title: box('.gtitle'),
          notes: notes.length > 1 ? {top: notes[1].top, bottom: notes[1].bottom} : null};
}"""


@pytest.mark.parametrize("n", [2, 5, 12])
def test_the_legend_and_the_notes_never_meet_the_axis_or_leave_the_image(n, browser):
    fig = _figure(n, ["Skipped: abc0001 (values equal).", "Renamed a → b at abc0002."])
    height = fig["layout"]["height"]
    page = browser.new_page(viewport={"width": 1000, "height": height})
    page.set_content("<!doctype html><body style='margin:0'><div id='p'></div>")
    page.add_script_tag(path=str(PLOTLY_JS))
    page.evaluate(
        "async (f) => { await Plotly.react('p', f.data, f.layout, f.config); }", fig
    )
    got = page.evaluate(_BOXES)
    page.close()
    assert got["legend"]["top"] >= got["xtitle"]["bottom"], got
    assert got["notes"]["top"] >= got["legend"]["bottom"], got
    assert got["notes"]["bottom"] <= height, got
    assert got["title"]["right"] <= 1000, got


def test_without_chromium_the_install_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "none"))
    with pytest.raises(ImagesUnavailable) as e:
        with Renderer():
            pass
    assert str(e.value).startswith("Playwright has no Chromium here: run ")
    assert str(e.value).endswith(" -m playwright install chromium")
