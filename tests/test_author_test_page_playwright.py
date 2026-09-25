"""The Test page in Chromium, served by `dstf author` (brief §3.2; roadmap
§3). NiceGUI's user simulation renders nothing and runs no Plotly, so this
is where the page's geometry and its gestures are said: the sticky test
header stacks under the shared one instead of beside it, a card's plot is
drawn when it is scrolled to and not before, a criterion's editor opens
BELOW its row with the plot above it as its editing surface, a ring dragged
there stages an edit while the zoom stays, a drag of another leaf's bound
line is refused by name and the picture snaps back, and Focus shows one
plot of seven.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes and by role and name.

The plots do NOT zoom on the wheel (`figures.plotly._config`: `scrollZoom`
is false for the tool's figure), which is what makes a wheel over a plot
scroll the PAGE — the gesture that draws the cards below."""

from __future__ import annotations

import json
import os
import re
import shutil
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
from test_session import A, _page_project  # noqa: E402

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
def _served(tmp_path: Path, playwright_browser, error_scale: float = 1.0):
    """`dstf author` over the fixture, with `/test/FakeLib.A` open. On the
    way out: assertion 7 — no page error, no console error, and the spec
    file byte-for-byte as it was (nothing is saved by looking).
    ``error_scale`` scales x's error against its baseline (`_scale_x_error`)."""
    cfg, work = _page_project(tmp_path)
    if error_scale != 1.0:
        _scale_x_error(work, error_scale)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
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


def _scale_x_error(work: Path, scale: float) -> None:
    """Rewrite x's result so its error against the baseline (x = t) is
    ``0.25 * scale * t`` instead of ``0.25 * t``: at 1e-4 an error around
    2.5e-5, the size a passing test's error usually is."""
    for key in ("test_0001", "test_0002"):
        path = work / key / "result.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["variables"]["x"] = [v + 0.25 * scale * v for v in data["time"]]
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
        actions = page.locator(".dstf-subheader .dstf-actions").bounding_box()
        assert actions is not None
        assert actions["x"] + actions["width"] <= VIEWPORT["width"], actions
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
        #     it be dragged: the open editor refuses it BY NAME, stages
        #     nothing, and the picture goes back.
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
        expect(editor).to_contain_text("is not a handle of", timeout=EXPECT_TIMEOUT)
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

        # 5d. Adding a criterion from a variable's footer changes the tree's
        #     SHAPE, which rebuilds every card. The slots are NEW elements,
        #     so the page's IntersectionObserver has to be told about them
        #     or nothing below the fold ever draws again — and the rebuild
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
        apply_add = page.get_by_role("button", name="apply add")
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
        expect(page.locator(".dstf-focus-bar")).to_contain_text(
            "Showing 1 of 7 plots: x"
        )
        page.get_by_role("button", name="Show all 7 plots").click()
        expect(page.locator(".dstf-plot:visible")).to_have_count(
            7, timeout=EXPECT_TIMEOUT
        )


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


def test_a_hover_over_a_card_reads_the_series_it_is_over(tmp_path, playwright_browser):
    """The hover box a person sees in the TOOL, under a real mouse.

    The tool re-hosts the figure the report draws, under NiceGUI's own
    plotly build, and every labelled hover on both tiers printed the
    literal `%{text}: 1.234`: plotly.js substitutes `%{text}` only from a
    per-point array. Nothing but a rendered page says so — the figure dict
    carried the label all along.

    A card drawing SEVERAL series is the one that always carries a hover:
    a single-series card names its traces "Actual" / "Reference" and adds
    one only when the label is not the name. Plot 2 of the fixture is
    `[x, y]`; Focus is how it is drawn without scrolling to it."""
    with _served(tmp_path, playwright_browser) as page:
        page.locator(".dstf-plot").nth(2).get_by_role("button", name="Focus").click()
        expect(page.locator(".dstf-plot:visible")).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-plot:visible .js-plotly-plot")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        plot = page.locator(".dstf-plot:visible .js-plotly-plot")
        pixel = None
        for _ in range(30):
            pixel = plot.evaluate(POINT_PIXEL, ["actual/x", 3])
            if pixel is not None:
                break
            page.wait_for_timeout(200)
        assert pixel is not None, "the focused card never drew actual/x"
        x, y = pixel
        page.mouse.move(x - 40, y - 20)
        page.wait_for_timeout(100)
        page.mouse.move(x, y, steps=4)
        page.wait_for_timeout(400)
        texts = page.evaluate(HOVER_TEXTS)
        _shot(page, tmp_path, "hover")
        assert texts, "no hover box under the mouse"
        assert any(line.startswith("x: ") for line in texts), texts
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
    with _served(tmp_path, playwright_browser, error_scale=scale) as page:
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

        # (b) Edit on the root: Unwrap, Cancel, Apply.
        page.evaluate("() => window.scrollTo(0, 0)")
        _icon(rows.nth(0), "tune").click()
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(form).to_contain_text("Edit this combinator")
        expect(form).to_contain_text(
            "Changes this node's kind and fields; its children stay."
        )
        buttons = form.locator("button").all_inner_texts()
        assert [b.strip() for b in buttons] == ["Unwrap", "Cancel", "Apply"], buttons
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


def test_a_form_row_scrolls_into_view_and_a_weighted_wrap_waits_for_a_threshold(
    tmp_path, playwright_browser
):
    """A row opened from a criteria row at the viewport's foot scrolls
    itself into view, just enough to show all of it (the owner's ruling;
    the review measured such a row at 882-1052 px in a 900 px viewport).
    A weighted Wrap is grey, saying why under a real mouse, until its
    threshold is typed."""
    with _served(tmp_path, playwright_browser) as page:
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
        form = page.locator(".dstf-form-row")
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        page.wait_for_timeout(600)
        form_box = form.bounding_box()
        assert form_box is not None
        assert form_box["y"] + form_box["height"] <= VIEWPORT["height"], form_box
        # Just enough: the row it opened under is still on screen above it.
        crit_box = crit.bounding_box()
        assert crit_box is not None and crit_box["y"] >= 0, crit_box
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
        # error sits on its own line under the row, and the row's icons
        # stay where every other row's are.
        icons = _icon(crit, "add").bounding_box()
        form.get_by_label("Weights, one per child").fill("1, 2")
        wrap.click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        error = page.locator(".dstf-plot").nth(1).locator(".text-negative")
        expect(error).to_contain_text("weights", timeout=EXPECT_TIMEOUT)
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
        # The row reopened: the error goes with it; typed right, it stages.
        _icon(crit, "data_array").click()
        expect(form).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(error).to_be_hidden(timeout=EXPECT_TIMEOUT)
        _choose(page, form, "weighted")
        form.get_by_label("Threshold").fill("0.5")
        wrap.click()
        expect(form).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
