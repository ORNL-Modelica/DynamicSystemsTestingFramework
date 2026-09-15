"""The page paints figure dicts and nothing else.

Hand-built contexts (``tests/report_fixtures.py``), so a figure can be constructed on its own and
asserted trace for trace. The end-to-end path — a page out of
``generate_comparison_plots`` — is ``tests/test_interactive_report.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

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
    ctx["figures"][var] = variable_figure(
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
