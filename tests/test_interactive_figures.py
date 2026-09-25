"""The page paints figure dicts and nothing else.

Hand-built contexts (``tests/report_fixtures.py``), so a figure can be constructed on its own and
asserted trace for trace. The end-to-end path — a page out of
``generate_comparison_plots`` — is ``tests/test_interactive_report.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from dstf.reporting.report_suite import plot_page_keys  # noqa: E402
from report_fixtures import (  # noqa: E402
    _context_with_figure,
    _render_with_context,
)


def test_first_paint_draws_exactly_the_figure_dict(tmp_path, playwright_browser):
    ctx, fig = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    painted = page.evaluate(
        "() => { const el = document.getElementById('plot-0');"
        " return { n: el.data.length, python: !!el._mt_python,"
        " ids: el.data.map(t => (t.meta || {}).id || null) }; }"
    )
    page.close()
    assert painted["python"] is True
    assert painted["n"] == len(fig["data"])
    assert "/metrics/children/0/tube/band" in painted["ids"]


def test_error_overlay_toggles_the_embedded_error_trace(tmp_path, playwright_browser):
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    visible = page.evaluate("""async () => {
        setErrorOverlay(0, 'abs');
        await new Promise(r => setTimeout(r, 100));   // Plotly.restyle settles on the next tick
        const el = document.getElementById('plot-0');
        return el.data.filter(t => (t.meta || {}).kind === 'error').map(t => [t.meta.id, t.visible]);
    }""")
    page.close()
    assert dict(visible) == {
        "error/signed": "legendonly",
        "error/abs": True,
        "error/nrmse": "legendonly",
    }


def test_overlay_picker_toggles_a_python_painted_overlay(tmp_path, playwright_browser):
    """The picker finds an overlay trace by name; the Python figure must
    name it the way ``overlayTraceName`` does, or ticking a soft check
    toggles nothing."""
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx, _ = _context_with_figure()
    var = next(iter(ctx["variables_by_name"]))
    traj = ctx["variables_by_name"][var]["trajectory"]
    overlay = {
        "name": "alt",
        "role": "soft_check",
        "kind": None,
        "time": list(traj["ref_time"]),
        "values": [v * 1.01 for v in traj["ref_values"]],
    }
    ctx["variables_by_name"][var]["overlays"] = [overlay]
    # The overlay joined the context AFTER the sections were built, so the
    # picker's rows only exist once they are rebuilt — before the figure is
    # replaced, which the rebuild would otherwise overwrite.
    ctx.update(plot_page_keys(ctx, None, {}, None, None))
    ctx["plot_figures"][0] = variable_figure(
        build_view(
            var,
            act_time=traj["act_time"],
            act_values=traj["act_values"],
            ref_time=traj["ref_time"],
            ref_values=traj["ref_values"],
            overlays=[overlay],
        )
    )
    ctx["nobaseline_trajectories"] = [
        {
            "name": "fresh",
            "time": list(traj["act_time"]),
            "values": list(traj["act_values"]),
            "overlays": [overlay],
        }
    ]
    ctx["nb_figures"] = [
        variable_figure(
            build_view(
                "fresh",
                act_time=traj["act_time"],
                act_values=traj["act_values"],
                overlays=[overlay],
            )
        )
    ]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    assert page.evaluate("!!document.getElementById('plot-0')._mt_python")
    assert page.evaluate("!!document.getElementById('nb-plot-0')._mt_python")
    seen = {}
    for plot_id in ("plot-0", "nb-plot-0"):
        js = (
            f"() => document.getElementById('{plot_id}')._fullData"
            ".filter(t => (t.meta || {}).kind === 'overlay').map(t => t.visible)"
        )
        before = page.evaluate(js)
        page.locator(f'[data-plot-id="{plot_id}"] input.overlay-toggle').first.check()
        page.wait_for_timeout(100)
        seen[plot_id] = (before, page.evaluate(js))
    page.close()
    assert seen == {
        "plot-0": (["legendonly"], [True]),
        "nb-plot-0": (["legendonly"], [True]),
    }


def test_error_axis_is_hidden_until_an_error_series_is_shown(
    tmp_path, playwright_browser
):
    """A laid-out axis with only legend-only traces gets a placeholder range
    and draws its zero line as a stray black rule across the plot."""
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    probe = (
        "() => { const el = document.getElementById('plot-0');"
        " const y3 = el._fullLayout.yaxis3;"
        " return { visible: y3 ? y3.visible : null,"
        " y4: el._fullLayout.yaxis4.visible,"
        " zerolines: Array.from(el.querySelectorAll('.zl')).map(n => n.getAttribute('class')) }; }"
    )
    before = page.evaluate(probe)
    page.evaluate("setErrorOverlay(0, 'abs')")
    page.wait_for_timeout(100)
    after = page.evaluate(probe)
    page.evaluate("setErrorOverlay(0, 'none')")
    page.wait_for_timeout(100)
    again = page.evaluate(probe)
    page.close()
    assert before["visible"] is False
    assert not any("y3zl" in c for c in before["zerolines"])
    assert after["visible"] is True and after["y4"] is False
    assert again["visible"] is False and again["y4"] is False


#: The pixel of one point of the page's actual trace, from the axis objects
#: — a hover is a real mouse move, and nothing on the page says where a
#: data point landed but Plotly's own layout.
POINT_PIXEL = """
(idx) => {
  const gd = document.getElementById('plot-0');
  const fl = gd._fullLayout, r = gd.getBoundingClientRect();
  const tr = gd.data.find(t => (t.meta || {}).kind === 'act');
  return [r.left + fl.xaxis._offset + fl.xaxis.l2p(tr.x[idx]),
          r.top + fl.yaxis._offset + fl.yaxis.l2p(tr.y[idx])];
}
"""

#: Every line of text in the hover box, whatever shape it takes: the
#: figure's `hovermode` is `x unified`, and a unified box is a
#: legend-shaped group (`.hoverlayer .legend`), not the `.hovertext` of
#: closest-mode. Reading every `text` under the layer says what a person
#: sees under either.
HOVER_TEXTS = (
    "() => Array.from(document.querySelectorAll('.hoverlayer text'))"
    ".map(e => e.textContent)"
)


def test_a_labelled_hover_reads_its_label_under_a_real_mouse(
    tmp_path, playwright_browser
):
    """The hover box a person sees, read off a real mouse move.

    The figure dict looked right in every unit test while the page printed
    the literal `%{text}: 1.234`: plotly.js substitutes `%{text}` only from
    a PER-POINT array, and the label is one string for the whole trace. It
    rides in `meta` now, and `%{meta.label}` substitutes from that.

    Only a browser can say so — `Plotly.Fx.hover` shows the same box, but
    it is the mouse that a person uses, and this test is the one place the
    rendered text is asserted on the REPORT's own vendored plotly.js.

    The value is read too, the whole line: the actual is 0.600025 at the
    hovered point, which the owner's `.4~g` reads `0.6` and a `.4g` left in
    `hover_of`'s template read `0.6000` — a format only the dict shape
    pinned until a mouse read it.
    """
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx, _ = _context_with_figure(label="pipe.T[1]")
    var = next(iter(ctx["variables_by_name"]))
    t = np.linspace(0.0, 1.0, 21)
    ctx["plot_figures"] = [
        variable_figure(
            build_view(
                var,
                label="pipe.T[1]",
                act_time=t,
                act_values=t + 2.5e-5,
                ref_time=t,
                ref_values=t,
            )
        )
    ]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(html_path.as_uri())
    page.wait_for_selector("#plot-0 .scatterlayer")
    # The plot sits below the page's summary: a mouse move to a pixel
    # outside the viewport reaches nothing.
    page.evaluate("() => document.getElementById('plot-0').scrollIntoView()")
    page.wait_for_timeout(200)
    x, y = page.evaluate(POINT_PIXEL, 12)
    page.mouse.move(x, y)
    page.wait_for_timeout(300)
    texts = page.evaluate(HOVER_TEXTS)
    page.close()
    assert texts, "no hover box under the mouse"
    assert "pipe.T[1]: 0.6" in texts, texts
    assert not any("%{" in line for line in texts), texts


#: Which error-axis labels — tick text and axis titles — sit under the
#: vertical toolbar (`.modebar`) of plot ``gd``, by their boxes. Shared with
#: the tool's browser test; ``labels`` says the read found any at all.
#: ``inside`` says the toolbar's box lies inside the plot's CARD — the
#: tool's `.dstf-plot`, the report's `.variable-section` / `.plot-section`:
#: a gutter that moved the toolbar clear of the axes but past the card's
#: edge (the plot at full width plus the gutter's margin) passed the
#: intersect read alone. It is read against the card's CONTENT box: its
#: border box left the toolbar 17 px of slack in the card's padding.
#: ``card`` is the class the card was found by, so each tier asserts the
#: toolbar was measured against ITS card and not some wider ancestor.
#: ``crossed`` names every y3 label or title that intersects a y4 one: the
#: report can show both axes at once.
UNDER_TOOLBAR = """
(gd) => {
  const hit = (a, b) => a.left < b.right && b.left < a.right
                     && a.top < b.bottom && b.top < a.bottom;
  const kinds = ['dstf-plot', 'variable-section', 'plot-section'];
  const mb = gd.querySelector('.modebar');
  const card = gd.closest(kinds.map(k => '.' + k).join(', '));
  if (!mb || !card) return null;
  const m = mb.getBoundingClientRect(), r = card.getBoundingClientRect();
  const cs = getComputedStyle(card), px = (k) => parseFloat(cs[k]) || 0;
  const c = {
    left: r.left + px('borderLeftWidth') + px('paddingLeft'),
    right: r.right - px('borderRightWidth') - px('paddingRight'),
    top: r.top + px('borderTopWidth') + px('paddingTop'),
    bottom: r.bottom - px('borderBottomWidth') - px('paddingBottom'),
  };
  const read = (sel) => Array.from(gd.querySelectorAll(sel))
    .map(e => [e.textContent, e.getBoundingClientRect()])
    .filter(([, b]) => b.width > 0);
  const y3 = read('g[class*="y3tick"] text, .g-y3title text');
  const y4 = read('g[class*="y4tick"] text, .g-y4title text');
  const boxes = [...y3, ...y4];
  return {
    labels: boxes.length,
    under: boxes.filter(([, b]) => hit(b, m)).map(([t]) => t),
    inside: m.left >= c.left && m.right <= c.right
         && m.top >= c.top && m.bottom <= c.bottom,
    card: kinds.find(k => card.classList.contains(k)),
    crossed: y3.filter(([, a]) => y4.some(([, b]) => hit(a, b))).map(([t]) => t),
  };
}
"""


def _page_with_errors(tmp_path, scale: float):
    """The report's page over one signal whose error ramps from 0 to
    ``0.25 * scale``: the tool fixture's x (1.25 t against t) at 1, and at
    1e-4 an error around 2.5e-5 — the size a passing test's error usually
    is, whose labels were the widest (`0.00002000` under `.4g`)."""
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx, _ = _context_with_figure()
    var = next(iter(ctx["variables_by_name"]))
    t = np.linspace(0.0, 1.0, 21)
    ctx["plot_figures"] = [
        variable_figure(
            build_view(
                var,
                act_time=t,
                act_values=t + 0.25 * scale * t,
                ref_time=t,
                ref_values=t,
            )
        )
    ]
    return _render_with_context(tmp_path, ctx)


@pytest.mark.parametrize("width", [1280, 900])
@pytest.mark.parametrize("scale", [1.0, 1e-4])
@pytest.mark.parametrize(
    "shown",
    [["error/signed"], ["error/abs"], ["error/nrmse"], ["error/signed", "error/nrmse"]],
)
def test_no_error_axis_label_sits_under_the_toolbar(
    tmp_path, playwright_browser, shown, scale, width
):
    """Hovered, so the toolbar shows: no tick label and no title of a shown
    error axis intersects it — signed and NRMSE together included (the
    report's legend can show both, the second axis standing past the first),
    and errors around 1e-5 too. The toolbar stands in its own gutter right
    of the plot (the page's stylesheet), so the axes never compete with it
    for the figure's right margin, however wide their labels grow; and the
    gutter is inside the plot's section, so the toolbar stays inside it
    (a plot at full width beside the gutter's margin hung the toolbar
    15 px past its section)."""
    html_path = _page_with_errors(tmp_path, scale)
    page = playwright_browser.new_page(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(html_path.as_uri())
    page.wait_for_selector("#plot-0 .scatterlayer")
    # A legend click and the select are both restyles; axes.js follows.
    page.evaluate(
        """(shown) => { const el = document.getElementById('plot-0');
           const idx = [], vis = [];
           el.data.forEach((t, i) => { if ((t.meta || {}).kind !== 'error') return;
             idx.push(i); vis.push(shown.includes(t.meta.id) ? true : 'legendonly'); });
           Plotly.restyle(el, {visible: vis}, idx);
           el.scrollIntoView(); }""",
        shown,
    )
    page.wait_for_timeout(300)
    plot = page.locator("#plot-0")
    box = plot.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.5)
    page.wait_for_timeout(300)
    read = plot.evaluate(UNDER_TOOLBAR)
    # The gutter costs no horizontal scroll: the page is as wide as the view.
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    shot = os.environ.get("DSTF_KEEP_SHOTS")
    if shot and shown == ["error/signed", "error/nrmse"]:
        Path(shot).mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(Path(shot) / f"report-signed-nrmse-{scale:g}-{width}.png")
        )
    page.close()
    assert errors == []
    assert read is not None and read["labels"] > 0, read
    assert read["under"] == [], read
    assert read["inside"] is True, read
    assert read["card"] == "variable-section", read
    # Two axes shown: NRMSE's stands past signed's (free + autoshift), so
    # none of their labels meet. Anchored to x it drew over the first.
    assert read["crossed"] == [], read
    assert overflow <= 0, overflow


def test_a_hover_trims_its_digits_under_a_real_mouse(tmp_path, playwright_browser):
    """The owner's ruling: a hover reads 4 significant digits with the
    trailing zeros trimmed (d3 `.4~g`). At t = 0.5 the page's abs error is
    1.25e-5, which `.4g` printed `0.00001250`; a real mouse reads the box
    the report's own vendored plotly.js draws from the trace's template.
    The actual there, 0.5000125, has no template of its own (its label is
    its name): it reads through the y axis' `hoverformat`, `0.5` where a
    `.4g` there read `0.5000`."""
    html_path = _page_with_errors(tmp_path, 1e-4)
    page = playwright_browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(html_path.as_uri())
    page.wait_for_selector("#plot-0 .scatterlayer")
    page.evaluate(
        """() => { const el = document.getElementById('plot-0');
           const idx = [], vis = [];
           el.data.forEach((t, i) => { if ((t.meta || {}).kind !== 'error') return;
             idx.push(i); vis.push(t.meta.id === 'error/abs' ? true : 'legendonly'); });
           Plotly.restyle(el, {visible: vis}, idx);
           el.scrollIntoView(); }"""
    )
    page.wait_for_timeout(300)
    x, y = page.evaluate(POINT_PIXEL, 10)
    page.mouse.move(x - 40, y - 20)
    page.wait_for_timeout(100)
    page.mouse.move(x, y, steps=4)
    page.wait_for_timeout(300)
    texts = page.evaluate(HOVER_TEXTS)
    page.close()
    assert "Abs error: 0.0000125" in texts, texts
    assert "Actual : 0.5" in texts, texts


#: The page's error axes and the Abs error trace's y extent, from the
#: figure Plotly holds.
READ_ERROR_AXIS = """
() => {
  const el = document.getElementById('plot-0');
  const fl = el._fullLayout;
  const tr = el.data.find(t => (t.meta || {}).id === 'error/abs');
  return {
    y3: fl.yaxis3.range.slice(), y4: fl.yaxis4.range.slice(),
    visible: fl.yaxis3.visible, shown: tr.visible,
    abs: [Math.min(...tr.y), Math.max(...tr.y)],
  };
}
"""


@pytest.mark.parametrize("by", ["select", "legend"])
def test_an_error_axis_zoomed_while_hidden_scales_fresh_when_shown(
    tmp_path, playwright_browser, by
):
    """A real-mouse box zoom made before any error series shows scales the
    hidden error axes too: they overlay `y`. Then Abs error is shown — by
    the page's select or by a legend click — and its axis covers the
    series, which fills most of it."""
    html_path = _page_with_errors(tmp_path, 1.0)
    page = playwright_browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(html_path.as_uri())
    page.wait_for_selector("#plot-0 .scatterlayer")
    page.evaluate("() => document.getElementById('plot-0').scrollIntoView()")
    before = page.evaluate(READ_ERROR_AXIS)
    left, top, width, height = page.evaluate(
        "() => { const el = document.getElementById('plot-0');"
        " const fl = el._fullLayout, r = el.getBoundingClientRect();"
        " return [r.left + fl._size.l, r.top + fl._size.t, fl._size.w, fl._size.h]; }"
    )
    page.mouse.move(left + width * 0.2, top + height * 0.15)
    page.mouse.down()
    page.mouse.move(left + width * 0.7, top + height * 0.75, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)
    zoomed = page.evaluate(READ_ERROR_AXIS)
    if by == "select":
        page.locator(".error-overlay-select").first.select_option("abs")
    else:
        entry = page.locator("#plot-0 .legend .traces").filter(has_text="Abs error")
        entry.locator(".legendtoggle").click(force=True)
    # A legend click lands after plotly's double-click window (300 ms).
    page.wait_for_function(
        "() => document.getElementById('plot-0')._fullLayout.yaxis3.visible === true",
        timeout=5000,
    )
    page.wait_for_timeout(200)
    shown = page.evaluate(READ_ERROR_AXIS)
    page.close()
    assert errors == []
    # The box zoom reached the hidden axis: the defect's precondition.
    assert zoomed["y3"] != before["y3"], (before, zoomed)
    assert shown["shown"] is True, shown
    (lo, hi), (a, b) = shown["y3"], shown["abs"]
    assert lo <= a and b <= hi, (zoomed, shown)
    assert (b - a) > 0.5 * (hi - lo), (zoomed, shown)
