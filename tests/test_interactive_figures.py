"""The page paints figure dicts and nothing else.

Hand-built contexts (``tests/report_fixtures.py``), so a figure can be constructed on its own and
asserted trace for trace. The end-to-end path — a page out of
``generate_comparison_plots`` — is ``tests/test_interactive_report.py``.
"""

from __future__ import annotations

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
    """
    ctx, _ = _context_with_figure(label="pipe.T[1]")
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(html_path.as_uri())
    page.wait_for_selector("#plot-0 .scatterlayer")
    # The plot sits below the page's summary: a mouse move to a pixel
    # outside the viewport reaches nothing.
    page.evaluate("() => document.getElementById('plot-0').scrollIntoView()")
    page.wait_for_timeout(200)
    x, y = page.evaluate(POINT_PIXEL, 20)
    page.mouse.move(x, y)
    page.wait_for_timeout(300)
    texts = page.evaluate(HOVER_TEXTS)
    page.close()
    assert texts, "no hover box under the mouse"
    assert any(line.startswith("pipe.T[1]: ") for line in texts), texts
    assert not any("%{" in line for line in texts), texts
