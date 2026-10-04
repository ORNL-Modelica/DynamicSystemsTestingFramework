"""The Test page in Chromium, served by `dstf author` (brief §3.2; roadmap
§3). NiceGUI's user simulation renders nothing and runs no Plotly, so this
is where the page's geometry and its gestures are said: the sticky test
header stacks under the shared one instead of beside it, a card's plot is
drawn when it is scrolled to and not before, a criterion's editor opens
BELOW its row with the plot above it as its editing surface, a ring dragged
there stages an edit while the zoom stays, a drag of another leaf's bound
line is refused in the editor and the picture snaps back, and Focus shows one
plot of seven.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes and by role and name.

The plots do NOT zoom on the wheel (`figures.plotly._config`: `scrollZoom`
is false for the tool's figure), which is what makes a wheel over a plot
scroll the PAGE — the gesture that draws the cards below."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from playwright.sync_api import expect  # noqa: E402

from test_author_suite_playwright import (  # noqa: E402
    EXPECT_TIMEOUT,
    READY_TIMEOUT,
    _start,
    _wait_until_serving,
)
from test_interactive_figures import UNDER_TOOLBAR  # noqa: E402
from test_session import (  # noqa: E402
    LONG,
    LONG_WATCH,
    A,
    C,
    _drawer_project,
    _long_names_project,
    _page_project,
    _stats_project,
    _two_var_project,
)

VIEWPORT = {"width": 1280, "height": 900}
#: The zoom set before a criterion opens and read back after: the tool's
#: figure pins `uirevision`, and its `config` never depends on what is being
#: edited (decision 7), so opening an editor is a `Plotly.react` that keeps
#: it. A `newPlot` — which is what a `config` that moved would cost — does
#: not.
ZOOM_X = [0.1, 0.8]
#: The rings live at y 0.3 and 0.6; this keeps them apart on screen and the
#: dragged one inside the plot.
ZOOM_Y = [0.0, 1.4]
#: The range leaf's upper bound; a line has to be INSIDE the plot to be
#: dragged, and Plotly does not autorange over shapes.
LINE_Y = [-1.5, 2.5]

TUBE = "/metrics/children/0"
RANGE_X = "/metrics/children/1"
MAX_LINE = f"{RANGE_X}/range/max"
MIN_LINE = f"{RANGE_X}/range/min"


@contextmanager
def _served(
    tmp_path: Path,
    playwright_browser,
    x_error: Callable[[float], float] | None = None,
    project: Callable[[Path], tuple[Path, Path]] = _page_project,
    hooks: bool = False,
):
    """`dstf author` over ``project`` (`_page_project` unless said), with
    `/test/FakeLib.A` open. On the way out: assertion 7 — no page error, no
    console error, and the spec file byte-for-byte as it was (nothing is
    saved by looking). ``x_error`` replaces x's error against its baseline
    (`_set_x_error`); ``hooks`` serves `author_hooks.py`'s routes."""
    cfg, work = project(tmp_path)
    if x_error is not None:
        _set_x_error(work, x_error)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log, hooks=hooks)
    context = None
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        problems: list[str] = []
        page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda m: (
                problems.append(f"console: {m.text}") if m.type == "error" else None
            ),
        )
        page.goto(f"{url}test/{A}")
        expect(page.locator(".dstf-subheader")).to_contain_text(
            A, timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".js-plotly-plot").first).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        yield page
        assert problems == []
    finally:
        if context is not None:
            context.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(READY_TIMEOUT)
    assert spec_file.read_bytes() == before


def _set_x_error(work: Path, error: Callable[[float], float]) -> None:
    """Rewrite x's result so its error against the baseline (x = t) is
    ``error(t)`` instead of ``0.25 * t``."""
    for key in ("test_0001", "test_0002"):
        path = work / key / "result.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["variables"]["x"] = [v + error(v) for v in data["time"]]
        path.write_text(json.dumps(data), encoding="utf-8")


def _shot(page, tmp_path: Path, name: str) -> None:
    """Viewport-sized, never full page (the global image rules). With
    `DSTF_KEEP_SHOTS` set it is also copied where the gate can read it."""
    path = tmp_path / f"task8-{name}.png"
    page.screenshot(path=str(path))
    keep = os.environ.get("DSTF_KEEP_SHOTS")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(keep) / path.name)


def _pending(page) -> str:
    return (
        page.locator(".dstf-header-row")
        .get_by_role("button", name=re.compile(r"pending edit"))
        .inner_text()
    )


def _crit(page, index: int, name: str):
    """The criteria row of plot ``index`` whose text names ``name``."""
    return (
        page.locator(".dstf-plot")
        .nth(index)
        .locator(".dstf-crit")
        .filter(has_text=name)
    )


def _swatch(page, index: int, name: str):
    """The swatch on that criteria row: its drawing on the plot, on or off."""
    return _crit(page, index, name).locator(".dstf-swatch")


def _open_crit(page, index: int, name: str) -> dict:
    """Click a criteria row open and return its box. The click lands left of
    the node's controls: a click there is the controls', not the row's."""
    row = _crit(page, index, name)
    box = row.bounding_box()
    assert box is not None
    row.click(position={"x": 30.0, "y": box["height"] / 2})
    return box


# -- the page's own JavaScript --------------------------------------------
#
# Plotly is reached the way the page reaches it (NiceGUI's import map), and
# every geometric read goes through the axis objects: a ring is grabbed at
# its CENTRE, because an off-centre grab is a pixel RESIZE that stages
# nothing.

ZOOM_AND_STAMP = """
async ([xrange, yrange]) => {
  const {Plotly} = await import('nicegui-plotly');
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  gd.__dstfStamp = 'before-open';
  await Plotly.relayout(gd, {'xaxis.range': xrange, 'yaxis.range': yrange});
}
"""

RELAYOUT_Y = """
async (yrange) => {
  const {Plotly} = await import('nicegui-plotly');
  await Plotly.relayout(document.querySelectorAll('.js-plotly-plot')[0], {'yaxis.range': yrange});
}
"""

READ_PLOT = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout;
  return {
    stamp: gd.__dstfStamp,
    xrange: fl.xaxis.range.slice(),
    yrange: fl.yaxis.range.slice(),
    shapes: (fl.shapes || []).map(s => [s.type, s.name]),
  };
}
"""

#: The first ring whose pixel centre really belongs to a shape: a grab that
#: lands on anything drawn over the ring drags nothing.
FIND_RING = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, rect = gd.getBoundingClientRect();
  const xa = fl.xaxis, ya = fl.yaxis;
  const shapes = fl.shapes || [];
  for (let i = 0; i < shapes.length; i++) {
    const s = shapes[i];
    if (s.type !== 'circle') continue;
    const cx = rect.left + xa._offset + xa.l2p(s.xanchor);
    const cy = rect.top + ya._offset + ya.l2p(s.yanchor);
    const el = document.elementFromPoint(cx, cy);
    if (el && el.closest('g.shapelayer')) {
      return {name: s.name, index: i, cx: cx, cy: cy, yanchor: s.yanchor};
    }
  }
  return null;
}
"""

#: Every visible ring inside the zoomed ranges, and whether its pixel centre
#: hits the shape layer: the legend above the plot area and the vertical
#: toolbar on its right edge must cover none of them.
ALL_RINGS = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, rect = gd.getBoundingClientRect();
  const xa = fl.xaxis, ya = fl.yaxis;
  const inside = (v, r) => v >= Math.min(r[0], r[1]) && v <= Math.max(r[0], r[1]);
  return (fl.shapes || [])
    .filter(s => s.type === 'circle' && s.visible !== false
      && inside(s.xanchor, xa.range) && inside(s.yanchor, ya.range))
    .map(s => {
      const cx = rect.left + xa._offset + xa.l2p(s.xanchor);
      const cy = rect.top + ya._offset + ya.l2p(s.yanchor);
      const el = document.elementFromPoint(cx, cy);
      return [s.name, !!(el && el.closest('g.shapelayer'))];
    });
}
"""

#: A bound LINE is a one-and-a-half pixel target, so the hit test sweeps a
#: few pixels either side of it and a few positions along it.
FIND_LINE = """
(name) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, rect = gd.getBoundingClientRect();
  const xa = fl.xaxis, ya = fl.yaxis;
  const shapes = fl.shapes || [];
  const i = shapes.findIndex(s => s.name === name);
  if (i < 0) return null;
  const y0 = shapes[i].y0;
  const cy0 = rect.top + ya._offset + ya.l2p(y0);
  for (const fx of [0.5, 0.35, 0.65, 0.25, 0.75]) {
    const cx = rect.left + xa._offset + xa._length * fx;
    for (const dy of [0, -1, 1, -2, 2, -3, 3]) {
      const el = document.elementFromPoint(cx, cy0 + dy);
      if (el && el.closest('g.shapelayer')) {
        return {name: name, index: i, cx: cx, cy: cy0 + dy, y0: y0};
      }
    }
  }
  return null;
}
"""

#: What the page DREW, read twice over: the figure Plotly holds
#: (`_fullLayout`) and the SVG path itself, back through `yaxis.p2l`. A
#: snap-back that only reached the first would leave the line where the
#: mouse left it.
READ_LINE = """
(name) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, ya = fl.yaxis;
  const shapes = fl.shapes || [];
  const i = shapes.findIndex(s => s.name === name);
  const path = document.querySelector('g.shapelayer path[data-index="' + i + '"]');
  let drawn = null;
  if (path) {
    const m = /M\\s*(-?[0-9.]+)\\s*,\\s*(-?[0-9.]+)/.exec(path.getAttribute('d'));
    if (m) drawn = ya.p2l(parseFloat(m[2]) - ya._offset);
  }
  return {
    y0: i < 0 ? null : shapes[i].y0,
    drawn: drawn,
    xrange: fl.xaxis.range.slice(),
    yrange: ya.range.slice(),
  };
}
"""

SHAPE_IS = """
([name, value, tol]) => {
  const s = (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout.shapes || []).find(x => x.name === name);
  return !!s && Math.abs((s.y0 === undefined ? s.yanchor : s.y0) - value) <= tol;
}
"""

SHAPE_ABOVE = """
([name, value]) => {
  const s = (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout.shapes || []).find(x => x.name === name);
  return !!s && s.yanchor > value;
}
"""


def _drag(page, x: float, y: float, dy: float) -> None:
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + dy, steps=12)
    page.mouse.up()


# -- the status strip (Q2 A) -------------------------------------------------


def test_the_strips_menu_says_it_is_open_and_why_an_item_is_grey(
    tmp_path, playwright_browser
):
    """C's last run never finished it: Accept is in the strip's ⋮, greyed,
    and a real mouse over it reads why. The ⋮ says whether its menu is
    open, as the header's does."""
    with _served(tmp_path, playwright_browser, project=_two_var_project) as page:
        page.goto(page.url.split("test/")[0] + f"test/{C}")
        expect(page.locator(".dstf-strip-text")).to_have_text(
            "No result yet — rerun to simulate it.", timeout=EXPECT_TIMEOUT
        )
        more = page.locator('.dstf-strip button[aria-label="More actions"]')
        expect(more).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert more.get_attribute("aria-haspopup") == "menu"
        expect(more).to_have_attribute("aria-expanded", "false")
        more.click()
        expect(more).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        item = page.get_by_role("menuitem").filter(has_text="Accept")
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert item.get_attribute("aria-disabled") == "true"
        box = item.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        expect(page.locator(".q-tooltip")).to_have_text(
            "re-run before accepting", timeout=EXPECT_TIMEOUT
        )
        page.keyboard.press("Escape")
        expect(more).to_have_attribute("aria-expanded", "false", timeout=EXPECT_TIMEOUT)


def test_the_strips_preview_opens_the_drawer_on_this_tests_section(
    tmp_path, playwright_browser
):
    """Q6 A: Preview and save opens the pending drawer — the preview — with
    this test's section scrolled to the drawer's top. D's edits are staged
    first and G's last, so A's section is the middle one of three, below
    the fold of a 600 px window until the drawer scrolls to it."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        page.set_viewport_size({"width": 1280, "height": 600})
        base = page.url.split("test/")[0]
        for model in ("FakeLib.D", A, "FakeLib.G"):
            with urllib.request.urlopen(f"{base}__stage?model={model}") as r:
                json.loads(r.read())
        expect(page.locator(".dstf-strip-text")).to_contain_text(
            "unsaved", timeout=EXPECT_TIMEOUT
        )
        page.get_by_role("button", name="Preview and save").click()
        drawer = page.locator(".dstf-pending-drawer")
        expect(drawer).to_contain_text("6 pending edits · 3 tests")
        sections = drawer.locator(".dstf-drawer-section")
        expect(sections).to_have_count(3, timeout=EXPECT_TIMEOUT)
        expect(sections.nth(1)).to_contain_text(A)
        content = page.locator(".q-drawer--right .q-drawer__content")
        page.wait_for_function(
            "document.querySelector('.q-drawer--right .q-drawer__content')"
            ".scrollTop > 0",
            timeout=EXPECT_TIMEOUT,
        )
        page.wait_for_timeout(400)  # the drawer's slide
        header = page.locator(".q-header").bounding_box()
        top = content.bounding_box()
        box = sections.nth(1).bounding_box()
        assert header is not None and top is not None and box is not None
        assert box["y"] >= header["y"] + header["height"] - 1, (header, box)
        # Its top at the drawer's: scrolled to its START, not merely into view.
        assert abs(box["y"] - top["y"]) <= 2, (top, box)
        assert content.evaluate("e => e.scrollHeight > e.clientHeight")


def _drawer_open(page) -> bool:
    box = page.locator(".dstf-pending-drawer").bounding_box()
    width = page.viewport_size["width"]
    return box is not None and box["x"] < width - 1


@pytest.mark.parametrize("width", [900, 1280])
def test_the_pending_drawer_closes_by_its_button_and_by_escape(
    tmp_path, playwright_browser, width
):
    """Under 1024 px the drawer is an overlay over the header and the strip:
    its own close button and Esc take it away, at either width. Its line
    under each test reads as one sentence as rendered — `innerText`, what a
    copy or a screen reader takes, not only its text nodes — the separator
    spaced."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        page.set_viewport_size({"width": width, "height": 800})
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={A}") as r:
            json.loads(r.read())
        expect(page.locator(".dstf-strip-text")).to_contain_text(
            "unsaved", timeout=EXPECT_TIMEOUT
        )
        preview = page.get_by_role("button", name="Preview and save")
        preview.click()
        drawer = page.locator(".dstf-pending-drawer")
        status = drawer.locator(".dstf-drawer-status")
        expect(status).to_have_count(1, timeout=EXPECT_TIMEOUT)
        assert status.inner_text() == "2 edits · verdict stays FAIL"
        page.wait_for_timeout(400)  # the drawer's slide
        assert _drawer_open(page)
        _shot(page, tmp_path, f"pending-drawer-{width}")
        page.keyboard.press("Escape")
        page.wait_for_function(
            f"(() => {{ const b = document.querySelector('.dstf-pending-drawer')"
            f".getBoundingClientRect(); return b.x >= {width - 1}; }})()",
            timeout=EXPECT_TIMEOUT,
        )
        _shot(page, tmp_path, f"pending-drawer-{width}-after-esc")
        preview.click()
        close = drawer.get_by_role("button", name="Close", exact=True)
        expect(close).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(400)
        assert _drawer_open(page)
        close.click()
        page.wait_for_function(
            f"(() => {{ const b = document.querySelector('.dstf-pending-drawer')"
            f".getBoundingClientRect(); return b.x >= {width - 1}; }})()",
            timeout=EXPECT_TIMEOUT,
        )


def _keydown_listeners(page) -> int:
    """The keydown listeners on `document`, read over CDP."""
    cdp = page.context.new_cdp_session(page)
    doc = cdp.send("Runtime.evaluate", {"expression": "document"})
    got = cdp.send(
        "DOMDebugger.getEventListeners", {"objectId": doc["result"]["objectId"]}
    )
    return sum(1 for listener in got["listeners"] if listener["type"] == "keydown")


def test_the_pending_drawers_escape_is_one_listener_per_page_load(
    tmp_path, playwright_browser
):
    """Three opens, three Escs: the Esc listener is added once, on the first
    open, never once per open."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={A}") as r:
            json.loads(r.read())
        expect(page.locator(".dstf-strip-text")).to_contain_text(
            "unsaved", timeout=EXPECT_TIMEOUT
        )
        preview = page.get_by_role("button", name="Preview and save")
        before = _keydown_listeners(page)
        for _ in range(3):
            preview.click()
            page.wait_for_function(
                "document.querySelector('.dstf-pending-drawer')"
                ".getBoundingClientRect().x < window.innerWidth - 1",
                timeout=EXPECT_TIMEOUT,
            )
            page.keyboard.press("Escape")
            page.wait_for_function(
                "document.querySelector('.dstf-pending-drawer')"
                ".getBoundingClientRect().x >= window.innerWidth - 1",
                timeout=EXPECT_TIMEOUT,
            )
        assert _keydown_listeners(page) - before == 1


def test_escape_on_a_menu_or_a_dialog_over_the_pending_drawer_closes_only_it(
    tmp_path, playwright_browser
):
    """With the drawer open at 1280 px, the strip's ⋮ menu beside it: one Esc
    closes the menu, and the drawer stays. Then the file changed on disk,
    Save these: the stale-file dialog opens over the drawer, and one Esc
    closes the dialog — Quasar's — with the drawer still open under it.
    A second Esc 40 ms after the first, while the menu or the dialog is
    still fading out, is the drawer's: it closes."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={A}") as r:
            json.loads(r.read())
        expect(page.locator(".dstf-strip-text")).to_contain_text(
            "unsaved", timeout=EXPECT_TIMEOUT
        )
        page.get_by_role("button", name="Preview and save").click()
        page.wait_for_timeout(400)  # the drawer's slide
        assert _drawer_open(page)
        more = page.locator('.dstf-strip button[aria-label="More actions"]')
        more.click()
        expect(more).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        page.keyboard.press("Escape")
        expect(more).to_have_attribute("aria-expanded", "false", timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(400)  # a drawer Esc closed would have slid
        assert _drawer_open(page)
        more.click()
        expect(more).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        _two_escapes_close_the_drawer(page)
        page.get_by_role("button", name="Preview and save").click()
        page.wait_for_timeout(400)
        assert _drawer_open(page)
        spec = next(tmp_path.rglob("test_spec.json"))
        original = spec.read_bytes()
        spec.write_bytes(original + b"\n")
        try:
            page.get_by_role("button", name="Save these").click()
            dialog = page.get_by_text("changed on disk since it was loaded")
            expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
            _shot(page, tmp_path, "stale-dialog-over-drawer")
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
            page.wait_for_timeout(400)  # a drawer Esc closed would have slid
            assert _drawer_open(page)
            _shot(page, tmp_path, "stale-dialog-after-esc")
            page.get_by_role("button", name="Save these").click()
            expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
            _two_escapes_close_the_drawer(page)
            expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
        finally:
            spec.write_bytes(original)


def _two_escapes_close_the_drawer(page) -> None:
    """Esc, and Esc again 40 ms later: the first closes the menu or the
    dialog, which is still fading out (~300 ms (Quasar's default
    transition-duration)) when the second lands."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(40)
    page.keyboard.press("Escape")
    page.wait_for_function(
        "document.querySelector('.dstf-pending-drawer')"
        ".getBoundingClientRect().x >= window.innerWidth - 1",
        timeout=EXPECT_TIMEOUT,
    )


def test_a_dialog_still_open_under_a_fading_menu_keeps_the_drawers_escape(
    tmp_path, playwright_browser
):
    """The drawer open, the strip's ⋮ menu over it, and a dialog open under
    the menu (a stand-in `.q-dialog`: a select's menu inside the
    Change-backend dialog is the real case). Esc closes the menu; a second
    Esc 40 ms later, while the menu fades out, finds the dialog still open —
    it is the dialog's, and the drawer stays."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={A}") as r:
            json.loads(r.read())
        expect(page.locator(".dstf-strip-text")).to_contain_text(
            "unsaved", timeout=EXPECT_TIMEOUT
        )
        page.get_by_role("button", name="Preview and save").click()
        page.wait_for_timeout(400)  # the drawer's slide
        assert _drawer_open(page)
        more = page.locator('.dstf-strip button[aria-label="More actions"]')
        more.click()
        expect(more).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        page.evaluate(
            """() => { const d = document.createElement('div');
            d.className = 'q-dialog dstf-stand-in';
            d.innerHTML = '<div class="q-dialog__inner"></div>';
            document.body.appendChild(d); }"""
        )
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(40)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)  # a drawer Esc closed would have slid
            assert _drawer_open(page)
        finally:
            page.evaluate("() => document.querySelector('.dstf-stand-in').remove()")


# -- the page's frame, its plots on scroll, and the tree -------------------


def test_the_test_page_stacks_its_header_draws_on_scroll_and_jumps(
    tmp_path, playwright_browser
):
    with _served(tmp_path, playwright_browser) as page:
        # Read before anything scrolls: the observer fires once per slot, so
        # "at load" cannot be read back after assertion 1's scroll.
        page.wait_for_timeout(700)
        slots = page.locator(".dstf-plot-slot").count()
        drawn_at_load = page.locator(".js-plotly-plot").count()
        first_card = page.locator(".dstf-plot").first.bounding_box()

        # 1. The sub-header is a second ROW of the shared header (decision
        #    8), not a column beside it, and it is sticky.
        row = page.locator(".dstf-header-row").bounding_box()
        sub = page.locator(".dstf-subheader").bounding_box()
        assert row is not None and sub is not None and first_card is not None
        assert sub["y"] >= row["y"] + row["height"] - 1, (row, sub)
        assert abs(sub["x"] - row["x"]) <= 1, (row, sub)
        # The TEST actions are the status strip's: a third row, under the
        # sub-header, full width, and the first card below it.
        actions = page.locator(".dstf-strip .dstf-actions").bounding_box()
        assert actions is not None
        assert actions["x"] + actions["width"] <= VIEWPORT["width"], actions
        assert page.locator(".dstf-subheader .dstf-actions").count() == 0
        strip = page.locator(".dstf-strip").bounding_box()
        assert strip is not None
        assert page.locator(".q-header .dstf-strip").count() == 1
        assert strip["y"] >= sub["y"] + sub["height"] - 1, (sub, strip)
        assert strip["x"] >= 0 and strip["x"] + strip["width"] <= VIEWPORT["width"]
        assert first_card["y"] >= strip["y"] + strip["height"], (strip, first_card)
        # The verdict's own fill, not Quasar's `bg-primary` blue.
        assert (
            page.locator(".dstf-subheader .dstf-badge").evaluate(
                "e => getComputedStyle(e).backgroundColor"
            )
            == "rgb(244, 67, 54)"
        )
        page.evaluate("() => window.scrollTo(0, 2000)")
        page.wait_for_timeout(200)
        still = page.locator(".dstf-subheader").bounding_box()
        assert still is not None and 0 <= still["y"], still
        assert still["y"] + still["height"] <= VIEWPORT["height"], still
        kept = page.locator(".dstf-strip").bounding_box()
        assert kept is not None and abs(kept["y"] - strip["y"]) <= 1, (strip, kept)
        layout = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth,"
            " client: document.documentElement.clientWidth})"
        )
        assert layout["scroll"] <= layout["client"], layout

        # 2. A card is a slot until it is scrolled to. The wheel scrolls the
        #    PAGE over a plot (`scrollZoom` is off), 600 px at a time.
        assert slots == 7, slots
        assert 1 <= drawn_at_load < slots, (drawn_at_load, slots)
        # From the top: assertion 1's JUMP legitimately skipped the slots it
        # never crossed, and they draw when they are reached.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
        for _ in range(40):
            if page.evaluate(
                "() => window.scrollY + window.innerHeight"
                " >= document.documentElement.scrollHeight - 2"
            ):
                break
            page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(200)
        expect(page.locator(".js-plotly-plot")).to_have_count(
            slots, timeout=EXPECT_TIMEOUT
        )
        assert page.evaluate("() => window.scrollY") > first_card["height"]

        # 3. The tree line's tally, the rows it opens to, and a node's wrap
        #    form in a row under it — which is not a click on the row that
        #    hosts it.
        page.evaluate("() => window.scrollTo(0, 0)")
        line = page.locator(".dstf-tree-line")
        expect(line).to_contain_text(
            re.compile(r"\d+ pass · \d+ fail"), timeout=EXPECT_TIMEOUT
        )
        assert page.locator(".dstf-tree-row").count() == 0
        line.get_by_role("button", name="Show tree").click()
        expect(page.locator(".dstf-tree-row")).to_have_count(
            4, timeout=EXPECT_TIMEOUT
        )  # the root and its three criteria
        wrap = (
            page.locator(".dstf-tree-row")
            .first.locator(".dstf-node-controls button")
            .filter(has=page.locator("i:text-is('data_array')"))
        )
        wrap.click()
        form = page.locator(".dstf-form-row")
        apply_wrap = form.get_by_role("button", name="Wrap", exact=True)
        expect(apply_wrap).to_be_visible(timeout=EXPECT_TIMEOUT)
        # Retrying reads: an editor the click opened arrives on the NEXT
        # round trip, and a bare count() the moment the row appeared would
        # have said zero either way.
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-crit-open")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # 8. The top of the page, the tree open, the wrap form showing.
        _shot(page, tmp_path, "tree")
        # The form row's Cancel (a menu closed on Escape; a row does not).
        form.get_by_role("button", name="Cancel").click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # The same node's controls are drawn again on the criteria row under
        # the plot, and THAT row is the way into its editor: a click on the
        # controls inside it must not also be a click on the row. (A tree
        # row has no click of its own, so the tree cannot say this: the
        # `stopPropagation` mutation survives the paragraph above.)
        crit_wrap = (
            _crit(page, 0, "tube · x")
            .locator(".dstf-node-controls button")
            .filter(has=page.locator("i:text-is('data_array')"))
        )
        crit_wrap.click()
        expect(apply_wrap).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert page.locator(".dstf-editor").count() == 0
        assert page.locator(".dstf-crit-open").count() == 0
        form.get_by_role("button", name="Cancel").click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)

        # 3b. The closed line's jump opens that criterion where it lives —
        #     on the card of the variable it scores — and brings it on screen.
        page.locator(".dstf-tree-line").get_by_role("button", name="Hide tree").click()
        # Hidden, not dropped: the rows are repainted in place, so the card
        # keeps them and hides the column they sit in.
        expect(page.locator(".dstf-tree-row:visible")).to_have_count(
            0, timeout=EXPECT_TIMEOUT
        )
        page.locator(".dstf-tree-line button").filter(has_text="range · y").click()
        opened = page.locator(".dstf-crit-open")
        expect(opened).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(opened).to_contain_text("range · y")
        # y is plot 1's series; the jump must not have opened a row on x.
        expect(
            page.locator(".dstf-plot").nth(1).locator(".dstf-crit-open")
        ).to_have_count(1)
        box = opened.bounding_box()
        assert box is not None and 0 < box["y"] < VIEWPORT["height"], box


# -- the editor below its row, and the plot as its editing surface --------


def test_a_criterion_opens_below_its_row_and_the_plot_stages_and_refuses(
    tmp_path, playwright_browser
):
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        # 4. The zoom and the stamp are set BEFORE the editor opens and read
        #    right after it: the figure's `config` can only change at an
        #    open or a close, so a zoom set afterwards would never see a
        #    `newPlot`.
        page.evaluate(ZOOM_AND_STAMP, [ZOOM_X, ZOOM_Y])
        row_box = _open_crit(page, 0, "tube · x")
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        state = page.evaluate(READ_PLOT)
        assert state["stamp"] == "before-open", state  # the same graph div
        assert [round(v, 6) for v in state["xrange"]] == ZOOM_X, state
        circles = [name for kind, name in state["shapes"] if kind == "circle"]
        assert circles, state
        assert all(n.startswith(f"{TUBE}/") for n in circles), state
        ed_box = editor.bounding_box()
        assert ed_box is not None
        assert ed_box["y"] >= row_box["y"] + row_box["height"] - 1, (row_box, ed_box)
        assert abs(ed_box["x"] - (row_box["x"] + 16)) <= 2, (row_box, ed_box)
        page.locator(".dstf-plot").first.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        # 8. Plot 0 with its editor open.
        _shot(page, tmp_path, "editor")
        page.evaluate("() => window.scrollTo(0, 0)")

        # 4b. Typing in a field edits the plot and keeps the field: the
        #     panel that submitted is the one a rebuild skips.
        _open_crit(page, 0, "range · x")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        lower = (
            page.locator(".dstf-editor .q-field")
            .filter(has_text="Lower bound")
            .locator("input")
        )
        expect(lower).to_be_visible(timeout=EXPECT_TIMEOUT)
        lower.fill("-0.4")
        page.wait_for_function(
            SHAPE_IS, arg=[MIN_LINE, -0.4, 1e-9], timeout=EXPECT_TIMEOUT
        )
        assert page.locator(".dstf-editor").count() == 1
        assert lower.evaluate("e => e === document.activeElement")
        expect(
            page.locator(".dstf-header-row").get_by_role(
                "button", name="1 pending edit"
            )
        ).to_be_visible(timeout=EXPECT_TIMEOUT)

        # 5. A ring dragged by its CENTRE stages an edit; the zoom and the
        #    graph div survive it.
        _open_crit(page, 0, "tube · x")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        ring = page.evaluate(FIND_RING)
        assert ring is not None, page.evaluate(READ_PLOT)
        _drag(page, ring["cx"], ring["cy"], -40)
        # 4b staged one replace already, so this is the SECOND pending edit
        # (the plan's "1 edit" predates assertion 4b, which stages its own).
        expect(
            page.locator(".dstf-header-row").get_by_role(
                "button", name="2 pending edits"
            )
        ).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_function(
            SHAPE_ABOVE, arg=[ring["name"], ring["yanchor"]], timeout=EXPECT_TIMEOUT
        )
        after = page.evaluate(READ_PLOT)
        assert [round(v, 6) for v in after["xrange"]] == ZOOM_X, after
        assert after["stamp"] == "before-open", after

        # 5b. Another leaf's bound line is on the same plot and Plotly lets
        #     it be dragged: the open editor refuses it, says to open that
        #     criterion, stages nothing, and the picture goes back.
        # A closed range starts hidden (legend b): its swatch draws it.
        _swatch(page, 0, "range · x").click()
        page.wait_for_function(
            "(n) => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).some(s => s.name === n && s.visible)",
            arg=MAX_LINE,
            timeout=EXPECT_TIMEOUT,
        )
        # The click scrolled its row into view, and plot 0 off the top.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate(RELAYOUT_Y, LINE_Y)
        page.wait_for_timeout(300)
        line = page.evaluate(FIND_LINE, MAX_LINE)
        assert line is not None, page.evaluate(READ_PLOT)
        _drag(page, line["cx"], line["cy"], 30)
        expect(editor).to_contain_text(
            "Open that criterion to drag its lines.", timeout=EXPECT_TIMEOUT
        )
        assert _pending(page) == "2 pending edits"
        page.wait_for_function(SHAPE_IS, arg=[MAX_LINE, 2.0, 1e-9], timeout=2_000)
        back = page.evaluate(READ_LINE, MAX_LINE)
        assert back["drawn"] is not None and abs(back["drawn"] - 2.0) < 0.02, back
        assert [round(v, 6) for v in back["xrange"]] == ZOOM_X, back
        assert [round(v, 6) for v in back["yrange"]] == LINE_Y, back

        # 5c. The same drag with NO editor open: nothing to refuse, nothing
        #     staged, and the picture still goes back.
        page.locator(".dstf-editor").get_by_role("button", name="Done").click()
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        line = page.evaluate(FIND_LINE, MAX_LINE)
        assert line is not None, page.evaluate(READ_PLOT)
        # Held mid-drag: the line has actually MOVED off its spec value, so
        # the snap-back below is a return and not a picture that never left
        # (a drag Plotly refused outright would pass the same assertions).
        page.mouse.move(line["cx"], line["cy"])
        page.mouse.down()
        page.mouse.move(line["cx"], line["cy"] + 30, steps=12)
        moved = page.evaluate(READ_LINE, MAX_LINE)
        page.mouse.up()
        assert moved["drawn"] is not None and abs(moved["drawn"] - 2.0) > 0.05, moved
        page.wait_for_function(SHAPE_IS, arg=[MAX_LINE, 2.0, 1e-9], timeout=2_000)
        assert _pending(page) == "2 pending edits"
        idle = page.evaluate(READ_LINE, MAX_LINE)
        assert idle["drawn"] is not None and abs(idle["drawn"] - 2.0) < 0.02, idle

        # 5d. Adding a criterion from a plot's `+` changes the tree's SHAPE,
        #     which rebuilds every card. The slots are NEW elements, so the
        #     page's IntersectionObserver has to be told about them or
        #     nothing below the fold ever draws again — and the rebuild
        #     must not throw the page back to the top.
        page.evaluate("() => window.scrollTo(0, 500)")
        page.wait_for_timeout(400)
        drawn = page.locator(".js-plotly-plot").count()
        assert drawn < 7, drawn  # something below is still a slot
        rows = page.locator(".dstf-crit").count()
        before_scroll = page.evaluate("() => window.scrollY")
        assert before_scroll > 0, before_scroll
        page.locator(".dstf-plot").nth(1).get_by_role(
            "button", name="Add a criterion for y"
        ).click()
        apply_add = page.get_by_role("button", name="Add", exact=True)
        expect(apply_add).to_be_visible(timeout=EXPECT_TIMEOUT)
        apply_add.click()
        expect(page.locator(".dstf-crit")).not_to_have_count(
            rows, timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(400)
        assert page.evaluate("() => window.scrollY") > 0, "the rebuild scrolled to top"
        for _ in range(40):
            if page.evaluate(
                "() => window.scrollY + window.innerHeight"
                " >= document.documentElement.scrollHeight - 2"
            ):
                break
            page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(200)
        expect(page.locator(".js-plotly-plot")).to_have_count(7, timeout=EXPECT_TIMEOUT)

        # 6. Focus shows one plot of seven, and the bar is the way back.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-plot").first.get_by_role("button", name="Focus").click()
        expect(page.locator(".dstf-plot:visible")).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-focus-bar")).to_contain_text("Showing 1 of 7 plots")
        # The switcher names the plot; the sentence only counts.
        expect(page.locator(".dstf-focus-bar")).not_to_contain_text("plots:")
        # The switcher (Q11 c): every plot by name, a watch plot marked;
        # choosing one shows it alone.
        page.locator(".dstf-focus-bar .q-select").click()
        menu = page.locator(".q-menu .q-item")
        expect(menu).to_have_count(7, timeout=EXPECT_TIMEOUT)
        assert menu.all_inner_texts()[:3] == ["x", "y", "w1 · watch"]
        menu.nth(1).click()
        expect(page.locator(".dstf-focus-bar .q-select")).to_contain_text(
            "y", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-plot:visible")).to_have_count(1)
        expect(page.locator(".dstf-plot:visible .dstf-card-title")).to_contain_text("y")
        page.get_by_role("button", name="Show all 7 plots").click()
        expect(page.locator(".dstf-plot:visible")).to_have_count(
            7, timeout=EXPECT_TIMEOUT
        )


#: The range on x's window: its bound is solid inside it, dotted outside.
WINDOW = {"start": 0.3, "end": 0.7}


def _windowed_page_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` with the range on x scored inside `WINDOW` only."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"]["children"][1]["window"] = dict(WINDOW)
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


#: The shape the mouse at data (x, y) would grab, by name. Plotly's hit
#: target is the `g[data-index][drag-helper]` around a shape's path, not the
#: path; a line is 1.5 px, so a few pixels either side of y are tried.
HIT_AT = """
([x, y]) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, rect = gd.getBoundingClientRect();
  const xa = fl.xaxis, ya = fl.yaxis;
  const cx = rect.left + xa._offset + xa.l2p(x);
  const cy0 = rect.top + ya._offset + ya.l2p(y);
  for (const dy of [0, -1, 1, -2, 2]) {
    const el = document.elementFromPoint(cx, cy0 + dy);
    const hit = el && el.closest('g.shapelayer') && el.closest('[data-index]');
    if (hit) {
      const i = parseInt(hit.getAttribute('data-index'), 10);
      return {cx: cx, cy: cy0 + dy, name: (fl.shapes[i] || {}).name || null};
    }
  }
  return {cx: cx, cy: cy0, name: null};
}
"""


def test_a_windowed_bound_drags_inside_and_outside_its_window(
    tmp_path, playwright_browser
):
    """The window's rect was drawn over the bound and swallowed every drag
    on it; the `/outside` line was refused by name (measured in Chromium on
    plotly.js 3.1.1, 2026-09-26). Now both move the bound, and the window's
    interior takes a drag that snaps back with no error."""
    with _served(tmp_path, playwright_browser, project=_windowed_page_project) as page:
        page.wait_for_timeout(700)
        _open_crit(page, 0, "range · x")
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        upper = (
            page.locator(".dstf-editor .q-field")
            .filter(has_text="Upper bound")
            .locator("input")
        )
        expect(upper).to_have_value("2", timeout=EXPECT_TIMEOUT)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate(RELAYOUT_Y, LINE_Y)
        page.wait_for_timeout(400)
        error = page.locator(".dstf-editor .text-negative")

        # 1. Inside the window. The full-width dotted continuation comes
        #    after the bound, so a grab here takes it or the bound: both are
        #    the bound. The window's rect, drawn first, takes neither.
        hit = page.evaluate(HIT_AT, [0.5, 2.0])
        assert hit["name"] in (MAX_LINE, f"{MAX_LINE}/outside"), hit
        _drag(page, hit["cx"], hit["cy"], -15)
        expect(
            page.locator(".dstf-header-row").get_by_role(
                "button", name="1 pending edit"
            )
        ).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(upper).not_to_have_value("2", timeout=EXPECT_TIMEOUT)
        assert [t for t in error.all_inner_texts() if t.strip()] == []

        # 2. Outside the window: the dotted line moves the same bound.
        line = page.evaluate(
            "(n) => document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes.find(s => s.name === n).y0",
            MAX_LINE,
        )
        dragged_to = upper.input_value()
        hit = page.evaluate(HIT_AT, [0.1, line])
        assert hit["name"] == f"{MAX_LINE}/outside", hit
        _drag(page, hit["cx"], hit["cy"], 30)
        page.wait_for_function(
            "([n, v]) => document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes.find(s => s.name === n).y0 < v - 1e-6",
            arg=[MAX_LINE, line],
            timeout=EXPECT_TIMEOUT,
        )
        expect(upper).not_to_have_value(dragged_to, timeout=EXPECT_TIMEOUT)
        assert _pending(page) == "1 pending edit"  # the same leaf, collapsed
        assert [t for t in error.all_inner_texts() if t.strip()] == []

        # 3. The window's interior, away from every line: nothing staged,
        #    no error.
        pending = _pending(page)
        hit = page.evaluate(HIT_AT, [0.45, 0.2])
        assert hit["name"] == f"{RANGE_X}/window", hit
        _drag(page, hit["cx"], hit["cy"], 40)
        page.wait_for_timeout(800)
        assert _pending(page) == pending
        assert [t for t in error.all_inner_texts() if t.strip()] == []


#: A pixel at the x of one point of a drawn trace, on the VISIBLE card's
#: drag layer. `hovermode` is `x unified`, so the hover is decided by the x
#: alone and the y only has to be somewhere the mouse reaches the plot: the
#: tool's figure draws every criterion's geometry ABOVE the drag layer, and
#: a pixel that lands on a band or a ring hovers nothing. The column is
#: swept the way `FIND_RING` sweeps for a shape.
POINT_PIXEL = """
(gd, [id, idx]) => {
  const fl = gd._fullLayout, r = gd.getBoundingClientRect();
  const tr = (gd.data || []).find(t => (t.meta || {}).id === id);
  if (!tr) return null;
  const x = r.left + fl.xaxis._offset + fl.xaxis.l2p(tr.x[idx]);
  const top = r.top + fl.yaxis._offset;
  for (let f = 0.05; f < 1; f += 0.05) {
    const y = top + fl.yaxis._length * f;
    const el = document.elementFromPoint(x, y);
    if (el && el.closest && el.closest('.nsewdrag')) return [x, y];
  }
  return null;
}
"""

#: Every line of the hover box. The figure's `hovermode` is `x unified`, so
#: the box is a legend-shaped group under `.hoverlayer`, not a `.hovertext`.
HOVER_TEXTS = (
    "() => Array.from(document.querySelectorAll('.hoverlayer text'))"
    ".map(e => e.textContent)"
)


def _context_page_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` with y drawn as CONTEXT on x's plot, and no watch
    plots: x's card draws y grey beside it."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["plots"] = [{"var": "x", "context": ["y"]}]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


def test_a_hover_over_a_context_series_reads_its_name(tmp_path, playwright_browser):
    """The hover box a person sees in the TOOL, under a real mouse.

    The tool re-hosts the figure the report draws, under NiceGUI's own
    plotly build, and every labelled hover on both tiers printed the
    literal `%{text}: 1.234`: plotly.js substitutes `%{text}` only from a
    per-point array. Nothing but a rendered page says so — the figure dict
    carried the label all along.

    A context series always carries a hover — its variable's text — and
    it is what a person reads to tell the grey line from the subject's.
    Focus draws x's card without a scroll."""
    with _served(tmp_path, playwright_browser, project=_context_page_project) as page:
        card = page.locator(".dstf-plot").nth(0)
        expect(card.locator(".dstf-context")).to_contain_text("y")
        card.get_by_role("button", name="Focus").click()
        expect(page.locator(".dstf-plot:visible")).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-plot:visible .js-plotly-plot")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        plot = page.locator(".dstf-plot:visible .js-plotly-plot")
        pixel = None
        for _ in range(30):
            pixel = plot.evaluate(POINT_PIXEL, ["context/y", 3])
            if pixel is not None:
                break
            page.wait_for_timeout(200)
        assert pixel is not None, "the focused card never drew context/y"
        x, y = pixel
        page.mouse.move(x - 40, y - 20)
        page.wait_for_timeout(100)
        page.mouse.move(x, y, steps=4)
        page.wait_for_timeout(400)
        texts = page.evaluate(HOVER_TEXTS)
        _shot(page, tmp_path, "hover")
        assert texts, "no hover box under the mouse"
        assert any(line.startswith("y: ") for line in texts), texts
        assert not any("%{" in line for line in texts), texts


# -- legend b: the swatch, the error select, the legend above ---------------

READ_SHAPES = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout;
  return {
    stamp: gd.__dstfStamp,
    xrange: fl.xaxis.range.slice(),
    shapes: (fl.shapes || []).map(s => [s.type, s.name, s.visible]),
    painted: gd.querySelectorAll('g.shapelayer path').length,
  };
}
"""

#: A pixel on plot 0's drag layer at data-x ``x``: the unified hover is
#: decided by x alone, and the column is swept for the drag layer the way
#: `POINT_PIXEL` sweeps it.
PIXEL_AT_X = """
(x) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, r = gd.getBoundingClientRect();
  const px = r.left + fl.xaxis._offset + fl.xaxis.l2p(x);
  const top = r.top + fl.yaxis._offset;
  for (let f = 0.05; f < 1; f += 0.05) {
    const y = top + fl.yaxis._length * f;
    const el = document.elementFromPoint(px, y);
    if (el && el.closest && el.closest('.nsewdrag')) return [px, y];
  }
  return null;
}
"""


def _hover_at(page, x: float) -> list[str]:
    """Every line of the unified hover box under a real mouse at data-x
    ``x`` on plot 0 (a screenshot cannot show a hover)."""
    pixel = page.evaluate(PIXEL_AT_X, x)
    assert pixel is not None, page.evaluate(READ_PLOT)
    page.mouse.move(pixel[0] - 60, pixel[1] - 20)
    page.wait_for_timeout(150)
    page.mouse.move(pixel[0], pixel[1], steps=4)
    page.wait_for_timeout(400)
    return page.evaluate(HOVER_TEXTS)


def test_a_swatch_hides_a_criterion_and_the_zoom_stays(tmp_path, playwright_browser):
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        page.evaluate(ZOOM_AND_STAMP, [ZOOM_X, LINE_Y])
        page.wait_for_timeout(200)
        start = page.evaluate(READ_SHAPES)
        # range · x passes, and a range draws nothing until asked.
        assert [s[2] for s in start["shapes"] if s[1].startswith(RANGE_X)] == [
            False,
            False,
        ], start
        swatch = _swatch(page, 0, "range · x")
        expect(swatch).to_be_visible(timeout=EXPECT_TIMEOUT)
        swatch.click()
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).filter(s => s.visible && s.name.startsWith("
            f"'{RANGE_X}/')).length === 2",
            timeout=EXPECT_TIMEOUT,
        )
        after = page.evaluate(READ_SHAPES)
        # Hidden shapes leave the shape layer (nothing else draws a shape on
        # plot 0 while no criterion is open); shown, the lines are painted.
        assert start["painted"] == 0 and after["painted"] > 0, (start, after)
        # A `react`, not a `newPlot`: the zoom and the stamp are still there.
        assert [round(v, 6) for v in after["xrange"]] == ZOOM_X, after
        assert after["stamp"] == "before-open", after
        # The swatch's click is the swatch's: it did not reach the row, which
        # would have opened the editor too. Retrying reads, because an editor
        # the click opened arrives on the next round trip.
        page.wait_for_timeout(300)
        expect(page.locator(".dstf-crit-open")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
        # 8. Plot 0 with range · x's swatch on: two bound lines, the legend
        #    above the plot area.
        _shot(page, tmp_path, "range-swatch-on")

        # The legend sits above the plot area, so the ring finder's hit test
        # lands on a ring, not on a legend entry.
        _open_crit(page, 0, "tube · x")
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate(RELAYOUT_Y, ZOOM_Y)
        page.wait_for_timeout(300)
        assert page.evaluate(FIND_RING) is not None, page.evaluate(READ_PLOT)
        # Every ring in view, not only the first: one the legend or the
        # toolbar covered would be one a person cannot grab.
        rings = page.evaluate(ALL_RINGS)
        assert rings and all(hit for _, hit in rings), rings

        # A real-mouse hover at t = 0.5 lists the tube's handles while it
        # draws. Its marker groups hover as bare values (`hoverinfo: "x+y"`):
        # the handles at t = 0.5 sit at 0.6 (upper) and 0.3 (lower), read
        # through the y axis's `hoverformat` — `.4~g` trims the trailing
        # zeros `.4g` printed as `0.6000` (the owner's 2026-09-24 ruling).
        texts = _hover_at(page, 0.5)
        assert "0.6" in texts and "0.3" in texts, texts
        # An OPEN criterion cannot be hidden: close it, then its swatch.
        page.mouse.move(5, 5)
        page.locator(".dstf-editor").get_by_role("button", name="Done").click()
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        _swatch(page, 0, "tube · x").click()
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0].data || [])"
            f".filter(t => (t.meta || {{}}).leaf === '{TUBE}')"
            ".every(t => t.visible === false)",
            timeout=EXPECT_TIMEOUT,
        )
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
        texts = _hover_at(page, 0.5)
        assert texts, "no hover box under the mouse"
        assert "0.6" not in texts and "0.3" not in texts, texts

        # The error select shows one series, and the figure shows its axis.
        page.mouse.move(5, 5)
        plot0 = page.locator(".dstf-plot").first
        plot0.locator(".q-select").click()
        page.locator(".q-menu .q-item").filter(has_text="Abs error").click()
        page.wait_for_function(
            "() => document.querySelectorAll('.js-plotly-plot')[0]"
            "._fullLayout.yaxis3.visible === true",
            timeout=EXPECT_TIMEOUT,
        )
        page.wait_for_timeout(300)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
        # 8. Plot 0 with "Abs error" chosen: its trace and its right axis.
        _shot(page, tmp_path, "error-abs")
        # No error: the right margin automargin grew for the axis comes back.
        _choose_error(page, "No error", None)
        assert (
            page.evaluate(
                "() => document.querySelectorAll('.js-plotly-plot')[0]._fullLayout._size.r"
            )
            == 20
        )


def _choose_error(page, label: str, axis: str | None) -> None:
    """Pick ``label`` in plot 0's error select and wait for ``axis`` to show
    (or, for None, for both error axes to hide)."""
    page.mouse.move(5, 5)
    page.locator(".dstf-plot").first.locator(".q-select").click()
    page.locator(".q-menu .q-item").filter(has_text=label).click()
    want = (
        f"fl.{axis}.visible === true"
        if axis
        else "fl.yaxis3.visible === false && fl.yaxis4.visible === false"
    )
    page.wait_for_function(
        "() => { const fl = document.querySelectorAll('.js-plotly-plot')[0]"
        f"._fullLayout; return {want}; }}",
        timeout=EXPECT_TIMEOUT,
    )
    page.wait_for_timeout(300)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)


def _error_labels_under_toolbar(page) -> list[str]:
    """Hover plot 0 so its toolbar shows, then name every error-axis label
    under it (`UNDER_TOOLBAR`); a read that found no label at all fails, and
    so does a toolbar outside its card (`.dstf-plot`): the gutter that
    clears the axes must not push the toolbar past the card's edge."""
    plot = page.locator(".js-plotly-plot").first
    box = plot.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.5)
    page.wait_for_timeout(300)
    read = plot.evaluate(UNDER_TOOLBAR)
    assert read is not None and read["labels"] > 0, read
    assert read["inside"] is True, read
    assert read["card"] == "dstf-plot", read
    return read["under"]


@pytest.mark.parametrize("scale", [1.0, 1e-4])
def test_no_error_axis_label_sits_under_the_toolbar(
    tmp_path, playwright_browser, scale
):
    """Hovered, so the toolbar shows: no tick label and no title of the
    error axis the select shows intersects it, for each series it offers
    and for errors around 1e-5 too. The toolbar stands in its own gutter
    right of the plot (`shell/style.py`), so the axes never compete with it
    for the figure's right margin, however wide their labels grow."""
    # At 1e-4 an error around 2.5e-5, the size a passing test's error
    # usually is.
    with _served(
        tmp_path, playwright_browser, x_error=lambda t: 0.25 * scale * t
    ) as page:
        page.wait_for_timeout(700)
        # Each step flips the axis it waits on: signed and abs share y3.
        for label, axis in (
            ("Signed error", "yaxis3"),
            ("NRMSE", "yaxis4"),
            ("Abs error", "yaxis3"),
        ):
            _choose_error(page, label, axis)
            assert _error_labels_under_toolbar(page) == [], label
        # A real-mouse hover at t = 0.5 reads Abs error trimmed to 4
        # significant digits (d3 `.4~g`, the owner's 2026-09-24 ruling): at
        # 1e-4 the error there is 1.25e-5, which `.4g` printed `0.00001250`.
        texts = _hover_at(page, 0.5)
        want = "Abs error: 0.0000125" if scale != 1.0 else "Abs error: 0.125"
        # Still hovered: plot 0, Abs error, its hover box and its axis, the
        # toolbar in the gutter.
        _shot(page, tmp_path, f"gutter-abs-{scale:g}")
        assert want in texts, texts
        # The gutter costs no horizontal scroll: the page is as wide as the view.
        assert (
            page.evaluate(
                "() => document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth"
            )
            <= 0
        )
        _choose_error(page, "No error", None)
        box = page.locator(".js-plotly-plot").first.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, f"gutter-none-{scale:g}")


#: Plot 0's error axes and each error trace's y extent, from the figure
#: Plotly holds.
READ_ERROR_AXIS = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout;
  const extent = (id) => {
    const t = gd.data.find(t => (t.meta || {}).id === id);
    return [Math.min(...t.y), Math.max(...t.y)];
  };
  return {
    xrange: fl.xaxis.range.slice(),
    y3: fl.yaxis3.range.slice(),
    y4: fl.yaxis4.range.slice(),
    abs: extent('error/abs'),
    signed: extent('error/signed'),
    nrmse: extent('error/nrmse'),
  };
}
"""


def _covers_and_fills(axis: list[float], extent: list[float]) -> bool:
    """The axis covers the series, and the series fills most of it: a stale
    [-0.49, 3.51] covered [0, 0.25] too, with the line flat."""
    (lo, hi), (a, b) = axis, extent
    return lo <= a and b <= hi and (b - a) > 0.5 * (hi - lo)


def _box_zoom(page, fx: tuple[float, float], fy: tuple[float, float]) -> None:
    """A real-mouse box zoom on plot 0, from and to these fractions of its
    plot area (x from the left, y from the top). A box zoom scales every
    axis of the subplot, the hidden ones overlaying `y` too; a
    `Plotly.relayout` of `yaxis.range` scales `y` alone."""
    box = page.evaluate(
        "() => { const gd = document.querySelectorAll('.js-plotly-plot')[0];"
        " const fl = gd._fullLayout, r = gd.getBoundingClientRect();"
        " return [r.left + fl._size.l, r.top + fl._size.t, fl._size.w, fl._size.h]; }"
    )
    left, top, width, height = box
    page.mouse.move(left + width * fx[0], top + height * fy[0])
    page.mouse.down()
    page.mouse.move(left + width * fx[1], top + height * fy[1], steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)


def test_an_error_axis_zoomed_while_hidden_scales_fresh_when_chosen(
    tmp_path, playwright_browser
):
    """A box zoom made before any error series shows zooms the hidden error
    axes too: they overlay `y`. Under the figure's `uirevision: "keep"` that
    range outlived the choice, so Abs error drew on [-0.49, 3.51] as a flat
    line along 0 (the TRANSFORM gate, 2026-09-25). Each error axis's
    `uirevision` is the chosen series (`figures.plotly.variable_figure`):
    choosing one scales its axis to its data, and a zoom made while it
    shows is kept through a redraw. x's error is ``0.25 sin(2 pi t)``:
    signed spans [-0.25, 0.25] where abs spans [0, 0.25], so Signed after
    Abs tells whether their shared axis scaled afresh; NRMSE, on the other
    axis, tells whether that one did."""
    wave = lambda t: 0.25 * math.sin(2 * math.pi * t)  # noqa: E731
    with _served(tmp_path, playwright_browser, x_error=wave) as page:
        page.wait_for_timeout(700)
        page.evaluate("() => window.scrollTo(0, 0)")
        before = page.evaluate(READ_ERROR_AXIS)
        _box_zoom(page, (0.2, 0.7), (0.15, 0.75))
        zoomed = page.evaluate(READ_ERROR_AXIS)
        # The box zoom reached the hidden axes: the defect's precondition.
        assert zoomed["y3"] != before["y3"], (before, zoomed)
        assert zoomed["y4"] != before["y4"], (before, zoomed)

        _choose_error(page, "Abs error", "yaxis3")
        shown = page.evaluate(READ_ERROR_AXIS)
        _shot(page, tmp_path, "zoomed-then-abs")
        assert _covers_and_fills(shown["y3"], shown["abs"]), shown
        # The time zoom is the page's, and stays.
        assert shown["xrange"] == zoomed["xrange"], (zoomed, shown)

        # A zoom made while Abs error shows is kept through a redraw: the
        # swatch's send is a `Plotly.react` with the same error chosen.
        _box_zoom(page, (0.3, 0.6), (0.3, 0.6))
        rezoomed = page.evaluate(READ_ERROR_AXIS)
        assert rezoomed["y3"] != shown["y3"], (shown, rezoomed)
        _swatch(page, 0, "range · x").click()
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).filter(s => s.visible && s.name.startsWith("
            f"'{RANGE_X}/')).length === 2",
            timeout=EXPECT_TIMEOUT,
        )
        page.wait_for_timeout(300)
        redrawn = page.evaluate(READ_ERROR_AXIS)
        assert redrawn["y3"] == rezoomed["y3"], (rezoomed, redrawn)
        assert redrawn["xrange"] == rezoomed["xrange"], (rezoomed, redrawn)

        # Signed takes the axis Abs held, zoomed: it scales afresh to
        # Signed's negative half too.
        _choose_error(page, "Signed error", "yaxis3")
        page.wait_for_function(
            "() => document.querySelectorAll('.js-plotly-plot')[0].data"
            ".find(t => (t.meta || {}).id === 'error/signed').visible === true",
            timeout=EXPECT_TIMEOUT,
        )
        page.wait_for_timeout(300)
        signed = page.evaluate(READ_ERROR_AXIS)
        assert _covers_and_fills(signed["y3"], signed["signed"]), (redrawn, signed)

        # NRMSE's axis took both box zooms while it hid; chosen, it scales
        # to NRMSE.
        _choose_error(page, "NRMSE", "yaxis4")
        nrmse = page.evaluate(READ_ERROR_AXIS)
        assert _covers_and_fills(nrmse["y4"], nrmse["nrmse"]), (redrawn, nrmse)


def _range_lines_shown(page) -> int:
    return page.evaluate(
        "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
        ".shapes || []).filter(s => s.visible && s.name.startsWith("
        f"'{RANGE_X}/')).length"
    )


def test_a_swatch_answers_the_keyboard(tmp_path, playwright_browser):
    """The swatch is a button to the keyboard: Tab reaches it, Enter and
    Space toggle it the way a click does, and the open criterion's is
    locked to the keyboard too."""
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        expect(_swatch(page, 0, "range · x")).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert _range_lines_shown(page) == 0
        # Tab from the plot's title — a click there sets where the
        # keyboard's next Tab starts — until the range's swatch has focus.
        page.locator(".dstf-plot").first.locator(".text-subtitle2").first.click()
        on_swatch = (
            "() => { const a = document.activeElement;"
            " return !!a && a.classList.contains('dstf-swatch')"
            " && a.closest('.dstf-crit').textContent.includes('range · x'); }"
        )
        for _ in range(40):
            page.keyboard.press("Tab")
            if page.evaluate(on_swatch):
                break
        assert page.evaluate(on_swatch), page.evaluate(
            "() => document.activeElement.outerHTML.slice(0, 200)"
        )
        # A screen reader announces it as a button.
        assert page.evaluate("() => document.activeElement.getAttribute('role')") == (
            "button"
        )
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).filter(s => s.visible && s.name.startsWith("
            f"'{RANGE_X}/')).length === 2",
            timeout=EXPECT_TIMEOUT,
        )
        # Space on a focused element scrolls the page unless the handler
        # prevents it: in a scrolled page, the toggle leaves the scroll be.
        page.evaluate("() => window.scrollTo(0, 120)")
        page.wait_for_timeout(200)
        scrolled = page.evaluate("() => window.scrollY")
        assert scrolled > 0 and page.evaluate(on_swatch)
        page.keyboard.press(" ")
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).filter(s => s.visible && s.name.startsWith("
            f"'{RANGE_X}/')).length === 0",
            timeout=EXPECT_TIMEOUT,
        )
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.scrollY") == scrolled
        # A key on the swatch is the swatch's: no editor opened.
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)

        # The open criterion's swatch does nothing to the keyboard either:
        # closed again, range · x is its metric's default — hidden — because
        # the Enter made no choice.
        _open_crit(page, 0, "range · x")
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        _swatch(page, 0, "range · x").focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.locator(".dstf-editor").get_by_role("button", name="Done").click()
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(500)
        assert _range_lines_shown(page) == 0


# -- a node's forms as a row under it (board 1A-1D) ------------------------


def _icon(row, name: str):
    """A node's control on ``row`` by its Material icon's name."""
    return row.locator(".dstf-node-controls button").filter(
        has=row.page.locator(f"i:text-is('{name}')")
    )


def _choose(page, form, label: str) -> None:
    """Pick ``label`` in the form row's Combinator select."""
    form.locator(".q-select").first.click()
    page.locator(".q-menu .q-item").filter(has_text=label).first.click()


def test_a_form_row_opens_under_its_node_and_nothing_covers_it(
    tmp_path, playwright_browser
):
    with _served(tmp_path, playwright_browser) as page:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
        rows = page.locator(".dstf-tree-row")
        expect(rows).to_have_count(4, timeout=EXPECT_TIMEOUT)
        tube_row, next_row = rows.nth(1), rows.nth(2)
        before = next_row.bounding_box()
        assert before is not None

        # (a) Wrap under the tube's tree row, `k-of-n` chosen: only k shows.
        _icon(tube_row, "data_array").click()
        form = page.locator(".dstf-form-row")
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(form).to_contain_text("Wrap tube · x in a combinator")
        _choose(page, form, "k-of-n")
        expect(form).to_contain_text(
            "The criterion becomes the only child of a new k-of-n; add siblings "
            "to it with its +.",
            timeout=EXPECT_TIMEOUT,
        )
        expect(form.get_by_label("k — how many must pass")).to_be_visible()
        for gone in ("Weights, one per child", "Threshold", "Direction"):
            assert form.get_by_label(gone).count() == 0, gone
        page.wait_for_timeout(300)
        row_box, form_box = tube_row.bounding_box(), form.bounding_box()
        after = next_row.bounding_box()
        assert row_box is not None and form_box is not None and after is not None
        # BELOW its node's row, and the next node's row moved down for it.
        assert form_box["y"] >= row_box["y"] + row_box["height"] - 1, (
            row_box,
            form_box,
        )
        assert after["y"] >= form_box["y"] + form_box["height"] - 1, (form_box, after)
        assert after["y"] > before["y"], (before, after)
        page.mouse.move(5, 5)
        _shot(page, tmp_path, "form-row-k-of-n")

        # The tube's criteria row opens its editor: the form row goes.
        _open_crit(page, 0, "tube · x")
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)

        # (b) The root `and` has no fields and no Edit icon: its kind
        #     dropdown, k-of-n chosen, opens the Edit row preset to k-of-n,
        #     with Cancel and Apply (unwrap is the dropdown's).
        page.evaluate("() => window.scrollTo(0, 0)")
        assert _icon(rows.nth(0), "tune").count() == 0
        rows.nth(0).get_by_label("Combinator kind").click()
        page.get_by_role("option", name="k-of-n").click()
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(form).to_contain_text("Edit this combinator")
        expect(form).to_contain_text(
            "Changes this node's kind and fields; its children stay."
        )
        expect(form.get_by_label("k — how many must pass")).to_be_visible()
        buttons = form.locator("button").all_inner_texts()
        assert [b.strip() for b in buttons] == ["Cancel", "Apply"], buttons
        page.mouse.move(5, 5)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "form-row-edit-root")
        form.get_by_role("button", name="Cancel").click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)

        # (c) Wrap under the tube's CRITERIA row, `warn` chosen.
        crit = _crit(page, 0, "tube · x")
        crit.scroll_into_view_if_needed()
        _icon(crit, "data_array").click()
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        _choose(page, form, "warn")
        expect(form).to_contain_text(
            "A failure of this criterion becomes a warning; the test still passes.",
            timeout=EXPECT_TIMEOUT,
        )
        crit_box, form_box = crit.bounding_box(), form.bounding_box()
        assert crit_box is not None and form_box is not None
        assert form_box["y"] >= crit_box["y"] + crit_box["height"] - 1
        page.mouse.move(5, 5)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "form-row-warn")
        form.get_by_role("button", name="Wrap", exact=True).click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )

        # Under the warn it now sits in, the tube's `+` is grey and says
        # why — read from the tooltip a real mouse raises.
        crit = _crit(page, 0, "tube · x")
        crit.scroll_into_view_if_needed()
        plus = _icon(crit, "add")
        expect(plus).to_be_disabled(timeout=EXPECT_TIMEOUT)
        box = plus.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        tip = page.locator(".q-tooltip")
        expect(tip).to_have_count(1, timeout=EXPECT_TIMEOUT)
        assert tip.inner_text().strip() == "a warn takes exactly one child"
        _shot(page, tmp_path, "plus-under-warn")


def _header_bottom(page) -> float:
    """Where the fixed header ends in the viewport: a row scrolled into view
    must not sit under it."""
    box = page.locator(".q-header").bounding_box()
    assert box is not None
    return box["y"] + box["height"]


def test_a_form_row_scrolls_into_view_and_a_weighted_wrap_waits_for_a_threshold(
    tmp_path, playwright_browser
):
    """A row opened from a criteria row at the viewport's foot scrolls
    itself into view, just enough to show all of it and clear of the fixed
    header (the owner's ruling; the review measured such a row at 882-1052
    px in a 900 px viewport, and with `block: "start"` it sat at y=70 under
    a header ending at 106). A row that opens already in view does not move
    the page. A weighted Wrap is grey, saying why under a real mouse, until
    its threshold is typed; refused, the row stays with what was typed."""
    with _served(tmp_path, playwright_browser) as page:
        # Already in view: the root's tree row at the top of the page, its
        # form opening right under it. Nothing scrolls.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
        rows = page.locator(".dstf-tree-row")
        expect(rows).to_have_count(4, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        before = page.evaluate("() => window.scrollY")
        _icon(rows.nth(0), "data_array").click()
        form = page.locator(".dstf-form-row")
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(600)
        form_box = form.bounding_box()
        assert form_box is not None
        assert form_box["y"] + form_box["height"] <= VIEWPORT["height"], form_box
        assert page.evaluate("() => window.scrollY") == before
        form.get_by_role("button", name="Cancel", exact=True).click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.locator(".dstf-tree-line").get_by_role("button", name="Hide tree").click()
        expect(rows.first).to_be_hidden(timeout=EXPECT_TIMEOUT)

        # Plot 1's row starts below the fold; brought up until its foot is
        # 4 px above the viewport's — twice, since the plot it scrolls into
        # view is drawn and may move it.
        crit = _crit(page, 1, "range · y")
        for _ in range(2):
            box = crit.bounding_box()
            assert box is not None
            page.evaluate(
                "(dy) => window.scrollBy(0, dy)",
                box["y"] + box["height"] - VIEWPORT["height"] + 4,
            )
            page.wait_for_timeout(500)
        box = crit.bounding_box()
        assert box is not None
        foot = box["y"] + box["height"]
        assert VIEWPORT["height"] - 40 < foot <= VIEWPORT["height"], box
        _icon(crit, "data_array").click()
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(600)
        form_box = form.bounding_box()
        assert form_box is not None
        assert form_box["y"] + form_box["height"] <= VIEWPORT["height"], form_box
        # Just enough: the row it opened under is still on screen above it,
        # and neither is under the fixed header.
        head = _header_bottom(page)
        crit_box = crit.bounding_box()
        assert crit_box is not None and crit_box["y"] >= head, (crit_box, head)
        assert form_box["y"] >= head, (form_box, head)
        page.mouse.move(5, 5)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "form-row-scrolled-into-view")

        _choose(page, form, "weighted")
        expect(form.get_by_label("Threshold")).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert form.get_by_label("Threshold").input_value() == ""
        assert form.get_by_label("Weights, one per child").input_value() == "1"
        wrap = form.get_by_role("button", name="Wrap", exact=True)
        expect(wrap).to_be_disabled(timeout=EXPECT_TIMEOUT)
        form.scroll_into_view_if_needed()
        box = wrap.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        tip = page.locator(".q-tooltip")
        expect(tip).to_have_count(1, timeout=EXPECT_TIMEOUT)
        assert tip.inner_text().strip() == "enter a threshold"
        page.wait_for_timeout(400)  # the tooltip's fade-in, for the picture
        _shot(page, tmp_path, "wrap-weighted-waiting")

        form.get_by_label("Threshold").fill("0.5")
        expect(wrap).to_be_enabled(timeout=EXPECT_TIMEOUT)
        page.mouse.move(5, 5)
        page.wait_for_timeout(300)
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(600)
        assert page.locator(".q-tooltip:visible").count() == 0
        _shot(page, tmp_path, "wrap-weighted-typed")

        # Two weights for one child: refused by the validator, by name. The
        # row stays with what was typed; the error sits on its own line
        # under the criteria row, and the row's icons stay where every
        # other row's are.
        icons = _icon(crit, "add").bounding_box()
        form.get_by_label("Weights, one per child").fill("1, 2")
        wrap.click()
        error = (
            page.locator(".dstf-plot")
            .nth(1)
            .locator(".text-negative")
            .filter(has_text="weights")
        )
        expect(error).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        assert form.get_by_label("Weights, one per child").input_value() == "1, 2"
        row_box, error_box = crit.bounding_box(), error.bounding_box()
        assert row_box is not None and error_box is not None and icons is not None
        assert error_box["y"] >= row_box["y"] + row_box["height"] - 1, (
            row_box,
            error_box,
        )
        assert _icon(crit, "add").bounding_box() == icons
        page.mouse.move(5, 5)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "wrap-refused-error")
        # Typed right, it stages; the row and its error go.
        form.get_by_label("Weights, one per child").fill("1")
        wrap.click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(error).to_be_hidden(timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )


def test_a_refused_wrap_after_the_editor_closed_shows_its_error(
    tmp_path, playwright_browser
):
    """Closing a criterion's editor cleared its row's whole slot, and the
    row's host keeps its error labels there: a refused Wrap on that row
    then staged nothing and showed nothing, and NiceGUI logged an element
    deleted but still used (the review, in Chromium). The editor now goes
    alone, and the refusal shows under the row with the form still open and
    the typed weights in it."""
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        crit = _crit(page, 0, "tube · x")
        _open_crit(page, 0, "tube · x")
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.locator(".dstf-editor").get_by_role("button", name="Done").click()
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        _icon(crit, "data_array").click()
        form = page.locator(".dstf-form-row")
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        _choose(page, form, "weighted")
        expect(form.get_by_label("Threshold")).to_be_visible(timeout=EXPECT_TIMEOUT)
        form.get_by_label("Weights, one per child").fill("1, 2")
        form.get_by_label("Threshold").fill("0.5")
        form.get_by_role("button", name="Wrap", exact=True).click()
        error = (
            page.locator(".dstf-plot")
            .nth(0)
            .locator(".text-negative")
            .filter(has_text="weights")
        )
        expect(error).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        assert form.get_by_label("Weights, one per child").input_value() == "1, 2"
        assert _pending(page).strip().startswith("0 "), _pending(page)
        form.scroll_into_view_if_needed()
        page.mouse.move(5, 5)
        page.wait_for_timeout(400)
        _shot(page, tmp_path, "wrap-refused-after-editor")


# -- the page as one piece: the legend holds the signals, above the plot ---

#: Plot 0's legend: the names of its entries, from the figure Plotly holds
#: and from the SVG it drew, and the legend's box against the plot area's
#: top (`_size.t` below the plot's own top).
READ_LEGEND = """
() => {
  const gd = document.querySelectorAll('.js-plotly-plot')[0];
  const fl = gd._fullLayout, rect = gd.getBoundingClientRect();
  const legend = gd.querySelector('.infolayer .legend');
  const box = legend ? legend.getBoundingClientRect() : null;
  return {
    listed: gd._fullData
      .filter(t => t.showlegend !== false && t.visible !== false)
      .map(t => t.name),
    drawn: Array.from(gd.querySelectorAll('.legend .legendtext'))
      .map(e => e.textContent),
    bottom: box ? box.bottom : null,
    plotTop: rect.top + fl._size.t,
  };
}
"""


def test_the_legend_holds_signals_and_sits_above(tmp_path, playwright_browser):
    """Legend b, in Chromium: plot 0's legend lists the signals and nothing
    else — the failing tube draws by default, range · x's swatch is on and
    an error series is chosen, and not one of their names is in it — and
    its box ends at or above the plot area's top. A real mouse reads the
    hover box: the actual's line renders its label (never a template's
    `%{...}`) with its digits trimmed (`.4~g`: 0.625, where `.4g` printed
    0.6250)."""
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        _swatch(page, 0, "range · x").click()
        page.wait_for_function(
            "() => (document.querySelectorAll('.js-plotly-plot')[0]._fullLayout"
            ".shapes || []).filter(s => s.visible && s.name.startsWith("
            f"'{RANGE_X}/')).length === 2",
            timeout=EXPECT_TIMEOUT,
        )
        page.evaluate("() => window.scrollTo(0, 0)")
        _choose_error(page, "Abs error", "yaxis3")
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        legend = page.evaluate(READ_LEGEND)
        assert legend["listed"] == ["Actual", "Reference"], legend
        assert legend["drawn"] == ["Actual", "Reference"], legend
        assert legend["bottom"] is not None, legend
        assert legend["bottom"] <= legend["plotTop"] + 0.5, legend

        page.mouse.move(5, 5)
        texts = _hover_at(page, 0.5)
        assert texts, "no hover box under the mouse"
        assert not any("%{" in line for line in texts), texts
        assert "Actual : 0.625" in texts, texts
        assert "Reference : 0.5" in texts, texts


# -- 3c: the rail narrows the plots ---------------------------------------------

#: Every rail label, read whole: its text fits its own box, and its box sits
#: inside the drawer, whose content does not scroll sideways. The labels
#: clipped at "Tes" once by the DRAWER's padding pushing the buttons past
#: its edge, which a label measured only against itself or its button does
#: not see.
RAIL_LABELS = """
() => {
  const drawer = document.querySelector('.q-drawer--left .q-drawer__content');
  const d = drawer.getBoundingClientRect();
  return [...drawer.querySelectorAll('.dstf-rail-label')].map((label) => {
    const l = label.getBoundingClientRect();
    return {text: label.textContent, fits: label.scrollWidth <= label.clientWidth
      && l.left >= d.left && l.right <= d.right
      && drawer.scrollWidth <= drawer.clientWidth};
  });
}
"""
WHOLE = [{"text": "Tests", "fits": True}, {"text": "Vars", "fits": True}]


def _plot_width(page) -> float:
    return page.locator(".js-plotly-plot").first.evaluate(
        "e => e.getBoundingClientRect().width"
    )


def _steady_width(page) -> float:
    """The plot's width once a resize has finished: two reads 300 ms apart
    agree."""
    last = -1.0
    for _ in range(40):
        width = _plot_width(page)
        if abs(width - last) < 0.5:
            return width
        last = width
        page.wait_for_timeout(300)
    raise AssertionError(f"the plot's width never settled: {last}")


def _plot_width_is(page, width: float) -> None:
    page.wait_for_function(
        "() => Math.abs(document.querySelector('.js-plotly-plot')"
        f".getBoundingClientRect().width - ({width})) <= 4",
        timeout=EXPECT_TIMEOUT,
    )


def test_the_rail_narrows_the_plots_and_nothing_scrolls_sideways(
    tmp_path, playwright_browser
):
    """The owner's ruling (2026-09-25): the drawer PUSHES the plots, at a
    desktop width and below Quasar's 1024 px breakpoint too, and the rail's
    labels read whole. The current test's row wears the selection tint over
    its status tint (A fails, so the fail tint is what it would show)."""
    with _served(tmp_path, playwright_browser) as page:
        tests = page.locator(".dstf-rail").get_by_text("Tests", exact=True)
        assert page.evaluate(RAIL_LABELS) == WHOLE
        for width in (1280, 900):
            page.set_viewport_size({"width": width, "height": 900})
            w0 = _steady_width(page)
            tests.click()
            current = page.locator(".dstf-row-current")
            expect(current).to_be_visible(timeout=EXPECT_TIMEOUT)
            _plot_width_is(page, w0 - 320)
            # The width lands before the layout settles: a single read as
            # the plot's width landed saw 1551 at a 1280 px viewport.
            page.wait_for_function(
                "() => document.documentElement.scrollWidth == window.innerWidth",
                timeout=EXPECT_TIMEOUT,
            )
            svg = page.locator(".js-plotly-plot .main-svg").first
            assert abs(float(svg.get_attribute("width")) - (w0 - 320)) <= 4
            assert page.evaluate(RAIL_LABELS) == WHOLE
            assert (
                current.evaluate("e => getComputedStyle(e).backgroundColor")
                == "rgb(227, 242, 253)"
            )
            _shot(page, tmp_path, f"rail-{width}")
            tests.click()
            _plot_width_is(page, w0)


#: Each drawn plot: its figure's width against what its card's plot slot
#: leaves it, the slot less the toolbar's gutter.
FIGURE_FITS_SLOT = """() => [...document.querySelectorAll('.dstf-plot-slot')]
  .filter((e) => e.querySelector('.main-svg'))
  .map((e) => [e.querySelector('.main-svg').getBoundingClientRect().width,
               e.getBoundingClientRect().width - parseFloat(
                 getComputedStyle(e).getPropertyValue('--toolbar-gutter'))])"""


def _figures_fill_their_slots(page, low: float = 0, high: float = 1e9) -> float:
    """Wait for every drawn figure to fill its slot, each slot ``low`` to
    ``high`` px wide — the width is in the wait, so a figure that still fits
    the slot it had before the drawer moved does not end it; return the
    narrowest slot."""
    page.wait_for_function(
        f"""([low, high]) => {{
          const widths = ({FIGURE_FITS_SLOT})();
          return widths.length > 0 && widths.every(([svg, slot]) =>
            Math.abs(svg - slot) <= 2 && slot >= low && slot <= high);
        }}""",
        arg=[low, high],
        timeout=EXPECT_TIMEOUT,
    )
    return min(slot for _, slot in page.evaluate(FIGURE_FITS_SLOT))


def test_a_closed_drawer_leaves_every_figure_filling_its_card(
    tmp_path, playwright_browser
):
    """At 1280 px the pending drawer sits beside the page, narrowing it:
    once it closes, each drawn figure fills its card again. The rail's
    Variables drawer likewise. NiceGUI's plotly element observes its size
    and Plotly redraws 100 ms after it is told: read as the drawer lands,
    the figure is still the drawer's width narrower, and it fills the card
    ~150 ms later. Open, each drawer narrows the slots by at least 300 px
    (the pending drawer by ~520, the rail's by its 320)."""
    with _served(
        tmp_path, playwright_browser, project=_two_var_project, hooks=True
    ) as page:
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={A}") as r:
            json.loads(r.read())
        preview = page.get_by_role("button", name="Preview and save")
        expect(preview).to_be_visible(timeout=EXPECT_TIMEOUT)
        closed = _figures_fill_their_slots(page)
        preview.click()
        drawer = page.locator(".dstf-pending-drawer")
        expect(drawer.locator(".dstf-drawer-status")).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )
        _figures_fill_their_slots(page, high=closed - 300)
        drawer.get_by_role("button", name="Close", exact=True).click()
        _figures_fill_their_slots(page, low=closed - 2)
        _shot(page, tmp_path, "pending-drawer-closed-1280")
        variables = page.locator(".dstf-rail").get_by_text("Vars", exact=True)
        variables.click()
        _figures_fill_their_slots(page, high=closed - 300)
        variables.click()
        _figures_fill_their_slots(page, low=closed - 2)


#: Every criteria row's name (metric · variable), measured: its height
#: against its own line height. "normal" is 1.2 of the font size.
CRIT_NAME_LINES = """
() => [...document.querySelectorAll('.dstf-crit .dstf-crit-name')]
  .filter((e) => e.offsetParent !== null)
  .map((e) => {
    const s = getComputedStyle(e);
    const line = s.lineHeight === 'normal'
      ? 1.2 * parseFloat(s.fontSize) : parseFloat(s.lineHeight);
    return {text: e.textContent, lines: e.getBoundingClientRect().height / line};
  })
"""


def test_a_criteria_rows_name_stays_on_one_line_beside_an_open_drawer(
    tmp_path, playwright_browser
):
    """At 900 px with the Tests drawer open a card is ~540 px wide, and a
    row's name broke inside itself ("tube" over "· x"). The name keeps to one
    line; the readout beside it may wrap."""
    with _served(tmp_path, playwright_browser) as page:
        page.set_viewport_size({"width": 900, "height": 900})
        w0 = _steady_width(page)
        page.locator(".dstf-rail").get_by_text("Tests", exact=True).click()
        _plot_width_is(page, w0 - 320)
        _steady_width(page)
        names = page.evaluate(CRIT_NAME_LINES)
        assert names, "no criteria row is drawn"
        assert [n for n in names if n["lines"] > 1.5] == []


#: The index, among the page's plot cards, of each card wearing the ring.
RINGED = """
() => [...document.querySelectorAll('.dstf-plot')]
  .map((card, i) => card.classList.contains('dstf-plot-selected') ? i : -1)
  .filter((i) => i >= 0)
"""


def _ring_is(page, index: int) -> None:
    page.wait_for_function(
        f"() => JSON.stringify(({RINGED})()) === '[{index}]'", timeout=EXPECT_TIMEOUT
    )


def test_a_click_selects_a_plot_outside_its_plot_area(tmp_path, playwright_browser):
    """Planner's choice 8: a click on a card selects its plot — its title, a
    criteria row — but a click on the plot itself does not: the plot takes
    zooms and handle drags. Remove plot's click stops before the card, so
    the ring stays on the plot it was on, which has moved up one."""
    with _served(tmp_path, playwright_browser) as page:
        cards = page.locator(".dstf-plot")
        _ring_is(page, 0)
        cards.nth(3).scroll_into_view_if_needed()
        cards.nth(3).locator(".text-subtitle2").first.click()
        _ring_is(page, 3)
        cards.nth(4).scroll_into_view_if_needed()
        plot = cards.nth(4).locator(".js-plotly-plot")
        expect(plot).to_be_visible(timeout=EXPECT_TIMEOUT)
        plot.click()
        page.wait_for_timeout(500)
        assert page.evaluate(RINGED) == [3]
        crit = _crit(page, 1, "range · y")
        crit.scroll_into_view_if_needed()
        crit.locator(".dstf-crit-name").click()
        _ring_is(page, 1)
        cards.nth(1).scroll_into_view_if_needed()
        # The ring is PAINTED, not only classed: `.dstf-card`'s own
        # `box-shadow: none` came later in the sheet and won.
        ring = cards.nth(1).evaluate("e => getComputedStyle(e).boxShadow")
        assert "inset" in ring and "2px" in ring, ring
        assert cards.nth(0).evaluate("e => getComputedStyle(e).boxShadow") == "none"
        _shot(page, tmp_path, "ring")
        cards.nth(3).locator(".text-subtitle2").first.click()
        _ring_is(page, 3)
        # Plot 2 is a watch plot (of w1): a derived one has no Remove.
        cards.nth(2).get_by_role("button", name="Remove plot").click()
        expect(cards).to_have_count(6, timeout=EXPECT_TIMEOUT)
        _ring_is(page, 2)


# -- 3c: the Variables drawer ---------------------------------------------------

#: A pixel on the drag layer at the middle of a plot's x-axis: the unified
#: hover is decided by x alone.
MID_PIXEL = """
(gd) => {
  const fl = gd._fullLayout, r = gd.getBoundingClientRect();
  const x = r.left + fl.xaxis._offset + fl.xaxis._length / 2;
  const top = r.top + fl.yaxis._offset;
  for (let f = 0.05; f < 1; f += 0.05) {
    const y = top + fl.yaxis._length * f;
    const el = document.elementFromPoint(x, y);
    if (el && el.closest && el.closest('.nsewdrag')) return [x, y];
  }
  return null;
}
"""

#: A card's legend entries, read as SVG text (`inner_text` has none).
LEGEND = "e => [...e.querySelectorAll('.legendtext')].map(t => t.textContent)"


def _var_row(page, text: str):
    """The drawer's row that reads ``text`` exactly."""
    return page.locator(".dstf-var-row").filter(
        has=page.locator(".dstf-var-text", has_text=re.compile(f"^{re.escape(text)}$"))
    )


def _open_vars(page) -> None:
    page.locator(".dstf-rail").get_by_role("button", name="Vars").click()


def test_the_variables_drawer_edits_the_extras_with_a_real_mouse(
    tmp_path, playwright_browser
):
    """The owner's rulings Q5-Q6 and Q12-Q13 under a real mouse: a click on
    a card selects its plot for the drawer, "+ new plot" is there on the
    hovered row only, a check adds a CONTEXT series to the selected plot —
    grey on the plot, named in its legend and listed above it — a family's
    "+ new plot" is grey, and a lone variable's makes a watch plot after
    the others, selected. Discard puts it all back; `_served` checks the
    spec file never moved."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        cards = page.locator(".dstf-plot")
        cards.nth(1).scroll_into_view_if_needed()
        cards.nth(1).locator(".dstf-card-title").click()
        _ring_is(page, 1)
        _open_vars(page)
        target = page.locator(".dstf-var-target")
        expect(target).to_have_text(
            "Checkboxes add context to plot 2 of 2", timeout=EXPECT_TIMEOUT
        )

        z = _var_row(page, "z")
        expect(z.locator(".dstf-var-new")).to_be_hidden()
        z.hover()
        expect(z.locator(".dstf-var-new")).to_be_visible(timeout=EXPECT_TIMEOUT)
        target.hover()
        expect(z.locator(".dstf-var-new")).to_be_hidden(timeout=EXPECT_TIMEOUT)

        z.locator('[role="checkbox"]').click()
        expect(z.locator('[role="checkbox"]')).to_have_attribute(
            "aria-checked", "true", timeout=EXPECT_TIMEOUT
        )
        expect(cards.nth(1).locator(".dstf-context")).to_contain_text(
            "z", timeout=EXPECT_TIMEOUT
        )
        cards.nth(1).scroll_into_view_if_needed()
        expect(cards.nth(1).locator(".js-plotly-plot")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('.dstf-plot')[1]"
            ".querySelectorAll('.legendtext')].some(t => t.textContent === "
            "'z · context')",
            timeout=EXPECT_TIMEOUT,
        )
        _shot(page, tmp_path, "var-check")

        port = _var_row(page, "port[2, 1].C[1:3]")
        port.hover()
        expect(port.locator(".dstf-var-new")).to_be_disabled(timeout=EXPECT_TIMEOUT)

        a0 = _var_row(page, "a0")
        a0.hover()
        a0.locator(".dstf-var-new").click()
        expect(cards).to_have_count(3, timeout=EXPECT_TIMEOUT)
        _ring_is(page, 2)
        expect(target).to_have_text(
            "Checkboxes add context to plot 3 of 3", timeout=EXPECT_TIMEOUT
        )
        cards.nth(2).scroll_into_view_if_needed()
        expect(cards.nth(2)).to_contain_text("No criteria yet.")
        expect(cards.nth(2).get_by_role("button", name="Remove plot")).to_be_visible()
        _shot(page, tmp_path, "var-new-plot")

        # One op on /plots: the check's and the new plot's are one edit.
        assert "1 pending edit" in _pending(page)
        page.locator(".dstf-header-row").get_by_role(
            "button", name=re.compile(r"^Discard")
        ).click()
        expect(cards).to_have_count(2, timeout=EXPECT_TIMEOUT)
        _ring_is(page, 1)


#: An element's computed background, and the selection token's as the
#: browser resolves it on a probe element.
BACKGROUNDS = """e => {
  const probe = document.createElement('div');
  probe.style.background = 'var(--dstf-selection)';
  document.body.appendChild(probe);
  const token = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return [getComputedStyle(e).backgroundColor, token];
}"""


def _tooltip_width(page, hover, text: str) -> float:
    """Hover ``hover`` and measure the tooltip that says ``text``."""
    hover.hover()
    tip = page.locator(".q-tooltip").filter(has_text=text)
    expect(tip).to_be_visible(timeout=EXPECT_TIMEOUT)
    box = tip.bounding_box()
    assert box is not None
    return box["width"]


def test_the_drawers_target_is_a_tag_and_a_subjects_check_says_why(
    tmp_path, playwright_browser
):
    """The drawer's target line is a tag on the accent tint with the ring
    inside it, as the owner's board drew it: a bare ring beside the text
    read as an empty checkbox. The selected plot's subject is on and grey,
    and its tooltip says why — on the span around it, since a disabled
    Quasar button takes no pointer events."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        target = page.locator(".dstf-var-target")
        expect(target).to_have_text(
            "Checkboxes add context to plot 1 of 2", timeout=EXPECT_TIMEOUT
        )
        background, token = target.evaluate(BACKGROUNDS)
        assert background == token and token != "rgba(0, 0, 0, 0)", background
        assert target.locator(".dstf-var-ring").count() == 1

        check = _var_row(page, "x").locator('[role="checkbox"]')
        expect(check).to_be_disabled()
        expect(check).to_have_attribute("aria-checked", "true")
        width = _tooltip_width(page, check.locator(".."), "The plot's subject")
        _shot(page, tmp_path, "subject-check")
        assert width <= 330, width


def test_a_drawer_row_answers_the_keyboard_and_a_screen_reader(
    tmp_path, playwright_browser
):
    """A check is named by its row's text — its only text was the icon's
    ligature, ``check_box_outline_blank`` — and a family's chevron says
    what it opens and whether it is open. "+ new plot" shows on the hovered
    row only (the owner's ruling, Q6 A), and a `visibility: hidden` button
    takes no focus: Tab from a check skipped its own "+ new plot". It shows
    while anything in its row has focus, so Tab reaches it."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        expect(page.locator(".dstf-var-target")).to_have_text(
            "Checkboxes add context to plot 1 of 2", timeout=EXPECT_TIMEOUT
        )
        drawer = page.locator(".dstf-drawer-panel")
        check = drawer.get_by_role("checkbox", name="z", exact=True)
        expect(check).to_have_count(1)
        chevron = drawer.get_by_role("button", name="Open pipe.T[1:3]", exact=True)
        expect(chevron).to_have_attribute("aria-expanded", "false")
        chevron.click()
        chevron = drawer.get_by_role("button", name="Close pipe.T[1:3]", exact=True)
        expect(chevron).to_have_attribute(
            "aria-expanded", "true", timeout=EXPECT_TIMEOUT
        )

        z = _var_row(page, "z")
        new = z.locator(".dstf-var-new")
        page.mouse.move(5, 895)
        expect(new).to_be_hidden()
        check.focus()
        page.keyboard.press("Tab")
        expect(new).to_be_focused(timeout=EXPECT_TIMEOUT)
        expect(new).to_be_visible()
        _shot(page, tmp_path, "var-keyboard")


#: Every plot card's toolbar — Focus, and Remove plot or the derived
#: plot's icon: where its right edge is.
TOOLS_RIGHTS = """() => [...document.querySelectorAll('.dstf-plot')].map(c =>
  c.querySelector('.dstf-card-tools').getBoundingClientRect().right)"""

#: Each element matching the selector: its height in its own line heights.
LINES = """(sel) => [...document.querySelectorAll(sel)].map(e => {
  const s = getComputedStyle(e);
  const line = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2;
  return [e.textContent.trim(), e.getBoundingClientRect().height / line];
})"""

#: Each plot card's title: it wraps inside the card's header row rather
#: than shrinking in it.
TITLES = ".dstf-card-title .text-subtitle2"

#: How far short of its row's right end each card's toolbar and each
#: criteria row's node controls stop: both are pushed to that end, on the
#: title's or the name's line or, their row wrapped, on the next.
RIGHT_GAPS = """() => {
  const gap = (row, el) => {
    const r = row.getBoundingClientRect(), s = getComputedStyle(row);
    return r.right - parseFloat(s.paddingRight) - el.getBoundingClientRect().right;
  };
  const tools = [...document.querySelectorAll('.dstf-card-head')].map(
    h => gap(h, h.querySelector(':scope > .dstf-card-tools')));
  const nodes = [...document.querySelectorAll('.dstf-crit')].map(
    r => gap(r, r.querySelector(':scope > .dstf-node-host')));
  return [tools, nodes];
}"""


def _pushed_right(page, cards: int) -> None:
    tools, nodes = page.evaluate(RIGHT_GAPS)
    assert len(tools) == cards and all(abs(g) <= 4 for g in tools), tools
    assert nodes and all(abs(g) <= 4 for g in nodes), nodes


#: One element's height in its own line heights.
LINES_OF = """e => { const s = getComputedStyle(e);
  return e.getBoundingClientRect().height / parseFloat(s.lineHeight); }"""

#: A model id as long as a real library's: FakeLib's `FakeLib.A` left the
#: Test page's header room enough that nothing beside it wrapped.
LONG_MODEL = "FakeLib.HeatExchangers.GenericDistributed.Examples.CounterFlowWithWall"
#: Longer still, the length of a TRANSFORM example's id: with the subheader
#: kept to one line, Accept and Rerun ended wholly past a 900 px window.
TRANSFORM_MODEL = (
    "TRANSFORM.HeatExchangers.GenericDistributed_HX.Examples."
    "GenericDistributed_HX_withMass"
)
#: Longer than a 900 px window's line: the id breaks inside itself.
OVERLONG_MODEL = TRANSFORM_MODEL.replace("Examples.", "Examples.Variants.WithWall.")

#: The Test page's subheader and the status strip under it: each child
#: that starts left of the window or ends past its right edge, or whose
#: text overflows its own box (cut off, as an id that cannot break is
#: inside a box that fits), each of their buttons whose label is taller
#: than `k` line heights (1.5 finds a second line, 2.5 a third), and how
#: far short of the strip's right edge the TEST actions stop.
SUBHEADER_PAST = """(k) => {
  const past = [], tall = [];
  const sub = document.querySelector('.dstf-subheader');
  // The TEST actions are the status strip's, the header's third row.
  const strip = document.querySelector('.dstf-strip');
  for (const el of [...sub.children, ...strip.children]) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > window.innerWidth) past.push([el.textContent, r.right]);
    if (r.width && r.left < 0) past.push([el.textContent, r.left]);
    if (el.scrollWidth > el.clientWidth + 1) past.push([el.textContent, el.scrollWidth]);
  }
  const pad = parseFloat(getComputedStyle(strip).paddingRight);
  const act = strip.querySelector(':scope > .dstf-strip-actions').getBoundingClientRect();
  for (const b of [...sub.querySelectorAll('.q-btn'), ...strip.querySelectorAll('.q-btn')]) {
    const c = b.querySelector('.q-btn__content') || b;
    const s = getComputedStyle(c);
    const line = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2;
    const h = c.getBoundingClientRect().height;
    if (h > k * line) tall.push([b.textContent, h, line]);
  }
  return [past, tall, strip.getBoundingClientRect().right - pad - act.right];
}"""


def _no_sideways_scroll(page) -> None:
    page.wait_for_function(
        "document.documentElement.scrollWidth == window.innerWidth",
        timeout=EXPECT_TIMEOUT,
    )


def test_plot_cards_wrap_at_900_px_beside_a_drawer(tmp_path, playwright_browser):
    """3c's gate over a real library: at 900 px with either drawer open the
    page scrolled sideways (1030 px, 1202 after an edit), because a card's
    header row — its title, pill(s), error select and toolbar — neither
    wrapped nor shrank, on names as long as `LONG`;
    with that fixed, a criteria row's one-line name still pushed its node
    controls 54 px past the edge. Both rows now wrap, before and after an
    edit, and a card's title wraps rather than shrinks: with the header row
    kept to one line it squeezed to three and four lines at 900 px. In the
    Variables drawer a long name is cut, and "+ new plot" and "plot 2, 3"
    keep one line. With an edit pending and a model id as long as a real
    library's, a TRANSFORM example's, or longer than a line, nothing in the
    Test page's subheader is cut off at 1280 or 900 px: kept to one line, it
    put Accept and Rerun past the window's edge. Its buttons keep one line
    at 1280 and at most two at 900 (the owner's ruling for the header), the
    TEST actions end at the status strip's right edge (the strip under it
    holds them now), and "← Suite" keeps one line. The
    row wraps rather than squeezing the id: a subheader kept to one line
    fitted by breaking the id over lines it did not need. A
    card's toolbar and a criteria row's node controls end at their row's
    right edge, wrapped or not."""
    with _served(tmp_path, playwright_browser, project=_long_names_project) as page:
        page.set_viewport_size({"width": 900, "height": 900})
        cards = page.locator(".dstf-plot")
        expect(cards).to_have_count(2, timeout=EXPECT_TIMEOUT)
        # The first name's card holds the second's long name as context.
        expect(page.locator(".dstf-context")).to_have_count(1)
        expect(page.locator(".dstf-context")).to_contain_text(LONG[1])
        rail = page.locator(".dstf-rail .q-btn")
        for i, drawer in enumerate(("tests", "variables")):
            rail.nth(i).click()
            expect(rail.nth(i)).to_have_class(
                re.compile(r"dstf-rail-on"), timeout=EXPECT_TIMEOUT
            )
            _no_sideways_scroll(page)
            rights = page.evaluate(TOOLS_RIGHTS)
            assert len(rights) == 2 and max(rights) <= 900, (drawer, rights)
            titles = page.evaluate(LINES, TITLES)
            assert len(titles) == 2 and all(n <= 2.5 for _, n in titles), (
                drawer,
                titles,
            )
            _pushed_right(page, 2)
            _shot(page, tmp_path, f"cards-900-{drawer}")

        # A title with no place to break: it is cut (a variable label, cut
        # in its middle) rather than scrolling the page sideways (1477 px
        # without that).
        title = page.locator(TITLES).first
        kept = title.text_content()
        title.evaluate("(e, text) => { e.textContent = text; }", "Q" + "x" * 149)
        _no_sideways_scroll(page)
        rights = page.evaluate(TOOLS_RIGHTS)
        assert len(rights) == 2 and max(rights) <= 900, rights
        title.evaluate("(e, text) => { e.textContent = text; }", kept)
        expect(title).to_have_text(kept)

        # The Variables drawer at 320 px, on names longer than it.
        rows = page.locator(".dstf-var-row")
        expect(rows).to_have_count(3, timeout=EXPECT_TIMEOUT)
        for text in (*LONG, LONG_WATCH):
            expect(_var_row(page, text).locator(".dstf-var-text")).to_have_attribute(
                "title", text
            )
        for sel in (".dstf-var-row .dstf-var-new", ".dstf-var-where"):
            lines = page.evaluate(LINES, sel)
            assert lines and all(n <= 1.5 for _, n in lines), (sel, lines)

        # After an edit: a third plot, a watch plot with Remove plot.
        watch = _var_row(page, LONG_WATCH)
        watch.hover()
        watch.locator(".dstf-var-new").click()
        expect(cards).to_have_count(3, timeout=EXPECT_TIMEOUT)
        expect(cards.nth(2).get_by_role("button", name="Remove plot")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        _no_sideways_scroll(page)
        rights = page.evaluate(TOOLS_RIGHTS)
        assert len(rights) == 3 and max(rights) <= 900, rights
        titles = page.evaluate(LINES, TITLES)
        assert len(titles) == 3 and all(n <= 2.5 for _, n in titles), titles
        _pushed_right(page, 3)
        _shot(page, tmp_path, "cards-900-edit")

        model = page.locator(".dstf-subheader").locator(".dstf-mono").first
        back = page.get_by_role("link", name="← Suite")
        for text in (LONG_MODEL, TRANSFORM_MODEL, OVERLONG_MODEL):
            model.evaluate("(e, text) => { e.textContent = text; }", text)
            expect(model).to_have_text(text)
            for width in (1280, 900):
                page.set_viewport_size({"width": width, "height": 900})
                _no_sideways_scroll(page)
                past, tall, gap = page.evaluate(
                    SUBHEADER_PAST, 1.5 if width >= 1280 else 2.5
                )
                assert past == [] and tall == [], (text, width, past, tall)
                assert abs(gap) <= 4, (text, width, gap)
                # The header grew by the strip and a wrapped sub-header:
                # the page's top padding follows it, and nothing is under it.
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_function(
                    "document.querySelector('.dstf-test').getBoundingClientRect().top"
                    " >= document.querySelector('.q-header')"
                    ".getBoundingClientRect().bottom - 1",
                    timeout=EXPECT_TIMEOUT,
                )
                assert back.evaluate(LINES_OF) <= 1.5, (text, width)
                # The row wraps rather than squeezing the id: an id a line
                # holds keeps one line, and the one no line holds two.
                most = 2.5 if text == OVERLONG_MODEL and width < 1280 else 1.5
                assert model.evaluate(LINES_OF) <= most, (text, width)
        _shot(page, tmp_path, "subheader-900-edit")
        page.locator(".dstf-header-row").get_by_role(
            "button", name=re.compile(r"^Discard")
        ).click()
        expect(cards).to_have_count(2, timeout=EXPECT_TIMEOUT)


# -- a combinator's kind is a dropdown in its row (Q3 A) --------------------

#: The kinds every combinator's dropdown lists, in the registry's order.
KINDS = ["and", "or", "k-of-n", "warn", "weighted"]
UNWRAP = "unwrap — its children take its place"


def _nested_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` with A's tree one level deeper: `and[k-of-n(k=1)[tube
    · x, range · x], range · y]` — a root of two, whose dropdown offers no
    unwrap, over a k-of-n of two, whose dropdown does."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            tube, range_x, range_y = entry["metrics"]["children"]
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {"combinator": "k-of-n", "k": 1, "children": [tube, range_x]},
                    range_y,
                ],
            }
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


def test_a_combinators_kind_dropdown_opens_with_a_real_mouse(
    tmp_path, playwright_browser
):
    """The kind is a dropdown between a combinator row's pill and its count.
    A real click opens it: on the k-of-n, the five kinds, a separator and
    the unwrap entry; on the root of two, the five kinds alone (unwrap is
    refused there by rule). A refused `warn` says why under the row and the
    dropdown reads the node's kind again."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
        rows = page.locator(".dstf-tree-row")
        expect(rows).to_have_count(5, timeout=EXPECT_TIMEOUT)
        root, k_of_n = rows.nth(0), rows.nth(1)
        expect(root).to_contain_text("3 criteria", timeout=EXPECT_TIMEOUT)
        expect(k_of_n).to_contain_text("k=1 · 2 criteria")
        # Between the pill and the count, on the row's one line.
        pill = k_of_n.locator(".dstf-pill").first.bounding_box()
        kind_box = k_of_n.get_by_label("Combinator kind").bounding_box()
        text = k_of_n.get_by_text("k=1 · 2 criteria").bounding_box()
        assert pill is not None and kind_box is not None and text is not None
        assert pill["x"] + pill["width"] <= kind_box["x"] + 1, (pill, kind_box)
        assert kind_box["x"] + kind_box["width"] <= text["x"] + 1, (kind_box, text)

        k_of_n.get_by_label("Combinator kind").click()
        options = page.get_by_role("option")
        expect(options).to_have_count(6, timeout=EXPECT_TIMEOUT)
        assert [o.strip() for o in options.all_inner_texts()] == [*KINDS, UNWRAP]
        assert page.locator(".q-menu .q-separator").count() == 1
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "kind-open")
        page.keyboard.press("Escape")
        expect(options).to_have_count(0, timeout=EXPECT_TIMEOUT)

        # An option chosen in the k-of-n's own dropdown is the dropdown's:
        # weighted opens the form preset to weighted, not the row's k.
        k_of_n.get_by_label("Combinator kind").click()
        page.get_by_role("option", name="weighted").click()
        form = page.locator(".dstf-form-row")
        expect(form.get_by_label("Threshold")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(form.get_by_label("k — how many must pass")).to_have_count(0)
        form.get_by_role("button", name="Cancel").click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # A k-of-n's row opens its fields (U5): its form holds k alone.
        k_of_n.get_by_text("k=1 · 2 criteria").click()
        expect(form).to_contain_text(
            "Changes this node's fields; its children stay.", timeout=EXPECT_TIMEOUT
        )
        expect(form.get_by_label("k — how many must pass")).to_have_value("1")
        assert [t.strip() for t in form.get_by_role("button").all_inner_texts()] == [
            "Cancel",
            "Apply",
        ]
        row_box, form_box = k_of_n.bounding_box(), form.bounding_box()
        assert row_box is not None and form_box is not None
        assert form_box["y"] >= row_box["y"] + row_box["height"] - 1, (
            row_box,
            form_box,
        )
        form.get_by_role("button", name="Cancel").click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)

        root.get_by_label("Combinator kind").click()
        expect(options).to_have_count(5, timeout=EXPECT_TIMEOUT)
        assert [o.strip() for o in options.all_inner_texts()] == KINDS
        assert page.locator(".q-menu .q-separator").count() == 0
        page.get_by_role("option", name="warn").click()
        expect(page.locator(".dstf-tree .text-negative").first).to_have_text(
            "'warn' combinator requires exactly 1 child, got 2",
            timeout=EXPECT_TIMEOUT,
        )
        expect(root.get_by_label("Combinator kind")).to_contain_text("and")
        expect(page.locator(".dstf-header-row")).to_contain_text("0 pending edits")


# -- move a subtree (U4) ----------------------------------------------------

MENU_ACTIVE = "() => document.activeElement.innerText.replace(/\\s+/g, ' ').trim()"

#: The tree card's live region, kept to compare with the one there later.
LIVE_AT_FIRST = "() => { window.liveAtFirst = document.querySelector('.dstf-live'); }"
SAME_LIVE = "() => document.querySelector('.dstf-live') === window.liveAtFirst"


def test_move_to_answers_the_keyboard_and_moves_the_row(tmp_path, playwright_browser):
    """range · y's Move to…, by the keyboard alone: Enter opens its menu
    (the button says so), End goes to the last place that is a CHOICE —
    "where it is", greyed, is skipped — Home to the first, and Enter there
    moves y to the root's front: the row moves, one edit is pending, and
    the moved row says so under it, in view (V4)."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
        rows = page.locator(".dstf-tree-row")
        expect(rows).to_have_count(5, timeout=EXPECT_TIMEOUT)
        expect(rows.nth(4)).to_contain_text("range · y")
        button = rows.nth(4).get_by_role("button", name="Move to…")
        button.focus()
        page.keyboard.press("Enter")
        items = page.get_by_role("menuitem")
        expect(items).to_have_count(7, timeout=EXPECT_TIMEOUT)
        expect(button).to_have_attribute("aria-expanded", "true")
        expect(items.nth(6)).to_have_attribute("aria-disabled", "true")
        # The menu takes the focus once its transition ends.
        expect(page.locator(".dstf-move-menu")).to_be_focused(timeout=EXPECT_TIMEOUT)
        page.keyboard.press("End")
        assert page.evaluate(MENU_ACTIVE) == "k-of-n · 2 criteria · k=1 — at the end"
        page.keyboard.press("Home")
        assert page.evaluate(MENU_ACTIVE) == (
            "and · 3 criteria (root) — before k-of-n · 2 criteria · k=1"
        )
        page.evaluate(LIVE_AT_FIRST)
        page.keyboard.press("Enter")
        expect(rows.nth(1)).to_contain_text("range · y", timeout=EXPECT_TIMEOUT)
        expect(items).to_have_count(0)
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")
        # The moved row, in view, says it moved on one line right under it.
        said = page.locator(".dstf-moved")
        expect(said).to_have_text("Moved.", timeout=EXPECT_TIMEOUT)
        # Told to the card's live region, there since the page was built:
        # its text changed, so it is read out. The line is none.
        live = page.get_by_role("status").and_(page.locator(".dstf-live"))
        expect(live).to_have_text("Moved.")
        assert page.evaluate(SAME_LIVE)
        expect(said).not_to_have_attribute("role", re.compile(".*"))
        page.wait_for_timeout(300)
        line, row = said.bounding_box(), rows.nth(1).bounding_box()
        assert line is not None and row is not None
        assert row["y"] >= 0 and line["y"] + line["height"] <= VIEWPORT["height"]
        assert 0 <= line["y"] - (row["y"] + row["height"]) < 12, (row, line)


#: A 100-character expression for x: its rows' names, and "before" them.
LONG_X = (
    "pipe.mediums[3].state.T - pipe.mediums[2].state.T + "
    "heatTransfer.surfaceConductance[1].Q_flow * 1e-3"
)

#: Each Move to… place's widths against its menu's: the item's and its
#: label's, the node text's (the label's first cut), and the menu's own.
MOVE_WIDTHS = """() => {
  const menu = document.querySelector('.dstf-move-menu');
  return [...menu.querySelectorAll('[role="menuitem"]')].map(i => {
    const l = i.querySelector('.dstf-move-label');
    return {text: l.innerText.replace(/\\s+/g, ' ').slice(0, 50),
            item: i.scrollWidth, label: l.scrollWidth,
            node: l.querySelector(':scope > .dstf-mid').getBoundingClientRect().width,
            menu: menu.clientWidth, menuScroll: menu.scrollWidth};
  });
}"""


@pytest.mark.parametrize("width", [1280, 900])
def test_a_long_place_is_cut_in_the_middle_inside_the_menu(
    tmp_path, playwright_browser, width
):
    """x reads a 100-character expression: y's Move to… names it in its
    places ("— before range · <x>") and as a node ("range · <x>"). The
    named row is cut in its middle, "— before" stays whole, and the node's
    text keeps a width: no place is wider than the menu."""
    assert len(LONG_X) == 100
    served = _served(tmp_path, playwright_browser, project=_nested_project, hooks=True)
    with served as page:
        base = page.url.split("test/")[0]
        query = urllib.parse.urlencode({"name": "x", "expression": LONG_X})
        with urllib.request.urlopen(f"{base}__expression?{query}") as r:
            assert json.loads(r.read()) == {"stamped": 1}
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        _show_tree(page, 5)
        _tree_row(page, "/metrics/children/1").get_by_role(
            "button", name="Move to…"
        ).click()
        items = page.get_by_role("menuitem")
        expect(items.first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(400)  # the menu's transition
        places = page.evaluate(MOVE_WIDTHS)
        _shot(page, tmp_path, f"move-long-{width}")
        assert any(LONG_X[:20] in p["text"] for p in places), places
        for p in places:
            assert p["item"] <= p["menu"] and p["label"] <= p["menu"], p
            assert p["menuScroll"] <= p["menu"], p
            assert p["node"] > 0, p


def test_a_move_said_while_the_loop_is_busy_is_still_read_out(
    tmp_path, playwright_browser
):
    """A move's line said and the UI loop then held 0.4 s: the line and its
    text arrive in one message. What a screen reader is told is no part of
    that line: the card's live region, there and empty since the page was
    built, changes its text in place — and that is read out."""
    with _served(
        tmp_path, playwright_browser, project=_nested_project, hooks=True
    ) as page:
        _show_tree(page, 5)
        live = page.locator(".dstf-live")
        expect(live).to_have_text("")
        page.evaluate(LIVE_AT_FIRST)
        base = page.url.split("test/")[0]
        query = urllib.parse.urlencode({"path": "/metrics/children/1", "block": 0.4})
        with urllib.request.urlopen(f"{base}__say?{query}") as r:
            assert json.loads(r.read()) == {"said": 1}
        expect(page.locator(".dstf-moved")).to_have_text(
            "Moved.", timeout=EXPECT_TIMEOUT
        )
        expect(live).to_have_text("Moved.")
        assert page.evaluate(SAME_LIVE)


def _tree_row(page, path: str):
    return page.locator(f'.dstf-tree-row[data-tree-path="{path}"]')


def _show_tree(page, rows: int) -> None:
    page.evaluate("() => window.scrollTo(0, 0)")
    page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
    expect(page.locator(".dstf-tree-row")).to_have_count(rows, timeout=EXPECT_TIMEOUT)


#: A target row's place for a real mouse: its top half is before it, its
#: bottom half onto it (D1 as amended).
BEFORE, ONTO = 0.2, 0.8


def _mouse_drag(page, src: str, target: str, frac: float, during=None):
    """A REAL mouse from ``src``'s handle to ``frac`` of ``target``'s row
    height — `page.mouse` down / move / up, which Chromium turns into
    dragstart / dragover / drop — and ``during(page)``'s answer, read while
    the button is still down."""
    grip = _tree_row(page, src).locator(".dstf-grip").bounding_box()
    assert grip is not None
    page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
    page.mouse.down()
    page.mouse.move(grip["x"] + 12, grip["y"] + 8, steps=4)
    box = _tree_row(page, target).bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * frac
    page.mouse.move(x, y, steps=10)
    page.wait_for_timeout(300)  # the zones' map: one round trip
    page.mouse.move(x + 2, y, steps=2)
    page.wait_for_timeout(100)
    seen = during(page) if during is not None else None
    page.mouse.up()
    return seen


def _classes(path: str):
    return lambda page: _tree_row(page, path).get_attribute("class") or ""


def _plot_titles(page) -> list[str]:
    return [
        t.strip()
        for t in page.locator(".dstf-plot .dstf-card-title .dstf-mid").all_inner_texts()
    ]


#: Where ``p``'s row content starts, from the row's left: its chevron's place.
CONTENT_LEFT = """(p) => {
  const r = document.querySelector(`[data-tree-path="${p}"]`);
  return Math.round(r.querySelector(':scope > .dstf-chev-slot').getBoundingClientRect().x
    - r.getBoundingClientRect().x);
}"""
#: The "Moved." line's text start and its row's pill start, page x.
MOVED_LINE = """(p) => {
  const r = document.querySelector(`[data-tree-path="${p}"]`);
  const l = document.querySelector('.dstf-moved');
  return {pill: Math.round(r.querySelector('.dstf-pill').getBoundingClientRect().x),
          text: Math.round(l.getBoundingClientRect().x
            + parseFloat(getComputedStyle(l).paddingLeft))};
}"""


def test_a_drag_before_a_row_reorders_the_tree_and_the_plots(
    tmp_path, playwright_browser
):
    """range · y by its handle to the k-of-n row's top half: an accent line
    drawn from that row's indent, then y is the root's first child and its
    plot the first plot (the plots follow the tree, D140)."""
    k, y = "/metrics/children/0", "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        assert _plot_titles(page)[:2] == ["x", "y"]

        def during(page):
            row = _tree_row(page, k)
            line = page.evaluate(
                "(p) => getComputedStyle(document.querySelector("
                "`[data-tree-path=\"${p}\"]`), '::before').left",
                k,
            )
            return row.get_attribute("class") or "", line

        content = page.evaluate(CONTENT_LEFT, k)
        classes, line = _mouse_drag(page, y, k, BEFORE, during)
        assert "dstf-drop-before" in classes, classes
        # From the k-of-n's content, right of its one bar (C15, U5 E4).
        assert line == f"{content}px", (line, content)
        assert content == 8 + 12
        expect(_tree_row(page, "/metrics/children/0")).to_contain_text(
            "range · y", timeout=EXPECT_TIMEOUT
        )
        # The move's line starts under the moved row's pill (E4).
        expect(page.locator(".dstf-moved")).to_be_visible(timeout=EXPECT_TIMEOUT)
        at = page.evaluate(MOVED_LINE, "/metrics/children/0")
        assert at["text"] == at["pill"], at
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")
        expect(
            page.locator(".dstf-plot .dstf-card-title .dstf-mid").first
        ).to_have_text("y", timeout=EXPECT_TIMEOUT)


def test_a_drag_onto_a_combinator_appends_and_onto_a_criterion_wraps(
    tmp_path, playwright_browser
):
    k, kx, ky = (
        "/metrics/children/0",
        "/metrics/children/0/children/0",
        "/metrics/children/0/children/1",
    )
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        classes = _mouse_drag(page, "/metrics/children/1", k, ONTO, _classes(k))
        assert "dstf-drop-onto" in classes, classes
        expect(_tree_row(page, f"{k}/children/2")).to_contain_text(
            "range · y", timeout=EXPECT_TIMEOUT
        )
        # range · x onto tube · x: both wrapped in an `and`, the target first.
        classes = _mouse_drag(page, ky, kx, ONTO, _classes(kx))
        assert "dstf-drop-onto" in classes, classes
        expect(_tree_row(page, f"{kx}/children/1")).to_contain_text(
            "range · x", timeout=EXPECT_TIMEOUT
        )
        expect(_tree_row(page, f"{kx}/children/0")).to_contain_text("tube · x")
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")


def test_a_refused_place_says_why_in_the_header_line_and_takes_nothing(
    tmp_path, playwright_browser
):
    """The k-of-n onto its own criterion: the row outlined as refused, the
    reason in the tree card's header line in place of its text — the row's
    icons stay where they were — and nothing staged. A second drag, from
    another row, draws by ITS map: there the same zone is a wrap."""
    from dstf.authoring.tree_move import INTO_ITSELF

    k, kx = "/metrics/children/0", "/metrics/children/0/children/0"
    icons = (
        '(p) => Math.round(document.querySelector(`[data-tree-path="${p}"]`)'
        ".querySelector('.dstf-node-host').getBoundingClientRect().x)"
    )
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        line = page.locator(".dstf-tree-line")
        x_before = page.evaluate(icons, kx)

        def during(page):
            return (
                _tree_row(page, kx).get_attribute("class") or "",
                line.get_attribute("data-why"),
                line.locator("button").first.is_visible(),
                page.evaluate(icons, kx),
            )

        classes, why, toggle_shown, x_during = _mouse_drag(page, k, kx, ONTO, during)
        assert "dstf-drop-no" in classes, classes
        assert why == INTO_ITSELF
        assert not toggle_shown  # the reason is the line's whole text
        assert x_during == x_before
        page.wait_for_timeout(300)
        assert line.get_attribute("data-why") is None
        expect(line.get_by_role("button", name="Hide tree")).to_be_visible()
        expect(page.locator(".dstf-header-row")).to_contain_text("0 pending edits")
        classes = _mouse_drag(page, "/metrics/children/1", kx, ONTO, _classes(kx))
        assert "dstf-drop-onto" in classes and "dstf-drop-no" not in classes
        expect(_tree_row(page, f"{kx}/children/1")).to_contain_text(
            "range · y", timeout=EXPECT_TIMEOUT
        )


#: Starts a drag on ``p``'s handle with a stale map in place and reads the
#: map in the SAME task — before any server reply can land — then ends it.
STALE_MAP = """(p) => {
  window.dstfMove = window.dstfMove || {src: null, map: {}};
  window.dstfMove.map = {stale: {onto: 'the last drag'}};
  const grip = document.querySelector(`[data-tree-path="${p}"] .dstf-grip`);
  const dt = new DataTransfer();
  grip.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
  const seen = JSON.stringify(window.dstfMove.map);
  grip.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
  return seen;
}"""


def test_a_drag_starts_with_no_map_until_its_own_arrives(tmp_path, playwright_browser):
    """C17: a reply slower than the next drag must not draw that drag with
    the last one's refusals — every drag starts from an empty map."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        assert page.evaluate(STALE_MAP, "/metrics/children/1") == "{}"


#: What a drag has drawn: the header line's reason, each row outlined
#: (path:class), and the drag's source in the page's record of it.
DRAWN = """() => {
  const line = document.querySelector('.dstf-tree-line');
  const drop = [...document.querySelectorAll('.dstf-tree-row')]
    .filter((r) => /dstf-drop-(before|onto|no)/.test(r.className))
    .map((r) => r.dataset.treePath + ':'
      + r.className.match(/dstf-drop-(before|onto|no)/)[0]);
  return {why: line.dataset.why ?? null, drop,
          src: window.dstfMove ? window.dstfMove.src : 'none'};
}"""


def _grab(page, src: str) -> None:
    """A REAL mouse down on ``src``'s handle, moved enough to start a drag."""
    grip = _tree_row(page, src).locator(".dstf-grip").bounding_box()
    assert grip is not None
    x, y = grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 12, y + 8, steps=4)


def _hover(page, target: str, frac: float) -> None:
    """The held mouse to ``frac`` of ``target``'s row height, settled."""
    box = _tree_row(page, target).bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * frac
    page.mouse.move(x, y, steps=10)
    page.wait_for_timeout(300)  # the zones' map: one round trip
    page.mouse.move(x + 2, y, steps=2)
    page.wait_for_timeout(100)


def test_a_drag_ended_anywhere_leaves_nothing_drawn(tmp_path, playwright_browser):
    """Every way a drag ends or leaves leaves no outline, no reason in the
    header line and no source behind: Escape over a refused place; out of
    the tree with the button held, then released outside; from an allowed
    place to the dragged row itself (where it is: nothing drawn); released
    on its own handle. And the root's row is all "onto": its top half is no
    "before". Nothing is staged by any of it."""
    k, kx = "/metrics/children/0", "/metrics/children/0/children/0"
    y = "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        none = "0 pending edits"
        # Escape over a refused place (the k-of-n onto its own criterion).
        _grab(page, k)
        _hover(page, kx, ONTO)
        held = page.evaluate(DRAWN)
        assert held["drop"] == [f"{kx}:dstf-drop-no"] and held["why"]
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert page.evaluate(DRAWN)["drop"] == []
        assert page.evaluate(DRAWN)["why"] is None
        page.mouse.up()
        page.wait_for_timeout(300)
        assert page.evaluate(DRAWN)["src"] is None
        expect(page.locator(".dstf-header-row")).to_contain_text(none)
        # Out of the tree with the button held, released outside.
        _grab(page, k)
        _hover(page, kx, ONTO)
        plot = page.locator(".js-plotly-plot").first.bounding_box()
        assert plot is not None
        page.mouse.move(
            plot["x"] + plot["width"] / 2, plot["y"] + plot["height"] / 2, steps=12
        )
        page.wait_for_timeout(300)
        outside = page.evaluate(DRAWN)
        assert outside["drop"] == [] and outside["why"] is None
        page.mouse.up()
        page.wait_for_timeout(300)
        assert page.evaluate(DRAWN)["src"] is None
        expect(page.locator(".dstf-header-row")).to_contain_text(none)
        # From an allowed place to the dragged row's own top half.
        _grab(page, y)
        _hover(page, kx, ONTO)
        assert page.evaluate(DRAWN)["drop"] == [f"{kx}:dstf-drop-onto"]
        _hover(page, y, BEFORE)
        assert page.evaluate(DRAWN)["drop"] == []
        # Released on its own handle: nothing staged, nothing said.
        grip = _tree_row(page, y).locator(".dstf-grip").bounding_box()
        assert grip is not None
        page.mouse.move(
            grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2, steps=8
        )
        page.wait_for_timeout(200)
        page.mouse.up()
        page.wait_for_timeout(300)
        expect(page.locator(".dstf-header-row")).to_contain_text(none)
        assert page.locator(".text-negative:visible").all_inner_texts() == []
        # The root's top half: onto it, never before it.
        _grab(page, kx)
        _hover(page, "/metrics", 0.15)
        assert page.evaluate(DRAWN)["drop"] == ["/metrics:dstf-drop-onto"]
        page.keyboard.press("Escape")
        page.mouse.up()
        page.wait_for_timeout(300)
        expect(page.locator(".dstf-header-row")).to_contain_text(none)


#: Over ``p``'s row, onto it: a dragover carrying ``mime`` alone and, when
#: ``drop``, a drop — with a source in place, as a drag of ours leaves it.
#: Whether the row was drawn on and whether each event was taken.
FOREIGN = """([p, mime, drop]) => {
  const D = window.dstfMove || (window.dstfMove = {src: null, map: {}});
  D.src = '/metrics/children/1'; D.map = {};
  const row = document.querySelector(`[data-tree-path="${p}"]`);
  const r = row.getBoundingClientRect();
  const at = {bubbles: true, cancelable: true,
              clientX: r.x + r.width * 0.4, clientY: r.y + r.height * 0.8};
  const dt = new DataTransfer();
  dt.setData(mime, 'x');
  const over = new DragEvent('dragover', {...at, dataTransfer: dt});
  row.dispatchEvent(over);
  const out = [/dstf-drop/.test(row.className), over.defaultPrevented];
  if (drop) {
    const ev = new DragEvent('drop', {...at, dataTransfer: dt});
    row.dispatchEvent(ev);
    out.push(ev.defaultPrevented);
  }
  row.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
  return out;
}"""


def test_a_drag_that_carries_no_node_draws_and_drops_nothing(
    tmp_path, playwright_browser
):
    """A drag of text or a file over the tree is not a move: the rows read
    the drag's own MIME type, so it outlines no row and takes no drop —
    while one carrying that type does. And a node's drag, by a real mouse,
    carries that type ALONE: no text a field it is released over would
    take."""
    from dstf.authoring.shell.tree_card import MIME

    k, y = "/metrics/children/0", "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        assert page.evaluate(FOREIGN, [k, "text/plain", True]) == [False, False, False]
        assert page.evaluate(FOREIGN, [k, "Files", True]) == [False, False, False]
        assert page.evaluate(FOREIGN, [k, MIME, False]) == [True, True]
        # On the window, so after the rows' own handler has set its data.
        page.evaluate(
            """() => window.addEventListener('dragstart', (e) => {
              window.dragTypes = [...e.dataTransfer.types]; })"""
        )
        grip = _tree_row(page, y).locator(".dstf-grip").bounding_box()
        assert grip is not None
        page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
        page.mouse.down()
        page.mouse.move(grip["x"] + 12, grip["y"] + 8, steps=4)
        page.mouse.move(5, 5, steps=10)  # off the tree: no drop there
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.dragTypes") == [MIME]
        page.mouse.up()
        page.wait_for_timeout(300)
        expect(page.locator(".dstf-header-row")).to_contain_text("0 pending edits")


def test_a_drag_over_no_row_draws_no_place(tmp_path, playwright_browser):
    """From a row's place to a "Moved." line under another row — inside
    the rows, on no row: the outline the row drew goes, and nothing is
    drawn there or dropped."""
    k, y = "/metrics/children/0", "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        _tree_row(page, y).get_by_role("button", name="Move to…").click()
        items = page.get_by_role("menuitem")
        expect(items.first).to_be_visible(timeout=EXPECT_TIMEOUT)
        items.first.click()  # y to the root's front: its line under it
        said = page.locator(".dstf-moved")
        expect(said).to_have_text("Moved.", timeout=EXPECT_TIMEOUT)
        expect(_tree_row(page, k)).to_contain_text("range · y")

        def over_the_line(page):
            drawn = page.locator(".dstf-tree-row.dstf-drop").count()
            box = said.bounding_box()
            assert box is not None
            x, y_ = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y_, steps=6)
            page.wait_for_timeout(200)
            page.mouse.move(x + 2, y_, steps=2)
            page.wait_for_timeout(100)
            return drawn, page.locator(".dstf-tree-row.dstf-drop").count()

        # the k-of-n over y's top half (before it), then over y's line
        drawn, after = _mouse_drag(page, y, k, BEFORE, over_the_line)
        assert (drawn, after) == (1, 0)
        page.wait_for_timeout(300)
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")
        expect(_tree_row(page, k)).to_contain_text("range · y")


def test_a_move_below_the_fold_brings_its_row_and_its_line_into_view(
    tmp_path, playwright_browser
):
    """A 330 px window: the k-of-n moved to the root's end lands below the
    fold. Its row comes into view AND the "Moved." line under it: scrolling
    the row alone to its nearest edge left the line under the fold."""
    k, y = "/metrics/children/0", "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        page.set_viewport_size({"width": 1280, "height": 330})
        _show_tree(page, 5)
        page.evaluate("() => window.scrollTo(0, 0)")
        _tree_row(page, k).get_by_role("button", name="Move to…").click()
        items = page.get_by_role("menuitem")
        expect(items.first).to_be_visible(timeout=EXPECT_TIMEOUT)
        items.filter(has_text="(root)").filter(has_text="at the end").first.click()
        said = page.locator(".dstf-moved")
        expect(said).to_have_text("Moved.", timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        row, line = _tree_row(page, y).bounding_box(), said.bounding_box()
        assert row is not None and line is not None
        expect(_tree_row(page, y)).to_contain_text("k-of-n")
        height = page.evaluate("() => window.innerHeight")
        assert 0 <= row["y"] and row["y"] + row["height"] <= height + 1, row
        assert line["y"] + line["height"] <= height, (line, height)


#: Each tree row's handle: its width, whether it shows, and where the pill
#: right after it starts.
GRIPS = """() => [...document.querySelectorAll('.dstf-tree-row')].map(r => {
  const g = r.querySelector('.dstf-grip');
  return {path: r.dataset.treePath, w: g.getBoundingClientRect().width,
          shown: getComputedStyle(g).visibility !== 'hidden',
          pill: Math.round(g.nextElementSibling.getBoundingClientRect().x)};
})"""


def test_a_hidden_handle_keeps_its_place_and_a_lock_shifts_no_pill(
    tmp_path, playwright_browser
):
    """The root's handle is hidden and keeps its 20 px, so the root's pill
    and a depth-1 pill stand exactly one level's bar (8 px) apart; a job's
    lock hides every handle and moves no pill. Quasar's `.hidden` (what
    `set_visibility(False)` adds) sits in a cascade layer that beats an
    unlayered `!important`, so it took the handle's width with it."""
    k = "/metrics/children/0"
    with _served(
        tmp_path, playwright_browser, project=_nested_project, hooks=True
    ) as page:
        _show_tree(page, 5)
        free = {g["path"]: g for g in page.evaluate(GRIPS)}
        assert free["/metrics"]["w"] == 20 and not free["/metrics"]["shown"]
        assert free[k]["shown"] and free[k]["w"] == 20
        assert free[k]["pill"] - free["/metrics"]["pill"] == 8
        base = page.url.split("test/")[0]
        try:
            with urllib.request.urlopen(f"{base}__lock?model={A}") as r:
                json.loads(r.read())
            expect(page.locator(".dstf-tree .dstf-why").first).to_contain_text(
                "rerun is running", timeout=EXPECT_TIMEOUT
            )
            locked = {g["path"]: g for g in page.evaluate(GRIPS)}
            assert all(not g["shown"] and g["w"] == 20 for g in locked.values())
            assert {p: g["pill"] for p, g in locked.items()} == {
                p: g["pill"] for p, g in free.items()
            }
        finally:
            with urllib.request.urlopen(f"{base}__unlock") as r:
                json.loads(r.read())


# -- the plot's +, inline; the editor opens after Add (Q3 A) ----------------


def test_the_plots_plus_opens_its_row_and_the_new_editor_is_in_view(
    tmp_path, playwright_browser
):
    """The plot's `+` in its criteria header opens the Add row under the
    header with a real click — no dialog — and after Add the new
    criterion's editor is open and inside the viewport, at 1280 and at
    900 px."""
    with _served(tmp_path, playwright_browser) as page:
        for width in (1280, 900):
            page.set_viewport_size({"width": width, "height": VIEWPORT["height"]})
            page.evaluate("() => window.scrollTo(0, 0)")
            card = page.locator(".dstf-plot").first
            head = card.locator(".dstf-criteria-head")
            expect(head).to_contain_text("Criteria", timeout=EXPECT_TIMEOUT)
            plus = head.get_by_role("button", name="Add a criterion for x")
            head_box, plus_box = head.bounding_box(), plus.bounding_box()
            assert head_box is not None and plus_box is not None
            # At the header's right end, inside the viewport.
            assert plus_box["x"] + plus_box["width"] <= width, (width, plus_box)
            assert plus_box["x"] > head_box["x"] + head_box["width"] / 2, (
                head_box,
                plus_box,
            )
            plus.click()
            add = page.get_by_role("button", name="Add", exact=True)
            expect(add).to_be_visible(timeout=EXPECT_TIMEOUT)
            assert page.locator(".q-dialog").count() == 0
            form = page.locator(".dstf-form-row")
            expect(form).to_contain_text("Add a criterion for x")
            head_box, row = head.bounding_box(), form.bounding_box()
            assert head_box is not None and row is not None
            assert row["y"] >= head_box["y"] + head_box["height"] - 1, (head_box, row)
            page.mouse.move(5, 5)
            page.wait_for_timeout(300)
            _shot(page, tmp_path, f"plus-row-{width}")
            editors = page.locator(".dstf-editor")
            add.click()
            expect(editors).to_have_count(1, timeout=EXPECT_TIMEOUT)
            expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
            page.wait_for_timeout(600)
            box = editors.bounding_box()
            assert box is not None, width
            assert _header_bottom(page) - 1 <= box["y"] < VIEWPORT["height"], (
                width,
                box,
            )
            page.mouse.move(5, 5)
            page.wait_for_timeout(300)
            _shot(page, tmp_path, f"plus-editor-{width}")
            editors.get_by_role("button", name="Done").click()
            expect(editors).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # Two criteria added, none saved: the spec is untouched on the way
        # out (`_served`).
        expect(page.locator(".dstf-header-row")).to_contain_text("2 pending edits")


def _editor_card(page) -> int:
    """The index of the plot card whose rows hold the open editor."""
    return page.evaluate(
        """() => { const e = document.querySelector('.dstf-editor');
            return [...document.querySelectorAll('.dstf-plot')]
                .findIndex(c => c.contains(e)); }"""
    )


def _editor_in_view(page) -> None:
    box = page.locator(".dstf-editor").bounding_box()
    assert box is not None
    assert _header_bottom(page) - 1 <= box["y"] < VIEWPORT["height"], box


def test_a_lower_plots_plus_opens_the_editor_on_that_plot_in_view(
    tmp_path, playwright_browser
):
    """Plot 1 (y's) with its criteria header at the window's foot: its `+`
    then Add opens the new editor on plot 1 and brings it into view — the
    editor opens under the plot's rows, below the fold, and it once opened
    on plot 0, ~855 px from the click. From plot 0's form, choosing y (plot
    0 is x's) opens the editor on the plot that draws y, in view too."""
    with _served(tmp_path, playwright_browser) as page:
        cards, editors = page.locator(".dstf-plot"), page.locator(".dstf-editor")
        expect(cards).to_have_count(7, timeout=EXPECT_TIMEOUT)
        card = cards.nth(1)
        head = card.locator(".dstf-criteria-head")
        head.evaluate("e => e.scrollIntoView({block: 'end'})")
        page.wait_for_timeout(300)
        head.get_by_role("button", name="Add a criterion for y").click()
        add = page.get_by_role("button", name="Add", exact=True)
        expect(add).to_be_visible(timeout=EXPECT_TIMEOUT)
        add.click()
        expect(editors).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(600)
        assert _editor_card(page) == 1
        _editor_in_view(page)
        page.mouse.move(5, 5)
        _shot(page, tmp_path, "reveal-plot-1")
        editors.get_by_role("button", name="Done").click()
        expect(editors).to_have_count(0, timeout=EXPECT_TIMEOUT)

        page.evaluate("() => window.scrollTo(0, 0)")
        head = cards.first.locator(".dstf-criteria-head")
        head.get_by_role("button", name="Add a criterion for x").click()
        form = page.locator(".dstf-form-row")
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        form.locator(".q-select").first.click()
        page.get_by_role("option", name="y", exact=True).click()
        add.click()
        expect(editors).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(600)
        assert _editor_card(page) == 1
        _editor_in_view(page)
        expect(page.locator(".dstf-header-row")).to_contain_text("2 pending edits")


# -- the Tests drawer's control set (Q7) -------------------------------------

#: The Tests drawer's row names, in drawing order, the hidden ones left out.
DRAWER_NAMES = """() => [...document.querySelectorAll('.dstf-drawer-panel .dstf-row')]
  .filter(r => r.offsetParent !== null)
  .map(r => r.querySelector('.dstf-name').innerText.trim())"""

#: The focused menu choice's text, its check icon's ligature left out.
ACTIVE_TEXT = """() => { const a = document.activeElement;
  const l = a && a.querySelector('.q-item__label');
  return l ? l.innerText.trim() : null; }"""


def test_the_tests_drawers_menus_answer_a_mouse_and_the_keyboard(
    tmp_path, playwright_browser
):
    """The Tests drawer's sort menu chooses with a real click and reorders
    the rows; its names menu opens from the keyboard, the arrow keys, Home
    and End move among its choices (Quasar's QMenu has no keyboard of its
    own), and Enter chooses one. Each choice is a `menuitemradio`, the
    current one checked."""
    with _served(tmp_path, playwright_browser) as page:
        page.locator(".dstf-rail").get_by_text("Tests", exact=True).click()
        drawer = page.locator(".dstf-drawer-panel").filter(has_text="Tests")
        sort = drawer.get_by_role("button", name="Sort", exact=True)
        expect(sort).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(drawer.locator(".dstf-row")).to_have_count(5)
        assert page.evaluate(DRAWER_NAMES) == ["A", "B", "C", "D", "G"]

        sort.click()
        items = page.get_by_role("menuitemradio")
        expect(items).to_have_count(3, timeout=EXPECT_TIMEOUT)
        assert [
            t.strip() for t in items.locator(".q-item__label").all_inner_texts()
        ] == ["Package order", "Name A→Z", "Status, failures first"]
        # Named by its text alone: the check icon is hidden from the
        # accessibility tree, or every choice read "check …".
        checked = page.get_by_role("menuitemradio", name="Package order", exact=True)
        expect(checked).to_have_attribute("aria-checked", "true")
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "tests-drawer-sort-open")
        items.nth(2).click()
        expect(items).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        # A FAIL, B NO_REF, the passes in package order, then C NOT RUN.
        assert page.evaluate(DRAWER_NAMES) == ["A", "B", "D", "G", "C"]

        names = drawer.get_by_role("button", name="Names", exact=True)
        names.focus()
        page.keyboard.press("Enter")
        expect(items).to_have_count(2, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        page.keyboard.press("ArrowDown")
        assert page.evaluate(ACTIVE_TEXT) == "Short"
        page.keyboard.press("ArrowDown")
        assert page.evaluate(ACTIVE_TEXT) == "Full"
        page.keyboard.press("ArrowDown")  # wraps
        assert page.evaluate(ACTIVE_TEXT) == "Short"
        page.keyboard.press("End")
        assert page.evaluate(ACTIVE_TEXT) == "Full"
        page.keyboard.press("Home")
        assert page.evaluate(ACTIVE_TEXT) == "Short"
        page.keyboard.press("ArrowUp")  # wraps
        assert page.evaluate(ACTIVE_TEXT) == "Full"
        page.keyboard.press("Enter")
        expect(items).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(drawer.locator(".dstf-name").first).to_have_text(
            "FakeLib.A", timeout=EXPECT_TIMEOUT
        )
        names.click()
        expect(items.nth(1)).to_have_attribute(
            "aria-checked", "true", timeout=EXPECT_TIMEOUT
        )
        expect(items.nth(0)).to_have_attribute("aria-checked", "false")
        page.keyboard.press("Escape")


def test_the_variables_drawers_menus_answer_a_real_mouse(tmp_path, playwright_browser):
    """The Variables drawer's group, sort and names menus, each chosen with
    a real click: Flat puts every row under one "All variables" group, A→Z
    sorts it by what it reads, and the choice is checked when the menu
    opens again."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        page.locator(".dstf-rail").get_by_text("Vars", exact=True).click()
        drawer = page.locator(".dstf-drawer-panel").filter(has_text="Variables")
        group = drawer.get_by_role("button", name="Group", exact=True)
        expect(group).to_be_visible(timeout=EXPECT_TIMEOUT)
        group.click()
        items = page.get_by_role("menuitemradio")
        expect(items).to_have_count(3, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        _shot(page, tmp_path, "var-group-open")
        page.get_by_role("menuitemradio", name="Flat", exact=True).click()
        expect(drawer.locator(".dstf-group")).to_have_text(
            ["All variables"], timeout=EXPECT_TIMEOUT
        )
        drawer.get_by_role("button", name="Sort", exact=True).click()
        page.get_by_role("menuitemradio", name="A→Z", exact=True).click()
        page.wait_for_timeout(300)
        texts = drawer.locator(".dstf-var-text").all_inner_texts()
        assert texts == sorted(texts, key=lambda t: (t.casefold(), t)), texts
        group.click()
        expect(
            page.get_by_role("menuitemradio", name="Flat", exact=True)
        ).to_have_attribute("aria-checked", "true", timeout=EXPECT_TIMEOUT)
        page.keyboard.press("Escape")


def test_a_menus_arrow_up_goes_last_and_tab_closes_it(tmp_path, playwright_browser):
    """The menu pattern: from outside its choices ArrowUp goes to the LAST
    one; Tab closes the menu; the choices are out of the tab order."""
    with _served(tmp_path, playwright_browser) as page:
        page.locator(".dstf-rail").get_by_text("Tests", exact=True).click()
        drawer = page.locator(".dstf-drawer-panel").filter(has_text="Tests")
        sort = drawer.get_by_role("button", name="Sort", exact=True)
        expect(sort).to_be_visible(timeout=EXPECT_TIMEOUT)
        sort.focus()
        page.keyboard.press("Enter")
        items = page.get_by_role("menuitemradio")
        expect(items).to_have_count(3, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert [items.nth(i).get_attribute("tabindex") for i in range(3)] == (
            ["-1"] * 3
        )
        page.keyboard.press("ArrowUp")
        assert page.evaluate(ACTIVE_TEXT) == "Status, failures first"
        page.keyboard.press("Tab")
        expect(items).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # Opened by the mouse, the focus on the menu itself: Tab closes it.
        sort.click()
        expect(items).to_have_count(3, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        page.keyboard.press("Tab")
        expect(items).to_have_count(0, timeout=EXPECT_TIMEOUT)


LONG_IDS = [
    "TRANSFORM.HeatExchangers.GenericDistributed_HX.Examples.Variants.WithWall."
    "GenericDistributed_HX_withMass",
    # ends in `_`: a bare `direction: rtl` drew it at the name's left end
    "TRANSFORM.HeatExchangers.GenericDistributed_HX.Examples.Variants.WithWall."
    "GenericDistributed_HX_noMassXY_",
    # its short name is 40 characters
    "TRANSFORM.Fluid.Pipes.Examples.Variants.WithWallAndMoreWords."
    "GenericPipe_withWall_withHeatTransfer_ab",
]


def _long_ids_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` and three tests with TRANSFORM-length ids; the last
    has a tree of its own, so an edit stages on it with no result."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for model in LONG_IDS:
        spec["tests"].append(
            {
                "model": model,
                "source": "../Examples/A.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
    spec["tests"][-1]["metrics"] = {
        "combinator": "and",
        "children": [
            {"metric": "range", "variable": "x", "min_value": -1.0, "max_value": 2.0}
        ],
    }
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


#: Every visible descendant of the open drawer's panel whose right edge is
#: more than 1 px past the panel's, every badge of the panel outside it,
#: and each shown edits pill's height in lines.
PAST_THE_PANEL = """() => {
  const p = [...document.querySelectorAll('.dstf-drawer-panel')]
    .find((x) => x.offsetParent !== null);
  const right = p.getBoundingClientRect().right;
  const past = [...p.querySelectorAll('*')]
    .filter((e) => e.getClientRects().length > 0)
    .filter((e) => e.getBoundingClientRect().right > right + 1)
    .map((e) => `${e.tagName}.${e.className}`);
  const badges = [...p.querySelectorAll('.dstf-badge')];
  const lines = [...p.querySelectorAll('.dstf-pill-named')]
    .filter((e) => e.offsetParent !== null)
    .map((e) => { const r = document.createRange(); r.selectNodeContents(e);
      return new Set([...r.getClientRects()].map((b) => Math.round(b.top))).size; });
  return {past, badges: badges.length, lines,
          outside: badges.filter((b) => b.getBoundingClientRect().right > right + 1).length};
}"""


#: Each Tests-drawer name, by its title: its `.block`'s text-overflow,
#: whether it overflows, the indices of the characters it shows whole, and
#: whether they sit left to right in the id's order.
NAME_CUTS = """() => {
  const p = [...document.querySelectorAll('.dstf-drawer-panel')]
    .find((x) => x.offsetParent !== null);
  const out = {};
  for (const n of p.querySelectorAll('.dstf-name')) {
    const b = n.querySelector('.q-btn__content > .block');
    const box = b.getBoundingClientRect();
    const node = document.createTreeWalker(b, NodeFilter.SHOW_TEXT).nextNode();
    const seen = [];
    for (let i = 0; i < node.textContent.length; i++) {
      const r = document.createRange();
      r.setStart(node, i);
      r.setEnd(node, i + 1);
      const c = r.getBoundingClientRect();
      if (c.width > 0 && c.left >= box.left - 0.5 && c.right <= box.right + 0.5)
        seen.push([i, c.left]);
    }
    out[n.getAttribute('title')] = {
      overflow: getComputedStyle(b).textOverflow,
      cut: b.scrollWidth > b.clientWidth,
      text: node.textContent,
      shown: seen.map(([i]) => i),
      inOrder: seen.every(([, x], k) => k === 0 || x > seen[k - 1][1]),
    };
  }
  return out;
}"""


@pytest.mark.parametrize("names", ["Short", "Full"])
def test_the_tests_drawer_fits_long_ids_at_900_px(tmp_path, playwright_browser, names):
    """TRANSFORM's ids at 900 px: a name is cut with an ellipsis and carries
    the whole id as its title, a package label wraps, every badge stays
    inside the drawer, and the edits pill beside a long name keeps to one
    line. A Short name is cut at its end; a Full one at its START, so two
    ids sharing a long package prefix read apart — and its characters stay
    in order (a bare `direction: rtl` moved a trailing `_` to the left)."""
    with _served(
        tmp_path, playwright_browser, project=_long_ids_project, hooks=True
    ) as page:
        page.set_viewport_size({"width": 900, "height": 900})
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__stage?model={LONG_IDS[2]}") as r:
            json.loads(r.read())
        page.locator(".dstf-rail").get_by_text("Tests", exact=True).click()
        drawer = page.locator(".dstf-drawer-panel").filter(has_text="Tests")
        expect(drawer.locator(".dstf-row")).to_have_count(8, timeout=EXPECT_TIMEOUT)
        if names == "Full":
            drawer.get_by_role("button", name="Names", exact=True).click()
            page.get_by_role("menuitemradio", name="Full", exact=True).click()
            expect(drawer.locator(".dstf-name").first).to_have_text(
                "FakeLib.A", timeout=EXPECT_TIMEOUT
            )
        page.wait_for_timeout(600)
        measured = page.evaluate(PAST_THE_PANEL)
        assert measured == {"past": [], "badges": 8, "lines": [1], "outside": 0}, (
            measured
        )
        name = drawer.locator(".dstf-name").filter(has_text="withHeatTransfer_ab")
        assert name.get_attribute("title") == LONG_IDS[2]
        cuts = page.evaluate(NAME_CUTS)
        assert {c["overflow"] for c in cuts.values()} == {"ellipsis"}, cuts
        assert cuts[LONG_IDS[2]]["cut"], cuts[LONG_IDS[2]]
        for model in LONG_IDS:
            assert cuts[model]["inOrder"], cuts[model]
        if names == "Short":
            assert cuts[LONG_IDS[2]]["shown"][0] == 0, cuts[LONG_IDS[2]]
        else:
            shown = []
            for model in LONG_IDS[:2]:
                cut = cuts[model]
                assert cut["cut"] and cut["shown"][-1] == len(model) - 1, cut
                assert cut["shown"][0] > 0, cut
                shown.append("".join(model[i] for i in cut["shown"]))
            assert shown[0] != shown[1], shown
        page.mouse.move(600, 5)
        _shot(page, tmp_path, f"tests-drawer-900-{names.lower()}")


# -- Q20: variable labels cut in the middle ------------------------------------

#: A BranchingDynamicPipes-shaped UnitTests expression, x's here.
CAT3 = "cat(1, pipe2.mediums.T, pipe2.mediums.p)[3]"

#: One `.dstf-mid` element's cut: whether its head overflows and how, the
#: head's characters shown whole (by range rects, as `NAME_CUTS` reads the
#: Tests drawer), whether the tail shows whole inside the element and the
#: open drawer's panel, its title and its innerText.
MID_CUT = """(e) => {
  const head = e.querySelector('.dstf-mid-head');
  const tail = e.querySelector('.dstf-mid-tail');
  const box = head.getBoundingClientRect(), outer = e.getBoundingClientRect();
  const node = document.createTreeWalker(head, NodeFilter.SHOW_TEXT).nextNode();
  const shown = [];
  for (let i = 0; node && i < node.textContent.length; i++) {
    const r = document.createRange();
    r.setStart(node, i);
    r.setEnd(node, i + 1);
    const c = r.getBoundingClientRect();
    if (c.width > 0 && c.left >= box.left - 0.5 && c.right <= box.right + 0.5)
      shown.push(i);
  }
  const t = tail.getBoundingClientRect();
  const panel = e.closest('.dstf-drawer-panel');
  const edge = panel ? panel.getBoundingClientRect().right : window.innerWidth;
  return {
    cut: head.scrollWidth > head.clientWidth,
    overflow: getComputedStyle(head).textOverflow,
    head: node ? node.textContent : '',
    shown,
    tail: tail.textContent,
    tailWhole: t.width > 0 && t.left >= box.right - 0.5
      && t.right <= outer.right + 0.5 && t.right <= edge + 0.5,
    title: e.getAttribute('title'),
    inner: e.innerText,
  };
}"""


def test_a_variable_label_is_cut_in_the_middle_at_900_px(tmp_path, playwright_browser):
    """Q20 in Chromium: x reads a BranchingDynamicPipes expression, and in
    the 320 px Variables drawer its row is cut in the MIDDLE — the head's
    first characters, an ellipsis, the tail `[3]` whole — with the whole
    label as its title; its innerText and a copied selection are the whole
    label (two inline-blocks, never a flex pair: HIGH 6). The card's title
    and the criteria row have the room: not cut."""
    with _served(tmp_path, playwright_browser, hooks=True) as page:
        base = page.url.split("test/")[0]
        query = urllib.parse.urlencode({"name": "x", "expression": CAT3})
        with urllib.request.urlopen(f"{base}__expression?{query}") as r:
            assert json.loads(r.read()) == {"stamped": 1}
        page.set_viewport_size({"width": 900, "height": 900})
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        _open_vars(page)
        row = _var_row(page, CAT3)
        expect(row).to_have_count(1, timeout=EXPECT_TIMEOUT)
        _no_sideways_scroll(page)
        text = row.locator(".dstf-mid")
        cut = text.evaluate(MID_CUT)
        _shot(page, tmp_path, "mid-cut-900")
        assert cut["cut"] and cut["overflow"] == "ellipsis", cut
        head = "cat(1, pipe2.mediums.T, pipe2.mediums.p)"
        assert cut["head"] == head and cut["tail"] == "[3]", cut
        # the head's start, in order, and not all of it; the tail whole
        assert cut["shown"] == list(range(len(cut["shown"]))), cut
        assert 0 < len(cut["shown"]) < len(head), cut
        assert cut["tailWhole"], cut
        assert cut["title"] == f"{CAT3} (x)", cut
        assert cut["inner"] == CAT3, cut
        # a copy of the row's text is the whole label
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=base.rstrip("/")
        )
        text.evaluate(
            "(e) => { const r = document.createRange(); r.selectNodeContents(e);"
            " const s = getSelection(); s.removeAllRanges(); s.addRange(r); }"
        )
        page.keyboard.press("Control+C")
        assert page.evaluate("() => navigator.clipboard.readText()") == CAT3
        # the card's title and x's criteria rows are not cut at this width
        title = page.locator(".dstf-plot").first.locator(".dstf-card-title .dstf-mid")
        assert not title.first.evaluate(MID_CUT)["cut"]
        names = page.locator(".dstf-plot").first.locator(".dstf-crit-name")
        expect(names.first).to_contain_text(CAT3)
        for i in range(names.count()):
            assert not names.nth(i).evaluate(MID_CUT)["cut"], i


#: A tree whose x rows sit at three depths with both verdicts: the nrmse
#: FAILs (x is 1.25t against t) and draws nothing, so its swatch is hidden;
#: the two ranges pass and draw.
_ALIGN_TREE = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [
                {
                    "combinator": "or",
                    "children": [
                        {
                            "combinator": "warn",
                            "children": [
                                {"metric": "range", "variable": "x", "min_value": -1.0}
                            ],
                        },
                        {"metric": "range", "variable": "y", "min_value": -1.0},
                    ],
                },
                {"metric": "range", "variable": "x", "max_value": 100.0},
            ],
        },
    ],
}


def _align_project(tmp_path: Path) -> tuple[Path, Path]:
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = _ALIGN_TREE
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


def test_a_plots_leaf_names_start_in_one_column(tmp_path, playwright_browser):
    """D140: every leaf name on a card starts in one column, whatever its
    row's depth, its verdict and whether its swatch shows — a hidden swatch
    keeps its box and a pill is as wide as PASS or FAIL."""
    with _served(tmp_path, playwright_browser, project=_align_project) as page:
        card = page.locator(".dstf-plot").first
        expect(card.locator(".dstf-crit")).to_have_count(3, timeout=EXPECT_TIMEOUT)
        # Drawn: the swatches of the leaves that draw show.
        expect(card.locator(".dstf-swatch >> visible=true").first).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        rows = card.evaluate(
            """(card) => [...card.querySelectorAll('.dstf-crit')].map(r => ({
                 pill: r.querySelector('.dstf-pill').innerText,
                 swatch: getComputedStyle(r.querySelector('.dstf-swatch')).visibility,
                 name: r.querySelector('.dstf-crit-name').getBoundingClientRect().left,
               }))"""
        )
        _shot(page, tmp_path, "leaf-names-one-column")
        names = [r["name"] for r in rows]
        assert max(names) - min(names) < 0.5, rows
        assert {r["pill"] for r in rows} == {"PASS", "FAIL"}, rows
        assert {r["swatch"] for r in rows} == {"visible", "hidden"}, rows


def test_the_pickers_keyboard_chooses_a_variable_and_add_stages_it(
    tmp_path, playwright_browser
):
    """U2's picker in Chromium: a glob typed in the Variable box narrows the
    list on the server; ArrowDown moves the ring, Enter chooses (the row on
    the selection tint, the focus still in the box, the Chosen line naming
    it), and Add stages the leaf on the CHOSEN variable — a watch plot's
    untracked `w3`."""
    with _served(tmp_path, playwright_browser) as page:
        head = page.locator(".dstf-plot").first.locator(".dstf-criteria-head")
        expect(head).to_contain_text("Criteria", timeout=EXPECT_TIMEOUT)
        head.get_by_role("button", name="Add a criterion for x").click()
        box = page.get_by_role("combobox", name="Variable")
        expect(box).to_be_visible(timeout=EXPECT_TIMEOUT)
        box.fill("w?")
        rows = page.locator(".dstf-pick-row")
        expect(rows).to_have_count(5, timeout=EXPECT_TIMEOUT)
        for _ in range(3):
            page.keyboard.press("ArrowDown")
        expect(page.locator(".dstf-pick-cursor")).to_have_text("w3")
        page.keyboard.press("Enter")
        expect(page.locator(".dstf-pick-on")).to_have_text("w3")
        expect(page.locator(".dstf-pick-chosen")).to_have_text("Chosen: w3")
        assert page.evaluate("document.activeElement.tagName") == "INPUT"
        _shot(page, tmp_path, "picker")
        page.get_by_role("button", name="Add", exact=True).click()
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")
        expect(page.get_by_text("nrmse · w3").first).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )


def _tolerance_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with A at a test tolerance of 0.01, scored by an
    nrmse leaf with no tolerance of its own and a scalar."""
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["comparison"] = {"tolerance": 0.01}
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {
                        "metric": "scalar",
                        "variable": "y",
                        "quantity": {"reduce": "max"},
                    },
                ],
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


#: Each one-line field of the open editor: its label, its input line's y
#: (the control's bottom), the field's own bottom — what a wrapped row
#: aligns (`items-end`), so it names the row — its hint, and how many line
#: boxes the hint's text takes.
READ_FIELDS = """() => [...document.querySelectorAll(
    '.dstf-editor .dstf-fields .q-field:not(.q-textarea)')].map(f => {
  const c = f.querySelector('.q-field__control').getBoundingClientRect();
  const m = f.querySelector('.q-field__messages');
  const tops = new Set();
  if (m) {
    const walk = document.createTreeWalker(m, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t; t = walk.nextNode()) {
      const r = document.createRange();
      r.selectNodeContents(t);
      for (const q of r.getClientRects()) tops.add(Math.round(q.top));
    }
  }
  const lines = tops.size;
  return [(f.querySelector('.q-field__label') || {}).innerText || '',
          c.bottom, f.getBoundingClientRect().bottom,
          m ? m.innerText : '', lines];
})"""


def _tolerance_line(fields: list) -> float:
    """The Tolerance field's input line (`READ_FIELDS`)."""
    return next(line for label, line, *_ in fields if label == "Tolerance")


def test_the_editors_fields_line_up_and_say_the_value_in_effect(
    tmp_path, playwright_browser
):
    """K3 + K4 + K8 at 1280: every one-line field of a wrapped row has its
    input line at one y (±1 px) — a number with a validation rule reserved
    the bottom line and floated 20 px above the rest — and the tolerance
    the leaf does not set says the one the test applied, on ONE line."""
    with _served(tmp_path, playwright_browser, project=_tolerance_project) as page:
        page.wait_for_timeout(700)
        for index, name in ((0, "nrmse · x"), (1, "scalar · y")):
            _open_crit(page, index, name)
            expect(page.locator(".dstf-editor")).to_have_count(
                1, timeout=EXPECT_TIMEOUT
            )
            # Centred first: the Statistics card above the tree puts the
            # editor at the viewport's foot, and the Tab below scrolled the
            # page under the fields it compares.
            page.locator(".dstf-editor").evaluate(
                "e => e.scrollIntoView({block: 'center'})"
            )
            page.wait_for_timeout(400)
            fields = page.evaluate(READ_FIELDS)
            rows: dict[int, list[float]] = {}
            for _label, line, bottom, _hint, _n in fields:
                rows.setdefault(round(bottom), []).append(line)
            for ys in rows.values():
                assert max(ys) - min(ys) <= 1, (name, fields)
            hints = {label: (hint, n) for label, _b, _t, hint, n in fields}
            # nrmse has one ceiling; a scalar's tolerance has two sides, and
            # the first field says what is in effect.
            tolerance = "Tolerance" if index == 0 else "− Tolerance"
            assert hints[tolerance] == (
                "in effect: 0.01 (the test's tolerance)",
                1,
            ), fields
            _shot(page, tmp_path, f"editor-fields-{index}")
            if index == 0:
                # a typed value is the one in effect: the hint goes, and
                # the row does not move
                tolerance = page.locator(".dstf-editor").get_by_label("Tolerance")
                tolerance.fill("0.5")
                tolerance.press("Tab")
                page.wait_for_timeout(800)
                typed = {f[0]: f for f in page.evaluate(READ_FIELDS)}
                assert typed["Tolerance"][3] == "", typed
                assert abs(typed["Tolerance"][1] - _tolerance_line(fields)) <= 1
                _shot(page, tmp_path, "editor-typed-tolerance")
            page.locator(".dstf-editor").get_by_role("button", name="Done").click()
            expect(page.locator(".dstf-editor")).to_have_count(
                0, timeout=EXPECT_TIMEOUT
            )


def _own_tolerance_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_tolerance_project` with the nrmse leaf's own tolerance 0.5."""
    cfg, work = _tolerance_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"]["children"][0]["tolerance"] = 0.5
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


def test_an_own_tolerance_cleared_says_the_base_in_effect(tmp_path, playwright_browser):
    """K3: the hint names what applies while the field is EMPTY. The
    leaf's own 0.5 shows no hint; cleared, the field says the test's 0.01
    on its hint line — it said nothing, the base being carried only for a
    leaf without its own."""
    with _served(tmp_path, playwright_browser, project=_own_tolerance_project) as page:
        page.wait_for_timeout(700)
        _open_crit(page, 0, "nrmse · x")
        expect(page.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(400)
        fields = {f[0]: f for f in page.evaluate(READ_FIELDS)}
        assert fields["Tolerance"][3] == "", fields
        tolerance = page.locator(".dstf-editor").get_by_label("Tolerance")
        assert tolerance.input_value() == "0.5"
        tolerance.fill("")
        tolerance.press("Tab")
        page.wait_for_timeout(800)
        cleared = {f[0]: f for f in page.evaluate(READ_FIELDS)}
        assert cleared["Tolerance"][3:] == [
            "in effect: 0.01 (the test's tolerance)",
            1,
        ], cleared
        _shot(page, tmp_path, "editor-own-tolerance-cleared")


# -- a tolerance's two sides: two fields and a link toggle ----------------------


def _sides_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with A at a test tolerance of 0.01, scored by a
    points leaf (the final value) and a scalar, neither with a tolerance of
    its own: the two forms whose tolerance has two sides."""
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["comparison"] = {"tolerance": 0.01}
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {"metric": "points", "variable": "x"},
                    {
                        "metric": "scalar",
                        "variable": "y",
                        "quantity": {"reduce": "max"},
                    },
                ],
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


#: Each two-sided control of the open editor (`.dstf-sides`), in document
#: order: whether it may wrap, its box, and its children left to right —
#: a field's label, box, input line (its control's bottom and middle), the
#: min-width in effect, its message and whether it is marked an error; the
#: toggle's label, box, icon and `aria-pressed`.
READ_SIDES = """() => [...document.querySelectorAll('.dstf-editor .dstf-sides')]
  .map(g => {
    const box = e => { const r = e.getBoundingClientRect();
                       return [r.left, r.top, r.right, r.bottom]; };
    return {
      wrap: getComputedStyle(g).flexWrap, box: box(g),
      kids: [...g.children].map(k => {
        const c = k.querySelector('.q-field__control');
        if (!c) return {toggle: k.getAttribute('aria-label'), box: box(k),
                        icon: k.innerText.trim(),
                        pressed: k.getAttribute('aria-pressed')};
        const r = c.getBoundingClientRect();
        return {label: k.querySelector('.q-field__label').innerText,
                box: box(k), line: r.bottom, middle: (r.top + r.bottom) / 2,
                minWidth: getComputedStyle(k).minWidth,
                message: (k.querySelector('.q-field__messages') || {}).innerText
                         || '',
                error: k.classList.contains('q-field--error')};
      }),
    };
  })"""

#: The page's and the open editor's `[scrollWidth, clientWidth]`, and the
#: editor's right edge.
OVERFLOW = """() => {
  const e = document.querySelector('.dstf-editor');
  const d = document.documentElement;
  return [[d.scrollWidth, d.clientWidth], [e.scrollWidth, e.clientWidth],
          e.getBoundingClientRect().right];
}"""

#: The preview's entry as Save would write it: every line of the open
#: drawer's diff but the removed ones, without its `+` / two-space prefix.
PREVIEW_AFTER = """() => [...document.querySelectorAll(
    '.dstf-pending-drawer .dstf-diff-line:not(.dstf-diff-del)')]
  .map(l => [...l.children].slice(1).map(c => c.innerText).join(''))
  .join('')"""


def _staged_in_preview(page) -> str:
    """The entry the preview drawer shows for the staged edits, with no
    whitespace; the drawer is closed again."""
    page.get_by_role("button", name="Preview and save").click()
    drawer = page.locator(".dstf-pending-drawer")
    expect(drawer.locator(".dstf-diff-line").first).to_be_visible(
        timeout=EXPECT_TIMEOUT
    )
    page.wait_for_timeout(400)  # the drawer's slide
    text = re.sub(r"\s+", "", page.evaluate(PREVIEW_AFTER))
    page.keyboard.press("Escape")
    width = page.viewport_size["width"]
    page.wait_for_function(
        f"document.querySelector('.dstf-pending-drawer')"
        f".getBoundingClientRect().x >= {width - 1}",
        timeout=EXPECT_TIMEOUT,
    )
    return text


def _link_toggle(scope, label: str):
    """The link toggle of the tolerance ``label`` inside ``scope``, in
    either state: its name says which it is in."""
    return scope.get_by_role(
        "button",
        name=re.compile(
            rf"^(Linked: both sides|Unlinked: each side) of {re.escape(label)} "
        ),
    )


#: The colour a toggle's icon is drawn in.
ICON_COLOUR = "e => getComputedStyle(e.querySelector('.q-icon')).color"


def _icon_colour(toggle, page) -> str:
    """The toggle's icon colour once it has stopped changing: a Quasar
    button fades from one colour to the next."""
    seen = toggle.evaluate(ICON_COLOUR)
    for _ in range(40):
        page.wait_for_timeout(100)
        now = toggle.evaluate(ICON_COLOUR)
        if now == seen:
            break
        seen = now
    return seen


def _retype(page, field, text: str) -> None:
    """Replace ``field``'s content with real keystrokes, as a person does:
    select it all and type over it (an empty ``text`` deletes it)."""
    field.click()
    page.keyboard.press("Control+A")
    if text:
        page.keyboard.type(text)
    else:
        page.keyboard.press("Delete")


def test_a_tolerances_two_fields_and_link_toggle_under_real_keys(
    tmp_path, playwright_browser
):
    """Round 1's Q5 A in Chromium, typed with real keystrokes: linked, what
    is typed into `−` is read back from `+`; Tab reaches the toggle and
    Space unlinks; unlinked, each side is its own and the preview shows the
    leaf holding the pair; a side cleared says "required" — as a mark the
    browser renders — and stages nothing; linking again takes the larger
    side for both."""
    pending = re.compile(r"1 pending edit")
    with _served(tmp_path, playwright_browser, project=_sides_project) as page:
        page.wait_for_timeout(700)
        _open_crit(page, 0, "points · x")
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        editor.evaluate("e => e.scrollIntoView({block: 'center'})")
        lower = editor.get_by_label("− Default tolerance", exact=True)
        upper = editor.get_by_label("+ Default tolerance", exact=True)
        toggle = _link_toggle(editor, "Default tolerance")
        expect(lower).to_have_value("")
        expect(upper).to_have_value("")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        expect(toggle).to_have_attribute(
            "aria-label", "Linked: both sides of Default tolerance change together"
        )
        tokens = page.evaluate(
            "() => { const s = getComputedStyle(document.documentElement);"
            " return ['--dstf-accent', '--dstf-text-muted']"
            ".map(t => s.getPropertyValue(t).trim()); }"
        )
        accent, muted = (_rgb(token) for token in tokens)
        assert _icon_colour(toggle, page) == accent != muted
        (unset,) = page.evaluate(READ_SIDES)
        assert unset["kids"][0]["message"] == "in effect: 0.01 (the test's tolerance)"
        assert unset["kids"][1]["icon"] == "link"
        _shot(page, tmp_path, "sides-points-unset")

        lower.click()
        page.keyboard.type("0.05")
        expect(upper).to_have_value("0.05", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            pending, timeout=EXPECT_TIMEOUT
        )
        # A side holds a value: the hint naming another is gone.
        expect(editor.locator(".dstf-fields")).not_to_contain_text(
            "in effect", timeout=EXPECT_TIMEOUT
        )
        assert '"tolerance":0.05' in _staged_in_preview(page)
        _shot(page, tmp_path, "sides-points-linked")

        # The keyboard: Tab from the first field reaches the toggle, Space
        # flips it — unlinked, the number is the pair holding it twice.
        lower.focus()
        page.keyboard.press("Tab")
        expect(toggle).to_be_focused()
        page.keyboard.press("Space")
        expect(toggle).to_have_attribute(
            "aria-pressed", "false", timeout=EXPECT_TIMEOUT
        )
        expect(toggle).to_contain_text("link_off")
        # The two states are told apart at a glance and by name: the icon,
        # its colour — accent linked, muted unlinked — and what it is called.
        expect(toggle).to_have_attribute(
            "aria-label", "Unlinked: each side of Default tolerance is set on its own"
        )
        assert _icon_colour(toggle, page) == muted
        page.keyboard.press("Tab")
        expect(upper).to_be_focused()
        page.wait_for_timeout(500)
        assert '"tolerance":{"lower":0.05,"upper":0.05}' in _staged_in_preview(page)

        _retype(page, upper, "0.2")
        page.wait_for_timeout(800)
        expect(upper).to_have_value("0.2")
        expect(lower).to_have_value("0.05")  # its own side only
        assert '"tolerance":{"lower":0.05,"upper":0.2}' in _staged_in_preview(page)
        expect(page.locator(".dstf-header-row")).to_contain_text(pending)
        _shot(page, tmp_path, "sides-points-unlinked")

        # Half a pair: the blank side is marked where a person sees it, and
        # the leaf keeps the last whole pair.
        _retype(page, lower, "")
        expect(
            editor.locator(".q-field--error").filter(has_text="− Default tolerance")
        ).to_contain_text("required", timeout=EXPECT_TIMEOUT)
        expect(editor.locator(".q-field--error")).to_have_count(1)
        page.wait_for_timeout(500)
        assert '"tolerance":{"lower":0.05,"upper":0.2}' in _staged_in_preview(page)
        _shot(page, tmp_path, "sides-points-half")
        _retype(page, lower, "0.01")
        expect(editor.locator(".q-field--error")).to_have_count(
            0, timeout=EXPECT_TIMEOUT
        )
        expect(lower).to_have_value("0.01")
        expect(upper).to_have_value("0.2")

        # Linked again: the larger side, for both.
        toggle.click()
        expect(toggle).to_have_attribute("aria-pressed", "true", timeout=EXPECT_TIMEOUT)
        expect(lower).to_have_value("0.2", timeout=EXPECT_TIMEOUT)
        expect(upper).to_have_value("0.2")
        page.wait_for_timeout(500)
        assert '"tolerance":0.2' in _staged_in_preview(page)
        expect(page.locator(".dstf-header-row")).to_contain_text(pending)


@pytest.mark.parametrize("width", [1280, 900])
@pytest.mark.parametrize("drawer", [False, True])
def test_a_tolerances_two_fields_sit_on_the_rows_input_line(
    tmp_path, playwright_browser, width, drawer
):
    """The lower field, the toggle and the upper field are one line: both
    input lines at the y of the row's other fields (±1 px), the toggle
    between them and level with them, the hint on ONE line — and nothing
    wider than its card, at 1280 and 900 px, with the Variables drawer shut
    and open. Beside the open drawer at 900 px the form's row is under
    420 px: the width the hint asked for is dropped there, where it pushed
    the editor 15 px past its card."""
    with _served(tmp_path, playwright_browser, project=_sides_project) as page:
        page.set_viewport_size({"width": width, "height": 900})
        if drawer:
            _open_vars(page)
        page.wait_for_timeout(700)
        for index, name in ((0, "points · x"), (1, "scalar · y")):
            _open_crit(page, index, name)
            editor = page.locator(".dstf-editor")
            expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
            editor.evaluate("e => e.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(400)
            where = (width, drawer, name)
            (group,) = page.evaluate(READ_SIDES)
            lower, toggle, upper = group["kids"]
            # One line: left to right, the two input lines level.
            assert lower["box"][2] <= toggle["box"][0], where
            assert toggle["box"][2] <= upper["box"][0], where
            assert abs(lower["line"] - upper["line"]) <= 1, where
            # The toggle is level with the inputs, not with the hint line.
            middle = (toggle["box"][1] + toggle["box"][3]) / 2
            assert abs(middle - lower["middle"]) <= 2, (where, toggle, lower)
            # ... and with the row's other fields' input lines.
            fields = page.evaluate(READ_FIELDS)
            row = [f for f in fields if abs(f[2] - lower["box"][3]) <= 1]
            assert {f[0] for f in row} >= {lower["label"], upper["label"]}, where
            assert max(f[1] for f in row) - min(f[1] for f in row) <= 1, (where, row)
            # The hint, on one line under the first field.
            hints = {f[0]: (f[3], f[4]) for f in fields if f[3]}
            assert hints == {
                lower["label"]: ("in effect: 0.01 (the test's tolerance)", 1)
            }, where
            # Nothing is wider than what holds it.
            (page_scroll, page_client), (scroll, client), right = page.evaluate(
                OVERFLOW
            )
            assert page_scroll == page_client, where
            assert scroll <= client, where
            assert group["box"][2] <= right, where
            narrow = width == 900 and drawer
            assert group["wrap"] == ("wrap" if narrow else "nowrap"), where
            assert lower["minWidth"] == ("140px" if narrow else "230px"), where
            _shot(page, tmp_path, f"sides-{index}-{width}{'-drawer' if drawer else ''}")
            editor.get_by_role("button", name="Done").click()
            expect(editor).to_have_count(0, timeout=EXPECT_TIMEOUT)


def _tables_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with A scored on x by the three criteria whose
    rows carry a tolerance — declared points, declared events, declared
    peaks — each with one row holding a number (linked) and one holding a
    lower/upper pair (unlinked)."""
    cfg, work = _two_var_project(tmp_path)

    def pair(lower: float, upper: float) -> dict:
        return {"lower": lower, "upper": upper}

    tree = {
        "combinator": "and",
        "children": [
            {
                "metric": "points",
                "variable": "x",
                "points": [
                    {
                        "time": 0.25,
                        "value": 0.3,
                        "tolerance": 0.05,
                        "time_tolerance": 0.1,
                    },
                    {
                        "time": 0.75,
                        "value": 0.9,
                        "tolerance": pair(0.02, 0.08),
                        "time_tolerance": pair(0.05, 0.1),
                    },
                ],
            },
            {
                "metric": "event-timing",
                "variable": "x",
                "events": [
                    {"time": 0.25, "tolerance": 0.05},
                    {"time": 0.75, "tolerance": pair(0.01, 0.04)},
                ],
            },
            {
                "metric": "dominant-frequency",
                "variable": "x",
                "peaks": [
                    {"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"},
                    {
                        "freq": 2.0,
                        "tolerance": pair(0.05, 0.2),
                        "tolerance_mode": "rel",
                    },
                ],
            },
        ],
    }
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = tree
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


#: The open editor's table: the rows' box as it scrolls (`[scrollWidth,
#: clientWidth, left, right of what it shows]`) and, per row, every control
#: in document order — a field's label, box, input line and whether its
#: label is cut; a button's label and box.
READ_TABLE = """() => {
  const t = document.querySelector('.dstf-editor .dstf-table-scroll');
  const r = t.getBoundingClientRect();
  const box = e => { const b = e.getBoundingClientRect();
                     return [b.left, b.top, b.right, b.bottom]; };
  return {
    scroll: [t.scrollWidth, t.clientWidth, r.left, r.left + t.clientWidth],
    rows: [...t.children].map(row => ({
      wrap: getComputedStyle(row).flexWrap, box: box(row),
      cells: [...row.querySelectorAll('.q-field, .q-btn')].map(c => {
        const control = c.querySelector('.q-field__control');
        const label = c.querySelector('.q-field__label');
        return control
          ? {label: label.innerText, box: box(c),
             line: control.getBoundingClientRect().bottom,
             middle: (control.getBoundingClientRect().top
                      + control.getBoundingClientRect().bottom) / 2}
          : {button: c.getAttribute('aria-label') || c.innerText.trim(),
             box: box(c)};
      }),
    })),
  };
}"""


def _control_name(cell: dict) -> str:
    """A table control's name (`READ_TABLE`): a field's label, a button's
    — a link toggle's without its state, as `link <tolerance>`."""
    name = cell.get("label") or cell["button"]
    toggle = re.match(r"(?:Linked: both sides|Unlinked: each side) of (\S+) ", name)
    return f"link {toggle.group(1)}" if toggle else name


#: What each table's row holds, left to right (`_control_name`).
TABLE_ROWS = {
    "points · x": [
        "time",
        "value",
        "− tolerance",
        "link tolerance",
        "+ tolerance",
        "tolerance_mode",
        "before time_tolerance",
        "link time_tolerance",
        "after time_tolerance",
        "Remove row",
    ],
    "event-timing · x": [
        "time",
        "early tolerance",
        "link tolerance",
        "late tolerance",
        "Remove row",
    ],
    "dominant-frequency · x": [
        "freq",
        "− tolerance",
        "link tolerance",
        "+ tolerance",
        "tolerance_mode",
        "Remove row",
    ],
}


@pytest.mark.parametrize("width", [1280, 900])
@pytest.mark.parametrize("drawer", [False, True])
def test_a_tables_row_wraps_inside_the_row_and_nothing_is_clipped(
    tmp_path, playwright_browser, width, drawer
):
    """A points row is ten controls wide. At 1280 and 900 px, the Variables
    drawer shut and open, each of the three tables keeps every control
    inside the rows' box — a row wraps INSIDE itself, the table never
    scrolls sideways — with each visual line's inputs on one y and the link
    toggles level with them; neither the editor nor the page is wider than
    what holds it. One row holds a number (linked), one a pair (unlinked)."""
    with _served(tmp_path, playwright_browser, project=_tables_project) as page:
        page.set_viewport_size({"width": width, "height": 900})
        if drawer:
            _open_vars(page)
        page.wait_for_timeout(700)
        heights = {}
        for name, expected in TABLE_ROWS.items():
            _open_crit(page, 0, name)
            editor = page.locator(".dstf-editor")
            expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
            editor.locator(".dstf-table-scroll").evaluate(
                "e => e.scrollIntoView({block: 'center'})"
            )
            page.wait_for_timeout(400)
            where = (width, drawer, name)
            said = editor.locator(".text-negative").all_inner_texts()
            assert [text for text in said if text.strip()] == [], where
            table = page.evaluate(READ_TABLE)
            scroll_width, client_width, left, right = table["scroll"]
            assert scroll_width == client_width, where  # never sideways
            assert len(table["rows"]) == 2, where
            for row in table["rows"]:
                assert row["wrap"] == "wrap", where
                cells = row["cells"]
                names = [_control_name(c) for c in cells]
                assert names == expected, where
                for cell in cells:
                    assert left - 0.5 <= cell["box"][0], (where, cell)
                    assert cell["box"][2] <= right + 0.5, (where, cell)
                    assert cell["box"][3] <= row["box"][3] + 0.5, (where, cell)
                # Each visual line: its inputs on one y, its toggles level.
                lines: dict[int, list[dict]] = {}
                for cell in cells:
                    if "line" in cell:
                        lines.setdefault(round(cell["box"][1]), []).append(cell)
                for cell in cells:
                    if _control_name(cell).startswith("link "):
                        (top,) = [t for t in lines if t <= cell["box"][1] < t + 40]
                        middle = (cell["box"][1] + cell["box"][3]) / 2
                        assert abs(middle - lines[top][0]["middle"]) <= 2, where
                for fields in lines.values():
                    ys = [f["line"] for f in fields]
                    assert max(ys) - min(ys) <= 1, (where, fields)
            first = table["rows"][0]["box"]
            heights[name] = first[3] - first[1]
            (page_scroll, page_client), (scroll, client), _right = page.evaluate(
                OVERFLOW
            )
            assert page_scroll == page_client, where
            assert scroll <= client, where
            short = name.split(" ")[0]
            _shot(
                page,
                tmp_path,
                f"table-{short}-{width}{'-drawer' if drawer else ''}",
            )
            editor.get_by_role("button", name="Done").click()
            expect(editor).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # How much of the 336 px scroll box one row takes: a line of
        # controls is 40 px, and a points row is one line at 1280, two
        # beside the drawer or at 900, three at 900 beside the drawer
        # (where every control on its own line took 296 px).
        lines = {(1280, False): 1, (1280, True): 2, (900, False): 2, (900, True): 3}
        n = lines[(width, drawer)]
        assert heights["points · x"] == pytest.approx(40 * n + 8 * (n - 1), abs=2)
        assert heights["event-timing · x"] <= 40 * 2 + 8 + 2


def test_a_rows_tolerance_is_typed_as_two_sides_with_real_keys(
    tmp_path, playwright_browser
):
    """A table row's tolerance under real keystrokes: the linked row's `+`
    follows what is typed into `−`; unlinked, the preview shows the row
    holding the pair; a side cleared is marked "required" in the browser
    and holds the WHOLE list — a time typed meanwhile is not staged beside
    the stale pair — until the side is filled."""
    with _served(tmp_path, playwright_browser, project=_tables_project) as page:
        page.wait_for_timeout(700)
        _open_crit(page, 0, "points · x")
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        rows = editor.locator(".dstf-table-row")
        expect(rows).to_have_count(2, timeout=EXPECT_TIMEOUT)
        rows.first.evaluate("e => e.scrollIntoView({block: 'center'})")
        first = rows.nth(0)
        lower = first.get_by_label("− tolerance", exact=True)
        upper = first.get_by_label("+ tolerance", exact=True)
        toggle = _link_toggle(first, "tolerance")
        expect(lower).to_have_value("0.05")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        # The second row holds a pair: unlinked, each side its own.
        second = rows.nth(1)
        expect(_link_toggle(second, "tolerance")).to_have_attribute(
            "aria-pressed", "false"
        )
        expect(second.get_by_label("− tolerance", exact=True)).to_have_value("0.02")
        expect(second.get_by_label("+ tolerance", exact=True)).to_have_value("0.08")
        expect(second.get_by_label("before time_tolerance")).to_have_value("0.05")
        expect(second.get_by_label("after time_tolerance")).to_have_value("0.1")

        _retype(page, lower, "0.07")
        expect(upper).to_have_value("0.07", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        toggle.click()
        expect(toggle).to_have_attribute(
            "aria-pressed", "false", timeout=EXPECT_TIMEOUT
        )
        _retype(page, upper, "0.2")
        page.wait_for_timeout(800)
        expect(upper).to_have_value("0.2")
        expect(lower).to_have_value("0.07")
        staged = _staged_in_preview(page)
        assert '"tolerance":{"lower":0.07,"upper":0.2},"time_tolerance":0.1' in staged
        _shot(page, tmp_path, "table-row-unlinked")

        # Half a pair: marked, and the list is held — the time typed now
        # is not staged beside the pair the row still holds.
        _retype(page, lower, "")
        marked = editor.locator(".q-field--error")
        expect(marked).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(marked).to_contain_text("required")
        _retype(page, first.get_by_label("time", exact=True), "0.3")
        page.wait_for_timeout(800)
        expect(first.get_by_label("time", exact=True)).to_have_value("0.3")
        expect(upper).to_have_value("0.2")  # the side that is not blank
        held = _staged_in_preview(page)
        assert '"time":0.25' in held and '"time":0.3,' not in held
        assert '"tolerance":{"lower":0.07,"upper":0.2}' in held
        expect(editor.locator(".text-negative").filter(has_text="row")).to_have_count(0)
        _shot(page, tmp_path, "table-row-half")
        _retype(page, lower, "0.01")
        expect(marked).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        expect(lower).to_have_value("0.01")
        filled = _staged_in_preview(page)
        assert '"time":0.3,"value":0.3,"tolerance":{"lower":0.01,"upper":0.2}' in filled
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")


def test_a_blank_required_table_cell_says_so_in_the_browser(
    tmp_path, playwright_browser
):
    """A required cell cleared to retype it holds the whole list back, and
    says why WHERE A PERSON SEES IT: the cell turns red with "time is
    required" under it. It said nothing — the mark was an `error` prop,
    which NiceGUI resets on the validation that follows every change — so
    the table silently stopped staging."""
    with _served(tmp_path, playwright_browser) as page:
        page.wait_for_timeout(700)
        _open_crit(page, 0, "tube · x")
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        row = editor.locator(".dstf-table-scroll > div").first
        row.evaluate("e => e.scrollIntoView({block: 'center'})")
        time_cell = row.get_by_label("time", exact=True)
        expect(time_cell).to_have_value("0.5")
        _retype(page, time_cell, "")
        marked = editor.locator(".q-field--error")
        expect(marked).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(marked).to_contain_text("time is required")
        page.wait_for_timeout(500)
        assert _pending(page) == "0 pending edits"  # held, not refused
        expect(editor.locator(".text-negative").filter(has_text="row 0")).to_have_count(
            0
        )
        _shot(page, tmp_path, "table-required-cell")
        page.keyboard.type("0.6")
        expect(marked).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )


# -- a number field keeps what is typed in it ------------------------------------

#: What `/__slow` adds to every stage: the rescore of a large test, during
#: which a person goes on typing.
STAGE_MS = 250
#: A person's typing: ms between keys.
KEY_MS = 100


def _typing_project(tmp_path: Path, tolerance: float = 0.3) -> tuple[Path, Path]:
    """`_two_var_project` with A at a test tolerance of 0.01, scored on x by
    an nrmse leaf holding ``tolerance`` and a windowed range, and on y by a
    points leaf holding 0.3 (linked) with one declared row."""
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["comparison"] = {"tolerance": 0.01}
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": tolerance},
                    {
                        "metric": "range",
                        "variable": "x",
                        "min_value": -1.0,
                        "max_value": 2.0,
                        "window": {"start": 0.3, "end": 0.9},
                    },
                    {
                        "metric": "points",
                        "variable": "y",
                        "tolerance": 0.3,
                        "points": [{"time": 0.3, "value": 0.6}],
                    },
                ],
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return cfg, work


@contextmanager
def _slow_editor(
    tmp_path: Path,
    playwright_browser,
    index: int,
    name: str,
    project: Callable[[Path], tuple[Path, Path]] = _typing_project,
):
    """The open editor of criterion ``name`` on plot ``index``, over a server
    whose every stage takes `STAGE_MS` longer: each keystroke's answer comes
    back after the keys that followed it were typed."""
    with _served(tmp_path, playwright_browser, project=project, hooks=True) as page:
        base = page.url.split("test/")[0]
        with urllib.request.urlopen(f"{base}__slow?ms={STAGE_MS}") as r:
            assert json.loads(r.read()) == {"slowed": STAGE_MS}
        page.wait_for_timeout(700)
        _open_crit(page, index, name)
        editor = page.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        editor.evaluate("e => e.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(400)
        yield page, editor


def _type_over(page, field, text: str, delay: int = KEY_MS) -> None:
    """Select ``field``'s content and type ``text`` over it, a key every
    ``delay`` ms."""
    field.click()
    page.keyboard.press("Control+A")
    page.keyboard.type(text, delay=delay)


#: Where the preview shows each leaf's own number.
NRMSE_X = '"variable":"x","tolerance":'
POINTS_Y = '"variable":"y","tolerance":'


def _answered(page) -> None:
    """Wait until the server has staged nothing new for a second, and its
    last answer has had time to land — without touching the page: a click
    anywhere takes the focus from the field being typed in."""
    base = page.url.split("test/")[0]
    count, since, deadline = None, time.monotonic(), time.monotonic() + 30
    while time.monotonic() - since < 1.0:
        assert time.monotonic() < deadline, "the server never went quiet"
        with urllib.request.urlopen(f"{base}__staged") as r:
            now = json.loads(r.read())["staged"]
        if now != count:
            count, since = now, time.monotonic()
        page.wait_for_timeout(100)
    page.wait_for_timeout(300)


def _shown_and_staged(page, field, before: str) -> tuple[str, str | None]:
    """What ``field`` reads once the server has answered every key, read
    while it still has the focus, and the number the preview then shows
    right after ``before`` (None where there is none)."""
    _answered(page)
    shown = field.input_value()
    staged = _staged_in_preview(page)
    number = re.search(re.escape(before) + r"(-?[0-9.e-]+)", staged)
    return shown, number.group(1) if number else None


def test_a_number_typed_over_at_a_persons_speed_is_what_the_field_shows(
    tmp_path, playwright_browser
):
    """An nrmse tolerance of 0.3, selected and typed over with `0.25` at a
    person's speed while each stage takes 250 ms: the field reads `0.25` and
    0.25 is staged. It read `0.025` and staged it — the server's answer to
    the `0` came back as `0.0` under the keys that followed, the `.` was
    swallowed and `25` appended."""
    with _slow_editor(tmp_path, playwright_browser, 0, "nrmse · x") as (page, editor):
        tolerance = editor.get_by_label("Tolerance", exact=True)
        expect(tolerance).to_have_value("0.3")
        tolerance.click()
        page.keyboard.press("Control+A")
        page.keyboard.type("0.2", delay=KEY_MS)
        _shot(page, tmp_path, "typing-mid")
        page.keyboard.type("5", delay=KEY_MS)
        assert _shown_and_staged(page, tolerance, NRMSE_X) == ("0.25", "0.25")


def test_a_number_cleared_and_typed_at_once_is_what_the_field_shows(
    tmp_path, playwright_browser
):
    """Select all, Delete, and `0.7` typed before the server answered the
    clearing: the field reads `0.7` and 0.7 is staged. It showed nothing
    while 0.7 was staged — the answer to the clearing carried
    `model-value: null`, which is not the `""` the browser sent, and blanked
    the field under the keys."""
    with _slow_editor(tmp_path, playwright_browser, 0, "nrmse · x") as (page, editor):
        tolerance = editor.get_by_label("Tolerance", exact=True)
        tolerance.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type("0.7")
        assert _shown_and_staged(page, tolerance, NRMSE_X) == ("0.7", "0.7")


def test_a_number_backspaced_away_leaves_the_field_empty_and_unset(
    tmp_path, playwright_browser
):
    """`0.25` backspaced away, each key after the server answered the one
    before: the field is empty, the leaf holds no tolerance and the hint
    says the one in effect. It stuck at `0.` — the answer to `0.` came back
    as `0.0` and put back the digit just deleted."""
    project = partial(_typing_project, tolerance=0.25)
    with _slow_editor(tmp_path, playwright_browser, 0, "nrmse · x", project) as (
        page,
        editor,
    ):
        tolerance = editor.get_by_label("Tolerance", exact=True)
        expect(tolerance).to_have_value("0.25")
        tolerance.click()
        page.keyboard.press("End")
        for _ in range(4):
            page.keyboard.press("Backspace")
            page.wait_for_timeout(STAGE_MS + 150)
        _answered(page)
        shown = tolerance.input_value()
        staged = _staged_in_preview(page)
        assert (shown, NRMSE_X in staged) == ("", False), staged
        assert '{"metric":"nrmse","variable":"x"}' in staged, staged
        fields = {f[0]: f for f in page.evaluate(READ_FIELDS)}
        assert fields["Tolerance"][3] == "in effect: 0.01 (the test's tolerance)"


@pytest.mark.parametrize(("typed", "staged_as"), [("-0.5", "-0.5"), ("1e-3", "0.001")])
def test_a_sign_and_an_exponent_are_staged_as_typed(
    tmp_path, playwright_browser, typed, staged_as
):
    """`-0.5` keeps its minus and `1e-3` its exponent: the field reads what
    was typed and that number is staged. Both pass through text that is no
    number yet (`-`, `1e`, `1e-`), which the server's answer rewrote."""
    with _slow_editor(tmp_path, playwright_browser, 0, "nrmse · x") as (page, editor):
        tolerance = editor.get_by_label("Tolerance", exact=True)
        _type_over(page, tolerance, typed)
        assert _shown_and_staged(page, tolerance, NRMSE_X) == (typed, staged_as)


def test_a_linked_twin_typed_in_before_the_mirror_lands_keeps_what_was_typed(
    tmp_path, playwright_browser
):
    """Linked: `0.2` into `−`, Tab past the toggle into `+` and `9` at once,
    before the server mirrored the 0.2 there. `+` reads the 9 typed in it,
    `−` follows, and 9 is what is staged. `+` showed the mirrored 0.2 while
    9 was staged."""
    with _slow_editor(tmp_path, playwright_browser, 1, "points · y") as (page, editor):
        lower = editor.get_by_label("− Default tolerance", exact=True)
        upper = editor.get_by_label("+ Default tolerance", exact=True)
        expect(lower).to_have_value("0.3")
        _type_over(page, lower, "0.2", delay=0)
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.keyboard.type("9")
        assert _shown_and_staged(page, upper, POINTS_Y) == ("9", "9.0")
        assert float(lower.input_value()) == 9


@pytest.mark.parametrize(
    ("index", "name", "label", "before"),
    [
        (1, "points · y", "time", '"points":[{"time":'),
        (0, "range · x", "window start", '"window":{"start":'),
    ],
)
def test_a_table_cell_and_a_window_field_keep_what_is_typed(
    tmp_path, playwright_browser, index, name, label, before
):
    """The same typing in a table's cell and in a window field: `0.25` over
    0.3 at a person's speed is what the field reads and what is staged."""
    with _slow_editor(tmp_path, playwright_browser, index, name) as (page, editor):
        field = editor.get_by_label(label, exact=True)
        expect(field).to_have_value("0.3")
        _type_over(page, field, "0.25")
        assert _shown_and_staged(page, field, before) == ("0.25", "0.25")


def test_a_field_typed_in_and_left_takes_the_servers_value_again(
    tmp_path, playwright_browser
):
    """A field keeps what was typed only while it is typed in: `0.25` into
    the linked `−` and, the server having answered, `0.4` into `+` — `−`,
    left, reads the 0.4 the server mirrors into it."""
    with _slow_editor(tmp_path, playwright_browser, 1, "points · y") as (page, editor):
        lower = editor.get_by_label("− Default tolerance", exact=True)
        upper = editor.get_by_label("+ Default tolerance", exact=True)
        _type_over(page, lower, "0.25")
        _answered(page)
        _type_over(page, upper, "0.4")
        assert _shown_and_staged(page, upper, POINTS_Y) == ("0.4", "0.4")
        assert float(lower.input_value()) == 0.4


#: The open drawer's panel width, and the first plot's svg width.
WIDTHS = """() => {
  const panel = [...document.querySelectorAll('.dstf-drawer-panel')]
    .find(p => p.offsetParent !== null);
  const svg = document.querySelector('.js-plotly-plot .main-svg');
  return [panel ? panel.getBoundingClientRect().width : null,
          svg ? svg.getBoundingClientRect().width : null];
}"""

#: Starts recording every animation frame's panel width into
#: `window.__frames` for ``ms``: a release that re-rendered the drawer from
#: the server's old width flashes it back for a frame or more.
FRAMES = """(ms) => {
  const seen = window.__frames = [], end = performance.now() + ms;
  const tick = () => {
    const p = [...document.querySelectorAll('.dstf-drawer-panel')]
      .find(p => p.offsetParent !== null);
    seen.push(p ? Math.round(p.getBoundingClientRect().width) : null);
    if (performance.now() < end) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""


def _drag_grip(page, dx: float, *, watch_ms: int = 0) -> list | None:
    """Drag the grip ``dx`` px; with ``watch_ms``, the panel's width on
    every frame from just before the release until ``watch_ms`` after."""
    grip = page.locator(".dstf-drawer-handle")
    box = grip.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + 200
    page.mouse.move(x, y)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(x + dx * k / 10, y)
    if watch_ms:
        page.evaluate(FRAMES, watch_ms)
    page.mouse.up()
    if not watch_ms:
        return None
    page.wait_for_timeout(watch_ms + 200)
    return page.evaluate("window.__frames")


def test_the_drawer_resizes_by_its_edge_and_keeps_its_width(
    tmp_path, playwright_browser
):
    """K5 in Chromium: the grip on the open drawer's right edge, dragged
    200 px right, makes the 320 px panel 520 px with no frame back at 320
    after the release, and a plot 200 px narrower; past the bounds it
    clamps at the smaller of 60% of the window and the window less the rail
    and 480 px, and at 240 px; the width is the tab's, kept on a switch of
    test."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        expect(page.locator(".dstf-drawer-handle")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(500)
        panel0, svg0 = page.evaluate(WIDTHS)
        assert round(panel0) == 320, panel0
        frames = _drag_grip(page, 200, watch_ms=1500)
        assert len(frames) > 30, frames  # ~1.5 s of frames past the release
        assert all(w == 520 for w in frames), frames
        page.wait_for_timeout(600)
        panel1, svg1 = page.evaluate(WIDTHS)
        assert round(panel1) == 520, panel1
        assert abs((svg0 - svg1) - 200) <= 4, (svg0, svg1)
        _shot(page, tmp_path, "drawer-520")
        _drag_grip(page, 2000)
        page.wait_for_timeout(400)
        # at 1280 px the plots' 480 px bind before 60% of the window does
        widest = min(0.6 * VIEWPORT["width"], VIEWPORT["width"] - 40 - 480)
        assert round(page.evaluate(WIDTHS)[0]) == round(widest)
        _drag_grip(page, -2000)
        page.wait_for_timeout(400)
        assert round(page.evaluate(WIDTHS)[0]) == 240
        _drag_grip(page, 200)
        page.wait_for_timeout(400)
        # A switch of test (the Tests drawer): the width is the tab's.
        page.goto(page.url.replace(f"test/{A}", f"test/{C}"))
        expect(page.locator(".dstf-subheader")).to_contain_text(
            C, timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(600)
        assert round(page.evaluate(WIDTHS)[0]) == 440


#: The page's plots column (`.q-page`, right of the drawer), each plot
#: card's width, and each context chip's `×` against its card:
#: ``[column width, card widths, [[x right, card right], ...]]``.
CARD_ROOM = """() => {
  const column = document.querySelector('.q-page').getBoundingClientRect().width;
  const cards = [...document.querySelectorAll('.dstf-plot')];
  const xs = [...document.querySelectorAll('.dstf-context-chip .q-btn')].map((b) => {
    const card = b.closest('.dstf-plot');
    return [b.getBoundingClientRect().right, card.getBoundingClientRect().right];
  });
  return [column, cards.map((c) => c.getBoundingClientRect().width), xs];
}"""


def test_the_drawer_leaves_the_plots_480_px_at_900(tmp_path, playwright_browser):
    """At 900 px 60% of the window left the plots 320 px, and a context
    chip's `×` past its card: the drawer's widest is also the window less
    the rail and 480 px for the plots, so a drag to the far right stops at
    380 px, the plots column keeps 480 px and the chip's `×` stays inside
    its card. The Python side holds the same ceiling (`Rail.resize`)."""
    with _served(tmp_path, playwright_browser, project=_long_names_project) as page:
        page.set_viewport_size({"width": 900, "height": 900})
        cards = page.locator(".dstf-plot")
        expect(cards).to_have_count(2, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-context-chip")).to_have_count(1)
        _open_vars(page)
        expect(page.locator(".dstf-drawer-handle")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(500)
        # the browser's clamp, not the server's after the release: no frame
        # from just before it on is wider
        frames = _drag_grip(page, 2000, watch_ms=1000)
        assert frames and all(w == 380 for w in frames), frames
        page.wait_for_timeout(300)
        panel = page.evaluate(WIDTHS)[0]
        column, widths, xs = page.evaluate(CARD_ROOM)
        _shot(page, tmp_path, "drawer-widest-900")
        assert round(panel) == 900 - 40 - 480, panel
        assert round(column) >= 480, column
        assert len(widths) == 2, widths  # 416 px: the page's padding is 2 x 32
        assert len(xs) == 1 and all(x <= card + 0.5 for x, card in xs), xs
        _no_sideways_scroll(page)


def test_a_long_label_has_the_drawer_rows_first_line(tmp_path, playwright_browser):
    """2026-09-29: a row's label has its first line to itself; the name and
    "plot N" are on the second. At the default 320 px the `cat(...)[3]`
    label shows at least 30 of its characters (range rects)."""
    with _served(tmp_path, playwright_browser, hooks=True) as page:
        base = page.url.split("test/")[0]
        query = urllib.parse.urlencode({"name": "x", "expression": CAT3})
        with urllib.request.urlopen(f"{base}__expression?{query}") as r:
            assert json.loads(r.read()) == {"stamped": 1}
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        _open_vars(page)
        row = _var_row(page, CAT3)
        expect(row).to_have_count(1, timeout=EXPECT_TIMEOUT)
        cut = row.locator(".dstf-var-line1 .dstf-mid").evaluate(MID_CUT)
        assert len(cut["shown"]) + len("[3]") >= 30, cut
        # the name is on the second line, below the label
        label = row.locator(".dstf-var-line1").bounding_box()
        name = row.locator(".dstf-var-line2 .dstf-varname").bounding_box()
        assert label is not None and name is not None
        assert name["y"] >= label["y"] + label["height"] - 1, (label, name)
        _shot(page, tmp_path, "two-line-row")


# -- batch U2-B: the picker's keys, the drawer's width -----------------------


def _amb_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` whose A result file holds ten names that do not
    match `T_amb` and two that do, after them."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for name in [f"a{i}" for i in range(10)] + ["system.T_amb", "room.T_amb_set"]:
        payload["variables"][name] = list(payload["time"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg, work


def test_keys_typed_inside_the_filters_debounce_act_on_the_list_shown(
    tmp_path, playwright_browser
):
    """`T_amb` typed fast and ↓ Enter at once — inside the filter's 250 ms
    debounce — chose a row of the list BEFORE the filter (y), which the list
    then hid: the keys carry the box's text, so the choice is a row shown.
    The box is a combobox over a listbox in the browser too (Quasar hands
    the input its attributes), the cursor's row its active descendant."""
    with _served(tmp_path, playwright_browser, project=_amb_project) as page:
        head = page.locator(".dstf-plot").first.locator(".dstf-criteria-head")
        expect(head).to_contain_text("Criteria", timeout=EXPECT_TIMEOUT)
        head.get_by_role("button", name="Add a criterion for x").click()
        box = page.get_by_role("combobox", name="Variable")
        expect(box).to_be_visible(timeout=EXPECT_TIMEOUT)
        box.click()
        box.press_sequentially("T_amb", delay=20)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        rows = page.locator(".dstf-pick-row")
        expect(rows).to_have_count(2, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(700)  # the debounced filter has arrived
        expect(rows).to_have_count(2)
        chosen = page.locator(".dstf-pick-chosen").inner_text()
        shown = [
            page.locator(".dstf-pick-row").nth(i).get_attribute("aria-label")
            for i in range(2)
        ]
        assert chosen == f"Chosen: {shown[0]}", (chosen, shown)
        expect(page.locator(".dstf-pick-on")).to_have_count(1)
        _shot(page, tmp_path, "picker-fast-type")
        expect(page.get_by_role("combobox", name="Variable")).to_have_count(1)
        expect(page.get_by_role("listbox")).to_have_count(1)
        cursor = page.locator(".dstf-pick-cursor")
        expect(cursor).to_have_count(1)
        assert box.get_attribute("aria-activedescendant") == cursor.get_attribute("id")
        assert box.get_attribute("aria-controls") == page.get_by_role(
            "listbox"
        ).get_attribute("id")
        expect(page.get_by_role("option", name="system.T_amb")).to_have_count(1)


def test_a_needle_undone_inside_the_debounce_redraws_the_list(
    tmp_path, playwright_browser
):
    """`T_amb`, ↓ — drawn for `T_amb` by the key — then the text deleted
    inside the filter's 250 ms debounce. Quasar's model value had never
    left "", so the debounce emitted nothing and the list stayed at
    T_amb's rows under an empty box: the key now flushes the box's value
    first. The list is the box's."""
    with _served(tmp_path, playwright_browser, project=_amb_project) as page:
        head = page.locator(".dstf-plot").first.locator(".dstf-criteria-head")
        expect(head).to_contain_text("Criteria", timeout=EXPECT_TIMEOUT)
        head.get_by_role("button", name="Add a criterion for x").click()
        box = page.get_by_role("combobox", name="Variable")
        expect(box).to_be_visible(timeout=EXPECT_TIMEOUT)
        rows = page.locator(".dstf-pick-row")
        page.wait_for_timeout(500)
        full = rows.count()
        assert full > 2
        box.click()
        box.press_sequentially("T_amb", delay=20)
        page.keyboard.press("ArrowDown")
        for _ in range(5):
            page.keyboard.press("Backspace")
        page.wait_for_timeout(1200)
        assert box.input_value() == ""
        expect(rows).to_have_count(full)


def test_a_clicked_row_carries_the_one_ring(tmp_path, playwright_browser):
    """A click moves the ring with the choice: after ↓ ↓ from the box and a
    click on another row, one row has the ring — the clicked one — and it
    is the box's active descendant."""
    with _served(tmp_path, playwright_browser, project=_amb_project) as page:
        head = page.locator(".dstf-plot").first.locator(".dstf-criteria-head")
        expect(head).to_contain_text("Criteria", timeout=EXPECT_TIMEOUT)
        head.get_by_role("button", name="Add a criterion for x").click()
        box = page.get_by_role("combobox", name="Variable")
        expect(box).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(500)
        box.click()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        target = page.get_by_role("option", name="a3", exact=True)
        target.click()
        cursor = page.locator(".dstf-pick-cursor")
        expect(cursor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(cursor).to_have_attribute("aria-label", "a3")
        assert box.get_attribute("aria-activedescendant") == target.get_attribute("id")
        _shot(page, tmp_path, "picker-click-ring")


#: The open drawer's panel width and the drawer's (`.dstf-rail-drawer`,
#: the rail and the panel, less the `bordered` drawer's 1 px border).
PANEL_AND_DRAWER = """() => {
  const panel = [...document.querySelectorAll('.dstf-drawer-panel')]
    .find(p => p.offsetParent !== null);
  const drawer = document.querySelector('.dstf-rail-drawer');
  return [Math.round(panel.getBoundingClientRect().width),
          Math.round(drawer.getBoundingClientRect().width) + 1];
}"""


def test_the_drawer_follows_the_panel_during_a_drag(tmp_path, playwright_browser):
    """The drawer's width follows the pointer in the browser (NiceGUI's
    element store), not only once the server has the release: mid-drag,
    before the button is up, the drawer is the rail plus the panel."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        grip = page.locator(".dstf-drawer-handle")
        expect(grip).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(500)
        box = grip.bounding_box()
        assert box is not None
        x, y = box["x"] + box["width"] / 2, box["y"] + 200
        page.mouse.move(x, y)
        page.mouse.down()
        for k in range(1, 11):
            page.mouse.move(x + 200 * k / 10, y)
        page.wait_for_timeout(300)
        panel, drawer = page.evaluate(PANEL_AND_DRAWER)
        page.mouse.up()
        assert panel == 520, panel
        assert drawer == 40 + panel, (panel, drawer)


def test_a_width_the_window_no_longer_holds_is_clamped_in_the_browser(
    tmp_path, playwright_browser
):
    """The tab's width is kept from a wider window: 760 px (the widest at
    1280) left the plots 100 px at 900. A window shrunk under it clamps it
    to that window's widest (380 px at 900) — the plots keep 480 px — and
    so does a page opened in the narrower window: the browser clamps it on
    load and tells the server, which keeps it."""
    with _served(tmp_path, playwright_browser, project=_long_names_project) as page:
        url = page.url
        _open_vars(page)
        expect(page.locator(".dstf-drawer-handle")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(500)
        _drag_grip(page, 2000)
        page.wait_for_timeout(400)
        assert page.evaluate(PANEL_AND_DRAWER) == [760, 800]
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [380, 420]
        column = page.evaluate(CARD_ROOM)[0]
        assert round(column) >= 480, column
        _shot(page, tmp_path, "drawer-shrunk-900")
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [380, 420]
        # the load's own clamp: widest again at 1280, then a page opened
        # in a 900 px window
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_timeout(300)
        _drag_grip(page, 2000)
        page.wait_for_timeout(400)
        assert page.evaluate(PANEL_AND_DRAWER) == [760, 800]
        page.goto("about:blank")
        page.set_viewport_size({"width": 900, "height": 900})
        page.goto(url)
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [380, 420]
        page.reload()  # the server kept what the browser sent
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [380, 420]


def test_the_grip_answers_the_keyboard(tmp_path, playwright_browser):
    """The grip is a focusable separator (WAI-ARIA's window splitter): ←/→
    16 px, with Shift 64, Home the narrowest, End the widest, its
    `aria-valuenow` the width and `aria-valuemax` the widest this window
    allows; the width a key leaves is the tab's (kept on a reload)."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        grip = page.locator(".dstf-drawer-handle")
        expect(grip).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(500)
        assert grip.evaluate("g => g.tabIndex") == 0
        grip.focus()

        def width() -> int:
            return page.evaluate(PANEL_AND_DRAWER)[0]

        page.keyboard.press("ArrowRight")
        assert width() == 336
        page.keyboard.press("Shift+ArrowRight")
        assert width() == 400
        page.keyboard.press("ArrowLeft")
        assert width() == 384
        assert grip.get_attribute("aria-valuenow") == "384"
        assert grip.get_attribute("aria-valuemin") == "240"
        assert grip.get_attribute("aria-valuemax") == "760"
        _shot(page, tmp_path, "grip-focused")
        page.keyboard.press("Home")
        assert width() == 240
        page.keyboard.press("End")
        assert width() == 760
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(400)
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [744, 784]


def test_a_grip_key_the_focus_left_before_its_release_sends_the_width(
    tmp_path, playwright_browser
):
    """The key's release sends the width — but a key held while the focus
    moves away (→ held, Tab) is released on another element, and the
    width the panel took never reached the server: a reload drew the old
    one. Leaving the grip sends what a key moved and nothing sent."""
    with _served(tmp_path, playwright_browser, project=_drawer_project) as page:
        _open_vars(page)
        grip = page.locator(".dstf-drawer-handle")
        expect(grip).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(500)
        grip.focus()
        page.keyboard.down("ArrowRight")
        page.keyboard.press("Tab")
        page.keyboard.up("ArrowRight")
        assert page.evaluate(PANEL_AND_DRAWER)[0] == 336
        page.wait_for_timeout(400)
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(800)
        assert page.evaluate(PANEL_AND_DRAWER) == [336, 376]


def _plots_mounted(page) -> None:
    """Every plot on the page drawn, and a beat for its resize observer: a
    rebuild (a `+` stages a criterion) that unmounts a plot still awaiting
    its own mount leaves NiceGUI's observer on a detached div, and the
    page raises "Resize must be passed a displayed plot div element" — a
    `+` clicked within the page's first second did."""
    page.wait_for_function(
        "() => { const p = [...document.querySelectorAll('.js-plotly-plot')];"
        " return p.length > 0 && p.every(e => e.querySelector('.main-svg')); }",
        timeout=EXPECT_TIMEOUT,
    )
    page.wait_for_timeout(300)


#: An element's computed background, and the selection token's resolved.
SELECTION_BACKGROUND = """(el) => {
    const probe = document.createElement("div");
    probe.style.background = "var(--dstf-selection)";
    document.body.appendChild(probe);
    const want = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return [getComputedStyle(el).backgroundColor, want];
}"""


def test_a_statistics_plus_while_plotly_loads_raises_nothing(
    tmp_path, playwright_browser
):
    """A `+` on a Statistics row rebuilt every plot, and a plot unmounted
    while plotly.js was still loading left NiceGUI's resize observer on a
    detached div: the console said "Resize must be passed a displayed plot
    div element" (`_served` fails on it). A statistics criterion changes no
    plot, so nothing is unmounted. plotly.js is held back 2.5 s so the
    click lands before any plot is drawn."""
    with _served(tmp_path, playwright_browser, project=_stats_project) as page:
        held: list[str] = []

        def slow(route) -> None:
            held.append(route.request.url)
            time.sleep(2.5)
            route.continue_()

        page.route(re.compile(r".*plotly.*\.js.*"), slow)
        page.reload()
        card = page.locator(".dstf-stats")
        card.get_by_role("button", name="Statistics").click()
        row = card.locator(".dstf-stat-row").filter(
            has_text=re.compile(r"^Nonlinear count")
        )
        drawn = page.evaluate("() => document.querySelectorAll('.main-svg').length")
        row.get_by_role("button", name="Add a requirement: exact match").click()
        expect(row.locator(".dstf-pill")).to_have_text("fail", timeout=EXPECT_TIMEOUT)
        _plots_mounted(page)
        page.wait_for_timeout(1000)
        assert held and drawn == 0


def test_the_statistics_cards_plus_adds_a_criterion_to_the_tree(
    tmp_path, playwright_browser
):
    """U3: the card is closed to the changed rows; open, a count row's `+`
    stages its requirement at the root's end — the tree line counts one
    more criterion — and the row shows the criterion's pill instead."""
    with _served(tmp_path, playwright_browser, project=_stats_project) as page:
        card = page.locator(".dstf-stats")
        expect(card.locator(".dstf-stats-line")).to_contain_text(
            "1 changed: Nonlinear count 0→3 · CPUtime +40%", timeout=EXPECT_TIMEOUT
        )
        tree = page.locator(".dstf-tree-line")
        expect(tree).to_contain_text("and · 2 criteria")
        _plots_mounted(page)
        card.get_by_role("button", name="Statistics").click()
        rows = card.locator(".dstf-stat-row:not(.dstf-stat-head)")
        expect(rows).to_have_count(20, timeout=EXPECT_TIMEOUT)
        row = rows.filter(has_text=re.compile(r"^Nonlinear count"))
        row.get_by_role("button", name="Add a requirement: exact match").click()
        expect(tree).to_contain_text("and · 3 criteria", timeout=EXPECT_TIMEOUT)
        expect(row.locator(".dstf-pill")).to_have_text("fail", timeout=EXPECT_TIMEOUT)
        expect(row.get_by_role("button")).to_have_count(0)


def test_a_statistics_criterion_is_edited_under_its_row(tmp_path, playwright_browser):
    """U3 Task 6: after a `+` the new criterion's editor opens under its
    row, inside the card at both widths (B12: the editor's indent ran it
    16 px past the card's edge); a typed tolerance stages; Done closes it.
    (The add form's diagnostic add lands here too — in the user simulation:
    a JSON-driver run records no diagnostic to pick.)"""
    with _served(tmp_path, playwright_browser, project=_stats_project) as page:
        card = page.locator(".dstf-stats")
        _plots_mounted(page)
        card.get_by_role("button", name="Statistics").click()
        rows = card.locator(".dstf-stat-row:not(.dstf-stat-head)")
        row = rows.filter(has_text=re.compile(r"^Nonlinear count"))
        row.get_by_role("button", name="Add a requirement: exact match").click()
        editor = card.locator(".dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(row).to_have_class(re.compile(r"dstf-crit-open"))
        # A changed row is tinted; open, it wears the selection as every
        # open criteria row does — the tint hid which row was open.
        expect(row).to_have_class(re.compile(r"dstf-stat-changed"))
        got, want = row.evaluate(SELECTION_BACKGROUND)
        assert got == want, (got, want)
        for width in (1280, 900):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(300)
            outer, inner = card.bounding_box(), editor.bounding_box()
            assert outer is not None and inner is not None
            assert inner["x"] + inner["width"] <= outer["x"] + outer["width"], width
        expect(editor.get_by_label("mode", exact=True)).to_have_count(0)
        expect(row).to_contain_text("exact")
        # linked: either field of the tolerance's two sets both
        tolerance = editor.get_by_label("− Tolerance", exact=True)
        tolerance.fill("2")
        tolerance.press("Tab")
        # staged and rescored: the row says the new requirement
        expect(row).to_contain_text("±2", timeout=EXPECT_TIMEOUT)
        editor.get_by_role("button", name="Done").click()
        expect(editor).to_have_count(0, timeout=EXPECT_TIMEOUT)


# -- where each row sits (U5 Task 1) ------------------------------------------


def _deep_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` with A's tree four levels deep and a sibling group:
    and[and[tube · x, and[range · x, and[range · y]]], and[range · y]]."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            tube, range_x, range_y = entry["metrics"]["children"]
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {
                        "combinator": "and",
                        "children": [
                            tube,
                            {
                                "combinator": "and",
                                "children": [
                                    range_x,
                                    {"combinator": "and", "children": [range_y]},
                                ],
                            },
                        ],
                    },
                    {"combinator": "and", "children": [dict(range_y)]},
                ],
            }
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


DEEP_ROWS = 9
G1, G2, G3 = (
    "/metrics/children/0",
    "/metrics/children/0/children/1",
    "/metrics/children/0/children/1/children/1",
)
SIB = "/metrics/children/1"

#: Every row's bars: its path, and each bar's computed colour.
BARS = """() => [...document.querySelectorAll('.dstf-tree-row')].map(r => ({
  path: r.dataset.treePath,
  bars: [...r.querySelector(':scope > .dstf-bars').children].map(
    b => getComputedStyle(b).backgroundColor),
}))"""
TOKENS = """() => Object.fromEntries([1, 2, 3, 4].flatMap(i => [
  [`${i}`, getComputedStyle(document.documentElement).getPropertyValue(`--dstf-level-${i}`).trim()],
  [`${i}-on`, getComputedStyle(document.documentElement).getPropertyValue(`--dstf-level-${i}-on`).trim()],
]))"""


def _rgb(hex_: str) -> str:
    h = hex_.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(h[i : i + 2], 16) for i in (0, 2, 4)))


def _depth(path: str) -> int:
    return path.count("/children/")


def _expected_bars(tokens, path: str, on: str | None) -> list[str]:
    """``path``'s bars when the branch of group ``on`` is hovered."""
    k = _depth(on) if on else 0
    lit = on is not None and (path == on or path.startswith(on + "/"))
    return [
        _rgb(tokens[f"{(i - 1) % 4 + 1}{'-on' if lit and i == k else ''}"])
        for i in range(1, _depth(path) + 1)
    ]


def test_each_level_draws_its_bar_in_its_colour_and_a_hover_deepens_its_branch(
    tmp_path, playwright_browser
):
    """Q1 / Y2 with a real mouse: every bar is its level's token; hovering a
    group deepens that group's level bar on it and its descendants and
    nothing else — its sibling group stays; hovering a criterion deepens
    its parent's branch; the root deepens nothing. The root says "· root"
    (Q2; E7: the separator is U+00B7, not an escape printed as text)."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        tokens = page.evaluate(TOKENS)
        assert tokens["1"] == "#7fb0ec" and tokens["4-on"] == "#6b7b8f"
        tag = _tree_row(page, "/metrics").locator(".dstf-root-tag")
        expect(tag).to_have_text("root")
        before = tag.evaluate("e => getComputedStyle(e, '::before').content")
        assert before == '"\u00b7 "', before
        page.mouse.move(2, 2)

        def check(on):
            page.wait_for_timeout(150)
            for row in page.evaluate(BARS):
                assert row["bars"] == _expected_bars(tokens, row["path"], on), (
                    on,
                    row,
                )

        check(None)
        for hovered, on in (
            (G2, G2),
            (G1, G1),
            (f"{G3}/children/0", G3),  # a criterion: its parent's branch
            (f"{G1}/children/0", G1),
            (SIB, SIB),
            ("/metrics", None),
            (f"{SIB}/children/0", SIB),
        ):
            _tree_row(page, hovered).locator(".dstf-pill").first.hover()
            check(on)
        page.mouse.move(2, 2)
        check(None)


#: Every row's box against each of its bars' (top, bottom).
BAR_SPANS = """() => [...document.querySelectorAll('.dstf-tree-row')].map(r => {
  const box = r.getBoundingClientRect();
  return {path: r.dataset.treePath, row: [box.top, box.bottom],
          bars: [...r.querySelector(':scope > .dstf-bars').children].map(b => {
            const bb = b.getBoundingClientRect(); return [bb.top, bb.bottom]; })};
})"""


def test_a_level_bar_spans_its_rows_full_height(tmp_path, playwright_browser):
    """Q1 (K5): a row's bars run its full height, its top border included,
    so a branch's bars join from row to row — they stopped at the border,
    1 px short."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        spans = page.evaluate(BAR_SPANS)
        assert sum(len(r["bars"]) for r in spans) > 0
        for r in spans:
            top, bottom = r["row"]
            for bar in r["bars"]:
                assert abs(bar[0] - top) <= 0.5 and abs(bar[1] - bottom) <= 0.5, r


# -- folding (U5 Task 2) ------------------------------------------------------

#: Every tree row's and form slot's shown state, by path.
SHOWN = """() => Object.fromEntries([...document.querySelectorAll(
  '.dstf-tree [data-tree-path], .dstf-tree [data-slot-of]')].map(el => [
  (el.dataset.treePath ? 'row ' + el.dataset.treePath : 'slot ' + el.dataset.slotOf),
  getComputedStyle(el).display !== 'none']))"""
REV = "() => document.querySelector('.dstf-tree [data-rev]').dataset.rev"


def _fold_click(page, path: str) -> None:
    _tree_row(page, path).locator(".dstf-chev").click()


def test_a_chevron_folds_its_group_in_the_page_and_says_what_it_hides(
    tmp_path, playwright_browser
):
    """Q3 / Q4 / E2: a 28 px chevron with an 18 px glyph; a click hides
    every row and form slot under the group — rebuilding nothing (the rows'
    revision stands), the server's answer undoing nothing — and the row
    says what it hides; a second click shows them. Collapse all hides all
    but the root's children; Expand all shows every row."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        chev = _tree_row(page, G2).locator(".dstf-chev")
        box = chev.bounding_box()
        assert box is not None and (box["width"], box["height"]) == (28, 28), box
        glyph = chev.locator(".q-icon").evaluate("e => getComputedStyle(e).fontSize")
        assert glyph == "18px"
        assert chev.get_attribute("aria-expanded") == "true"
        assert chev.get_attribute("aria-label") == "Fold and · 2 criteria"
        assert _tree_row(page, "/metrics").locator(".dstf-chev").count() == 0
        rev = page.evaluate(REV)
        _fold_click(page, G2)
        shown = page.evaluate(SHOWN)
        under = [k for k in shown if k.split(" ", 1)[1].startswith(G2 + "/")]
        assert len(under) == 6  # 3 rows, 3 slots
        assert not any(shown[k] for k in under)
        assert all(v for k, v in shown.items() if k not in under)
        expect(_tree_row(page, G2).locator(".dstf-fold-summary")).to_have_text(
            "· 2 hidden · 1 pass · 1 fail", timeout=EXPECT_TIMEOUT
        )
        expect(chev).to_have_attribute("aria-expanded", "false")
        expect(chev).to_have_attribute("aria-label", "Unfold and · 2 criteria")
        page.wait_for_timeout(500)  # the server's answer: still folded
        assert not any(page.evaluate(SHOWN)[k] for k in under)
        assert page.evaluate(REV) == rev
        _fold_click(page, G2)
        page.wait_for_timeout(300)
        assert all(page.evaluate(SHOWN).values())
        expect(_tree_row(page, G2).locator(".dstf-fold-summary")).to_be_hidden()
        assert page.evaluate(REV) == rev
        line = page.locator(".dstf-tree-line")
        line.get_by_role("button", name="Collapse all groups", exact=True).click()
        expect(_tree_row(page, f"{G1}/children/0")).to_be_hidden(timeout=EXPECT_TIMEOUT)
        shown = page.evaluate(SHOWN)
        assert {k for k, v in shown.items() if v and k.startswith("row ")} == {
            "row /metrics",
            f"row {G1}",
            f"row {SIB}",
        }
        line.get_by_role("button", name="Expand all groups", exact=True).click()
        expect(_tree_row(page, f"{G3}/children/0")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        assert all(page.evaluate(SHOWN).values())
        assert page.evaluate(REV) == rev


def test_at_900_px_the_tree_line_is_one_line(tmp_path, playwright_browser):
    """H4 (the U5 review): Expand all / Collapse all are icon buttons named
    for a screen reader and a tooltip, so at 900 px the tree's line —
    toggle, verdict, tally, jumps, the two of them — stays one line."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        page.set_viewport_size({"width": 900, "height": 900})
        _show_tree(page, DEEP_ROWS)
        line = page.locator(".dstf-tree-line")
        for name in ("Expand all groups", "Collapse all groups"):
            expect(line.get_by_role("button", name=name, exact=True)).to_be_visible()
        tops = line.evaluate(
            """l => [...l.children].filter(c => c.offsetParent !== null)
              .map(c => [c.getBoundingClientRect().top, c.getBoundingClientRect().bottom])"""
        )
        assert len(tops) >= 5, tops
        # One line: every item's box spans the line's middle.
        mid = max(t for t, _ in tops) - 1
        assert all(t <= mid + 1 and b >= mid for t, b in tops), tops
        line.screenshot(path=str(tmp_path / "tree-line-900.png"))
        shots = os.environ.get("DSTF_LOOK_DIR")
        if shots:
            page.screenshot(path=os.path.join(shots, "tree-line-900.png"))


def _drag_rest(
    page, src: str, rest_on: str, rest_ms: int, then: str, frac: float, drop=True
):
    """A real-mouse drag from ``src``'s handle that rests ``rest_ms`` over
    ``rest_on``'s row, then goes to ``then``'s row at ``frac`` of its
    height — dropped there, or (``drop`` False) carried off the rows first.
    Returns ``rest_on``'s ``data-folded`` at the end of the rest."""
    grip = _tree_row(page, src).locator(".dstf-grip").bounding_box()
    assert grip is not None
    page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
    page.mouse.down()
    page.mouse.move(grip["x"] + 12, grip["y"] + 8, steps=4)
    box = _tree_row(page, rest_on).bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.8
    page.mouse.move(x, y, steps=8)
    # Chromium sends dragover every ~50 ms only while the mouse moves or
    # rests; rest by small moves inside the row.
    t = 0
    while t < rest_ms:
        page.wait_for_timeout(100)
        t += 100
        page.mouse.move(x + (t // 100) % 2, y)
    folded = _tree_row(page, rest_on).get_attribute("data-folded")
    box = _tree_row(page, then).bounding_box() if then else None
    if box is not None:
        page.mouse.move(
            box["x"] + box["width"] * 0.4, box["y"] + box["height"] * frac, steps=8
        )
        page.wait_for_timeout(300)
        page.mouse.move(
            box["x"] + box["width"] * 0.4 + 2, box["y"] + box["height"] * frac, steps=2
        )
        page.wait_for_timeout(100)
    if not drop:
        line = page.locator(".dstf-tree-line").bounding_box()
        page.mouse.move(line["x"] + 5, line["y"] + 5, steps=6)
        page.wait_for_timeout(100)
    page.mouse.up()
    return folded


def test_a_drag_resting_on_a_folded_group_unfolds_it_and_drops_inside(
    tmp_path, playwright_browser
):
    """Q10 / E6 with a real mouse: 300 ms on a folded group, then away, and
    it stays folded (the timer cancelled on leave); 900 ms unfolds it — in
    the page, the rows not rebuilt — and a drop before its now-shown first
    child lands there (the drag's revision still valid)."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        _fold_click(page, G2)
        page.wait_for_timeout(300)
        rev = page.evaluate(REV)
        # A short rest: still folded once the mouse has gone.
        _drag_rest(page, SIB, G2, 300, f"{G1}/children/0", BEFORE, drop=False)
        page.wait_for_timeout(900)
        assert _tree_row(page, G2).get_attribute("data-folded") == "1"
        assert page.evaluate(REV) == rev
        # A long one: unfolded during the drag, and the drop lands inside.
        folded = _drag_rest(page, SIB, G2, 900, f"{G2}/children/0", BEFORE)
        assert folded is None, folded
        expect(_tree_row(page, f"{G2}/children/0")).to_contain_text(
            "1 criterion", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")


def _hidden_rows(page) -> list[str]:
    return sorted(
        k[4:] for k, v in page.evaluate(SHOWN).items() if not v and k.startswith("row ")
    )


def test_a_fold_survives_an_edit_here_and_a_reload(tmp_path, playwright_browser):
    """R1 / Q8 in the browser: a folded group shifted by a remove before it
    is folded where it went — its rows hidden, its summary and chevron
    saying so — and a reload of the tab draws it folded again (the folds
    are the tab's)."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        _fold_click(page, G2)
        expect(_tree_row(page, G2)).to_have_attribute("data-folded", "1")
        page.wait_for_timeout(400)
        rev = page.evaluate(REV)
        # The tube (G1/children/0) removed: G2 moves up to G1/children/0.
        _tree_row(page, f"{G1}/children/0").get_by_role(
            "button", name="Remove this criterion"
        ).click()
        moved = f"{G1}/children/0"
        expect(_tree_row(page, moved)).to_have_attribute(
            "data-folded", "1", timeout=EXPECT_TIMEOUT
        )
        assert page.evaluate(REV) != rev
        hidden = _hidden_rows(page)
        assert hidden == sorted(
            [
                f"{moved}/children/0",
                f"{moved}/children/1",
                f"{moved}/children/1/children/0",
            ]
        )
        expect(_tree_row(page, moved).locator(".dstf-fold-summary")).to_have_text(
            "· 2 hidden · 1 pass · 1 fail", timeout=EXPECT_TIMEOUT
        )
        chev = _tree_row(page, moved).locator(".dstf-chev")
        expect(chev).to_have_attribute("aria-expanded", "false")
        page.reload()
        expect(page.locator(".js-plotly-plot").first).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        _show_tree(page, DEEP_ROWS - 1)
        expect(_tree_row(page, moved)).to_have_attribute(
            "data-folded", "1", timeout=EXPECT_TIMEOUT
        )
        assert _hidden_rows(page) == hidden
        expect(_tree_row(page, moved).locator(".dstf-fold-summary")).to_be_visible()
        chev = _tree_row(page, moved).locator(".dstf-chev")
        expect(chev).to_have_attribute("aria-expanded", "false")
        expect(chev).to_have_attribute("aria-label", "Unfold and · 2 criteria")


def test_the_suite_and_back_keeps_the_folds(tmp_path, playwright_browser):
    """Q8: the Suite page and back in the tab: the group is folded still."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        _fold_click(page, G1)
        page.wait_for_timeout(400)
        base = page.url.split("test/")[0]
        page.goto(base)
        expect(page.locator("#dstf-suite-tools")).to_be_visible(timeout=EXPECT_TIMEOUT)
        page.goto(f"{base}test/{A}")
        expect(page.locator(".js-plotly-plot").first).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        _show_tree(page, DEEP_ROWS)
        expect(_tree_row(page, G1)).to_have_attribute(
            "data-folded", "1", timeout=EXPECT_TIMEOUT
        )
        assert set(_hidden_rows(page)) == {
            f"{G1}/children/0",
            G2,
            f"{G2}/children/0",
            G3,
            f"{G3}/children/0",
        }


def test_a_chevron_folds_from_the_keyboard_and_the_branch_marks_stand(
    tmp_path, playwright_browser
):
    """Q3: the chevron is a button — Enter folds, Space unfolds, and
    `aria-expanded` says which; a fold and unfold by mouse under a hovered
    row leaves the branch's bars as the hover drew them."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        chev = _tree_row(page, G2).locator(".dstf-chev")
        chev.focus()
        page.keyboard.press("Enter")
        expect(chev).to_have_attribute("aria-expanded", "false", timeout=EXPECT_TIMEOUT)
        assert f"{G2}/children/0" in _hidden_rows(page)
        page.keyboard.press(" ")
        expect(chev).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert _hidden_rows(page) == []
        tokens = page.evaluate(TOKENS)
        _tree_row(page, G2).locator(".dstf-pill").first.hover()
        page.wait_for_timeout(150)
        chev.click()
        page.wait_for_timeout(400)
        chev.click()
        page.wait_for_timeout(400)
        bad = [
            (row["path"], row["bars"])
            for row in page.evaluate(BARS)
            if row["bars"] != _expected_bars(tokens, row["path"], G2)
        ]
        assert not bad, bad


def _twin_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_page_project` with A's tree two same-shaped groups:
    and[and[range · x], and[range · x']]."""
    cfg, work = _page_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            _, range_x, _ = entry["metrics"]["children"]
            other = dict(range_x)
            other["min_value"] = (other.get("min_value") or 0) - 1
            entry["metrics"] = {
                "combinator": "and",
                "children": [
                    {"combinator": "and", "children": [range_x]},
                    {"combinator": "and", "children": [other]},
                ],
            }
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return cfg, work


def test_a_swap_of_twin_groups_moves_the_fold_with_the_group(
    tmp_path, playwright_browser
):
    """R1 (the U5 review, H1) with a real mouse: group 0 folded and dragged
    onto the root goes last, where its twin was — the rows are painted, not
    rebuilt, and the fold is drawn on the row it went to."""
    a, b = "/metrics/children/0", "/metrics/children/1"
    with _served(tmp_path, playwright_browser, project=_twin_project) as page:
        _show_tree(page, 5)
        _fold_click(page, a)
        page.wait_for_timeout(400)
        _mouse_drag(page, a, "/metrics", ONTO)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        expect(_tree_row(page, b)).to_have_attribute(
            "data-folded", "1", timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(400)
        assert _tree_row(page, a).get_attribute("data-folded") is None
        assert _hidden_rows(page) == [f"{b}/children/0"]


def test_a_rebuild_under_a_resting_pointer_marks_the_branch_again(
    tmp_path, playwright_browser
):
    """Y2: the rows rebuilt (a remove from the keyboard) under a pointer
    resting on a group's pill: the next move marks that group's branch on
    the NEW rows — the hover's key carries the rows' revision."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        tokens = page.evaluate(TOKENS)
        pill = _tree_row(page, G2).locator(".dstf-pill").first
        pill.hover()
        page.wait_for_timeout(150)
        box = pill.bounding_box()
        assert box is not None
        rev = page.evaluate(REV)
        _tree_row(page, SIB).get_by_role(
            "button", name="Remove this node and everything under it"
        ).focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(rev) => document.querySelector('.dstf-tree [data-rev]')"
            ".dataset.rev !== rev",
            arg=rev,
        )
        page.wait_for_timeout(300)
        page.mouse.move(box["x"] + box["width"] / 2 + 1, box["y"] + box["height"] / 2)
        page.wait_for_timeout(150)
        bad = [
            (row["path"], row["bars"])
            for row in page.evaluate(BARS)
            if row["bars"] != _expected_bars(tokens, row["path"], G2)
        ]
        assert not bad, bad


def test_a_fold_hides_rows_and_slots_before_the_server_answers(
    tmp_path, playwright_browser
):
    """E2: the page folds at the click, in the same task — every row AND
    form slot under the group hidden before any answer could arrive."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        shown = page.evaluate(
            """(g) => {
              document.querySelector(`[data-tree-path="${g}"] .dstf-chev`).click();
              return [...document.querySelectorAll(
                `.dstf-tree [data-tree-path^="${g}/"], .dstf-tree [data-slot-of^="${g}/"]`)]
                .map(el => getComputedStyle(el).display !== 'none');
            }""",
            G2,
        )
        assert len(shown) == 6 and not any(shown), shown


def test_a_folded_groups_chevron_turns(tmp_path, playwright_browser):
    """Q3: the chevron's glyph turns a quarter on a folded group."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        icon = _tree_row(page, G2).locator(".dstf-chev .q-icon")
        assert icon.evaluate("e => getComputedStyle(e).transform") == "none"
        _fold_click(page, G2)
        page.wait_for_timeout(300)
        assert icon.evaluate("e => getComputedStyle(e).transform") != "none"


def test_a_drag_leaving_the_rows_cancels_a_folded_groups_rest(
    tmp_path, playwright_browser
):
    """Q10: a drag resting on a folded group, then carried straight off the
    rows (a dragleave with no row's dragenter after it), and held there past
    the rest: the group stays folded — the timer went with the leave."""
    with _served(tmp_path, playwright_browser, project=_deep_project) as page:
        _show_tree(page, DEEP_ROWS)
        _fold_click(page, G2)
        page.wait_for_timeout(300)
        grip = _tree_row(page, SIB).locator(".dstf-grip").bounding_box()
        assert grip is not None
        page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
        page.mouse.down()
        page.mouse.move(grip["x"] + 12, grip["y"] + 8, steps=4)
        box = _tree_row(page, G2).bounding_box()
        assert box is not None
        x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.8
        page.mouse.move(x, y, steps=8)
        page.wait_for_timeout(300)
        line = page.locator(".dstf-tree-line").bounding_box()
        assert line is not None
        page.mouse.move(line["x"] + 5, line["y"] + 5, steps=3)
        for i in range(9):  # 900 ms off the rows, still dragging
            page.wait_for_timeout(100)
            page.mouse.move(line["x"] + 5 + i % 2, line["y"] + 5)
        folded = _tree_row(page, G2).get_attribute("data-folded")
        page.mouse.up()
        assert folded == "1"


# -- a long row (U5) ----------------------------------------------------------

#: The page's width against its viewport's, and each tree row's label and
#: readout: its lines (by height) and whether its text is cut.
ROW_FIT = """() => ({
  page: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
  rows: [...document.querySelectorAll('.dstf-tree-row')].map(r => {
    const t = r.querySelector('.text-body2'), o = r.querySelector('.dstf-mono');
    return {path: r.dataset.treePath, right: r.getBoundingClientRect().right,
            card: r.closest('.dstf-tree').getBoundingClientRect().right,
            label: t.getBoundingClientRect().height,
            name: t.getBoundingClientRect().width,
            line: parseFloat(getComputedStyle(t).lineHeight),
            spill: t.scrollWidth - t.clientWidth,
            readout: o && {cut: o.scrollWidth > o.clientWidth, title: o.title,
                           width: o.clientWidth,
                           height: o.getBoundingClientRect().height,
                           line: parseFloat(getComputedStyle(o).lineHeight),
                           text: o.textContent,
                           ellipsis: getComputedStyle(o).textOverflow}};
  }),
})"""


@pytest.mark.parametrize("width", [1280, 900])
def test_a_long_tree_row_wraps_its_label_and_cuts_its_readout(
    tmp_path, playwright_browser, width
):
    """Criteria reading a 100-character expression, four levels down — x's
    with no space to break at, as a TRANSFORM name: a label wraps and its readout is cut with an ellipsis, the whole of
    it in its title — no row is wider than the card, and the page never
    scrolls sideways."""
    served = _served(tmp_path, playwright_browser, project=_deep_project, hooks=True)
    with served as page:
        base = page.url.split("test/")[0]
        # x's with no space to break at, as a TRANSFORM name; y's with.
        for name, text in (("x", LONG_X.replace(" ", "")), ("y", LONG_X)):
            query = urllib.parse.urlencode({"name": name, "expression": text})
            with urllib.request.urlopen(f"{base}__expression?{query}") as r:
                assert json.loads(r.read()) == {"stamped": 1}
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        _show_tree(page, DEEP_ROWS)
        page.mouse.move(2, 2)
        _shot(page, tmp_path, f"tree-long-{width}")
        fit = page.evaluate(ROW_FIT)
        scroll, client = fit["page"]
        assert scroll <= client, fit["page"]
        for row in fit["rows"]:
            assert row["right"] <= row["card"] + 0.5, row
        for path in (f"{G2}/children/0", f"{G3}/children/0"):  # range · x, · y
            deep = next(r for r in fit["rows"] if r["path"] == path)
            assert deep["label"] > 1.5 * deep["line"], deep  # wrapped
            assert deep["spill"] <= 0, deep  # inside its own box
            readout = deep["readout"]
            # The name keeps room to read in (K3) and the readout is cut,
            # never squeezed away by a name that cannot wrap.
            assert deep["name"] >= 240, deep
            assert readout["width"] >= 80, readout
            assert readout["height"] <= 1.5 * readout["line"], readout  # one line
            assert readout["ellipsis"] == "ellipsis", readout
            assert readout["title"] == readout["text"] != "", readout


@pytest.mark.parametrize("width", [900, 1280])
def test_a_200_character_name_leaves_every_readout_its_room(
    tmp_path, playwright_browser, width
):
    """J2: x reads a 200-character expression with no space to break at.
    Every criterion row's readout keeps 80 px and its name 240 px (K3) —
    the name wraps in that, the readout is cut — no row is wider than the
    card, and the page never scrolls sideways."""
    served = _served(tmp_path, playwright_browser, project=_deep_project, hooks=True)
    with served as page:
        base = page.url.split("test/")[0]
        long200 = (LONG_X.replace(" ", "") * 3)[:200]
        assert len(long200) == 200
        for name, text in (("x", long200), ("y", (LONG_X + " ") * 2)):
            query = urllib.parse.urlencode({"name": name, "expression": text})
            with urllib.request.urlopen(f"{base}__expression?{query}") as r:
                assert json.loads(r.read()) == {"stamped": 1}
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        expect(page.locator(".dstf-plot").first).to_be_visible(timeout=EXPECT_TIMEOUT)
        _show_tree(page, DEEP_ROWS)
        page.mouse.move(2, 2)
        _shot(page, tmp_path, f"tree-200-{width}")
        fit = page.evaluate(ROW_FIT)
        scroll, client = fit["page"]
        assert scroll <= client, fit["page"]
        readouts = [row for row in fit["rows"] if row["readout"]]
        assert len(readouts) == 4  # tube · x, range · x, range · y twice
        for row in fit["rows"]:
            assert row["right"] <= row["card"] + 0.5, row
        for row in readouts:
            assert row["name"] >= 240, row
            assert row["readout"]["width"] >= 80, row
        # The deepest x criterion's editor under its row: its right edge is
        # the row's (B12), however long the name it shows.
        deep = f"{G2}/children/0"
        _row_text_click(page, deep)
        editor = page.locator(".dstf-tree .dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        box, row = editor.bounding_box(), _tree_row(page, deep).bounding_box()
        assert box and row
        assert abs(box["x"] + box["width"] - (row["x"] + row["width"])) <= 1, (row, box)
        assert page.evaluate("document.documentElement.scrollWidth") <= width


# -- a click on a tree row (U5 Task 3) ----------------------------------------

K_OF_N, K_TUBE, K_RANGE_X, K_RANGE_Y = (
    "/metrics/children/0",
    "/metrics/children/0/children/0",
    "/metrics/children/0/children/1",
    "/metrics/children/1",
)


def _row_text_click(page, path: str) -> None:
    """A real click on the row's own text: not a control inside it."""
    _tree_row(page, path).locator(".dstf-tree-text").first.click()


@pytest.mark.parametrize("width", [1280, 900])
def test_a_tree_rows_click_opens_its_editor_and_its_controls_keep_theirs(
    tmp_path, playwright_browser, width
):
    """Q5 / Q6 / Q11 / Q12 with a real mouse: the kind dropdown, the grip
    and an icon keep their clicks (Y5, F4); a row with a form under it
    closes the form; a criterion's row opens its editor under it, inside
    the card (B12), with "Edit on its plot" for a tube, which moves it; a
    bound typed in the tree moves both readouts; a k-of-n's row opens its
    k; the root `and`'s opens nothing."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        page.set_viewport_size({"width": width, "height": 900})
        _show_tree(page, 5)
        tree_editor = page.locator(".dstf-tree .dstf-editor")
        plot_editor = page.locator(".dstf-plot .dstf-editor")
        forms = page.locator(".dstf-form-row")

        _tree_row(page, K_OF_N).get_by_label("Combinator kind").click()
        expect(page.get_by_role("option")).to_have_count(6, timeout=EXPECT_TIMEOUT)
        page.keyboard.press("Escape")
        expect(page.get_by_role("option")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert forms.count() == 0 and tree_editor.count() == 0
        _tree_row(page, K_TUBE).locator(".dstf-grip").click()
        page.wait_for_timeout(300)
        assert tree_editor.count() == 0
        _tree_row(page, K_TUBE).get_by_role("button", name=re.compile("Wrap")).click()
        expect(forms).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert tree_editor.count() == 0
        # The tube's row: the form goes, nothing opens (the open row rule).
        _row_text_click(page, K_TUBE)
        expect(forms).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert tree_editor.count() == 0

        _row_text_click(page, K_TUBE)
        expect(tree_editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(tree_editor).to_contain_text("Edit on its plot")
        card = page.locator(".dstf-tree").bounding_box()
        box = tree_editor.bounding_box()
        row = _tree_row(page, K_TUBE).bounding_box()
        assert card and box and row
        assert box["y"] >= row["y"] + row["height"] - 1, (row, box)
        assert box["x"] + box["width"] <= card["x"] + card["width"] + 0.5, (card, box)
        # Its right edge is its row's (B12: no wider, and no narrower).
        assert abs(box["x"] + box["width"] - (row["x"] + row["width"])) <= 1, (row, box)
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        tree_editor.locator(".dstf-why").first.click()  # the editor's own
        page.wait_for_timeout(300)
        assert tree_editor.count() == 1
        page.mouse.move(5, 5)
        _shot(page, tmp_path, f"u5-tree-editor-{width}")
        tree_editor.get_by_role("button", name="Edit on its plot").click()
        expect(plot_editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(tree_editor).to_have_count(0)
        expect(page.locator(".dstf-plot .dstf-crit-open")).to_be_in_viewport()
        page.mouse.move(5, 5)
        _shot(page, tmp_path, f"u5-edit-on-its-plot-{width}")

        page.evaluate("() => window.scrollTo(0, 0)")
        _row_text_click(page, K_RANGE_Y)
        expect(tree_editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(plot_editor).to_have_count(0)
        tree_ro = _tree_row(page, K_RANGE_Y).locator(".dstf-mono")
        plot_ro = _crit(page, 1, "range · y").locator(".dstf-mono")
        before = tree_ro.inner_text()
        upper = tree_editor.get_by_label("Upper bound (optional)")
        upper.fill("1000000")
        upper.press("Tab")
        expect(tree_ro).not_to_have_text(before, timeout=EXPECT_TIMEOUT)
        expect(plot_ro).to_have_text(tree_ro.inner_text(), timeout=EXPECT_TIMEOUT)
        expect(tree_editor).to_have_count(1)
        expect(page.locator(".dstf-header-row")).to_contain_text("1 pending edit")

        _row_text_click(page, K_OF_N)
        expect(forms).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(forms).to_contain_text("Edit this combinator")
        expect(forms.get_by_label("k — how many must pass")).to_have_value("1")
        expect(tree_editor).to_have_count(0)
        page.mouse.move(5, 5)
        _shot(page, tmp_path, f"u5-k-of-n-fields-{width}")
        _row_text_click(page, "/metrics")
        page.wait_for_timeout(300)
        assert forms.count() == 1  # still the k-of-n's
        _row_text_click(page, K_OF_N)  # a second click closes its fields
        expect(forms).to_have_count(0, timeout=EXPECT_TIMEOUT)


#: The focused element's tree path and whether it shows a focus ring.
FOCUSED = """() => { const a = document.activeElement;
  return {path: a.dataset.treePath || null, visible: a.matches(':focus-visible'),
          outline: getComputedStyle(a).outlineStyle}; }"""


def test_a_tree_rows_click_answers_the_keyboard(tmp_path, playwright_browser):
    """F5 / F8: a row whose click opens something is a tab stop with a
    visible focus ring, a pointer to the mouse; Enter or Space on it is its
    click — on a key pressed in a control inside it, the control's alone —
    and it says whether its editor or its fields are open."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        _show_tree(page, 5)
        tube, k_of_n, root = (
            _tree_row(page, K_TUBE),
            _tree_row(page, K_OF_N),
            _tree_row(page, "/metrics"),
        )
        cursor = "e => getComputedStyle(e).cursor"
        assert tube.evaluate(cursor) == "pointer"
        assert k_of_n.evaluate(cursor) == "pointer"
        assert root.evaluate(cursor) != "pointer"
        assert root.get_attribute("role") is None
        assert root.get_attribute("tabindex") is None
        # Into the tube's row from the keyboard: the ring shows.
        tube.focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        focused = page.evaluate(FOCUSED)
        assert focused == {"path": K_TUBE, "visible": True, "outline": "solid"}
        page.mouse.move(5, 5)
        _shot(page, tmp_path, "u5-focus-ring")
        expect(tube).to_have_attribute("aria-expanded", "false")
        page.keyboard.press("Enter")
        editor = page.locator(".dstf-tree .dstf-editor")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(tube).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Enter")
        expect(editor).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(tube).to_have_attribute("aria-expanded", "false")
        # Space on the tube's row, the page at its top: its editor, and the
        # page does not scroll (Space's default would).
        page.evaluate("() => window.scrollTo(0, 0)")
        tube.focus()
        top = page.evaluate("window.scrollY")
        page.keyboard.press(" ")
        expect(editor).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(300)
        assert page.evaluate("window.scrollY") == top
        page.keyboard.press(" ")
        expect(editor).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # Space on the k-of-n's row: its fields.
        k_of_n.focus()
        page.keyboard.press(" ")
        forms = page.locator(".dstf-form-row")
        expect(forms.get_by_label("k — how many must pass")).to_have_value(
            "1", timeout=EXPECT_TIMEOUT
        )
        expect(k_of_n).to_have_attribute("aria-expanded", "true")
        page.keyboard.press(" ")
        expect(forms).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # Enter on the k-of-n's chevron: it folds, and nothing of the row's.
        k_of_n.locator(".dstf-chev").focus()
        page.keyboard.press("Enter")
        expect(k_of_n).to_have_attribute("data-folded", "1", timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(400)
        assert forms.count() == 0
        expect(k_of_n).to_have_attribute("aria-expanded", "false")
        page.keyboard.press("Enter")
        expect(_tree_row(page, K_TUBE)).to_be_visible(timeout=EXPECT_TIMEOUT)


# -- the ⤓ jump (U5 Task 4) ---------------------------------------------------

#: The flash's own class, on the row the ⤓ landed on.
FLASHED = re.compile(r"dstf-flash")

#: A row's centre against the viewport's — the part of it below the page's
#: `scroll-padding-top` (the sticky header's; 124 px at 1280), which
#: `scrollIntoView` centres in — and whether the page's scroll is at either
#: end (a row near the top or foot cannot be brought to the middle).
CENTRED = """e => { const b = e.getBoundingClientRect(), d = document.documentElement;
  const pad = parseFloat(getComputedStyle(d).scrollPaddingTop) || 0;
  return {off: (b.top + b.bottom) / 2 - (pad + innerHeight) / 2, pad,
          clamped: scrollY <= 0 || scrollY >= d.scrollHeight - innerHeight - 1}; }"""


def _centred(locator) -> None:
    """``locator``'s row sits at the viewport's middle, ±20 px, unless the
    scroll is clamped (K5: the ⤓ scrolls to the centre, not the nearest
    edge)."""
    at = locator.evaluate(CENTRED)
    assert at["clamped"] or abs(at["off"]) <= 20, at


@pytest.mark.parametrize("width", [1280, 900])
def test_the_jump_icon_scrolls_to_the_plot_row_and_flashes_it(
    tmp_path, playwright_browser, width
):
    """Q7 / F4 with a real mouse: the ⤓ sits in the criterion row's
    cluster; its click is its own — no editor opens — and brings the
    criterion's plot row to the middle of the viewport, flashed; with
    Focus hiding that plot, the Focus ends."""
    with _served(tmp_path, playwright_browser, project=_nested_project) as page:
        page.set_viewport_size({"width": width, "height": 900})
        _show_tree(page, 5)
        icon = _tree_row(page, K_RANGE_Y).get_by_role("button", name="Show on its plot")
        expect(icon).to_have_count(1)
        assert _tree_row(page, K_OF_N).locator(".dstf-show").count() == 0
        box, row = icon.bounding_box(), _tree_row(page, K_RANGE_Y).bounding_box()
        assert box and row
        assert (
            row["y"] <= box["y"]
            and box["y"] + box["height"] <= row["y"] + row["height"] + 0.5
        ), (row, box)
        page.mouse.move(5, 5)
        _shot(page, tmp_path, f"u5-jump-icons-{width}")
        icon.click()
        target = _crit(page, 1, "range · y")
        expect(target).to_be_in_viewport(timeout=EXPECT_TIMEOUT)
        expect(target).to_have_class(FLASHED)
        _centred(target)
        page.mouse.move(5, 5)
        _shot(page, tmp_path, f"u5-jump-arrived-{width}")
        assert page.locator(".dstf-editor").count() == 0
        assert page.locator(".dstf-form-row").count() == 0
        # Focus on x's plot hides y's: the ⤓ ends it.
        page.locator(".dstf-plot").nth(0).get_by_role("button", name="Focus").click()
        expect(page.locator(".dstf-plot").nth(1)).to_be_hidden(timeout=EXPECT_TIMEOUT)
        page.evaluate("() => window.scrollTo(0, 0)")
        icon.click()
        expect(target).to_be_in_viewport(timeout=EXPECT_TIMEOUT)
        _centred(target)
        assert page.locator(".dstf-editor").count() == 0


def test_a_statistics_criterions_jump_icon_opens_its_card_row(
    tmp_path, playwright_browser
):
    """Q7: a statistics criterion's ⤓ opens the closed Statistics card and
    brings its row to the middle of the viewport, flashed; nothing opens."""
    with _served(tmp_path, playwright_browser, project=_stats_project) as page:
        card = page.locator(".dstf-stats")
        _plots_mounted(page)
        card.get_by_role("button", name="Statistics").click()
        rows = card.locator(".dstf-stat-row:not(.dstf-stat-head)")
        row = rows.filter(has_text=re.compile(r"^Nonlinear count"))
        row.get_by_role("button", name="Add a requirement: exact match").click()
        expect(card.locator(".dstf-editor")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        card.locator(".dstf-editor").get_by_role("button", name="Done").click()
        card.get_by_role("button", name="Statistics").click()  # closed again
        expect(row).to_be_hidden(timeout=EXPECT_TIMEOUT)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".dstf-tree-line").get_by_role("button", name="Show tree").click()
        icons = page.get_by_role("button", name="Show on the Statistics card")
        expect(icons.first).to_be_visible(timeout=EXPECT_TIMEOUT)
        icons.last.click()  # the criterion just added: the root's last child
        expect(row).to_be_in_viewport(timeout=EXPECT_TIMEOUT)
        expect(row).to_have_class(FLASHED)
        _centred(row)
        assert page.locator(".dstf-editor").count() == 0
