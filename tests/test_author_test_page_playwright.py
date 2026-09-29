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
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
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

        # A k-of-n keeps its tune icon: its form holds k and nothing else.
        k_of_n.get_by_role("button", name="Change this combinator's fields").click()
        form = page.locator(".dstf-form-row")
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
        # A FAIL, C SIM_FAIL, B NO_REF, then the passes in package order.
        assert page.evaluate(DRAWER_NAMES) == ["A", "C", "B", "D", "G"]

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
