"""On-plot editing in Chromium with a real mouse (D149), served by `dstf
author`: a Ctrl+click adds a row that settles on the data; a field armed by
its ⌖ takes the next plain click; the fields follow a handle while it is
held and nothing is staged before its release; and every value the pointer
places is snapped to a step of what its axis shows. NiceGUI's user
simulation runs no Plotly and no `grips.js`: what a click, a double-click
and a Ctrl+drag ARE is only said here."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

import numpy as np  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from on_plot_project import PEAKS, times, wave, wave_project  # noqa: E402
from test_author_suite_playwright import EXPECT_TIMEOUT  # noqa: E402
from test_author_test_page_playwright import (  # noqa: E402
    _drag_arrow,
    _open_for_grips,
    _pending,
    _served,
    _shape,
    _shot,
    _staged_tree,
    _table_cell,
)

#: A data position of plot ``card``'s panel ``sub`` (`xy`: the time plot;
#: `x2y2`: the spectrum) in viewport pixels, with what its axes show.
AT = """
([card, sub, x, y]) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[card];
  const p = gd._fullLayout._plots[sub], r = gd.getBoundingClientRect();
  const xa = p.xaxis, ya = p.yaxis;
  const xspan = Math.abs(xa.range[1] - xa.range[0]);
  const yspan = Math.abs(ya.range[1] - ya.range[0]);
  return {
    x: r.left + xa._offset + xa.d2p(x), y: r.top + ya._offset + ya.d2p(y),
    xspan, yspan, ppx: xa._length / xspan, ppy: ya._length / yspan,
    xrange: xa.range.slice(), yrange: ya.range.slice(),
  };
}
"""

RELAYOUT = """
async ([card, update]) => {
  const { Plotly } = await import('nicegui-plotly');
  await Plotly.relayout(document.querySelectorAll('.js-plotly-plot')[card], update);
}
"""

#: Keep what the page sends of the plot, as it sends it: every held-handle
#: move, and every click.
TAP = """
() => {
  window.__moves = [];
  window.__clicks = [];
  const real = window.emitEvent;
  window.emitEvent = (name, arg) => {
    if (name === 'dstf_grip_move') window.__moves.push({at: performance.now(), arg});
    if (name === 'dstf_plot_click') window.__clicks.push(arg);
    return real(name, arg);
  };
}
"""

ARMED_HINT = "Click the plot to set {} — Esc cancels."


def _wave(tmp_path: Path) -> tuple[Path, Path]:
    return wave_project(tmp_path)


def _at(page, x: float, y: float, sub: str = "xy", card: int = 0) -> dict:
    return page.evaluate(AT, [card, sub, x, y])


def _step(span: float) -> float:
    """The rule under test, said again: the power of ten nearest a
    thousandth of what the axis shows."""
    return 10.0 ** round(math.log10(span / 1000))


def _click_at(page, at: dict, *keys: str) -> None:
    """A person's click: the pointer arrives, the keys go down, one press."""
    page.mouse.move(at["x"] - 12, at["y"] - 12)
    page.mouse.move(at["x"], at["y"], steps=3)
    for key in keys:
        page.keyboard.down(key)
    page.mouse.down()
    page.mouse.up()
    for key in keys:
        page.keyboard.up(key)


def _plot_on_top(page, card: int = 0) -> None:
    """Scroll plot ``card`` to just under the sticky header, so the plot
    and the editor's first table rows are on one screen."""
    page.locator(".js-plotly-plot").nth(card).evaluate(
        "e => window.scrollBy(0, e.getBoundingClientRect().top - 150)"
    )
    page.wait_for_timeout(300)


def _on_grid(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) < 1e-6


def _rows(page) -> int:
    return page.locator(".dstf-editor .dstf-table-row").count()


def _arm(page, field):
    """Press the ⌖ of a number field of the open editor; returns it."""
    button = field.locator("xpath=ancestor::label[1]").locator(".dstf-arm")
    button.scroll_into_view_if_needed()
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true", timeout=EXPECT_TIMEOUT)
    return button


def _hint(page):
    return page.locator(".dstf-editor .dstf-why").first


def _nothing_staged(page, rows: int) -> None:
    """Wait out a round trip, then: no edit, and the table as it was."""
    page.wait_for_timeout(500)
    assert _pending(page) == "0 pending edits"
    assert _rows(page) == rows


def _note(tmp_path: Path, name: str, value: object) -> None:
    """A measurement for the build's report, kept where its shots are."""
    keep = os.environ.get("DSTF_KEEP_SHOTS")
    for folder in filter(None, [str(tmp_path), keep]):
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / f"measure-{name}.json").write_text(json.dumps(value))


def test_a_ctrl_click_adds_a_checkpoint_on_the_reference_at_the_click(
    tmp_path, playwright_browser
):
    """Ctrl+click on the time plot: one more row, at the clicked time —
    snapped — and with no value of its own, so its ring stands ON the
    baseline there, within 2 px of the click along x. One pending edit."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        expect(_hint(page)).to_contain_text("Ctrl+click adds a point.")
        assert _rows(page) == 2
        # Well off both checkpoints, and off the curve: the y is not read.
        at = _at(page, 0.78, 0.4)
        _click_at(page, at, "Control")
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-editor .dstf-table-row")).to_have_count(3)
        row = _staged_tree(page)["children"][0]["points"][2]
        assert set(row) == {"time"}, row
        # A mouse event lands on a whole pixel: within one of the aim, and
        # then on the step's grid.
        step = _step(at["xspan"])
        assert abs(row["time"] - 0.78) <= 1 / at["ppx"] + step / 2, (row, at)
        assert _on_grid(row["time"], step), (row, step)
        expect(_table_cell(page, 2, "time")).to_have_value(f"{row['time']:g}")
        expect(_table_cell(page, 2, "value")).to_have_value("")
        ring = _shape(page, "/points/target/2")
        again = _at(page, 0.78, 0.4)  # the axes as the redraw left them
        assert abs(ring["x"] - again["x"]) <= 2, (ring, again)
        # ON the baseline: its samples, joined by lines.
        grid = times()
        baseline = float(np.interp(row["time"], grid, [0.95 * wave(t) for t in grid]))
        assert abs(ring["yanchor"] - baseline) < 1e-9, (ring, baseline)
        _note(tmp_path, "placed-row", {"dx_px": ring["x"] - again["x"]})
        _shot(page, tmp_path, "onplot-row-added")


def test_a_ctrl_click_on_the_spectrum_adds_the_nearest_spectral_peak(
    tmp_path, playwright_browser
):
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "dominant-frequency · x")
        expect(_hint(page)).to_contain_text("Ctrl+click adds a peak.")
        # The spectrum is drawn about the declared 3 Hz: zoomed out, the
        # run's second peak is on it.
        page.evaluate(RELAYOUT, [0, {"xaxis2.range": [0, 12]}])
        page.wait_for_timeout(300)
        assert _rows(page) == 1
        at = _at(page, 7.2, 0.0, "x2y2")
        top = _at(page, 7.2, 0.0, "x2y2")["yrange"][1]
        _click_at(page, _at(page, 7.2, top / 2, "x2y2"), "Control")
        expect(page.locator(".dstf-editor .dstf-table-row")).to_have_count(
            2, timeout=EXPECT_TIMEOUT
        )
        freq = _staged_tree(page)["children"][2]["peaks"][1]["freq"]
        # The run's own peak — a bin of its spectrum — not the click's 7.2.
        assert abs(freq - PEAKS[1]) < 0.2 and freq != 7.2, freq
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        ring = _shape(page, "/peaks/declared/1")
        assert ring["xanchor"] == freq
        assert at["xspan"] == 12
        # On the TIME plot the same gesture is refused, in words.
        _click_at(page, _at(page, 0.5, 0.0), "Control")
        refusal = page.locator(".dstf-plot-refusal").first
        expect(refusal).to_contain_text(
            "a peak is added by a Ctrl+click on the spectrum", timeout=EXPECT_TIMEOUT
        )
        assert _rows(page) == 2


def test_clicks_that_are_not_an_edit_change_nothing(tmp_path, playwright_browser):
    """A plain click with nothing armed; a Ctrl+click on a metric without
    rows; a Ctrl+click on a ring and on an arrow (a press there is a
    drag's); a Ctrl+drag, which is Plotly's own; a double-click, which
    still resets the axes."""
    problems: list[str] = []
    with _served(
        tmp_path, playwright_browser, project=_wave, hooks=True, problems=problems
    ) as page:

        def nothing(step: str, rows: int, said: int) -> None:
            """Nothing staged — and ``said`` clicks told to the server:
            what is no click at all is not sent."""
            _nothing_staged(page, rows)
            assert problems == [], step
            clicks = page.evaluate("() => window.__clicks.splice(0)")
            assert len(clicks) == said, (step, clicks)

        page.wait_for_timeout(700)
        page.evaluate(TAP)
        _open_for_grips(page, 0, "range · x")
        expect(_hint(page)).not_to_contain_text("Ctrl+click")
        _click_at(page, _at(page, 0.5, 0.3), "Control")
        nothing("Ctrl+click on a range", 0, said=1)

        _open_for_grips(page, 0, "points · x")
        _click_at(page, _at(page, 0.78, 0.4))
        nothing("a plain click", 2, said=1)
        # On the ring of checkpoint 0, and on one of its arrows.
        ring = _shape(page, "/points/target/0")
        _click_at(page, ring, "Control")
        nothing("Ctrl+click on a ring", 2, said=0)
        _click_at(page, ring)
        nothing("a click on a ring", 2, said=0)
        page.mouse.move(ring["x"], ring["y"], steps=3)
        arrow = _shape(page, "/points/upper/0")
        assert arrow["box"] is not None, arrow
        _click_at(page, arrow["box"], "Control")
        nothing("Ctrl+click on an arrow", 2, said=0)
        # Off the plot area: on the axis' own labels, under the time plot.
        below = _at(page, 0.5, 0.0)
        low = _at(page, 0.5, below["yrange"][0])
        _click_at(page, {"x": low["x"], "y": low["y"] + 12}, "Control")
        nothing("Ctrl+click under the plot area", 2, said=0)
        # A Ctrl+drag: it ends in a real click, 80 px from its press.
        start = _at(page, 0.45, 0.6)
        page.mouse.move(start["x"], start["y"], steps=3)
        page.keyboard.down("Control")
        page.mouse.down()
        page.mouse.move(start["x"] + 80, start["y"] + 30, steps=8)
        page.mouse.up()
        page.keyboard.up("Control")
        _nothing_staged(page, 2)
        # plotly.js itself rejects a promise where a Ctrl+drag is let go
        # (measured on the tool as it was before this module heard clicks):
        # that one page error is its, and the only one.
        assert problems in ([], ["pageerror: undefined"]), problems
        problems.clear()
        assert page.evaluate("() => window.__clicks.splice(0)") == []
        # A double-click resets a zoomed axis, and adds nothing.
        page.evaluate(RELAYOUT, [0, {"xaxis.range": [0.4, 0.9]}])
        page.wait_for_timeout(300)
        assert _at(page, 0.5, 0.0)["xspan"] == pytest.approx(0.5)
        spot = _at(page, 0.8, 0.5)
        page.mouse.move(spot["x"], spot["y"], steps=3)
        page.mouse.dblclick(spot["x"], spot["y"])
        page.wait_for_function(
            "() => { const r = document.querySelectorAll('.js-plotly-plot')[0]"
            "._fullLayout.xaxis.range; return r[1] - r[0] > 0.9; }",
            timeout=EXPECT_TIMEOUT,
        )
        # Its first click is a click; its second is the double-click's.
        nothing("a double-click", 2, said=1)


#: What says a field is armed ON THE PLOT of card 0: the card's attribute,
#: the outline the stylesheet draws about the plot by it, the accent that
#: outline should be, and the line under the plot.
ARMED_CUE = """
() => {
  const card = document.querySelectorAll('.dstf-plot')[0];
  const slot = card.querySelector('.dstf-plot-slot');
  const probe = document.createElement('span');
  probe.style.color = 'var(--dstf-accent)';
  card.appendChild(probe);
  const accent = getComputedStyle(probe).color;
  probe.remove();
  const style = getComputedStyle(slot);
  const line = card.querySelector('.dstf-plot-armed');
  const shown = line && line.getBoundingClientRect().height > 0;
  return {
    armed: card.hasAttribute('data-armed'), accent,
    outline: style.outlineStyle === 'none' ? null : style.outlineColor,
    width: style.outlineWidth,
    line: shown ? line.innerText : null,
    colour: shown ? getComputedStyle(line).color : null,
  };
}
"""


def _row_button(page, row: int):
    """The ⌖ of table row ``row``: in its first cell."""
    return page.locator(".dstf-editor .dstf-table-row").nth(row).locator(".dstf-arm")


def _arm_row(page, row: int):
    button = _row_button(page, row)
    expect(button).to_have_count(1)
    button.scroll_into_view_if_needed()
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true", timeout=EXPECT_TIMEOUT)
    return button


def test_an_armed_row_says_so_on_its_plot_and_takes_the_snapped_click(
    tmp_path, playwright_browser
):
    """Press a row's ⌖: the editor's hint says what the next click sets —
    and so does the PLOT, where the click is made and the ⌖ may be a
    screen away: an accent outline about it and the same sentence under
    it. The click writes the SNAPPED place, stages one edit and disarms.
    Esc disarms without a click."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        # One ⌖ a row, and none in any other cell.
        assert page.locator(".dstf-editor .dstf-table-row .dstf-arm").count() == 2
        expect(_table_cell(page, 0, "value")).to_have_value("-1")
        rest = _hint(page).inner_text()
        cue = page.evaluate(ARMED_CUE)
        assert (cue["armed"], cue["outline"], cue["line"]) == (False, None, None), cue
        button = _arm_row(page, 0)
        said = ARMED_HINT.format("row 1")
        expect(_hint(page)).to_have_text(said)
        _plot_on_top(page)
        cue = page.evaluate(ARMED_CUE)
        assert cue["armed"] and cue["line"] == said, cue
        assert cue["outline"] == cue["accent"] == cue["colour"], cue
        assert cue["width"] == "2px", cue
        _shot(page, tmp_path, "onplot-armed")
        at = _at(page, 0.3333, -0.4567)
        # Esc: nothing armed, and the click that follows is nothing.
        page.keyboard.press("Escape")
        expect(button).to_have_attribute("aria-pressed", "false")
        expect(_hint(page)).to_have_text(rest)
        cue = page.evaluate(ARMED_CUE)
        assert (cue["armed"], cue["outline"], cue["line"]) == (False, None, None), cue
        _click_at(page, at)
        _nothing_staged(page, 2)
        # Armed again, and clicked: the row holds a value of its own, so
        # the click is its place — time and value.
        _arm_row(page, 0)
        _plot_on_top(page)
        at = _at(page, 0.3333, -0.4567)
        _click_at(page, at)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        xstep, ystep = _step(at["xspan"]), _step(at["yspan"])
        staged = _staged_tree(page)["children"][0]["points"][0]
        assert abs(staged["value"] + 0.4567) <= ystep / 2 + 1 / at["ppy"], (staged, at)
        assert abs(staged["time"] - 0.3333) <= xstep / 2 + 1 / at["ppx"], (staged, at)
        assert _on_grid(staged["value"], ystep) and _on_grid(staged["time"], xstep)
        expect(_table_cell(page, 0, "value")).to_have_value(f"{staged['value']:g}")
        expect(_hint(page)).to_have_text(rest)
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(0)
        assert page.evaluate(ARMED_CUE)["armed"] is False
        _note(tmp_path, "armed-row", {"steps": [xstep, ystep], "row": staged})


def test_an_armed_click_on_the_other_panel_is_refused_and_stays_armed(
    tmp_path, playwright_browser
):
    """A row of checkpoints is set on the time plot. Armed, a click on the
    spectrum under it wrote a magnitude as its value and 1.0 as its time:
    it is refused in words, nothing is staged, the row stays armed — and
    the click on the time plot that follows sets it. And a click on the
    legend or the toolbar is no click on the plot at all."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        page.evaluate(TAP)
        _arm_row(page, 0)
        _plot_on_top(page)
        top = _at(page, 2.0, 0.0, "x2y2")["yrange"][1]
        _click_at(page, _at(page, 2.0, top / 2, "x2y2"))
        refusal = page.locator(".dstf-plot-refusal").first
        expect(refusal).to_have_text(
            "row 1 is set on the time plot", timeout=EXPECT_TIMEOUT
        )
        assert _pending(page) == "0 pending edits"
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(1)
        assert page.evaluate(ARMED_CUE)["armed"] is True
        assert len(page.evaluate("() => window.__clicks.splice(0)")) == 1
        # The legend's entries and the toolbar's buttons are theirs.
        page.locator(".js-plotly-plot").first.locator(".legend .traces").first.click()
        page.mouse.move(*[_at(page, 0.9, 0.0)[k] for k in ("x", "y")])
        page.locator(".js-plotly-plot").first.locator(
            ".modebar-btn[data-title='Zoom in']"
        ).click()
        page.wait_for_timeout(400)
        assert page.evaluate("() => window.__clicks.splice(0)") == []
        assert _pending(page) == "0 pending edits"
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(1)
        _click_at(page, _at(page, 0.33, -0.5))
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        expect(refusal).to_be_hidden()
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(0)
        assert abs(_staged_tree(page)["children"][0]["points"][0]["time"] - 0.33) < 0.01


def test_an_armed_tolerance_side_puts_its_edge_on_the_click(
    tmp_path, playwright_browser
):
    """The criterion's default tolerance, which row 2 defers to: the click
    is where that row's box edge goes — within one snapped step — and a
    click across the target is the arrow's own refusal: said under the
    plot, the field still armed. A row's own tolerance has no ⌖: it has
    its arrows."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        upper = page.locator(".dstf-editor").get_by_label(
            "+ Default tolerance", exact=True
        )
        expect(upper).to_have_value("0.2")
        _arm(page, upper)
        expect(_hint(page)).to_have_text(ARMED_HINT.format("+ Default tolerance"))
        _plot_on_top(page)
        target = float(np.interp(0.6, times(), [0.95 * wave(t) for t in times()]))
        # Under row 2's target: the upper side would cross it.
        _click_at(page, _at(page, 0.62, target - 0.4))
        refusal = page.locator(".dstf-plot-refusal").first
        expect(refusal).to_contain_text(
            "the upper side would cross its target", timeout=EXPECT_TIMEOUT
        )
        assert _pending(page) == "0 pending edits"
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(1)
        at = _at(page, 0.62, target + 0.4321)
        _click_at(page, at)
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        expect(refusal).to_be_hidden()
        expect(page.locator('.dstf-arm[aria-pressed="true"]')).to_have_count(0)
        step = _step(at["yspan"])
        tolerance = _staged_tree(page)["children"][0]["tolerance"]
        assert abs(tolerance - 0.4321) <= step / 2 + 1 / at["ppy"], (tolerance, step)
        page.wait_for_function(
            """([tail, want]) => { const gd = document.querySelectorAll('.js-plotly-plot')[0];
              const s = gd.layout.shapes.find(x => (x.name || '').endsWith(tail));
              return s && Math.abs(s.y1 - want) < 1e-9; }""",
            arg=["/points/box/1", target + tolerance],
            timeout=EXPECT_TIMEOUT,
        )
        box = _shape(page, "/points/box/1")
        assert abs(box["y1"] - (target + 0.4321)) <= step + 1 / at["ppy"], (box, step)


def _hold(page, tail: str) -> dict:
    """Bring the pointer onto the arrow or ring ``tail`` and press."""
    at = _shape(page, tail)
    page.mouse.move(at["x"] - 40, at["y"] - 40)
    page.mouse.move(at["x"], at["y"], steps=4)
    shown = _shape(page, tail)
    spot = shown["box"] or shown
    page.mouse.move(spot["x"], spot["y"], steps=3)
    page.mouse.down()
    return {**shown, "px": spot["x"], "py": spot["y"]}


def _shows(page, field, want: float, within: float) -> tuple[str, float]:
    """Wait until ``field`` shows a number ``within`` of ``want``; returns
    what it shows and how long that took, ms."""
    started = time.monotonic()
    while True:
        text = field.input_value()
        try:
            near = abs(float(text) - want) <= within
        except ValueError:
            near = False
        waited = (time.monotonic() - started) * 1000
        if near:
            return text, waited
        assert waited < EXPECT_TIMEOUT, (text, want, within)
        page.wait_for_timeout(10)


def test_the_fields_follow_a_held_arrow_and_stage_on_release(
    tmp_path, playwright_browser
):
    """Five steps with the arrow held: the side's field shows a new value
    at each — the edge's distance from its target, snapped — and nothing
    is pending; the release stages what the last one showed."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        upper = _table_cell(page, 0, "+ tolerance")
        lower = _table_cell(page, 0, "− tolerance")
        expect(upper).to_have_value("0.3")
        _plot_on_top(page)
        held = _hold(page, "/points/upper/0")
        ystep = _step(_at(page, 0.5, 0.0)["yspan"])
        within = ystep / 2 + 1.5 / held["ppy"]
        seen, waits = ["0.3"], []
        for step in range(1, 6):
            page.mouse.move(held["px"], held["py"] - 12 * step, steps=2)
            # The row's value is -1.0: the side is the edge's distance from it.
            want = held["yanchor"] + 12 * step / held["ppy"] + 1.0
            shown, waited = _shows(page, upper, want, within)
            seen.append(shown)
            waits.append(round(waited))
            assert _on_grid(float(shown), ystep), (shown, ystep)
            # A number is both sides: its twin follows too.
            expect(lower).to_have_value(shown)
            assert _pending(page) == "0 pending edits"
            if step == 3:
                # The plot with the arrow held; then, the page scrolled
                # under the still pointer, the field that follows it.
                _shot(page, tmp_path, "onplot-mid-drag")
                page.evaluate("() => window.scrollBy(0, 330)")
                page.wait_for_timeout(200)
                _shot(page, tmp_path, "onplot-mid-drag-field")
                page.evaluate("() => window.scrollBy(0, -330)")
                page.wait_for_timeout(200)
        assert len(set(seen)) == 6, seen
        assert _staged_tree(page)["children"][0]["points"][0]["tolerance"] == 0.3
        page.mouse.up()
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        staged = _staged_tree(page)["children"][0]["points"][0]["tolerance"]
        assert f"{staged:g}" == seen[-1], (staged, seen)
        expect(_table_cell(page, 0, "+ tolerance")).to_have_value(seen[-1])
        _note(tmp_path, "preview-latency-ms", waits)


def test_a_field_typed_in_during_a_drag_is_not_painted_over(
    tmp_path, playwright_browser
):
    """The field follows the held ring; a key typed in it while the ring
    is still held is the person's, and no later move shows over it. (A
    tube's control point: its time is required, so the key that leaves no
    number there stages nothing and redraws nothing under the drag.)"""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "tube · x")
        field = _table_cell(page, 0, "time")
        expect(field).to_have_value("0.1")
        held = _hold(page, "/tube/upper/0")
        page.mouse.move(held["px"] + 30, held["py"], steps=3)
        within = 2 / held["ppx"]
        followed, _ = _shows(page, field, 0.1 + 30 / held["ppx"], within)
        # A cell the drag does not move shows what it holds.
        expect(_table_cell(page, 1, "time")).to_have_value("0.9")
        # The keyboard goes to the field with the ring still held, and a
        # key is typed: no number any more.
        field.focus()
        page.keyboard.type("e")
        page.wait_for_timeout(300)
        typed = field.input_value()
        assert typed != followed, typed
        for dx in (45, 60, 75):
            page.mouse.move(held["px"] + dx, held["py"], steps=2)
            page.wait_for_timeout(250)
            assert field.input_value() == typed
        assert _pending(page) == "0 pending edits"
        page.mouse.up()
        # The release stages the ring where it was let go, and the editor
        # is rebuilt on it.
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        staged = _staged_tree(page)["children"][3]["tube_points"][0]["time"]
        assert abs(staged - (0.1 + 75 / held["ppx"])) <= within, staged
        expect(_table_cell(page, 0, "time")).to_have_value(f"{staged:g}")


def _drag_line(page, tail: str, dy: float) -> None:
    line = page.evaluate(
        """([tail]) => { const gd = document.querySelectorAll('.js-plotly-plot')[0];
          const fl = gd._fullLayout, r = gd.getBoundingClientRect();
          const s = gd.layout.shapes.find(x => (x.name || '').endsWith(tail));
          return {x: r.left + fl.xaxis._offset + fl.xaxis._length * 0.4,
                  y: r.top + fl.yaxis._offset + fl.yaxis.d2p(s.y0)}; }""",
        [tail],
    )
    page.mouse.move(line["x"] - 20, line["y"] - 20)
    page.mouse.move(line["x"], line["y"], steps=4)
    page.mouse.down()
    page.mouse.move(line["x"], line["y"] + dy, steps=8)
    page.mouse.up()


def test_a_zoom_makes_the_snap_step_finer(tmp_path, playwright_browser):
    """The same drag — 37 px — at two zooms writes on two grids: the step
    is a thousandth of what the axis SHOWS, to its power of ten."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "range · x")
        bound = page.locator(".dstf-editor").get_by_label(
            "Upper bound (optional)", exact=True
        )
        expect(bound).to_have_value("2")
        written = {}
        for span in (6.0, 0.6):
            # About the bound where it now is: a line off the plot takes
            # no pointer.
            low = float(bound.input_value()) - span / 2
            page.evaluate(RELAYOUT, [0, {"yaxis.range": [low, low + span]}])
            page.wait_for_timeout(400)
            was = bound.input_value()
            _drag_line(page, "/range/max", 37)
            expect(bound).not_to_have_value(was, timeout=EXPECT_TIMEOUT)
            page.wait_for_timeout(300)
            written[span] = float(bound.input_value())
        coarse, fine = _step(6.0), _step(0.6)
        assert (coarse, fine) == (0.01, 0.001)

        def on_grid(value: float, step: float) -> bool:
            return abs(value / step - round(value / step)) < 1e-6

        assert on_grid(written[6.0], coarse), written
        assert on_grid(written[0.6], fine) and not on_grid(written[0.6], coarse), (
            written
        )
        assert _staged_tree(page)["children"][4]["max_value"] == written[0.6]
        _note(tmp_path, "snap-at-two-zooms", written)


def test_a_move_that_arrives_after_its_release_changes_nothing(
    tmp_path, playwright_browser
):
    """Under a server slowed as a large test's rescore slows it: the
    fields follow, the release stages — and every move of that drag sent
    again AFTER it, late, is dropped: the fields and the leaf stay what
    the release made them."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        page.evaluate(TAP)
        page.evaluate("() => fetch('/__slow?ms=250').then(r => r.json())")
        page.wait_for_timeout(200)
        upper = _table_cell(page, 0, "+ tolerance")
        expect(upper).to_have_value("0.3")
        held = _hold(page, "/points/upper/0")
        started = time.monotonic()
        for step in range(1, 31):
            page.mouse.move(held["px"], held["py"] - 2 * step)
            page.wait_for_timeout(20)
        lasted = time.monotonic() - started
        page.mouse.up()
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        staged = _staged_tree(page)["children"][0]["points"][0]["tolerance"]
        expect(_table_cell(page, 0, "+ tolerance")).to_have_value(f"{staged:g}")
        moves = page.evaluate("() => window.__moves")
        sent = [m for m in moves if not m["arg"].get("end")]
        assert len(sent) >= 5, moves
        # At most 15 a second, numbered upwards.
        assert len(sent) <= 15 * lasted + 2, (len(sent), lasted)
        numbers = [m["arg"]["seq"] for m in moves]
        assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
        # Every one of them again, now late: a preview of 0.4, 0.5, ...
        page.evaluate(
            "() => { for (const m of window.__moves.slice(0, 8)) "
            "window.emitEvent('dstf_grip_move', m.arg); }"
        )
        page.wait_for_timeout(800)
        expect(_table_cell(page, 0, "+ tolerance")).to_have_value(f"{staged:g}")
        expect(_table_cell(page, 0, "− tolerance")).to_have_value(f"{staged:g}")
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        assert _staged_tree(page)["children"][0]["points"][0]["tolerance"] == staged
        _note(tmp_path, "moves-per-second", {"sent": len(sent), "seconds": lasted})
        # And the arrow still drags.
        _drag_arrow(page, "/points/upper/0", 0, -20)
        expect(_table_cell(page, 0, "+ tolerance")).not_to_have_value(
            f"{staged:g}", timeout=EXPECT_TIMEOUT
        )


def test_what_a_held_handle_shows_is_what_its_release_writes(
    tmp_path, playwright_browser
):
    """Three ways the preview could say another number than the release.
    An axis the pointer went under 8 px along is not moved, in the preview
    as in the release. A handle held past the plot's edge is held to it.
    And where it is let go is said once more before the release: the last
    move the fields were shown is the release's own position."""
    with _served(tmp_path, playwright_browser, project=_wave, hooks=True) as page:
        page.wait_for_timeout(700)
        _open_for_grips(page, 0, "points · x")
        _plot_on_top(page)
        time_cell, value = _table_cell(page, 0, "time"), _table_cell(page, 0, "value")
        held = _hold(page, "/points/target/0")
        # 30 px along x, 5 px along y: the value does not follow.
        page.mouse.move(held["px"] + 30, held["py"] + 5, steps=3)
        _shows(page, time_cell, 0.25 + 30 / held["ppx"], 2 / held["ppx"])
        page.wait_for_timeout(200)
        assert value.input_value() == "-1"
        page.mouse.up()
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        assert _staged_tree(page)["children"][0]["points"][0]["value"] == -1.0

        # Held 250 px above the plot: the edge of what the axis shows.
        _plot_on_top(page)
        upper = _table_cell(page, 0, "+ tolerance")
        top = _at(page, 0.5, 0.0)["yrange"][1]
        held = _hold(page, "/points/upper/0")
        page.mouse.move(held["px"], 30, steps=6)
        shown, _ = _shows(page, upper, top + 1.0, _step(_at(page, 0.5, 0.0)["yspan"]))
        page.mouse.up()
        expect(page.locator(".dstf-header-row")).to_contain_text(
            "1 pending edit", timeout=EXPECT_TIMEOUT
        )
        page.wait_for_timeout(500)
        staged = _staged_tree(page)["children"][0]["points"][0]["tolerance"]
        assert f"{staged:g}" == shown, (staged, shown)

        # Let go straight after the last move: the page says where, once
        # more, before the release. Press, two moves and the release are
        # dispatched in ONE task of the page, so no frame comes between
        # them and no throttled move is sent: the one move the server
        # hears is the one said at the release — the release's own place.
        page.evaluate(RELAYOUT, [0, {"yaxis.range": [-2.5, 2.5]}])
        page.wait_for_timeout(400)
        page.evaluate(TAP)
        ring = _shape(page, "/points/target/0")
        page.mouse.move(ring["x"], ring["y"], steps=4)  # its arrows show
        arrow = _shape(page, "/points/upper/0")
        assert arrow["box"] is not None, arrow
        said = page.evaluate(
            """([tail, dy]) => {
              const gd = document.querySelectorAll('.js-plotly-plot')[0];
              const i = gd.layout.shapes.findIndex(s => (s.name || '').endsWith(tail));
              const el = gd.querySelector('.shapelayer path[data-index="' + i + '"]');
              const b = el.getBoundingClientRect();
              const x = (b.left + b.right) / 2, y = (b.top + b.bottom) / 2;
              const fire = (on, type, cy) => on.dispatchEvent(new MouseEvent(type, {
                bubbles: true, cancelable: true, view: window, button: 0,
                buttons: type === 'mouseup' ? 0 : 1, clientX: x, clientY: cy}));
              const anchor = gd.layout.shapes[i].yanchor;
              fire(el, 'mousedown', y);
              fire(document, 'mousemove', y + 12);
              fire(document, 'mousemove', y + dy);
              fire(document, 'mouseup', y + dy);
              return {anchor, moves: window.__moves.map(m => m.arg)};
            }""",
            ["/points/upper/0", 40],
        )
        moves = [m["update"] for m in said["moves"] if not m.get("end")]
        assert len(moves) == 1, said
        (anchor,) = [v for k, v in moves[0].items() if k.endswith(".yanchor")]
        want = said["anchor"] - 40 / arrow["ppy"]
        assert abs(anchor - want) * arrow["ppy"] < 1.5, (anchor, want)
        # ... and what it showed is what the release then stages.
        shown, _ = _shows(page, upper, anchor + 1.0, 0.01 / 2 + 1e-9)
        page.wait_for_timeout(600)
        staged = _staged_tree(page)["children"][0]["points"][0]["tolerance"]
        assert f"{staged:g}" == shown, (staged, shown)
