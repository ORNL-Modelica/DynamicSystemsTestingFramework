"""templates/axes.js under the report's vendored plotly.js: a secondary axis
is visible exactly while a trace on it is visible, whether the trace was
shown by the page's select (Plotly.restyle) or by a legend click."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

sys.path.insert(0, str(Path(__file__).parent))
from dstf.figures.plotly import variable_figure  # noqa: E402
from dstf.reporting.report_suite import axes_js_source, copy_vendor_assets  # noqa: E402
from test_figures import _tube_view  # noqa: E402

PROBE = """() => {
  const el = document.getElementById('p');
  const fl = el._fullLayout;
  return {
    traces: Object.fromEntries(el.data.filter(t => (t.meta || {}).kind === 'error')
                                  .map(t => [t.meta.id, t.visible])),
    y3: fl.yaxis3.visible, y4: fl.yaxis4.visible,
  };
}"""


@pytest.fixture(scope="module")
def page_path(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("axes")
    copy_vendor_assets(root)
    fig = variable_figure(_tube_view())
    (root / "page.html").write_text(
        "<html><head><script src='plotly.min.js'></script>"
        f"<script>{axes_js_source()}</script></head><body>"
        "<div id='p' style='width:900px;height:350px'></div>"
        f"<script>const FIG={json.dumps(fig)};"
        "Plotly.newPlot('p', FIG.data, FIG.layout, FIG.config);"
        "dstfWireSecondaryAxes(document.getElementById('p'));</script>"
        "</body></html>",
        encoding="utf-8",
    )
    return root / "page.html"


def _open(browser, page_path):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(page_path.as_uri())
    page.wait_for_function("() => !!document.getElementById('p').data")
    return page, errors


def _toggle_nrmse_in_legend(page, want):
    """Click the NRMSE legend entry and wait until the click has landed.
    Plotly applies a single legend click only after its double-click window
    (``doubleClickDelay``, 300 ms in plotly.js 2.35.0: the click handler is a
    ``setTimeout``), so a fixed short wait reads the page before the click and
    lets the next click land inside the window as a double-click. The wait is
    on the TRACE flag; the axis gets the plan's 200 ms to follow, so the axis
    assertions still fail when axes.js does not."""
    entry = page.locator(".legend .traces").filter(has_text="NRMSE (per-point)")
    entry.locator(".legendtoggle").click(force=True)
    page.wait_for_function(
        """(want) => document.getElementById('p').data
             .some(t => (t.meta || {}).id === 'error/nrmse' && t.visible === want)""",
        arg=want,
        timeout=5000,
    )
    page.wait_for_timeout(200)


def test_both_axes_start_hidden(playwright_browser, page_path):
    page, errors = _open(playwright_browser, page_path)
    seen = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert seen["y3"] is False and seen["y4"] is False


def test_a_restyle_shows_exactly_the_axis_of_the_shown_series(
    playwright_browser, page_path
):
    page, errors = _open(playwright_browser, page_path)
    seen = {}
    for shown in ("error/nrmse", "error/abs", None):
        page.evaluate(
            """(shown) => { const el = document.getElementById('p');
               const idx = [], vis = [];
               el.data.forEach((t, i) => { if ((t.meta || {}).kind !== 'error') return;
                 idx.push(i); vis.push(t.meta.id === shown ? true : 'legendonly'); });
               Plotly.restyle(el, {visible: vis}, idx); }""",
            shown,
        )
        page.wait_for_timeout(150)
        seen[shown] = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert (seen["error/nrmse"]["y3"], seen["error/nrmse"]["y4"]) == (False, True)
    assert (seen["error/abs"]["y3"], seen["error/abs"]["y4"]) == (True, False)
    assert (seen[None]["y3"], seen[None]["y4"]) == (False, False)


def test_a_legend_click_drives_the_axis_too(playwright_browser, page_path):
    """The tool has no select: its only toggle is the legend, a client-side
    restyle the server never sees. Plotly's legend entries are SVG text
    over a `.legendtoggle` rect that takes the pointer."""
    page, errors = _open(playwright_browser, page_path)
    _toggle_nrmse_in_legend(page, True)
    after_show = page.evaluate(PROBE)
    _toggle_nrmse_in_legend(page, "legendonly")
    after_hide = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert after_show["traces"]["error/nrmse"] is True
    assert (after_show["y3"], after_show["y4"]) == (False, True)
    assert after_hide["traces"]["error/nrmse"] == "legendonly"
    assert (after_hide["y3"], after_hide["y4"]) == (False, False)


def test_the_axis_still_follows_after_a_re_plot_of_the_same_node(
    playwright_browser, page_path
):
    """Plotly.newPlot on a plotted node purges its listeners but keeps the
    node, so a wired-flag on the node would skip the re-wire; the emitter's
    listener count does not."""
    page, errors = _open(playwright_browser, page_path)
    page.evaluate(
        """() => { Plotly.newPlot('p', FIG.data, FIG.layout, FIG.config);
           dstfWireSecondaryAxes(document.getElementById('p')); }"""
    )
    page.wait_for_timeout(200)
    _toggle_nrmse_in_legend(page, True)
    after_show = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert after_show["traces"]["error/nrmse"] is True
    assert (after_show["y3"], after_show["y4"]) == (False, True)
