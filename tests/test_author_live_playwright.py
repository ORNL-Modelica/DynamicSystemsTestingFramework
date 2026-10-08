"""Live results in a real browser (D151): `dstf author` over the
two-package FakeLib on the Python backend made live (`live_hooks`,
`live_fake`) — a real pool, a real subprocess per run, a real DSresult file
growing a row at a time, and a test that steers how fast and how far.

What only a browser shows: the Actual trace gaining points second by
second, the time axis spanning the whole run from the first of them, a
box zoom surviving every update, the pills gone while nothing is scored,
the marker where a cancelled run stopped — in the muted token of both
themes — and Accept saying why it will not take a partial result."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from dstf.authoring.builtin.accept import PARTIAL  # noqa: E402
from dstf.authoring.live_rows import (  # noqa: E402
    NOT_AVAILABLE,
    NOT_SCORED,
    STOPPED,
    WAITING,
    seconds,
)
from dstf.reporting import theme  # noqa: E402
from live_fake import steer, write_scripts  # noqa: E402
from test_author_queue_playwright import (  # noqa: E402
    RUN_TIMEOUT,
    _console,
    _gated,
    _hold,
    _queue,
    _release,
    _row,
    _stop,
    _suite_status,
    _words,
)
from test_author_suite_playwright import (  # noqa: E402
    EXPECT_TIMEOUT,
    _box,
    _keep,
    _served,
)
from test_session import A  # noqa: E402
from test_themes_playwright import (  # noqa: E402
    PLOT_GROUND,
    assert_dark_plot,
    rgb,
    switch_theme,
)

pytestmark = pytest.mark.playwright

#: What makes the server's Python backend live.
LIVE_HOOKS = Path(__file__).with_name("live_hooks.py")

STRIP = ".dstf-strip-text"
#: A running strip that has read a row: `t = 0.42 / 1 s`.
RUNNING_AT = re.compile(r"^Running on worker 1 — t = ([\d.]+) / 1 s$")

#: Plot 0 as the browser holds it: its Actual trace, its time axis as
#: plotly.js resolved it, and the shape that marks where a run stopped.
PLOT = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  if (!gd || !gd._fullLayout) return null;
  const act = gd.data.find(t => (t.meta || {}).kind === 'act');
  const stopped = (gd.layout.shapes || []).find(s => s.name === 'stopped');
  const note = (gd.layout.annotations || []).find(a => a.name === 'stopped');
  return {
    x: act ? Array.from(act.x) : null,
    y: act ? Array.from(act.y) : null,
    xrange: gd._fullLayout.xaxis.range.map(v => +(+v).toFixed(6)),
    yrange: gd._fullLayout.yaxis.range.map(v => +(+v).toFixed(6)),
    stopped: stopped ? {x: stopped.x0, colour: stopped.line.color} : null,
    note: note ? {text: note.text, colour: note.font.color} : null,
    names: gd.data.map(t => t.name),
  };
}
"""

#: The criteria rows' readouts, the pills anyone can see, the tree's line.
ROWS = """
() => ({
  readouts: [...document.querySelectorAll('.dstf-crit .dstf-mono')]
    .map(e => e.textContent),
  pills: [...document.querySelectorAll('.dstf-test .dstf-pill')]
    .filter(e => e.offsetParent !== null).length,
  every: document.querySelectorAll('.dstf-test .dstf-pill').length,
})
"""


def _live(**files):
    """A `setup` for `_served`: `SIMULATE_LIVE` as every script, and A's
    run steered by ``files`` (`live_fake.steer`)."""

    def setup(cfg: Path) -> None:
        examples = cfg.parent.parent / "Examples"
        write_scripts(examples)
        steer(examples, A, **files)

    return setup


def _examples(cfg: Path) -> Path:
    return cfg.parent.parent / "Examples"


def _run_and_open(page) -> None:
    """Queue A from the Suite and open its page from the queue's row."""
    _box(page, A).click()
    _queue(page, "Queue 1")
    _row(page, A).get_by_role("link").click()
    expect(page).to_have_url(re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT)
    expect(page.locator(".js-plotly-plot")).to_have_count(1, timeout=EXPECT_TIMEOUT)
    page.wait_for_function(f"() => !!({PLOT})()", timeout=EXPECT_TIMEOUT)


def _plot(page) -> dict:
    return page.evaluate(PLOT)


def _points(page) -> int:
    return len(_plot(page)["x"] or [])


def _box_zoom(page, fx: tuple[float, float], fy: tuple[float, float]) -> None:
    """A real-mouse box zoom on plot 0, between these fractions of its plot
    area."""
    left, top, width, height = page.evaluate(
        "() => { const gd = document.querySelectorAll('.js-plotly-plot')[0];"
        " const fl = gd._fullLayout, r = gd.getBoundingClientRect();"
        " return [r.left + fl._size.l, r.top + fl._size.t, fl._size.w, fl._size.h]; }"
    )
    page.mouse.move(left + width * fx[0], top + height * fy[0])
    page.mouse.down()
    page.mouse.move(left + width * fx[1], top + height * fy[1], steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)


def test_a_running_test_s_trace_grows_and_ends_on_its_scored_result(
    tmp_path, playwright_browser
):
    """400 rows at 40 ms a row, held at the 300th: twelve seconds of a run
    to watch."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path,
        playwright_browser,
        hooks=LIVE_HOOKS,
        setup=_live(rows=400, pause=0.04, hold=300),
    )
    times = np.linspace(0.0, 1.0, 400)
    try:
        _run_and_open(page)
        strip = page.locator(STRIP)
        expect(strip).to_have_text(RUNNING_AT, timeout=RUN_TIMEOUT)
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text("NOT RUN")

        # The trace gains points over three consecutive seconds.
        page.wait_for_function(f"() => (({PLOT})() || {{x: []}}).x.length > 2")
        counts = [_points(page)]
        for _ in range(3):
            page.wait_for_timeout(1100)
            counts.append(_points(page))
        assert counts == sorted(set(counts)) and len(counts) == 4, counts
        assert counts[-1] < 300

        # The time axis is the whole run's from the start; no verdict stands.
        read = _plot(page)
        assert read["xrange"] == [0.0, 1.0]
        assert read["names"][:2] == ["Actual", "Reference"]
        rows = page.evaluate(ROWS)
        assert rows["readouts"] == [WAITING]
        assert rows["pills"] == 0 and rows["every"] > 0
        expect(page.locator(".dstf-tree-line")).to_contain_text(WAITING)
        _keep(page, tmp_path, "live-running")

        # A box zoom made mid-run survives the updates that follow.
        _box_zoom(page, (0.1, 0.3), (0.2, 0.8))
        zoomed = _plot(page)
        assert zoomed["xrange"] != [0.0, 1.0]
        at = _points(page)
        for _ in range(3):
            page.wait_for_function(
                f"(n) => ({PLOT})().x.length > n", arg=at, timeout=EXPECT_TIMEOUT
            )
            at = _points(page)
            kept = _plot(page)
            assert (kept["xrange"], kept["yrange"]) == (
                zoomed["xrange"],
                zoomed["yrange"],
            )

        # Held at row 300: the strip's time is the trace's last sample.
        want = seconds(float(times[299]))
        expect(strip).to_have_text(
            f"Running on worker 1 — t = {want} / 1 s", timeout=RUN_TIMEOUT
        )
        page.wait_for_function(f"() => ({PLOT})().x.length === 300")
        read = _plot(page)
        assert seconds(read["x"][-1]) == want
        assert np.allclose(read["x"], times[:300])
        assert np.allclose(read["y"], 1.25 * times[:300])
        assert _console(errors) == [], "running"

        # The end: the verdict, the pills, and the finished file's series.
        _release(cfg, A)
        expect(strip).to_have_text(re.compile(r"^Fails 1 of 1 "), timeout=RUN_TIMEOUT)
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text("FAIL")
        page.wait_for_function(f"() => ({PLOT})().x.length === 400")
        done = _plot(page)
        assert np.allclose(done["x"], times) and np.allclose(done["y"], 1.25 * times)
        # The run's zoom went with the run: the result is drawn whole.
        assert done["xrange"] == [0.0, 1.0]
        assert done["yrange"][0] <= 0.0 and done["yrange"][1] >= 1.25
        rows = page.evaluate(ROWS)
        assert rows["pills"] == rows["every"] > 0
        assert "NRMSE" in rows["readouts"][0]
        assert _console(errors) == [], "ended"
    finally:
        _stop(proc)


def test_a_cancelled_run_shows_its_partial_trace_and_is_not_accepted(
    tmp_path, playwright_browser
):
    proc, _url, page, _context, errors, _cfg = _served(
        tmp_path, playwright_browser, hooks=LIVE_HOOKS, setup=_live(hold=7)
    )
    times = np.linspace(0.0, 1.0, 21)
    try:
        _run_and_open(page)
        strip = page.locator(STRIP)
        expect(strip).to_have_text(
            "Running on worker 1 — t = 0.3 / 1 s", timeout=RUN_TIMEOUT
        )
        page.locator(".dstf-strip").get_by_role("button", name="Cancel").click()
        expect(strip).to_have_text(
            "Cancelled at t = 0.3 of 1 s — not scored", timeout=RUN_TIMEOUT
        )
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text("NOT RUN")

        # What it wrote, to where it stopped, and the marker there.
        page.wait_for_function(f"() => !!(({PLOT})() || {{}}).stopped")
        read = _plot(page)
        assert np.allclose(read["x"], times[:7])
        assert np.allclose(read["y"], 1.25 * times[:7])
        assert read["xrange"] == [0.0, 1.0]
        muted = theme.DEFAULT_THEME["text-muted"]
        assert read["stopped"] == {"x": float(times[6]), "colour": muted}
        assert read["note"] == {"text": f"{STOPPED} at t = 0.3 s", "colour": muted}
        token = page.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--dstf-text-muted').trim()"
        )
        assert token.lower() == muted.lower()
        rows = page.evaluate(ROWS)
        assert rows["readouts"] == [NOT_SCORED] and rows["pills"] == 0
        _keep(page, tmp_path, "live-cancelled")

        # Accept, in the strip's menu, says why it will not.
        page.locator(".dstf-strip").get_by_role("button", name="More actions").click()
        item = page.get_by_role("menuitem", name=re.compile("^Accept 0 results"))
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(item).to_have_class(re.compile("disabled"))
        item.hover()
        expect(page.locator(".q-tooltip", has_text=PARTIAL)).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        page.keyboard.press("Escape")

        # Dark: the plot's ground and grid, and the marker in ITS muted text.
        switch_theme(page, "dark")
        dark = theme.THEMES["dark"]
        page.wait_for_function(
            f"(want) => ((({PLOT})() || {{}}).stopped || {{}}).colour === want",
            arg=dark["text-muted"],
            timeout=EXPECT_TIMEOUT,
        )
        ground = page.evaluate(
            f"() => ({PLOT_GROUND})(document.querySelectorAll('.js-plotly-plot')[0])"
        )
        assert_dark_plot(ground)
        drawn = page.evaluate(
            "() => [...document.querySelectorAll('.js-plotly-plot .shapelayer path')]"
            ".map(p => getComputedStyle(p).stroke)"
        )
        assert rgb(dark["text-muted"]) in drawn
        assert _plot(page)["note"]["colour"] == dark["text-muted"]
        _keep(page, tmp_path, "live-cancelled-dark")
        assert _console(errors) == [], "cancelled"

        # The Suite row keeps the queue's word.
        page.get_by_role("link", name="← Suite").click()
        expect(_suite_status(page, A)).to_have_text("CANCELLED", timeout=EXPECT_TIMEOUT)
        expect(_words(page, A)).to_have_text("cancelled")
    finally:
        _stop(proc)


def test_a_watch_plot_added_mid_run_draws_live_and_the_dark_page_is_dark(
    tmp_path, playwright_browser
):
    """`z` is in the growing file and tracked by nothing: the Variables
    drawer lists it while the test runs, and its watch plot draws what has
    been written so far."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, hooks=LIVE_HOOKS, setup=_live(hold=5)
    )
    times = np.linspace(0.0, 1.0, 21)
    try:
        _run_and_open(page)
        strip = page.locator(STRIP)
        expect(strip).to_have_text(
            "Running on worker 1 — t = 0.2 / 1 s", timeout=RUN_TIMEOUT
        )
        page.locator(".dstf-rail").get_by_role("button", name="Vars").click()
        z = page.locator(".dstf-var-row").filter(
            has=page.locator(".dstf-var-text", has_text=re.compile("^z$"))
        )
        expect(z).to_have_count(1, timeout=EXPECT_TIMEOUT)
        z.hover()
        z.locator(".dstf-var-new").click()
        cards = page.locator(".dstf-plot")
        expect(cards).to_have_count(2, timeout=EXPECT_TIMEOUT)
        cards.nth(1).scroll_into_view_if_needed()
        expect(page.locator(".js-plotly-plot")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        watch = (
            "() => { const gd = document.querySelectorAll('.js-plotly-plot')[1];"
            " const act = gd && gd.data && gd.data.find("
            "t => (t.meta || {}).kind === 'act');"
            " return act ? [Array.from(act.x), Array.from(act.y)] : null; }"
        )
        page.wait_for_function(f"() => (({watch})() || [[]])[0].length === 5")
        xs, ys = page.evaluate(watch)
        assert np.allclose(xs, times[:5]) and np.allclose(ys, times[:5] ** 2)

        # It grows with the run.
        steer(_examples(cfg), A, hold=12)
        page.wait_for_function(f"() => (({watch})() || [[]])[0].length === 12")
        xs, ys = page.evaluate(watch)
        assert np.allclose(ys, times[:12] ** 2)

        # The running page under the dark theme: the plot is the theme's.
        page.locator(".dstf-rail").get_by_role("button", name="Vars").click()
        switch_theme(page, "dark")
        cards.nth(0).scroll_into_view_if_needed()
        page.wait_for_function(
            "(want) => { const gd = document.querySelectorAll('.js-plotly-plot')[0];"
            " return !!gd._fullLayout && gd._fullLayout.paper_bgcolor === want; }",
            arg=theme.THEMES["dark"]["surface"],
            timeout=EXPECT_TIMEOUT,
        )
        ground = page.evaluate(
            f"() => ({PLOT_GROUND})(document.querySelectorAll('.js-plotly-plot')[0])"
        )
        assert_dark_plot(ground)
        assert len(_plot(page)["x"]) == 12
        _keep(page, tmp_path, "live-running-dark")
        assert _console(errors) == [], "watch plot"
    finally:
        _release(cfg, A)
        _stop(proc)


def test_a_backend_without_a_live_view_says_so_under_the_strip(
    tmp_path, playwright_browser
):
    """The Python backend as it is: its result file is written once, at
    the end."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, setup=_gated()
    )
    try:
        _hold(cfg, A)
        _run_and_open(page)
        expect(page.locator(STRIP)).to_have_text(
            re.compile(r"^Running on worker 1 — \d+ s$"), timeout=RUN_TIMEOUT
        )
        note = page.locator(".dstf-strip-note")
        expect(note).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(note).to_have_text(NOT_AVAILABLE)
        expect(page.locator(".dstf-strip-note")).to_have_count(1)
        # its plot stays, the baseline drawn; no verdict stands
        read = _plot(page)
        assert read["x"] is None and read["names"][0] == "Reference"
        rows = page.evaluate(ROWS)
        assert rows["readouts"] == [WAITING] and rows["pills"] == 0
        _keep(page, tmp_path, "live-not-available")

        _release(cfg, A)
        expect(page.locator(STRIP)).to_have_text(
            re.compile(r"^Fails 1 of 1 "), timeout=RUN_TIMEOUT
        )
        expect(note).to_be_hidden()
        assert _console(errors) == []
    finally:
        _stop(proc)


def test_the_running_page_at_1024_keeps_its_words_on_the_page(
    tmp_path, playwright_browser
):
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path,
        playwright_browser,
        hooks=LIVE_HOOKS,
        setup=_live(hold=5),
        width=1024,
    )
    try:
        _run_and_open(page)
        expect(page.locator(STRIP)).to_have_text(
            "Running on worker 1 — t = 0.2 / 1 s", timeout=RUN_TIMEOUT
        )
        page.wait_for_function(f"() => ((({PLOT})() || {{}}).x || []).length === 5")
        # nothing is wider than the window, and the strip is one piece
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )
        box = page.locator(".dstf-strip").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 1024
        _keep(page, tmp_path, "live-running-1024")
        assert _console(errors) == []
    finally:
        _release(cfg, A)
        _stop(proc)
