"""The page's first paint comes from the embedded figure dict; an edit
falls back to the JavaScript path until sub-project 2 removes it."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from test_interactive_playwright import _fixture_context  # noqa: E402
from test_interactive_points import _render_with_context  # noqa: E402


def _context_with_figure():
    from dstf.comparison.algorithms import _compare_tube
    from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx = _fixture_context()
    var = next(iter(ctx["variables_by_name"]))
    traj = ctx["variables_by_name"][var]["trajectory"]
    t = np.asarray(traj["ref_time"], dtype=float)
    ref = np.asarray(traj["ref_values"], dtype=float)
    act_t = np.asarray(traj["act_time"], dtype=float)
    act = np.asarray(traj["act_values"], dtype=float)
    vc = _compare_tube(
        t, ref, act_t, act, {"tube_width_mode": "band", "tube_abs": 0.05}
    )
    vc.name = var
    stamp_leaf_path(leaf_from_variable(vc), "/metrics/children/0")
    fig = variable_figure(
        build_view(
            var,
            act_time=act_t,
            act_values=act,
            ref_time=t,
            ref_values=ref,
            leaves=[("/metrics/children/0", vc)],
        )
    )
    ctx["figures"] = {var: fig}
    ctx["diag_figures"] = []
    ctx["nb_figures"] = []
    return ctx, fig


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


def test_an_edit_after_the_python_paint_takes_the_js_path(tmp_path, playwright_browser):
    """The existing editor tests render contexts with no figures, so they
    never edit a Python-painted plot. This one does: the first paint is the
    dict, a tolerance edit re-renders through the JavaScript contributions,
    the live scorer still flips the pill, and nothing throws."""
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(html_path.as_uri())
    assert page.evaluate("!!document.getElementById('plot-0')._mt_python")

    sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    inp = page.locator(sel).first
    inp.fill("1e-12")
    inp.dispatch_event("input")
    state = page.evaluate(
        "() => { const el = document.getElementById('plot-0');"
        " return { python: !!el._mt_python, n: el.data.length }; }"
    )
    pill = page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header > .node-status'
    ).first
    pill_text = pill.text_content()
    # The error overlay must still work on the re-rendered (JS) plot.
    page.evaluate("setErrorOverlay(0, 'abs')")
    page.close()
    assert state["python"] is False and state["n"] > 0
    assert pill_text == "FAIL"
    assert errors == []
