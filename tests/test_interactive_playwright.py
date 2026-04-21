"""Browser-driven Playwright tests for interactive.html.

Catches behavioral regressions that structural-hash snapshots + Python
unit tests can't: click handlers firing, Plotly event wiring, state
mutations surviving through ``buildPatchData``, cross-mount input sync,
ESC deactivation.

Skipped automatically when Playwright isn't installed — the test suite
still runs clean on systems without it (CI without browser deps, etc.).
Install via:

    uv pip install pytest-playwright
    uv run playwright install chromium

Tests render a canonical BouncingBall-shaped fixture (one primary tree
with four leaves across two variables) into a temp directory — copying
the standalone ``interactive.js`` alongside the HTML so the
``<script src="interactive.js">`` reference resolves — then opens the
file URL in headless Chromium and exercises the UI.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, sync_playwright


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_JS_SRC = (
    Path(__file__).resolve().parents[1]
    / "src" / "modelica_testing" / "reporting" / "templates" / "interactive.js"
)
_TEMPLATE_DIR = _JS_SRC.parent


def _leaf(*, path: str, metric: str, variable: str, params: dict,
          passed: bool = True, score: float = 1e-5,
          window: dict | None = None) -> dict:
    return {
        "kind": "leaf",
        "path": path,
        "metric": metric,
        "variable": variable,
        "params": dict(params),
        "against": "primary",
        "window": window or {},
        "children": [],
        "passed": passed,
        "score": score,
        "label": variable,
        "name": variable,
        "mode_effective": metric,
        "nrmse": 1e-5, "rmse": 1e-5, "signal_range": 2.0,
        "max_abs_error": 1e-5, "max_abs_error_time": 5.0,
        "reference_final": 0.0, "actual_final": 0.0,
        "is_constant": False,
        "tolerance_used": params.get("tolerance", 1e-4),
        "score_display": f"{metric} 1e-5",
        "criterion": f"{metric} → PASS",
        "tube_points_inside": None,
        "tube_worst_violation": None,
        "tube_worst_violation_time": None,
        "mode_values": dict(params),
        "mode_controls_html": _mode_controls_html(metric, params),
        "window_controls_html": _window_controls_html(window or {}),
        "window_values": dict(window or {}),
        "cli_authoritative": metric in ("event-timing", "dominant-frequency"),
    }


def _mode_controls_html(metric: str, params: dict) -> str:
    # Minimal valid control HTML per mode; mirrors what the real schema
    # renderer emits (tested separately in test_mode_controls.py). We just
    # need data-field entries so Playwright can exercise input sync.
    if metric == "nrmse" or metric == "final-only":
        val = params.get("tolerance", 1e-4)
        return (
            '<div class="mode-controls" data-mode="%s" data-variable="x">'
            '<label><span>Tolerance</span>'
            '<input type="number" step="any" data-field="tolerance" value="%s"></label>'
            "</div>" % (metric, val)
        )
    if metric == "range":
        return (
            '<div class="mode-controls" data-mode="range" data-variable="x">'
            '<label><span>Min value</span>'
            '<input type="number" step="any" data-field="min_value" value="%s"></label>'
            '<label><span>Max value</span>'
            '<input type="number" step="any" data-field="max_value" value="%s"></label>'
            "</div>" % (params.get("min_value", ""), params.get("max_value", ""))
        )
    if metric == "tube":
        return (
            '<div class="mode-controls" data-mode="tube" data-variable="x">'
            '<label><span>tube_rel</span>'
            '<input type="number" step="any" data-field="tube_rel" value="%s"></label>'
            "</div>" % params.get("tube_rel", 0)
        )
    return ""


def _window_controls_html(window: dict) -> str:
    start = window.get("start", "")
    end = window.get("end", "")
    s_val = f' value="{start}"' if start != "" else ""
    e_val = f' value="{end}"' if end != "" else ""
    return (
        '<div class="window-controls" data-variable="x">'
        f'<input type="number" step="any" data-field="window_start"{s_val}>'
        f'<input type="number" step="any" data-field="window_end"{e_val}>'
        "</div>"
    )


def _fixture_context() -> dict:
    """A deliberately-varied tree so every editor gets exercised.

    Two variables (``h`` + ``v``), four leaves: nrmse on h, range on h,
    nrmse on v, and a warn-wrapped tube on v. The warn combinator adds
    nesting so per-variable filtering is non-trivial.
    """
    tree = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": True, "label": "and[3]",
        "children": [
            _leaf(path="/metrics/children/0",
                  metric="nrmse", variable="h",
                  params={"tolerance": 1e-3}),
            _leaf(path="/metrics/children/1",
                  metric="range", variable="h",
                  params={"min_value": -0.01, "max_value": 1.1}),
            {
                "kind": "combinator", "combinator": "warn",
                "path": "/metrics/children/2", "passed": True,
                "label": "warn",
                "children": [
                    _leaf(path="/metrics/children/2/children/0",
                          metric="tube", variable="v",
                          params={"tube_rel": 0.05}),
                ],
            },
        ],
    }
    # Simple trajectories — enough time points that LTTB decimation
    # doesn't kick in.
    def traj(name):
        import numpy as np
        t = np.linspace(0, 3, 50).tolist()
        if name == "v":
            # Shifted so ref is always >= 1 — avoids zero-crossing so a 5%
            # rel tube is wide enough to contain the small act offset; set
            # tube_rel=0 in a test to force failure.
            ref = [1 + np.sin(x) for x in t]
            act = [v + 0.01 for v in ref]
        else:
            ref = [1 - 0.3 * x for x in t]
            act = ref[:]
        return {
            "index": 1, "name": name,
            "act_time": t, "act_values": act,
            "ref_time": t, "ref_values": ref,
        }

    variables_by_name = {
        "h": {"name": "h", "trajectory": traj("h"), "overlays": [], "leaf_paths": ["/metrics/children/0", "/metrics/children/1"]},
        "v": {"name": "v", "trajectory": traj("v"), "overlays": [], "leaf_paths": ["/metrics/children/2/children/0"]},
    }

    return {
        "model_id": "Fixture.Playwright",
        "n_passed": 3, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [],
        "diagnostic_summaries": [],
        "artifacts": [],
        "trajectories": list(variables_by_name[k]["trajectory"] for k in variables_by_name),
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": variables_by_name,
        "mode_schemas": {},  # not needed for behavioral tests
        "overlay_rows": [],
    }


def _render_report(tmp_path: Path) -> Path:
    """Render the fixture context to ``tmp_path/interactive.html`` and
    copy the standalone interactive.js alongside so the browser can
    resolve ``<script src="interactive.js">``."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**_fixture_context())
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path


@pytest.fixture(scope="module")
def playwright_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def rendered_page(tmp_path, playwright_browser) -> Iterator[Page]:
    """One fresh page per test, navigated to the fixture report.

    Plotly loads from a CDN; if the browser can't reach it the page
    still loads and the non-plot JS runs, but tests that interact with
    the plot (Shift+click, drag) require it. Those tests ``pytest.skip``
    if ``window.Plotly`` didn't define.
    """
    html_path = _render_report(tmp_path)
    context = playwright_browser.new_context()
    page = context.new_page()
    # Bubble JS errors up to the test as a sanity signal.
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    # Wait for the JS to finish DOMContentLoaded initialization.
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")
    yield page
    context.close()


# ---------------------------------------------------------------------------
# Tests — structure + activation
# ---------------------------------------------------------------------------

def test_per_variable_sections_rendered(rendered_page: Page):
    """Two unique variables → two variable sections."""
    sections = rendered_page.locator(".variable-section")
    assert sections.count() == 2
    assert sections.nth(0).get_attribute("data-variable") == "h"
    assert sections.nth(1).get_attribute("data-variable") == "v"


def test_full_tree_mount_renders_every_leaf(rendered_page: Page):
    """The top-of-report mount shows the full unfiltered tree."""
    full = rendered_page.locator("#nodes-full")
    assert full.locator(".node-leaf").count() == 3


def test_per_variable_mount_filters_to_variable(rendered_page: Page):
    """The ``h`` plot's tree below shows only leaves targeting ``h``."""
    h_nodes = rendered_page.locator("#nodes-0")
    v_nodes = rendered_page.locator("#nodes-1")
    h_leaves = h_nodes.locator(".node-leaf")
    v_leaves = v_nodes.locator(".node-leaf")
    assert h_leaves.count() == 2
    assert v_leaves.count() == 1


def test_clicking_leaf_activates_it(rendered_page: Page):
    """Click on a leaf → .node-active across every mount; activeLeafPath set."""
    leaf = rendered_page.locator('#nodes-full [data-path="/metrics/children/0"]').first
    leaf.locator(".node-header").click()
    active = rendered_page.evaluate("activeLeafPath")
    assert active == "/metrics/children/0"
    # Both mounts should carry .node-active on that path
    active_nodes = rendered_page.locator('[data-path="/metrics/children/0"].node-active')
    assert active_nodes.count() >= 1


def test_escape_deactivates_leaf(rendered_page: Page):
    """ESC clears activeLeafPath + removes .node-active."""
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] .node-header'
    ).first.click()
    assert rendered_page.evaluate("activeLeafPath") is not None
    rendered_page.keyboard.press("Escape")
    assert rendered_page.evaluate("activeLeafPath") is None
    assert rendered_page.locator(".node-active").count() == 0


def test_clicking_different_leaf_switches_activation(rendered_page: Page):
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] .node-header'
    ).first.click()
    assert rendered_page.evaluate("activeLeafPath") == "/metrics/children/0"
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] .node-header'
    ).first.click()
    assert rendered_page.evaluate("activeLeafPath") == "/metrics/children/1"


# ---------------------------------------------------------------------------
# Tests — editing state
# ---------------------------------------------------------------------------

def test_editing_tolerance_updates_leaf_state(rendered_page: Page):
    """Type in the tolerance input → leafState.params.tolerance flips."""
    selector = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    inp = rendered_page.locator(selector).first
    inp.fill("0.01")
    inp.dispatch_event("input")
    val = rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.tolerance"
    )
    assert val == pytest.approx(0.01)


def test_cross_mount_input_sync(rendered_page: Page):
    """Edit in full-tree mount → per-variable mount's input reflects it."""
    full_sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    var_sel = '#nodes-0 [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    full = rendered_page.locator(full_sel).first
    var_inp = rendered_page.locator(var_sel).first
    full.fill("5e-3")
    full.dispatch_event("input")
    assert var_inp.input_value() == "5e-3" or var_inp.input_value() == "0.005"


def test_window_inputs_round_trip_through_leafstate(rendered_page: Page):
    """Filling window_start/end updates leafState.window."""
    sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_start"]'
    inp = rendered_page.locator(sel).first
    inp.fill("1.0")
    inp.dispatch_event("input")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].window.start"
    ) == pytest.approx(1.0)


def test_live_recompute_flips_pill_on_tolerance_edit(rendered_page: Page):
    """Tightening tolerance below the leaf's stored NRMSE should flip
    the pill to FAIL live (no CLI rerun). Proves MODE_SCORERS recompute
    is wired to the DOM post-Stage-5."""
    # Fixture leaf has nrmse=1e-5 and tolerance=1e-3 → passing.
    # Drop tolerance to 1e-12 → should fail.
    sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    inp = rendered_page.locator(sel).first
    inp.fill("1e-12")
    inp.dispatch_event("input")
    # Pill in the full-tree mount should say FAIL
    pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header > .node-status'
    ).first
    assert pill.text_content() == "FAIL"
    # Summary reflects the change
    summary = rendered_page.locator("#summary-text").first
    assert "2" in summary.text_content()  # 2 of 3 passed now
    # Variable-level pill for `h` should also flip (h had 2 leaves, one now fails)
    var_status = rendered_page.locator('.var-status[data-vidx="0"]').first
    assert var_status.text_content() == "FAIL"


def test_live_recompute_bubbles_through_and_combinator(rendered_page: Page):
    """When any child of a top-level AND fails live, the AND combinator's
    pill flips too."""
    sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    rendered_page.locator(sel).first.fill("1e-12")
    rendered_page.locator(sel).first.dispatch_event("input")
    and_pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header > .node-status'
    ).first
    assert and_pill.text_content() == "FAIL"


def test_live_recompute_warn_child_does_not_fail_parent(rendered_page: Page):
    """Warn wraps its child; the child can fail but the warn stays PASS
    (warn combinator by design). Confirms the recompute honours the
    combinator semantics."""
    # Push the warn-wrapped tube leaf to failure via a tight tube_rel.
    # Fixture's v leaf is tube with tube_rel=0.05. Set to 0 → fails.
    sel = '#nodes-full [data-path="/metrics/children/2/children/0"] input[data-field="tube_rel"]'
    inp = rendered_page.locator(sel).first
    inp.fill("0")
    inp.dispatch_event("input")
    # The tube leaf itself should fail
    leaf_pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] > .node-header > .node-status'
    ).first
    assert leaf_pill.text_content() == "FAIL"
    # But the warn combinator stays PASS
    warn_pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2"] > .node-header > .node-status'
    ).first
    assert warn_pill.text_content() == "PASS"


def test_buildPatchData_reflects_scalar_edit(rendered_page: Page):
    """Export JSON shows the right op-shape after a tolerance edit."""
    sel = '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    rendered_page.locator(sel).first.fill("0.1")
    rendered_page.locator(sel).first.dispatch_event("input")
    payload = rendered_page.evaluate("buildPatchData()")
    assert payload["model"] == "Fixture.Playwright"
    ops = payload["patch"]
    tolerance_op = next(
        (o for o in ops if o["path"].endswith("/tolerance")), None,
    )
    assert tolerance_op is not None, f"no tolerance op found in {ops}"
    assert tolerance_op["value"] == pytest.approx(0.1)


def test_buildPatchData_emits_window_add(rendered_page: Page):
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_start"]'
    ).first.fill("2.0")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_start"]'
    ).first.dispatch_event("input")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_end"]'
    ).first.fill("5.0")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_end"]'
    ).first.dispatch_event("input")
    payload = rendered_page.evaluate("buildPatchData()")
    window_op = next(
        (o for o in payload["patch"] if o["path"].endswith("/window")), None,
    )
    assert window_op is not None
    assert window_op["op"] == "add"
    assert window_op["value"] == {"start": 2.0, "end": 5.0}


# ---------------------------------------------------------------------------
# Tests — structural editing (+ / −)
# ---------------------------------------------------------------------------

def test_remove_leaf_marks_structure_dirty(rendered_page: Page):
    """Clicking − on a leaf marks structureDirty and emits a wholesale replace."""
    # Auto-confirm the dialog.
    rendered_page.on("dialog", lambda d: d.accept())
    remove_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] .node-btn-remove'
    ).first
    remove_btn.click()
    assert rendered_page.evaluate("structureDirty") is True
    payload = rendered_page.evaluate("buildPatchData()")
    assert len(payload["patch"]) == 1
    assert payload["patch"][0]["path"] == "/metrics"
    assert payload["patch"][0]["op"] == "add"
    new_children = payload["patch"][0]["value"]["children"]
    assert len(new_children) == 2  # one gone (was 3)


def test_add_leaf_via_prompt(rendered_page: Page):
    """Clicking + on a combinator prompts for metric + variable → appends leaf."""
    # Two prompts fire in sequence — metric first, then variable.
    answers = iter(["nrmse", "h"])
    rendered_page.on("dialog", lambda d: d.accept(next(answers)))
    add_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first
    add_btn.click()
    payload = rendered_page.evaluate("buildPatchData()")
    assert len(payload["patch"]) == 1
    assert payload["patch"][0]["path"] == "/metrics"
    new_children = payload["patch"][0]["value"]["children"]
    assert len(new_children) == 4  # was 3


# ---------------------------------------------------------------------------
# Tests — plot-editor wiring (require Plotly CDN)
# ---------------------------------------------------------------------------

def _has_plotly(page: Page) -> bool:
    return page.evaluate("typeof Plotly !== 'undefined'")


def test_tube_editor_activates_with_control_point_table(rendered_page: Page):
    """Click a tube leaf → the editor slot populates with the table UI."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    table = rendered_page.locator(
        '[data-path="/metrics/children/2/children/0"] .node-editor .tube-table'
    ).first
    # activate renders the header row regardless of point count
    assert table.is_visible()


def test_tube_editor_add_point_button_commits_to_state(rendered_page: Page):
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    add_btn = rendered_page.locator(
        '[data-path="/metrics/children/2/children/0"] .node-editor .node-btn-add'
    ).first
    add_btn.click()
    points = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points || []"
    )
    assert len(points) == 1


def test_tube_shift_right_click_removes_control_point(rendered_page: Page):
    """Shift+right-click near an existing tube control point removes it.
    The handler binds via ``addEventListener('mousedown', ..., true)`` to
    fire ahead of Plotly's own handlers; verify by synthesizing the
    native MouseEvent directly in the page context (Playwright's mouse
    API doesn't always propagate through Plotly's svg wrapper)."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    # Activate the tube leaf and seed two control points.
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    add_btn = rendered_page.locator(
        '[data-path="/metrics/children/2/children/0"] .node-editor .node-btn-add'
    ).first
    add_btn.click()
    add_btn.click()
    # Anchor one point at time=1.0 so we know exactly where to click.
    rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points[0].time = 1.0"
    )
    assert rendered_page.evaluate(
        "(leafState['/metrics/children/2/children/0'].params.tube_points||[]).length"
    ) == 2

    # Dispatch the MouseEvent directly — synthesizes the same event
    # shape the handler checks (button=2, shiftKey=true, clientX/Y
    # matching the t=1.0 pixel position on plot-1).
    ok = rendered_page.evaluate("""
        () => {
            const el = document.getElementById('plot-1');
            const fl = el._fullLayout;
            if (!fl || !fl.xaxis) return false;
            const rect = el.getBoundingClientRect();
            const clientX = rect.left + (fl._size?.l || 0) + fl.xaxis.d2p(1.0);
            const clientY = rect.top + (fl._size?.t || 0) + (fl._size?.h || 300) / 2;
            const evt = new MouseEvent('mousedown', {
                button: 2, buttons: 2, shiftKey: true,
                clientX, clientY, bubbles: true, cancelable: true,
            });
            el.dispatchEvent(evt);
            return true;
        }
    """)
    assert ok
    remaining = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points || []"
    )
    assert len(remaining) == 1
    # The surviving point is not the one we anchored at time=1.0.
    assert remaining[0].get("time") != 1.0


def test_window_brush_button_injected_on_activation(rendered_page: Page):
    """Activating any leaf injects the ``🔲 Set window from plot`` button
    (universal across modes). Plotly required only for the actual drag."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] .node-header'
    ).first.click()
    brush_btn = rendered_page.locator(
        '[data-path="/metrics/children/0"] .node-editor .window-brush-wrap button'
    ).first
    assert brush_btn.is_visible()
    assert "window" in brush_btn.text_content().lower()
