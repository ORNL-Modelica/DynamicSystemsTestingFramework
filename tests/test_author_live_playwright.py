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

import json
import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from dstf.authoring.builtin.accept import PARTIAL  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import UNSCORED  # noqa: E402
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

#: Whether plot 0's Actual and Reference are shown, as plotly.js holds it:
#: a click on a legend entry leaves its trace `legendonly`.
SHOWN = """
() => document.querySelectorAll('.js-plotly-plot')[0]._fullData
  .slice(0, 2).map(t => t.visible)
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

        # A box zoom made mid-run, and a trace hidden from the legend,
        # survive the updates that follow.
        _box_zoom(page, (0.1, 0.3), (0.2, 0.8))
        zoomed = _plot(page)
        assert zoomed["xrange"] != [0.0, 1.0]
        page.locator(".js-plotly-plot .legend .traces").nth(1).locator(
            ".legendtoggle"
        ).click()
        page.wait_for_function(f"() => ({SHOWN})()[1] === 'legendonly'")
        assert page.evaluate(SHOWN) == [True, "legendonly"]
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
            assert page.evaluate(SHOWN) == [True, "legendonly"]

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

        # Accept, in the strip's menu, says why it will not — on the item.
        page.locator(".dstf-strip").get_by_role("button", name="More actions").click()
        item = page.get_by_role("menuitem", name=re.compile("^Accept"))
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(item).to_have_class(re.compile("disabled"))
        assert item.inner_text().split("\n") == ["Accept", PARTIAL]
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


def test_a_zoom_on_a_partial_result_does_not_hold_the_next_run_s_plot(
    tmp_path, playwright_browser
):
    """The plot starts over whole at a run's first figure, after a PARTIAL
    result too: a box zoom on a cancelled run's trace was kept under the
    next run's figures (one revision for every run), and the new trace grew
    outside the plot."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, hooks=LIVE_HOOKS, setup=_live(hold=7)
    )
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
        page.wait_for_function(f"() => !!(({PLOT})() || {{}}).stopped")
        assert _plot(page)["xrange"] == [0.0, 1.0]
        _box_zoom(page, (0.1, 0.3), (0.2, 0.8))
        zoomed = _plot(page)
        assert 0.05 < zoomed["xrange"][0] < zoomed["xrange"][1] < 0.35

        steer(_examples(cfg), A, hold=3)
        page.locator(".dstf-strip").get_by_role("button", name="Queue again").click()
        page.locator(".q-dialog").get_by_role("button", name="Queue again").click()
        expect(strip).to_have_text(
            "Running on worker 1 — t = 0.1 / 1 s", timeout=RUN_TIMEOUT
        )
        page.wait_for_function(
            f"() => ((({PLOT})() || {{}}).x || []).length === 3", timeout=RUN_TIMEOUT
        )
        live = _plot(page)
        assert live["stopped"] is None
        assert live["xrange"] == [0.0, 1.0], "the partial result's zoom was kept"
        assert live["yrange"] != zoomed["yrange"]
        assert _console(errors) == [], "rerun after a partial result"
    finally:
        steer(_examples(cfg), A, hold=None)
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

        # It grows with the run, and the page stays where it was scrolled to.
        where = "() => document.querySelectorAll('.dstf-plot')[1].getBoundingClientRect().top"
        top = page.evaluate(where)
        steer(_examples(cfg), A, hold=12)
        page.wait_for_function(f"() => (({watch})() || [[]])[0].length === 12")
        xs, ys = page.evaluate(watch)
        assert np.allclose(ys, times[:12] ** 2)
        assert page.evaluate(where) == top

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


def test_a_failed_run_s_reason_is_on_its_page_after_its_queue_row_is_cleared(
    tmp_path, playwright_browser
):
    """The backend's own message, one line of it in the strip and the whole
    on hover — it was on the queue's row alone, and Clear finished took it."""
    proc, _url, page, _context, errors, _cfg = _served(
        tmp_path, playwright_browser, setup=_gated(A)
    )
    said = re.compile(
        r"^Python execution failed: RuntimeError: boom in simulate — fix the "
        r"model or its settings, then queue it again\.$"
    )
    try:
        _box(page, A).click()
        _queue(page, "Queue 1")
        expect(_words(page, A)).to_have_text(
            re.compile("^failed: .*boom in simulate$"), timeout=RUN_TIMEOUT
        )
        page.get_by_role("button", name="Clear finished").click()
        expect(page.locator(".dstf-queue-row")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(_suite_status(page, A)).to_have_text("SIM_FAIL")

        page.locator(f".ag-row[row-id='{A}'] a").first.click()
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT
        )
        strip = page.locator(STRIP)
        expect(strip).to_have_text(said, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-strip")).to_have_class(re.compile("dstf-strip-red"))
        strip.hover()
        tip = page.locator(".q-tooltip", has_text="Traceback (most recent call last)")
        expect(tip).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(tip).to_contain_text("RuntimeError: boom in simulate")
        assert _console(errors) == []
    finally:
        _stop(proc)


#: A's criteria as an explicit tree — a running test's stay editable there
#: (D150) — with a table and handles on x for on-plot editing (D149).
ON_PLOT_TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "points",
            "variable": "x",
            "tolerance": 0.2,
            "points": [{"time": 0.25}, {"time": 0.6}],
        },
        {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
    ],
}

#: What the open editor says and holds, and the armed cue on its plot.
EDITOR = """
() => ({
  hint: document.querySelector('.dstf-editor .dstf-why').textContent,
  why: [...document.querySelectorAll('.dstf-editor .dstf-why')].at(-1).textContent,
  arms: document.querySelectorAll('.dstf-editor .dstf-arm').length,
  pressed: document.querySelectorAll('.dstf-arm[aria-pressed=true]').length,
  outlined: document.querySelectorAll('.dstf-plot[data-armed]').length,
  line: [...document.querySelectorAll('.dstf-plot-armed')]
    .filter(e => e.offsetParent !== null).map(e => e.textContent),
  shapes: document.querySelectorAll('.js-plotly-plot')[0].layout.shapes || [],
})
"""


def _treed(**files):
    """`_live`, with `ON_PLOT_TREE` as A's criteria."""
    live = _live(**files)

    def setup(cfg: Path) -> None:
        live(cfg)
        spec_file = cfg.parent / "test_spec.json"
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == A:
                entry["metrics"] = ON_PLOT_TREE
        spec_file.write_text(json.dumps(spec), encoding="utf-8")

    return setup


def _click_plot(page, fx: float, fy: float, *keys: str) -> None:
    """A real click at these fractions of plot 0's plot area."""
    left, top, width, height = page.evaluate(
        "() => { const gd = document.querySelectorAll('.js-plotly-plot')[0];"
        " const fl = gd._fullLayout, r = gd.getBoundingClientRect();"
        " return [r.left + fl._size.l, r.top + fl._size.t, fl._size.w, fl._size.h]; }"
    )
    page.mouse.move(left + width * fx - 12, top + height * fy - 12)
    page.mouse.move(left + width * fx, top + height * fy, steps=3)
    for key in keys:
        page.keyboard.down(key)
    page.mouse.down()
    page.mouse.up()
    for key in keys:
        page.keyboard.up(key)


def test_a_running_test_s_editor_offers_what_a_click_takes_through_every_update(
    tmp_path, playwright_browser
):
    """On-plot editing on a page nothing is scored on (D149 × D151): no
    handle is drawn and the table's place says why, so the hint offers no
    Ctrl+click and one adds nothing; a window's ⌖ is drawn, and it stays
    armed — outline, line and all — beside a box zoom while the trace
    grows under both, then takes its click. The same after a cancel."""
    proc, _url, page, _context, errors, _cfg = _served(
        tmp_path,
        playwright_browser,
        hooks=LIVE_HOOKS,
        setup=_treed(rows=400, pause=0.04, hold=300),
    )
    pending = page.locator(".dstf-header-row").get_by_role(
        "button", name=re.compile("pending edit")
    )
    try:
        _run_and_open(page)
        expect(page.locator(STRIP)).to_have_text(RUNNING_AT, timeout=RUN_TIMEOUT)
        # The strip's menu: neither action has anything to act on, and each
        # says why under its name — they read "Accept 0 results as
        # baselines" and "Queue 0 (1 already queued)".
        page.locator(".dstf-strip").get_by_role("button", name="More actions").click()
        items = page.get_by_role("menuitem")
        expect(items).to_have_count(2, timeout=EXPECT_TIMEOUT)
        assert [i.split("\n") for i in items.all_inner_texts()] == [
            ["Accept", f"{A} is running: accept it when its run has ended"],
            ["Queue", f"{A} is already running"],
        ]
        # Disabled, the name is dimmed and the sentence under it is not.
        assert (
            items.evaluate_all(
                "e => e.map(i => [i.classList.contains('disabled'),"
                " getComputedStyle(i).opacity,"
                " getComputedStyle(i.querySelector('.dstf-menu-name')).opacity,"
                " getComputedStyle(i.querySelector('.dstf-why')).color])"
            )
            == [[True, "1", "0.6", rgb(theme.DEFAULT_THEME["text-muted"])]] * 2
        )
        page.wait_for_timeout(400)  # the menu's own fade
        _keep(page, tmp_path, "live-running-menu")
        page.keyboard.press("Escape")
        expect(items).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.locator(".dstf-crit", has_text="points · x").click()
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        read = page.evaluate(EDITOR)
        assert read["hint"] == (
            "Edit the fields. Edits are staged as you make them; Save writes them."
        )
        assert read["why"] == UNSCORED.format(WAITING)
        assert read["arms"] == 2 and read["shapes"] == []  # the window's two

        # Armed and zoomed, then three updates of the growing trace.
        page.locator(".dstf-editor label", has_text="window start").locator(
            ".dstf-arm"
        ).click()
        page.locator(".js-plotly-plot").first.evaluate(
            "e => window.scrollBy(0, e.getBoundingClientRect().top - 190)"
        )
        _box_zoom(page, (0.1, 0.9), (0.2, 0.8))  # a drag is no click
        zoomed = _plot(page)
        assert zoomed["xrange"] != [0.0, 1.0]
        said = "Click the plot to set window start — Esc cancels."
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
            read = page.evaluate(EDITOR)
            assert (read["pressed"], read["outlined"]) == (1, 1), read
            assert read["hint"] == said and read["line"] == [said]
        _keep(page, tmp_path, "live-armed")

        # Ctrl+click adds nothing; the plain click sets the armed window.
        _click_plot(page, 0.7, 0.5, "Control")
        page.wait_for_timeout(600)
        expect(pending).to_have_text("0 pending edits")
        assert page.evaluate(EDITOR)["pressed"] == 1
        _click_plot(page, 0.5, 0.5)
        expect(pending).to_have_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        start = page.locator(".dstf-editor").get_by_label("window start", exact=True)
        low, high = zoomed["xrange"]
        assert abs(float(start.input_value()) - (low + high) / 2) < 0.01
        read = page.evaluate(EDITOR)
        assert (read["pressed"], read["outlined"], read["line"]) == (0, 0, [])
        assert _console(errors) == [], "running"

        # Cancelled: what it wrote is shown, never scored — the same editor.
        page.locator(".dstf-strip").get_by_role("button", name="Cancel").click()
        page.wait_for_function(
            f"() => !!(({PLOT})() || {{}}).stopped", timeout=RUN_TIMEOUT
        )
        expect(page.locator(".dstf-editor .dstf-why").last).to_have_text(
            UNSCORED.format(NOT_SCORED), timeout=EXPECT_TIMEOUT
        )
        read = page.evaluate(EDITOR)
        assert "Ctrl+click" not in read["hint"] and read["arms"] == 2
        assert [s["name"] for s in read["shapes"]] == [STOPPED]
        _click_plot(page, 0.2, 0.5, "Control")
        page.wait_for_timeout(600)
        expect(pending).to_have_text("1 pending edit")
        assert _console(errors) == [], "cancelled"
    finally:
        _stop(proc)


def _live_family(tmp_path: Path) -> tuple[Path, Path]:
    """`family_project` — A scored on x and on `pipe.T[1]` … `pipe.T[4]`,
    whose four identical criteria are one folded row — with every script
    `SIMULATE_LIVE` writing those five series, A's held at its sixth row."""
    from family_project import family_project
    from live_fake import SIMULATE_LIVE

    cfg, work = family_project(tmp_path, 4)
    examples = tmp_path / "FakeLib" / "Examples"
    series = '{"x": scale * t, "y": 2 * t, "z": t * t}'
    assert series in SIMULATE_LIVE
    family = '{"x": scale * t, **{f"pipe.T[{i}]": i * t for i in range(1, 5)}}'
    for script in examples.rglob("*.py"):
        script.write_text(SIMULATE_LIVE.replace(series, family), encoding="utf-8")
    steer(examples, A, rows=40, hold=6)
    return cfg, work


def test_a_running_family_plot_under_dark_folds_to_waiting_for_result(
    tmp_path, playwright_browser
):
    """Live results × themes × a folded row (D151, D148, D152): the test
    is queued from its own page in a tab switched to dark; its family's
    plot is the dark theme's and draws every element's rows so far; the
    folded row reads `waiting for result` in place of a count of verdicts,
    with no pill; and no word of the page is the default theme's."""
    from test_author_test_page_playwright import _served as _test_page_served

    with _test_page_served(
        tmp_path, playwright_browser, project=_live_family, hooks=LIVE_HOOKS
    ) as page:
        switch_theme(page, "dark")
        fold = page.locator(".dstf-plot").nth(1).locator(".dstf-fold")
        expect(fold).to_contain_text("3 pass · 1 fail", timeout=EXPECT_TIMEOUT)
        _queue(page, "Queue 1")
        expect(page.locator(STRIP)).to_have_text(
            "Running on worker 1 — t = 0.128 / 1 s", timeout=RUN_TIMEOUT
        )
        expect(fold.locator(".dstf-mono")).to_have_text(WAITING)
        expect(fold).to_contain_text("nrmse · pipe.T[1:4]")
        rows = page.evaluate(ROWS)
        assert rows["pills"] == 0 and rows["every"] > 0
        assert rows["readouts"] == [WAITING] * 6  # x, the fold, its four members
        dark = theme.THEMES["dark"]
        assert fold.locator(".dstf-mono").evaluate(
            "e => getComputedStyle(e).color"
        ) == rgb(dark["text"])

        # The family's plot: the theme's ground, each element's six rows.
        fold.scroll_into_view_if_needed()
        family = (
            "() => { const gd = document.querySelectorAll('.js-plotly-plot')[1];"
            " if (!gd || !gd._fullLayout) return null;"
            " return gd.data.filter(t => (t.meta || {}).kind === 'act')"
            ".map(t => t.x.length); }"
        )
        page.wait_for_function(
            f"() => JSON.stringify(({family})()) === '[6,6,6,6]'",
            timeout=EXPECT_TIMEOUT,
        )
        ground = page.evaluate(
            f"() => ({PLOT_GROUND})(document.querySelectorAll('.js-plotly-plot')[1])"
        )
        assert_dark_plot(ground)
        _keep(page, tmp_path, "live-family-dark")
