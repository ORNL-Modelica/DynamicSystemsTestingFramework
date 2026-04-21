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
    """Warn wraps its child; the child can fail but the warn stays PASS.
    Force the tube leaf to fail by zeroing its scalar tube_rel (v2 editor
    hides the schema inputs, so we mutate leafState directly and call
    refreshPassStates — same code path as a DOM edit)."""
    rendered_page.evaluate("""
        const state = leafState['/metrics/children/2/children/0'];
        state.params.tube_points = [];       // force scalar-width scoring
        state.params.tube_width_mode = 'rel';
        state.params.tube_rel = 0;
        refreshPassStates();
    """)
    leaf_pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] > .node-header > .node-status'
    ).first
    assert leaf_pill.text_content() == "FAIL"
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

def test_remove_leaf_shows_inline_confirm_then_removes(rendered_page: Page):
    """Clicking − opens an inline confirm (not a browser dialog); ✓
    commits the removal, ✗ cancels, click-away / ESC also cancels."""
    # First click opens the popup; structureDirty stays false until ✓
    remove_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] .node-btn-remove'
    ).first
    remove_btn.click()
    popup = rendered_page.locator(".remove-confirm").first
    assert popup.is_visible()
    assert rendered_page.evaluate("structureDirty") is False

    # Cancel via ✗
    rendered_page.locator(".remove-confirm-no").first.click()
    assert rendered_page.locator(".remove-confirm").count() == 0
    assert rendered_page.evaluate("structureDirty") is False

    # Re-open and confirm
    remove_btn.click()
    rendered_page.locator(".remove-confirm-yes").first.click()
    assert rendered_page.evaluate("structureDirty") is True
    payload = rendered_page.evaluate("buildPatchData()")
    assert len(payload["patch"]) == 1
    assert payload["patch"][0]["path"] == "/metrics"
    new_children = payload["patch"][0]["value"]["children"]
    assert len(new_children) == 2


def test_remove_leaf_popup_closes_on_escape(rendered_page: Page):
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] .node-btn-remove'
    ).first.click()
    assert rendered_page.locator(".remove-confirm").count() == 1
    rendered_page.keyboard.press("Escape")
    assert rendered_page.locator(".remove-confirm").count() == 0
    assert rendered_page.evaluate("structureDirty") is False


def test_add_leaf_via_modal(rendered_page: Page):
    """Clicking + on a combinator opens an inline modal; metric dropdown
    + variable text input with datalist. Add → appends leaf to WORKING_TREE."""
    add_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first
    add_btn.click()
    modal = rendered_page.locator(".modal-dialog").first
    assert modal.is_visible()
    metric_sel = rendered_page.locator("#add-leaf-metric")
    var_inp = rendered_page.locator("#add-leaf-variable")
    assert metric_sel.locator("option").count() == 6
    metric_sel.select_option("nrmse")
    var_inp.fill("h")
    rendered_page.locator("#add-leaf-ok").click()
    assert rendered_page.locator(".modal-dialog").count() == 0
    payload = rendered_page.evaluate("buildPatchData()")
    assert len(payload["patch"]) == 1
    assert payload["patch"][0]["path"] == "/metrics"
    new_children = payload["patch"][0]["value"]["children"]
    assert len(new_children) == 4


def test_add_leaf_modal_cancel(rendered_page: Page):
    """Cancel in the add-leaf modal leaves WORKING_TREE untouched."""
    add_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first
    add_btn.click()
    rendered_page.locator("#add-leaf-cancel").click()
    assert rendered_page.locator(".modal-dialog").count() == 0
    assert rendered_page.evaluate("structureDirty") is False


def test_added_leaf_gets_schema_driven_controls(rendered_page: Page):
    """A leaf added via the modal gets schema-rendered inputs + a
    seeded leafState entry so it can contribute to the plot + be
    edited like a CLI-evaluated leaf."""
    # Provide MODE_SCHEMAS so the JS-side renderer has something to walk.
    rendered_page.evaluate("""
        MODE_SCHEMAS.range = {
            mode: 'range',
            fields: [
                {name: 'min_value', type: 'float', default: null, optional: true, choices: null, label: 'Min value'},
                {name: 'max_value', type: 'float', default: null, optional: true, choices: null, label: 'Max value'},
            ],
        };
    """)
    # Open the modal, add a range leaf on 'h'.
    rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first.click()
    rendered_page.locator("#add-leaf-metric").select_option("range")
    rendered_page.locator("#add-leaf-variable").fill("h")
    rendered_page.locator("#add-leaf-ok").click()

    # leafState[<new-path>] should exist and have default params.
    has_state = rendered_page.evaluate("""
        () => {
            const paths = Object.keys(leafState);
            // Last-appended leaf under /metrics/children/<N-1>
            const newPath = paths.find(p => p.startsWith('/metrics/children/') && !p.includes('/children/', 20));
            return !!leafState[newPath] && typeof leafState[newPath].params === 'object';
        }
    """)
    assert has_state

    # The newly-added leaf in the DOM should carry the range controls
    # (data-field="min_value" / "max_value") — proves renderModeControlsHtmlJs
    # produced real inputs, not an empty string.
    # Find the last leaf in the full-tree mount.
    new_leaf_controls = rendered_page.locator(
        '#nodes-full .node-leaf input[data-field="min_value"]'
    )
    assert new_leaf_controls.count() >= 1, (
        "added range leaf should have min_value input rendered"
    )


def test_add_leaf_from_per_variable_mount_locks_variable(rendered_page: Page):
    """Clicking + inside a per-variable mount opens the modal with the
    variable pre-filled + readonly so the new leaf stays on-topic."""
    add_btn = rendered_page.locator(
        '#nodes-0 .node-combinator > .node-header .node-btn-add'
    ).first
    add_btn.click()
    var_inp = rendered_page.locator("#add-leaf-variable")
    assert var_inp.input_value() == "h"
    # readonly — user can't change it
    assert var_inp.evaluate("el => el.readOnly") is True


def test_add_leaf_modal_custom_variable(rendered_page: Page):
    """Typing a not-yet-tracked variable name warns but still accepts."""
    add_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first
    add_btn.click()
    rendered_page.locator("#add-leaf-variable").fill("pipe.T")
    warning = rendered_page.locator("#add-leaf-var-warning")
    assert "isn't tracked" in warning.text_content()
    rendered_page.locator("#add-leaf-ok").click()
    payload = rendered_page.evaluate("buildPatchData()")
    new_children = payload["patch"][0]["value"]["children"]
    added = new_children[-1]
    assert added["variable"] == "pipe.T"


def test_add_leaf_modal_glob_variable_no_warning(rendered_page: Page):
    """Glob patterns (* ? chars) are accepted without the 'not tracked' warning."""
    rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first.click()
    rendered_page.locator("#add-leaf-variable").fill("pipe.T*")
    assert rendered_page.locator("#add-leaf-var-warning").text_content() == ""


def test_add_leaf_modal_datalist_populated(rendered_page: Page):
    """The variable datalist carries every tracked variable so the
    browser's native filter-as-you-type works out of the box."""
    rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-add'
    ).first.click()
    options = rendered_page.locator("#add-leaf-var-options option")
    values = [options.nth(i).get_attribute("value") for i in range(options.count())]
    assert "h" in values
    assert "v" in values


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
    assert table.is_visible()


def test_tube_editor_auto_seeds_two_points_at_trajectory_ends(rendered_page: Page):
    """Activating a tube leaf with no points seeds exactly two at
    trajectory start/end with widthMode='rel' and width=0.05."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    # Fixture's tube leaf starts with tube_rel=0.05 but no tube_points.
    # Clear any points to simulate a fresh leaf.
    rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points = [];"
    )
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    points = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points"
    )
    assert len(points) == 2
    assert points[0]["upper"] == pytest.approx(0.05)
    assert points[0]["lower"] == pytest.approx(0.05)
    assert points[1]["upper"] == pytest.approx(0.05)
    # widthMode was set to 'rel' on seed
    mode = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_width_mode"
    )
    assert mode == "rel"


def test_tube_editor_add_point_button_commits_to_state(rendered_page: Page):
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    # Seed two (auto), then click "+ add point" — should get a third.
    add_btn = rendered_page.locator(
        '[data-path="/metrics/children/2/children/0"] .node-editor .node-btn-add'
    ).first
    add_btn.click()
    n = rendered_page.evaluate(
        "(leafState['/metrics/children/2/children/0'].params.tube_points||[]).length"
    )
    assert n == 3


def test_tube_editor_width_mode_change_reprojects_points(rendered_page: Page):
    """Changing widthMode from 'rel' to 'band' re-projects point values
    so the visual tube stays roughly the same."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    # Default is 'rel' with 0.05 width. Flip to 'band' — values should change
    # (absolute signal-unit widths, not fractional).
    rendered_page.evaluate("""
        () => {
            const sel = document.querySelector(
                '[data-path=\"/metrics/children/2/children/0\"] .node-editor select[data-tube-field=\"widthMode\"]'
            );
            sel.value = 'band';
            sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
    """)
    mode = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_width_mode"
    )
    assert mode == "band"
    # Reprojection maps rel-fraction to band-offset (rel * |ref|). Point[1]
    # is at t=3 where the fixture's v-trajectory ref ≈ 1 + sin(3) ≈ 1.14,
    # so upper should land near 0.057, not 0.05.
    pts = rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points"
    )
    assert pts[1]["upper"] != pytest.approx(0.05, abs=0.001)


def test_tube_editor_unsync_reveals_per_side_mode_columns(rendered_page: Page):
    """Switching from synced to unsynced changes the table layout to
    6 columns (Time | Upper | Mode | Lower | Mode | ✕).

    Tube editor renders into every mount's .node-editor slot, so scope
    the column count to the full-tree mount for a deterministic read.
    """
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    cols_before = rendered_page.evaluate("""
        () => document.querySelectorAll(
            '#nodes-full [data-path=\"/metrics/children/2/children/0\"] .node-editor .tube-table tr:first-child th'
        ).length
    """)
    assert cols_before == 3  # synced default
    rendered_page.evaluate("""
        () => {
            const sel = document.querySelector(
                '#nodes-full [data-path=\"/metrics/children/2/children/0\"] .node-editor select[data-tube-field=\"synced\"]'
            );
            sel.value = 'false';
            sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
    """)
    cols_after = rendered_page.evaluate("""
        () => document.querySelectorAll(
            '#nodes-full [data-path=\"/metrics/children/2/children/0\"] .node-editor .tube-table tr:first-child th'
        ).length
    """)
    assert cols_after == 6


def test_tube_editor_shift_click_on_plot_adds_point(rendered_page: Page):
    """Shift+click on the plot background (not on an existing marker)
    adds a new control point at that (t, y). Mirrors prototype behaviour."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    before = rendered_page.evaluate(
        "(leafState['/metrics/children/2/children/0'].params.tube_points||[]).length"
    )
    # Synthesize shift+mousedown then mouseup (clickStart → addPointAt path).
    rendered_page.evaluate("""
        () => {
            const el = document.getElementById('plot-1');
            const fl = el._fullLayout;
            const rect = el.getBoundingClientRect();
            const mid = (fl._size.l + (fl._size.l + fl._size.w) / 2);
            const top = fl._size.t + fl._size.h / 2;
            const down = new MouseEvent('mousedown', {
                button: 0, shiftKey: true,
                clientX: rect.left + mid, clientY: rect.top + top,
                bubbles: true, cancelable: true,
            });
            el.dispatchEvent(down);
            const up = new MouseEvent('mouseup', {
                button: 0, shiftKey: true,
                clientX: rect.left + mid, clientY: rect.top + top,
                bubbles: true, cancelable: true,
            });
            document.dispatchEvent(up);
        }
    """)
    after = rendered_page.evaluate(
        "(leafState['/metrics/children/2/children/0'].params.tube_points||[]).length"
    )
    assert after == before + 1


def test_tube_shift_right_click_removes_control_point(rendered_page: Page):
    """Shift+right-click near an existing tube control point removes it.
    The handler binds via ``addEventListener('mousedown', ..., true)`` to
    fire ahead of Plotly's own handlers; verify by synthesizing the
    native MouseEvent directly in the page context (Playwright's mouse
    API doesn't always propagate through Plotly's svg wrapper)."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    # Start with a clean slate — clear auto-seeded points, then populate
    # exactly two with a known time so the right-click hit-test is
    # deterministic.
    rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points = [];"
    )
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    # Activation auto-seeded two points at trajectory start/end. Anchor
    # the first at t=1.0 so we know exactly where to right-click.
    rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].params.tube_points[0].time = 1.0"
    )
    assert rendered_page.evaluate(
        "(leafState['/metrics/children/2/children/0'].params.tube_points||[]).length"
    ) == 2

    # v2 editor wires right-click removal on the ``contextmenu`` event
    # (mousedown with button 2 is a no-op). Synthesize contextmenu at
    # the first point's upper-bound absolute y (so findNearestCP hits).
    ok = rendered_page.evaluate("""
        () => {
            const el = document.getElementById('plot-1');
            const fl = el._fullLayout;
            if (!fl || !fl.xaxis || !fl.yaxis) return false;
            // Point at t=1.0, upper=0.05 rel → y ≈ ref(1.0) * 1.05 (v trajectory 1+sin(t))
            const state = leafState['/metrics/children/2/children/0'];
            const pt = state.params.tube_points[0];
            // Resolve to absolute y via the tube editor helper
            const r = MODE_PLOT_EDITORS['tube']._resolvePoint(
                findLeaf(TREE_VIEW, '/metrics/children/2/children/0'),
                pt, 0
            );
            const rect = el.getBoundingClientRect();
            const clientX = rect.left + (fl._size?.l || 0) + fl.xaxis.d2p(pt.time);
            const clientY = rect.top + (fl._size?.t || 0) + fl.yaxis.d2p(r.upper);
            const evt = new MouseEvent('contextmenu', {
                shiftKey: true,
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


def test_switching_leaves_does_not_stack_window_brush(rendered_page: Page):
    """Regression: window-brush button was duplicating on each re-activation
    because deactivateLeaf wasn't clearing .node-editor. Switch between
    leaves multiple times, then re-activate — exactly one brush button
    per mount."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    paths = [
        "/metrics/children/0",               # nrmse on h
        "/metrics/children/1",               # range on h
        "/metrics/children/2/children/0",    # tube on v (warn-wrapped)
        "/metrics/children/0",               # back to the first
    ]
    for p in paths:
        rendered_page.locator(
            f'#nodes-full [data-path="{p}"] .node-header'
        ).first.click()
    # After settling on /metrics/children/0, its editor slot should have
    # exactly one window-brush-wrap per mount it appears in.
    count = rendered_page.locator(
        '[data-path="/metrics/children/0"] .node-editor .window-brush-wrap'
    ).count()
    # Leaf appears in both the full-tree mount and the per-variable h mount.
    assert count == 2


def test_tube_edit_in_one_mount_refreshes_every_mount(rendered_page: Page):
    """Tube leaf renders its table into every mount's .node-editor slot.
    Editing a point in one mount must refresh every mount (prior bug:
    only the source slot re-rendered)."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    # Sanity: two tables (full-tree + per-variable v mount), both with
    # the same current row count (seed = 2 rows + header).
    rows_full_before = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .tube-table tr'
    ).count()
    rows_var_before = rendered_page.locator(
        '#nodes-1 [data-path="/metrics/children/2/children/0"] .tube-table tr'
    ).count()
    assert rows_full_before == rows_var_before
    # Click + add point in the full-tree mount.
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-editor .node-btn-add'
    ).first.click()
    # Both mounts should have one more row than before — sync, not just
    # the mount that initiated the edit.
    rows_full_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .tube-table tr'
    ).count()
    rows_var_after = rendered_page.locator(
        '#nodes-1 [data-path="/metrics/children/2/children/0"] .tube-table tr'
    ).count()
    assert rows_full_after == rows_full_before + 1
    assert rows_var_after == rows_var_before + 1
    assert rows_full_after == rows_var_after


def test_tube_control_point_markers_not_in_legend(rendered_page: Page):
    """Legend should show Actual + Reference + Tube polygon — no clutter
    from control-point marker traces."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] .node-header'
    ).first.click()
    legend_entries = rendered_page.evaluate("""
        () => {
            const el = document.getElementById('plot-1');
            if (!el || !el.data) return [];
            return el.data
                .filter(t => t.showlegend !== false)
                .map(t => t.name);
        }
    """)
    # No "Tube upper pts" or "Tube lower pts" in visible legend
    for name in legend_entries:
        assert 'pts' not in name.lower(), f"marker trace {name!r} leaked into legend"


def test_error_overlay_dropdown_adds_and_removes_trace(rendered_page: Page):
    """Selecting signed / abs / NRMSE adds a right-axis error trace;
    selecting 'none' removes it. Switching between modes replaces
    cleanly — no accumulating traces."""
    if not _has_plotly(rendered_page):
        pytest.skip("Plotly CDN not reachable; plot-interactive test skipped")
    sel = rendered_page.locator('.error-overlay-select[data-vidx="0"]').first
    # Pick signed error
    sel.select_option("signed")
    count1 = rendered_page.evaluate("""
        () => (document.getElementById('plot-0').data || [])
            .filter(t => t.name === 'Error overlay').length
    """)
    assert count1 == 1
    # Switch to abs — still exactly one overlay trace
    sel.select_option("abs")
    count2 = rendered_page.evaluate("""
        () => (document.getElementById('plot-0').data || [])
            .filter(t => t.name === 'Error overlay').length
    """)
    assert count2 == 1
    # Switch to none — trace gone
    sel.select_option("none")
    count3 = rendered_page.evaluate("""
        () => (document.getElementById('plot-0').data || [])
            .filter(t => t.name === 'Error overlay').length
    """)
    assert count3 == 0


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
