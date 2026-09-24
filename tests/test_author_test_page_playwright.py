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
def _served(tmp_path: Path, playwright_browser):
    """`dstf author` over the fixture, with `/test/FakeLib.A` open. On the
    way out: assertion 7 — no page error, no console error, and the spec
    file byte-for-byte as it was (nothing is saved by looking)."""
    cfg, work = _page_project(tmp_path)
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

#: The first ring whose pixel centre really belongs to a shape: Plotly's
#: legend sits top-left INSIDE the plot and can cover one, and a grab that
#: lands on the legend drags nothing.
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
        #    form in a menu — which is not a click on the row that hosts it.
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
        apply_wrap = page.get_by_role("button", name="apply wrap")
        expect(apply_wrap).to_be_visible(timeout=EXPECT_TIMEOUT)
        # Retrying reads: an editor the click opened arrives on the NEXT
        # round trip, and a bare count() the moment the menu appeared would
        # have said zero either way.
        expect(page.locator(".dstf-editor")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-crit-open")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        # 8. The top of the page, the tree open, the wrap form showing.
        _shot(page, tmp_path, "tree")
        page.keyboard.press("Escape")
        expect(apply_wrap).to_have_count(0, timeout=EXPECT_TIMEOUT)
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
        expect(page.get_by_role("button", name="apply wrap")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        assert page.locator(".dstf-editor").count() == 0
        assert page.locator(".dstf-crit-open").count() == 0
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="apply wrap")).to_have_count(
            0, timeout=EXPECT_TIMEOUT
        )

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
