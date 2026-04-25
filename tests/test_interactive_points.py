"""Playwright tests for the points-mode JS layer.

Covers MODE_SCORERS['points'], MODE_PLOT_CONTRIBUTIONS['points'], and
MODE_PLOT_EDITORS['points'] (Tasks 5-8 of the points-mode plan).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page

from test_interactive_playwright import (
    _fixture_context, _leaf, _render_report, playwright_browser,
)


def _mode_controls_html_points(values: dict) -> str:
    """Minimal points-mode controls HTML — passthrough fallback for
    the points list, plus a numeric tolerance input."""
    tol = values.get("tolerance", 1e-4)
    return (
        '<div class="mode-controls" data-mode="points" data-variable="h">'
        '<label><span>Tolerance</span>'
        f'<input type="number" step="any" data-field="tolerance" value="{tol}"></label>'
        '<label class="mc-field mc-passthrough">'
        '<textarea data-field="points" data-passthrough="true" rows="2"></textarea>'
        '</label>'
        "</div>"
    )


def _context_with_points_leaf(points=None, tolerance=0.01):
    """Fixture: one points leaf on variable 'h' with a piecewise-constant
    trajectory — explicit values at t=0..5 so tests don't depend on
    grid alignment.
    """
    ctx = _fixture_context()
    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = "points"
    leaf["mode_effective"] = "points"
    leaf["variable"] = "h"
    params = {"points": points, "tolerance": tolerance}
    leaf["params"] = params
    leaf["mode_values"] = dict(params)
    leaf["mode_controls_html"] = _mode_controls_html_points(params)
    leaf["cli_authoritative"] = False
    # Hand-tuned trajectory: act tracks ref except a 0.05 spike at t=2.
    t = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    ref = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    act = [0.0, 1.001, 2.05, 3.001, 4.001, 5.001]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": list(t), "act_values": list(act),
        "ref_time": list(t), "ref_values": list(ref),
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"]
        for k in ctx["variables_by_name"]
    ]
    return ctx


def _render_with_context(tmp_path: Path, ctx: dict) -> Path:
    """Render a custom context to interactive.html."""
    from jinja2 import Environment, FileSystemLoader
    from test_interactive_playwright import _JS_SRC, _TEMPLATE_DIR
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path


# ---------------------------------------------------------------------------
# Task 5: live scorer
# ---------------------------------------------------------------------------

def test_points_scorer_implicit_final_passes(tmp_path, playwright_browser):
    """Empty points list → implicit final-value check. Fixture's
    act[-1]=5.001, ref[-1]=5.0, tolerance=0.01 → PASS."""
    ctx = _context_with_points_leaf(points=None, tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_declared_point_pass(tmp_path, playwright_browser):
    """Declared point at t=3 (delta 0.001) with tol 0.01 → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 3.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_declared_point_fail(tmp_path, playwright_browser):
    """Declared point at t=2 (delta 0.05) with tol 0.01 → FAIL."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is False


def test_points_scorer_baseline_free_with_explicit_value(
    tmp_path, playwright_browser,
):
    """Point with explicit value=2.0 at t=2: act(2)=2.05, delta=0.05.
    Tolerance 0.01 → FAIL. Confirms scorer reads explicit value over ref."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0, "value": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is False


def test_points_scorer_time_tolerance_box_pass(tmp_path, playwright_browser):
    """Strict t=2 fails (delta 0.05 > 0.01); box with x_tol=1.5 lets the
    scorer find a closer point near t=1 or t=3 (delta 0.001) → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0, "tolerance": 0.01, "time_tolerance": 1.5}],
        tolerance=0.05,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_window_clips_points(tmp_path, playwright_browser):
    """Window [3, 5] excludes the t=2 trouble point. Fixture's
    only-failing point is at t=2; with window applied → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}, {"time": 4.0}], tolerance=0.01,
    )
    leaf = ctx["tree_view"]["children"][0]
    leaf["window"] = {"start": 3.0, "end": 5.0}
    leaf["window_values"] = {"start": 3.0, "end": 5.0}
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


# ---------------------------------------------------------------------------
# Task 6: plot decoration (translucent box + diamond marker)
# ---------------------------------------------------------------------------

def test_points_plot_no_decoration_when_no_points(tmp_path, playwright_browser):
    """Empty points list → no plot contribution (the implicit final
    case is invisible on the plot — same as the rest of the reporter)."""
    ctx = _context_with_points_leaf(points=None, tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {traces: c.traces.length, shapes: c.shapes.length};
        }
    """)
    page.close()
    assert contrib["traces"] == 0
    assert contrib["shapes"] == 0


def test_points_plot_diamond_per_point_when_xtol_zero(
    tmp_path, playwright_browser,
):
    """Two declared points, no time_tolerance → 2 diamond markers as a
    single trace (one trace with 2 (x,y) entries) + 2 zero-width
    rectangle shapes (one per point)."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "value": 2.0, "tolerance": 0.5},
            {"time": 4.0, "value": 4.0, "tolerance": 0.5},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {
                traces: c.traces.length,
                trace0_xs: c.traces[0] && c.traces[0].x,
                trace0_ys: c.traces[0] && c.traces[0].y,
                trace0_symbol: c.traces[0] && c.traces[0].marker.symbol,
                shapes: c.shapes.length,
                shape0_x0: c.shapes[0] && c.shapes[0].x0,
                shape0_x1: c.shapes[0] && c.shapes[0].x1,
            };
        }
    """)
    page.close()
    assert contrib["traces"] == 1, "Single marker trace with 2 points"
    assert contrib["trace0_xs"] == [2.0, 4.0]
    assert contrib["trace0_ys"] == [2.0, 4.0]
    assert contrib["trace0_symbol"] == "diamond"
    assert contrib["shapes"] == 2, "One rectangle per point"
    # With time_tolerance=0, rectangle is zero-width.
    assert contrib["shape0_x0"] == contrib["shape0_x1"] == 2.0


def test_points_plot_box_has_width_when_xtol_set(tmp_path, playwright_browser):
    """time_tolerance=0.2 → rectangle spans [time-0.2, time+0.2]."""
    ctx = _context_with_points_leaf(
        points=[{"time": 3.0, "value": 30.0,
                 "tolerance": 0.5, "time_tolerance": 0.2}],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {x0: c.shapes[0].x0, x1: c.shapes[0].x1,
                    y0: c.shapes[0].y0, y1: c.shapes[0].y1};
        }
    """)
    page.close()
    assert contrib["x0"] == pytest.approx(2.8)
    assert contrib["x1"] == pytest.approx(3.2)
    assert contrib["y0"] == pytest.approx(29.5)
    assert contrib["y1"] == pytest.approx(30.5)


# ---------------------------------------------------------------------------
# Task 7: editor scaffold (table + add + delete)
# ---------------------------------------------------------------------------

def test_points_editor_mounts_on_leaf_click(tmp_path, playwright_browser):
    """Clicking the points leaf header mounts the editor in the
    .node-editor slot."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    mounted = page.locator(
        '[data-path="/metrics/children/0"] .points-editor'
    ).count()
    page.close()
    assert mounted >= 1


def test_points_editor_renders_existing_points(tmp_path, playwright_browser):
    """Two declared points → two table rows."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "tolerance": 0.01},
            {"time": 4.0, "value": 4.0},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    page.close()
    assert rows == 2


def test_points_editor_add_button_appends_row(tmp_path, playwright_browser):
    """+ add point appends to the table AND to leafState.params.points."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor button.node-btn-add'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    state_len = page.evaluate("""
        () => (leafState['/metrics/children/0'].params.points || []).length
    """)
    page.close()
    assert rows == 2
    assert state_len == 2


def test_points_editor_delete_removes_row(tmp_path, playwright_browser):
    """Per-row × button removes from DOM and leafState."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 1.0, "tolerance": 0.01},
            {"time": 3.0, "tolerance": 0.01},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'tbody tr button.row-delete'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    remaining_time = page.evaluate("""
        () => {
            const pts = leafState['/metrics/children/0'].params.points || [];
            return pts.length === 1 ? Number(pts[0].time) : null;
        }
    """)
    page.close()
    assert rows == 1
    assert remaining_time == 3.0


# ---------------------------------------------------------------------------
# Task 8: Snapshot from ref + zero-point fast-path placeholder
# ---------------------------------------------------------------------------

def test_points_editor_zero_point_placeholder_renders(
    tmp_path, playwright_browser,
):
    """Empty points list → italic placeholder row shows the implicit
    final-only behavior. No regular tbody rows for now."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    placeholder_count = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).count()
    placeholder_text = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).first.inner_text()
    page.close()
    assert placeholder_count == 1
    assert "final" in placeholder_text.lower()
    assert "ref" in placeholder_text.lower()


def test_points_editor_first_add_replaces_placeholder(
    tmp_path, playwright_browser,
):
    """Clicking + add point on an empty list removes the placeholder
    and shows a real editable row."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor button.node-btn-add'
    ).first.click()
    placeholder_count = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).count()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    page.close()
    assert placeholder_count == 0
    assert rows == 1


def test_points_editor_snapshot_from_ref_fills_empty_value_cells(
    tmp_path, playwright_browser,
):
    """📸 Snapshot from ref fills value for every row where value is
    None. Rows with explicit value are untouched."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "tolerance": 0.01},                       # ref-relative
            {"time": 4.0, "value": 99.0, "tolerance": 0.01},        # explicit
            {"time": 1.0, "tolerance": 0.01},                       # ref-relative
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'button.snapshot-btn'
    ).first.click()
    values = page.evaluate("""
        () => leafState['/metrics/children/0'].params.points.map(p => p.value)
    """)
    page.close()
    # Fixture trajectory has ref[t] = t exactly. So snapshot of t=2 → 2,
    # t=1 → 1. The explicit 99.0 is preserved.
    assert values == [2.0, 99.0, 1.0]


def test_points_editor_snapshot_idempotent_on_no_empty_rows(
    tmp_path, playwright_browser,
):
    """When all rows have explicit value, snapshot is a no-op."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "value": 2.5, "tolerance": 0.01},
            {"time": 4.0, "value": 4.5, "tolerance": 0.01},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'button.snapshot-btn'
    ).first.click()
    values = page.evaluate("""
        () => leafState['/metrics/children/0'].params.points.map(p => p.value)
    """)
    page.close()
    assert values == [2.5, 4.5]
