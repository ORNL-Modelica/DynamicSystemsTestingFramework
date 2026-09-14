"""The one browser test (§4.7): the real page, served by `dstf author`, in
Chromium.

Everything else about the shell is driven by NiceGUI's in-process user
simulation, which never renders anything. This says the page a user opens
actually works end to end: the process serves on 127.0.0.1, the row button
selects a test, the leaf row selects a leaf, Edit criteria's form fires
`on_change` from a real keystroke, and the edit reaches the Session — the
header counts two pending ops and the leaf's verdict flips to PASS — while
the spec file on disk is untouched, because nothing was saved.

Selectors: NiceGUI's `.mark()` is server-side only (`Element._markers` is
read by `ElementFilter`, never serialized to the DOM), so the row buttons
the simulation finds by marker have no DOM handle here. They are located by
role and their short name inside the left column instead.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

import time  # noqa: E402

from playwright.sync_api import expect  # noqa: E402

from test_session import A, D, _project  # noqa: E402

READY_TIMEOUT = 30.0  # seconds to wait for the server's first 200
EXPECT_TIMEOUT = 15_000  # ms; a refresh is a websocket round trip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_serving(url: str, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"dstf author exited with {proc.returncode}\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    pytest.fail(f"no 200 from {url} within {READY_TIMEOUT}s\n{log.read_text()}")


def test_the_real_page_edits_one_tolerance_and_saves_nothing(
    tmp_path, playwright_browser
):
    cfg, work = _project(tmp_path)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    log = tmp_path / "author.log"
    # The server is a plain process, not pytest: NiceGUI's `ui.run` treats an
    # inherited PYTEST_CURRENT_TEST as "I am a screen test" and demands
    # NICEGUI_SCREEN_TEST_PORT, ignoring --port.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}

    with log.open("wb") as sink:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dstf",
                "--config",
                str(cfg),
                "author",
                "--work-dir",
                str(work),
                "--port",
                str(port),
                "--no-browser",
            ],
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=env,
        )
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context()
        page = context.new_page()
        page.goto(url)

        # The page is mounted and the websocket is up once the header draws.
        pending = page.get_by_role("button", name=re.compile(r"\d+ pending"))
        expect(pending).to_contain_text("0 pending", timeout=EXPECT_TIMEOUT)

        # The test list is the first `w-1/5` column; its rows are buttons
        # named by the model's short name ("A", not "FakeLib.A").
        tests_column = page.locator("div.w-1\\/5").first
        tests_column.get_by_role(
            "button", name=A.rsplit(".", 1)[-1], exact=True
        ).click()

        # The tree row lives in the centre column: Edit tree's panel carries
        # buttons naming the same path ("wrap /metrics/children/0"), so the
        # path alone is not unique on the page. And `filter(has_text=...)`
        # rather than `get_by_role(name=re.compile(...))`: Playwright
        # serializes a regex into the role selector delimited by slashes,
        # and the leaf's path is full of them.
        centre = page.locator("div.w-3\\/5").first
        leaf = centre.get_by_role("button").filter(has_text="/metrics/children/0")
        expect(leaf).to_contain_text("FAIL", timeout=EXPECT_TIMEOUT)
        leaf.click()

        page.get_by_role("tab", name="Edit criteria").click()
        tolerance = page.get_by_label("Tolerance", exact=True)
        expect(tolerance).to_be_visible(timeout=EXPECT_TIMEOUT)
        tolerance.fill("0.5")
        tolerance.press("Tab")  # blur: Quasar emits the change NiceGUI listens for

        # The materialization op + the replace, and the rescored verdict.
        expect(pending).to_contain_text("2 pending", timeout=EXPECT_TIMEOUT)
        expect(leaf).to_contain_text("PASS", timeout=EXPECT_TIMEOUT)

        context.close()
    finally:
        proc.terminate()
        proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved


# NOT test_session's TREE: in a browser, overlapping shapes steal each other's
# drags (a scalar/max line at the same y took a drag aimed at range/max; two
# rings 6.6 px apart resized one instead of moving the other). These rings sit
# at (0.5, 0.6), (0.5, 0.3), (0.9, 1.0), (0.9, 0.8), the lines at -0.5 and
# 2.0 — the test asserts no two are within MIN_SHAPE_GAP_PX before dragging.
BROWSER_TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.5, "upper": 0.1, "lower": 0.2},
                {"time": 0.9, "upper": 0.1, "lower": 0.1},
            ],
        },
        {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 2.0},
    ],
}
MIN_SHAPE_GAP_PX = 20
RING = "/metrics/children/0/tube/upper/0"
LINE = "/metrics/children/1/range/max"

# The plot's graph div; `_fullLayout` is plotly's resolved layout, whose axes
# convert data to pixels inside the plot area (`d2p`, offset by `_offset`).
_GD = "document.querySelector('.js-plotly-plot')"
_TO_PAGE = (
    "([x, y]) => { const gd = " + _GD + "; const r = gd.getBoundingClientRect();"
    " const fl = gd._fullLayout;"
    " return [r.left + fl.xaxis._offset + fl.xaxis.d2p(x),"
    " r.top + fl.yaxis._offset + fl.yaxis.d2p(y)]; }"
)
_RANGES = (
    "() => { const fl = " + _GD + "._fullLayout;"
    " return [fl.xaxis.range.slice(), fl.yaxis.range.slice()]; }"
)
_SHAPE = "(name) => " + _GD + ".layout.shapes.find(s => s.name === name)"
_EDITREVISION = "() => " + _GD + ".layout.editrevision ?? null"


def _drag(page, start: list[float], end: list[float], moves: int = 10) -> None:
    """Press at `start`, move to `end` in `moves` steps, release — the pace
    the spike dragged at (plotly reads a shape drag from real mouse moves)."""
    page.mouse.move(*start)
    page.mouse.down()
    for i in range(1, moves + 1):
        f = i / moves
        page.mouse.move(
            start[0] + (end[0] - start[0]) * f, start[1] + (end[1] - start[1]) * f
        )
        page.wait_for_timeout(30)
    page.mouse.up()


def _wait_editrevision_change(page, before) -> None:
    page.wait_for_function(
        "(before) => (" + _GD + ".layout.editrevision ?? null) !== before",
        arg=before,
        timeout=EXPECT_TIMEOUT,
    )


def test_the_real_page_drags_a_tube_handle_and_a_range_bound(
    tmp_path, playwright_browser
):
    """A drag on the real plot is staged, rescored and redrawn in place — the
    zoom kept, one plot element — and nothing reaches the spec file."""
    cfg, work = _project(tmp_path)
    spec_file = cfg.parent / "test_spec.json"
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == D:
            entry["metrics"] = BROWSER_TREE
    spec_file.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    before = spec_file.read_bytes()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    log = tmp_path / "author.log"
    # See the test above: NiceGUI reads an inherited PYTEST_CURRENT_TEST as a
    # screen test and ignores --port.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}

    with log.open("wb") as sink:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dstf",
                "--config",
                str(cfg),
                "author",
                "--work-dir",
                str(work),
                "--port",
                str(port),
                "--no-browser",
            ],
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=env,
        )
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context()
        page = context.new_page()
        page.goto(url)

        pending = page.get_by_role("button", name=re.compile(r"\d+ pending"))
        expect(pending).to_contain_text("0 pending", timeout=EXPECT_TIMEOUT)

        tests_column = page.locator("div.w-1\\/5").first
        tests_column.get_by_role(
            "button", name=D.rsplit(".", 1)[-1], exact=True
        ).click()
        page.locator(".js-plotly-plot .main-svg").first.wait_for(timeout=EXPECT_TIMEOUT)
        page.wait_for_function(
            "(n) => { const gd = " + _GD + "; return gd && gd._fullLayout"
            " && gd.layout.shapes && gd.layout.shapes.filter("
            "s => s.name === n).length === 1; }",
            arg=RING,
            timeout=EXPECT_TIMEOUT,
        )
        page.locator(".js-plotly-plot").first.scroll_into_view_if_needed()

        # No two shapes close enough to steal each other's drags: rings by
        # their anchor, lines by their y.
        points = page.evaluate(
            "() => { const gd = " + _GD + "; const fl = gd._fullLayout;"
            " return gd.layout.shapes.map(s => s.type === 'circle'"
            " ? [s.name, fl.xaxis.d2p(s.xanchor), fl.yaxis.d2p(s.yanchor)]"
            " : [s.name, null, fl.yaxis.d2p(s.y0)]); }"
        )
        assert len(points) == 6, points  # four rings, two lines
        for i, (name_a, xa, ya) in enumerate(points):
            for name_b, xb, yb in points[i + 1 :]:
                if xa is not None and xb is not None:
                    gap = ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5
                else:
                    gap = abs(ya - yb)
                assert gap >= MIN_SHAPE_GAP_PX, (name_a, name_b, gap)

        # An x-only zoom: a flat drag at y = 1.4, where no shape is. Plotly
        # zooms x alone, so every shape stays in view. Pressed at the RIGHT
        # end: the legend sits in the plot's top-left corner, over (0.1, 1.4),
        # and a press there never starts a zoom.
        _drag(
            page,
            page.evaluate(_TO_PAGE, [0.95, 1.4]),
            page.evaluate(_TO_PAGE, [0.1, 1.4]),
        )
        page.wait_for_function(
            "() => Math.abs(" + _GD + "._fullLayout.xaxis.range[0] - 0.1) < 0.02",
            timeout=EXPECT_TIMEOUT,
        )
        zoom = page.evaluate(_RANGES)

        page.get_by_role("tab", name="Edit on plot").click()
        expect(page.get_by_text("drag a handle on the plot")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )

        # The ring, grabbed at its EXACT centre: off-centre, plotly resizes it.
        ring = page.evaluate(_SHAPE, RING)
        revision = page.evaluate(_EDITREVISION)
        _drag(
            page,
            page.evaluate(_TO_PAGE, [ring["xanchor"], ring["yanchor"]]),
            page.evaluate(_TO_PAGE, [ring["xanchor"], 0.8]),
        )
        expect(pending).to_contain_text("1 pending", timeout=EXPECT_TIMEOUT)
        _wait_editrevision_change(page, revision)
        moved = page.evaluate(_SHAPE, RING)
        assert moved["yanchor"] == pytest.approx(0.8, abs=0.02)
        after_zoom = page.evaluate(_RANGES)
        assert after_zoom[0] == pytest.approx(zoom[0], abs=1e-9)
        assert after_zoom[1] == pytest.approx(zoom[1], abs=1e-9)
        assert page.evaluate("document.querySelectorAll('.js-plotly-plot').length") == 1

        # The same ring dropped at y = 0.3, below the reference x(0.5) = 0.5:
        # a negative band width, refused. The shell re-sends the figure after
        # every shape relayout with a new `editrevision`, so the ring snaps
        # back to where the Session says it is — without that, Plotly.react
        # keeps the user's drop.
        revision = page.evaluate(_EDITREVISION)
        _drag(
            page,
            page.evaluate(_TO_PAGE, [moved["xanchor"], moved["yanchor"]]),
            page.evaluate(_TO_PAGE, [moved["xanchor"], 0.3]),
        )
        tab = page.get_by_role("tab", name="Edit on plot")
        if tab.get_attribute("aria-selected") != "true":
            tab.click()
        expect(page.get_by_text("negative")).to_be_visible(timeout=EXPECT_TIMEOUT)
        _wait_editrevision_change(page, revision)
        refused = page.evaluate(_SHAPE, RING)
        assert refused["yanchor"] == pytest.approx(0.8, abs=0.02)
        expect(pending).to_contain_text("1 pending", timeout=EXPECT_TIMEOUT)

        # The range line, grabbed mid-span: a whole-line move (y0 == y1).
        line = page.evaluate(_SHAPE, LINE)
        revision = page.evaluate(_EDITREVISION)
        _drag(
            page,
            page.evaluate(_TO_PAGE, [0.5, line["y0"]]),
            page.evaluate(_TO_PAGE, [0.5, 1.5]),
        )
        expect(pending).to_contain_text("2 pending", timeout=EXPECT_TIMEOUT)
        _wait_editrevision_change(page, revision)

        pending.click()
        drawer = page.locator(".q-drawer--right")
        expect(drawer).to_contain_text("/metrics/children/1", timeout=EXPECT_TIMEOUT)
        max_value = re.search(r'"max_value": (-?[0-9.eE+-]+)', drawer.inner_text())
        assert max_value is not None, drawer.inner_text()
        assert float(max_value.group(1)) == pytest.approx(1.5, abs=0.02)

        context.close()
    finally:
        proc.terminate()
        proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved
