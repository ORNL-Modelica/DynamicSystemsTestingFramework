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
    # ``setTimeout(..., 0)`` inside openRemoveConfirm defers the ESC listener
    # by a tick to avoid the opening click dismissing itself. Wait for
    # attachment or the ESC press is a no-op.
    rendered_page.wait_for_function(
        "() => document.querySelector('.remove-confirm') && "
        "document.querySelector('.remove-confirm')._cleanup"
    )
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


# ---------------------------------------------------------------------------
# Tests — wrap / unwrap / change-kind (#52)
# ---------------------------------------------------------------------------


def test_kind_dropdown_renders_on_every_combinator(rendered_page: Page):
    """Every combinator in the full-tree mount has a kind-select dropdown
    with 5 options (and/or/warn/k-of-n/weighted)."""
    selects = rendered_page.locator("#nodes-full .node-kind-select")
    # The fixture has two combinators: root AND + inner warn.
    assert selects.count() == 2
    opts = selects.first.locator("option")
    assert opts.count() == 5
    # Order matches VALID_COMBINATORS.
    values = rendered_page.evaluate(
        "Array.from(document.querySelectorAll('#nodes-full .node-kind-select')[0].options)"
        ".map(o => o.value)"
    )
    assert values == ["and", "or", "warn", "k-of-n", "weighted"]


def test_change_kind_and_to_or_flips_combinator_field(rendered_page: Page):
    """Selecting 'or' on the root AND mutates WORKING_TREE.combinator."""
    sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    sel.select_option("or")
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "or"
    assert rendered_page.evaluate("structureDirty") is True


def test_change_kind_to_warn_on_multi_child_is_refused(rendered_page: Page):
    """The warn option is disabled when the combinator has != 1 child.
    If the user bypasses the disabled flag, changeCombinatorKind returns
    false and the tree stays unchanged."""
    # The disabled <option> means Playwright's select_option would error;
    # verify by calling the API directly (mimics a hypothetical alt path).
    result = rendered_page.evaluate(
        "changeCombinatorKind('/metrics', 'warn')"
    )
    assert result is False
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "and"


def test_change_kind_to_k_of_n_seeds_k_default(rendered_page: Page):
    """Switching a combinator into k-of-n auto-seeds k=max(1, n-1)."""
    sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    sel.select_option("k-of-n")
    # Root has 3 children → k seeded to 2.
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "k-of-n"
    assert rendered_page.evaluate("WORKING_TREE.k") == 2
    # Header shows the live k input — editing it updates the model.
    k_inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-k-input'
    ).first
    assert k_inp.input_value() == "2"
    k_inp.fill("3")
    k_inp.dispatch_event("change")
    assert rendered_page.evaluate("WORKING_TREE.k") == 3


def test_change_kind_to_weighted_seeds_weights_and_threshold(rendered_page: Page):
    sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    sel.select_option("weighted")
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "weighted"
    # Default seeding: unit weights for every child, threshold=1.0, direction=less.
    assert rendered_page.evaluate("WORKING_TREE.weights") == [1.0, 1.0, 1.0]
    assert rendered_page.evaluate("WORKING_TREE.threshold") == 1.0
    assert rendered_page.evaluate("WORKING_TREE.direction") == "less"


def test_change_kind_k_of_n_to_and_strips_k(rendered_page: Page):
    """Switching a k-of-n back to AND removes the k field."""
    sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    sel.select_option("k-of-n")
    assert rendered_page.evaluate("WORKING_TREE.k") == 2
    sel.select_option("and")
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "and"
    assert rendered_page.evaluate("'k' in WORKING_TREE") is False


def test_wrap_leaf_in_warn_via_popup(rendered_page: Page):
    """Click ⊕ on a leaf, pick warn in the popup, Confirm → leaf becomes
    warn([leaf])."""
    wrap_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-wrap'
    ).first
    wrap_btn.click()
    popup = rendered_page.locator(".wrap-popup").first
    assert popup.is_visible()
    popup.locator(".wrap-popup-kind").select_option("warn")
    popup.locator(".wrap-popup-yes").click()
    # Inspect the wholesale patch.
    payload = rendered_page.evaluate("buildPatchData()")
    new_tree = payload["patch"][0]["value"]
    # Root AND still has 3 children, but children/0 is now a warn combinator.
    assert new_tree["combinator"] == "and"
    assert new_tree["children"][0]["combinator"] == "warn"
    assert new_tree["children"][0]["children"][0]["metric"] == "nrmse"


def test_wrap_popup_cancel_leaves_tree_untouched(rendered_page: Page):
    wrap_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-wrap'
    ).first
    wrap_btn.click()
    rendered_page.locator(".wrap-popup-no").click()
    assert rendered_page.locator(".wrap-popup").count() == 0
    assert rendered_page.evaluate("structureDirty") is False


def test_wrap_popup_closes_on_escape(rendered_page: Page):
    wrap_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-wrap'
    ).first
    wrap_btn.click()
    assert rendered_page.locator(".wrap-popup").count() == 1
    # ``setTimeout(..., 0)`` inside openWrapPopup defers the ESC listener
    # registration by a tick to avoid the opening click dismissing itself.
    # Give the event loop that tick before pressing ESC or it's a no-op.
    rendered_page.wait_for_function(
        "() => document.querySelector('.wrap-popup') && "
        "document.querySelector('.wrap-popup')._cleanup"
    )
    rendered_page.keyboard.press("Escape")
    assert rendered_page.locator(".wrap-popup").count() == 0
    assert rendered_page.evaluate("structureDirty") is False


def test_wrap_combinator_in_warn_produces_single_child(rendered_page: Page):
    """Wrapping a multi-child AND in warn always yields warn(and(...)) —
    warn has one child even when the wrapped thing has multiple children,
    because the wrap creates a new parent above the existing node."""
    # Wrap the root AND (path /metrics) in warn.
    result = rendered_page.evaluate("wrapWorkingNode('/metrics', 'warn')")
    assert result is True
    # WORKING_TREE is now the new warn; root path rebuilt to /metrics.
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "warn"
    assert rendered_page.evaluate("WORKING_TREE.children.length") == 1
    assert rendered_page.evaluate("WORKING_TREE.children[0].combinator") == "and"
    assert rendered_page.evaluate("WORKING_TREE.children[0].children.length") == 3


def test_unwrap_button_renders_only_on_single_child_combinator(rendered_page: Page):
    """The root AND has 3 children → no ⊖. The inner warn has 1 child → ⊖."""
    # Root has no unwrap button.
    root_unwrap = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-btn-unwrap'
    )
    assert root_unwrap.count() == 0
    # Inner warn (children/2) has 1 child → unwrap button present.
    warn_unwrap = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2"] > .node-header .node-btn-unwrap'
    )
    assert warn_unwrap.count() == 1


def test_unwrap_replaces_combinator_with_its_single_child(rendered_page: Page):
    """Clicking ⊖ on the inner warn replaces it with the tube leaf —
    warn(tube) → tube. Live tree reflects the change."""
    unwrap = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2"] > .node-header .node-btn-unwrap'
    ).first
    unwrap.click()
    # The root AND's children/2 is now the tube leaf directly.
    payload = rendered_page.evaluate("buildPatchData()")
    new_tree = payload["patch"][0]["value"]
    assert new_tree["children"][2]["metric"] == "tube"
    assert "combinator" not in new_tree["children"][2]


def test_unwrap_root_single_child_makes_child_the_new_root(rendered_page: Page):
    """wrap(root, 'warn') then unwrap → should restore the original root."""
    rendered_page.evaluate("wrapWorkingNode('/metrics', 'warn')")
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "warn"
    rendered_page.evaluate("unwrapWorkingNode('/metrics')")
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "and"
    assert rendered_page.evaluate("WORKING_TREE.children.length") == 3


def test_wrap_emits_wholesale_metrics_replace(rendered_page: Page):
    """Any structural edit produces exactly one RFC 6902 add op at /metrics —
    same envelope as the existing add-leaf / remove-leaf flows."""
    rendered_page.evaluate("wrapWorkingNode('/metrics/children/0', 'warn')")
    payload = rendered_page.evaluate("buildPatchData()")
    assert len(payload["patch"]) == 1
    assert payload["patch"][0]["op"] == "add"
    assert payload["patch"][0]["path"] == "/metrics"


def test_change_kind_in_per_variable_mount_sees_structural_edit(rendered_page: Page):
    """Changing root kind from the full-tree mount re-renders the
    per-variable mounts so they see the new dropdown value."""
    full_sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    full_sel.select_option("or")
    # Per-variable mount's combinator header (if present) should agree.
    var_select = rendered_page.locator(
        '#nodes-0 [data-path="/metrics"] > .node-header .node-kind-select'
    )
    if var_select.count() > 0:
        assert var_select.first.input_value() == "or"
    # WORKING_TREE definitely agrees.
    assert rendered_page.evaluate("WORKING_TREE.combinator") == "or"


def test_weighted_weights_input_updates_weights_array(rendered_page: Page):
    """Weighted mode exposes one weight input per child; editing one
    updates only that index."""
    sel = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first
    sel.select_option("weighted")
    # Root has 3 children → 3 weight inputs.
    w_inputs = rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-weight-input'
    )
    assert w_inputs.count() == 3
    # Edit the middle weight.
    w_inputs.nth(1).fill("2.5")
    w_inputs.nth(1).dispatch_event("change")
    assert rendered_page.evaluate("WORKING_TREE.weights") == [1.0, 2.5, 1.0]


def test_wrap_button_renders_on_leaves_too(rendered_page: Page):
    """⊕ is available on every node, leaves included, so a user can
    demote a single leaf to advisory with one click."""
    leaf_wrap = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-wrap'
    )
    assert leaf_wrap.count() == 1


# ---------------------------------------------------------------------------
# Tests — value persistence across re-renders + reset button
# ---------------------------------------------------------------------------


def test_edit_survives_sibling_removal(rendered_page: Page):
    """Edit a tolerance, then remove an earlier sibling. The edit must
    survive the path shift (old /metrics/children/0 edit reappears under
    the new /metrics/children/0)."""
    # Edit tolerance on children/0 (the first nrmse leaf on h).
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    inp.fill("0.05")
    inp.dispatch_event("input")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.tolerance"
    ) == pytest.approx(0.05)

    # The fixture has no earlier sibling for children/0, so instead
    # remove children/1 (the range leaf on h). That shifts nothing at
    # index 0 — verify the edit stays put.
    remove_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] > .node-header .node-btn-remove'
    ).first
    remove_btn.click()
    rendered_page.locator(".remove-confirm-yes").first.click()
    # children/0 still holds the edit.
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.tolerance"
    ) == pytest.approx(0.05)
    # The DOM input also reflects it after re-render (the bug we fixed).
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(0.05)


def test_edit_survives_path_shift_via_remove(rendered_page: Page):
    """Edit a leaf whose path *will* shift due to an earlier-sibling
    remove. The leaf moves from /metrics/children/1 to /metrics/children/0,
    and its edited tolerance must travel with it."""
    # Edit children/1 (range on h; uses min_value / max_value, but also
    # carries tolerance from the fixture params via variable_overrides.
    # Pick min_value for this test — it's range-specific).
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] input[data-field="min_value"]'
    ).first
    inp.fill("-0.99")
    inp.dispatch_event("input")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/1'].params.min_value"
    ) == pytest.approx(-0.99)

    # Now remove children/0 — children/1 shifts to children/0.
    remove_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-remove'
    ).first
    remove_btn.click()
    rendered_page.locator(".remove-confirm-yes").first.click()

    # State migrated to the new path.
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.min_value"
    ) == pytest.approx(-0.99)
    # Old path cleared.
    assert rendered_page.evaluate(
        "'/metrics/children/1' in leafState"
    ) is False
    # DOM input shows the edit post-render.
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="min_value"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(-0.99)


def test_edit_survives_wrap(rendered_page: Page):
    """Edit a tolerance, then wrap the leaf in warn. Leaf path deepens
    from /metrics/children/0 to /metrics/children/0/children/0; edit
    migrates with it."""
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    inp.fill("0.02")
    inp.dispatch_event("input")
    # Wrap in warn via API (popup tested separately).
    rendered_page.evaluate(
        "wrapWorkingNode('/metrics/children/0', 'warn')"
    )
    # State migrated to deeper path.
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0/children/0'].params.tolerance"
    ) == pytest.approx(0.02)
    # DOM reflects it.
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0/children/0"] input[data-field="tolerance"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(0.02)


def test_edit_survives_unwrap(rendered_page: Page):
    """Edit a field inside the inner warn, then unwrap the warn. Leaf
    moves from /metrics/children/2/children/0 → /metrics/children/2.
    Tube leaves suppress mode-controls HTML (their fields live in the
    activated editor), so edit window_start — universal across modes."""
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/2/children/0"] input[data-field="window_start"]'
    ).first
    inp.fill("0.4")
    inp.dispatch_event("input")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/2/children/0'].window.start"
    ) == pytest.approx(0.4)
    rendered_page.evaluate("unwrapWorkingNode('/metrics/children/2')")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/2'].window.start"
    ) == pytest.approx(0.4)


def test_edit_survives_change_kind(rendered_page: Page):
    """Path doesn't shift on change-kind, but the DOM re-renders — input
    values must be restored from leafState."""
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    inp.fill("0.05")
    inp.dispatch_event("input")
    # Change root AND → OR (triggers full re-render).
    rendered_page.locator(
        '#nodes-full [data-path="/metrics"] > .node-header .node-kind-select'
    ).first.select_option("or")
    # DOM input reflects the edit.
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(0.05)


def test_window_edit_survives_structural_edit(rendered_page: Page):
    """Window inputs (start / end) are on every leaf regardless of mode.
    Edit one, remove a sibling, verify it survives."""
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/1"] input[data-field="window_start"]'
    ).first
    inp.fill("0.5")
    inp.dispatch_event("input")
    # Remove earlier sibling → children/1 shifts to children/0.
    remove_btn = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-remove'
    ).first
    remove_btn.click()
    rendered_page.locator(".remove-confirm-yes").first.click()
    # State + DOM both reflect the window edit at the new path.
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].window.start"
    ) == pytest.approx(0.5)
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_start"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(0.5)


def test_reset_button_renders_on_every_leaf(rendered_page: Page):
    """↻ button shows up alongside +/⊕/−."""
    resets = rendered_page.locator("#nodes-full .node-btn-reset")
    # Three leaves in the fixture.
    assert resets.count() == 3


def test_reset_button_restores_original_params(rendered_page: Page):
    """Edit tolerance, click ↻, tolerance reverts to original_params value."""
    # Capture original.
    original = rendered_page.evaluate(
        "leafState['/metrics/children/0'].original_params.tolerance"
    )
    # Edit.
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    inp.fill("0.05")
    inp.dispatch_event("input")
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.tolerance"
    ) == pytest.approx(0.05)
    # Click reset.
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-reset'
    ).first.click()
    # leafState + DOM both reverted.
    assert rendered_page.evaluate(
        "leafState['/metrics/children/0'].params.tolerance"
    ) == pytest.approx(original)
    inp_after = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    assert float(inp_after.input_value()) == pytest.approx(original)


def test_reset_button_restores_original_window(rendered_page: Page):
    """Window edits also revert on ↻."""
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="window_start"]'
    ).first
    inp.fill("0.7")
    inp.dispatch_event("input")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-reset'
    ).first.click()
    # Window cleared (original was {}).
    assert rendered_page.evaluate(
        "Object.keys(leafState['/metrics/children/0'].window)"
    ) == []


def test_detect_respects_leaf_window_and_source(tmp_path, playwright_browser):
    """D76 — set a window on the leaf, click Detect, verify the detected
    peaks come from the WINDOWED signal (not the full trajectory). Also
    verify source dropdown [Reference | Actual] works. Each seeded peak
    carries ``derived_from_window`` metadata."""
    import shutil as _shutil
    from jinja2 import Environment as _Env, FileSystemLoader as _Loader
    import math

    # Ref: 2 Hz sine for t in [0, 2], silence for t in [2, 4].
    # Actual: silence for t in [0, 2], 5 Hz sine for t in [2, 4].
    # → Windowed ref [0, 2] has a 2 Hz peak; actual [0, 2] is near-zero.
    # → Windowed ref [2, 4] is near-zero; actual [2, 4] has a 5 Hz peak.
    _n = 512
    _t_end = 4.0
    _times = [i * _t_end / (_n - 1) for i in range(_n)]
    _ref_vals = [
        (math.sin(2 * math.pi * 2.0 * t) if t <= 2.0 else 0.0)
        for t in _times
    ]
    _act_vals = [
        (math.sin(2 * math.pi * 5.0 * t) if t > 2.0 else 0.0)
        for t in _times
    ]
    leaf = _leaf(
        path="/metrics/children/0", metric="dominant-frequency",
        variable="osc",
        params={"peaks": []},
    )
    leaf["spectrum"] = {
        "ref_freq": [], "ref_mag": [],
        "act_freq": [], "act_mag": [],
        "peaks_declared": [], "paired_peaks": [],
        "detected_reference_peaks_hz": [],
    }
    leaf["cli_authoritative"] = False
    tree = {
        "kind": "combinator", "combinator": "and", "path": "/metrics",
        "passed": False, "label": "and[1]", "children": [leaf],
    }
    traj = {
        "index": 1, "name": "osc",
        "act_time": _times, "act_values": _act_vals,
        "ref_time": _times, "ref_values": _ref_vals,
    }
    ctx = {
        "model_id": "Fixture.WindowedDetect",
        "n_passed": 0, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [], "diagnostic_summaries": [], "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": {
            "osc": {"name": "osc", "trajectory": traj, "overlays": [],
                    "leaf_paths": ["/metrics/children/0"]},
        },
        "mode_schemas": {},
        "overlay_rows": [],
    }
    env = _Env(loader=_Loader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    _shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    context = playwright_browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")
    page.evaluate("activateLeaf(TREE_VIEW.children[0])")
    page.wait_for_function("document.querySelector('.peak-editor-ui')")

    # Set window to [0, 2] programmatically then Detect from Reference.
    # Expect ~2 Hz (authored in the first half).
    page.evaluate("""
        leafState['/metrics/children/0'].window = {start: 0.0, end: 2.0};
        // Trigger editor refresh so the live spectrum is recomputed.
        activateLeaf(TREE_VIEW.children[0]);
    """)
    page.wait_for_function("document.querySelector('.peak-editor-ui')")
    # Source: Reference (the default).
    page.locator(".peak-editor-ui button", has_text="Detect").first.click()
    pks = page.evaluate("leafState['/metrics/children/0'].params.peaks")
    freqs = sorted([pk["freq"] for pk in pks])
    assert any(abs(f - 2.0) < 0.8 for f in freqs), (
        f"expected 2 Hz peak from windowed reference, got {freqs}"
    )
    # Each seeded peak carries derived_from_window = {start: 0, end: 2}.
    assert all(pk.get("derived_from_window") == {"start": 0.0, "end": 2.0}
               for pk in pks)

    # Switch to Actual + window [2, 4] → expect ~5 Hz.
    page.evaluate("""
        leafState['/metrics/children/0'].params.peaks = [];
        leafState['/metrics/children/0'].window = {start: 2.0, end: 4.0};
        activateLeaf(TREE_VIEW.children[0]);
    """)
    page.wait_for_function("document.querySelector('.peak-editor-ui')")
    page.locator(".detect-source-select").first.select_option("act")
    page.locator(".peak-editor-ui button", has_text="Detect").first.click()
    pks2 = page.evaluate("leafState['/metrics/children/0'].params.peaks")
    freqs2 = sorted([pk["freq"] for pk in pks2])
    assert any(abs(f - 5.0) < 0.8 for f in freqs2), (
        f"expected 5 Hz peak from windowed actual, got {freqs2}"
    )
    assert all(pk.get("derived_from_window") == {"start": 2.0, "end": 4.0}
               for pk in pks2)
    context.close()


def test_visibility_toggle_syncs_across_mounts(rendered_page: Page):
    """Clicking the visibility checkbox in the full-tree mount updates
    the matching checkbox in the per-variable mount (both read/write
    the same leafState.visible, but pre-fix, sibling DOM drifted)."""
    full_sel = (
        '#nodes-full [data-path="/metrics/children/0"] > .node-header '
        '> input.node-visible'
    )
    var_sel = (
        '#nodes-0 [data-path="/metrics/children/0"] > .node-header '
        '> input.node-visible'
    )
    # Both start checked.
    assert rendered_page.locator(full_sel).first.is_checked()
    assert rendered_page.locator(var_sel).first.is_checked()
    # Uncheck in the full-tree mount.
    rendered_page.locator(full_sel).first.uncheck()
    # Per-variable mount's checkbox follows.
    assert rendered_page.locator(var_sel).first.is_checked() is False
    # Re-check in the per-variable mount syncs back.
    rendered_page.locator(var_sel).first.check()
    assert rendered_page.locator(full_sel).first.is_checked()


def test_tube_polygon_follows_reference_curve(tmp_path, playwright_browser):
    """Regression for the straight-line tube bug. A two-point tube at
    t=0 and t=10 with rel-mode width=0.05 on a curvy reference must
    produce a polygon whose upper bound tracks ``ref(t) * 1.05`` at every
    sample, not a straight line between the endpoint y-values."""
    import shutil as _shutil
    from jinja2 import Environment as _Env, FileSystemLoader as _Loader
    # Curvy ref — asymmetric so the bug would be obvious if present.
    import math
    ref_time = [i * 0.1 for i in range(101)]
    ref_values = [1.0 + 0.5 * math.sin(t) for t in ref_time]
    leaf = _leaf(
        path="/metrics/children/0", metric="tube", variable="y",
        params={
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
            "tube_points": [
                {"time": 0.0, "upper": 0.05, "lower": 0.05},
                {"time": 10.0, "upper": 0.05, "lower": 0.05},
            ],
        },
    )
    leaf["mode_values"] = {
        "tube_width_mode": "rel",
        "tube_rel": 0.05,
        "tube_abs": 0.0,
        "tube_min_width": 0.0,
        "tube_interpolation": "linear",
    }
    tree = {
        "kind": "combinator", "combinator": "and", "path": "/metrics",
        "passed": True, "label": "and[1]", "children": [leaf],
    }
    traj = {
        "index": 1, "name": "y",
        "act_time": ref_time, "act_values": ref_values,
        "ref_time": ref_time, "ref_values": ref_values,
    }
    ctx = {
        "model_id": "Fixture.TubeCurve",
        "n_passed": 1, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [], "diagnostic_summaries": [], "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": {
            "y": {"name": "y", "trajectory": traj, "overlays": [],
                  "leaf_paths": ["/metrics/children/0"]},
        },
        "mode_schemas": {},
        "overlay_rows": [],
    }
    env = _Env(loader=_Loader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    _shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")

    context = playwright_browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")

    # Call the resolver on a sample of grid times and compare to the
    # curve-following expected values.
    # Grid: t=0, 2, 5, 8, 10 — non-endpoint times must track ref(t)*1.05.
    bounds = page.evaluate("""(() => {
        const leaf = TREE_VIEW.children[0];
        const editor = MODE_PLOT_EDITORS['tube'];
        const grid = [0, 2, 5, 8, 10];
        return editor._resolveAllBoundsOnGrid(leaf, grid);
    })()""")
    # Expected: for each grid point, upper = ref(t) + 0.05 * |ref(t)| and
    # lower = ref(t) - 0.05 * |ref(t)|.
    for i, t in enumerate([0, 2, 5, 8, 10]):
        ref_t = 1.0 + 0.5 * math.sin(t)
        expected_upper = ref_t + 0.05 * abs(ref_t)
        expected_lower = ref_t - 0.05 * abs(ref_t)
        assert bounds["upper"][i] == pytest.approx(expected_upper, rel=1e-6), (
            f"upper at t={t}: got {bounds['upper'][i]} expected {expected_upper}"
        )
        assert bounds["lower"][i] == pytest.approx(expected_lower, rel=1e-6)
    # Sanity: the midpoint bound is NOT on the straight line between
    # endpoints (which would be the bug). Straight line at t=5 would be
    # (ref(0)*1.05 + ref(10)*1.05) / 2; curve-following is ref(5) * 1.05.
    ref0, ref5, ref10 = (
        1.0 + 0.5 * math.sin(0),
        1.0 + 0.5 * math.sin(5),
        1.0 + 0.5 * math.sin(10),
    )
    straight_line_mid = (ref0 * 1.05 + ref10 * 1.05) / 2.0
    curve_following_mid = ref5 * 1.05
    # The true midpoint must be on the curve, NOT on the straight line.
    assert abs(bounds["upper"][2] - curve_following_mid) < 1e-6
    assert abs(bounds["upper"][2] - straight_line_mid) > 0.1  # clearly distinct
    context.close()


def test_buildPatchData_no_noise_when_tube_leaf_untouched(
    tmp_path, playwright_browser,
):
    """Regression for D75-era patch noise. A tube leaf with spec values
    for tube_rel/tube_width_mode should NOT emit redundant 'add' ops
    when the user hasn't touched anything. Pre-fix, _extract_mode_values
    read defaults from diagnostics and clobbered the spec values in
    leafState.params, making buildPatchData think the user had changed
    them back to defaults."""
    import shutil as _shutil
    from jinja2 import Environment as _Env, FileSystemLoader as _Loader
    leaf = _leaf(
        path="/metrics/children/0", metric="tube", variable="y",
        params={
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
        },
    )
    # Simulate what _extract_mode_values now produces (spec_params win).
    leaf["mode_values"] = {
        "tube_width_mode": "rel",
        "tube_rel": 0.05,
        "tube_abs": 0.0,
        "tube_min_width": 0.0,
        "tube_interpolation": "linear",
    }
    tree = {
        "kind": "combinator", "combinator": "and", "path": "/metrics",
        "passed": True, "label": "and[1]", "children": [leaf],
    }
    traj = {
        "index": 1, "name": "y",
        "act_time": [0, 1, 2], "act_values": [0, 1, 0],
        "ref_time": [0, 1, 2], "ref_values": [0, 1, 0],
    }
    ctx = {
        "model_id": "Fixture.TubeQuietPatch",
        "n_passed": 1, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [], "diagnostic_summaries": [], "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": {
            "y": {"name": "y", "trajectory": traj, "overlays": [],
                  "leaf_paths": ["/metrics/children/0"]},
        },
        "mode_schemas": {},
        "overlay_rows": [],
    }
    env = _Env(loader=_Loader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    _shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    context = playwright_browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")

    # Before any user interaction — patch must be empty. Pre-fix this
    # produced five 'add' ops for mode-defaulted fields that weren't in
    # the spec (tube_abs, tube_interpolation, tube_min_width, tube_rel,
    # tube_width_mode) because init merged mode_values into params but
    # not into original_params.
    payload = page.evaluate("buildPatchData()")
    assert payload.get("patch", []) == [], (
        f"untouched tube leaf emitted spurious patch ops: {payload['patch']}"
    )
    # Explicit belt-and-suspenders: if a 'tube_rel' op did appear, its
    # value must not be 0 (the pre-fix symptom).
    for op in payload.get("patch", []):
        if isinstance(op, dict) and "tube_rel" in str(op.get("path", "")):
            assert op["value"] != 0, f"spurious tube_rel reset: {op}"
    context.close()


def test_declared_peaks_editor_activates_with_table(tmp_path, playwright_browser):
    """Dominant-frequency leaf → activating it mounts a spectrum subplot
    and a declared-peaks table. Peaks from leafState.params.peaks render
    as rows; tolerance-mode select shows; Detect button visible."""
    # Synthesize a fixture with a dominant-frequency leaf carrying a
    # declared peak + embedded spectrum.
    import shutil as _shutil
    from jinja2 import Environment as _Env, FileSystemLoader as _Loader
    leaf = _leaf(
        path="/metrics/children/0", metric="dominant-frequency",
        variable="osc",
        params={"peaks": [{"freq": 1.0, "tolerance": 0.01, "tolerance_mode": "rel"}]},
    )
    # Spectrum payload the editor reads from.
    leaf["spectrum"] = {
        "ref_freq": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "ref_mag":  [0.1, 0.2, 1.0, 0.3, 0.15, 0.1, 0.05],
        "act_freq": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "act_mag":  [0.1, 0.2, 1.0, 0.3, 0.15, 0.1, 0.05],
        "peaks_declared": [{"freq": 1.0, "tolerance": 0.01, "tolerance_mode": "rel"}],
        "paired_peaks": [
            {"declared_hz": 1.0, "matched_hz": 1.0, "delta": 0.0,
             "passed": True, "tolerance": 0.01, "tolerance_mode": "rel"},
        ],
        "detected_reference_peaks_hz": [1.0, 2.0, 3.0],
    }
    leaf["cli_authoritative"] = False
    # Wrap in a minimal tree.
    tree = {
        "kind": "combinator", "combinator": "and", "path": "/metrics",
        "passed": True, "label": "and[1]",
        "children": [leaf],
    }
    traj = {
        "index": 1, "name": "osc",
        "act_time": [0, 1, 2, 3], "act_values": [0, 1, 0, -1],
        "ref_time": [0, 1, 2, 3], "ref_values": [0, 1, 0, -1],
    }
    ctx = {
        "model_id": "Fixture.DominantFreq",
        "n_passed": 1, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [], "diagnostic_summaries": [], "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": {
            "osc": {"name": "osc", "trajectory": traj, "overlays": [],
                    "leaf_paths": ["/metrics/children/0"]},
        },
        "mode_schemas": {},
        "overlay_rows": [],
    }
    env = _Env(loader=_Loader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    _shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")

    context = playwright_browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")

    # Activate the dominant-frequency leaf via direct call (avoids plot
    # wait in this plot-less fixture).
    page.evaluate("activateLeaf(TREE_VIEW.children[0])")
    page.wait_for_function("document.querySelector('.peak-editor-ui')")

    # Spectrum subplot mounted (each mount gets one).
    assert page.locator(".spectrum-subplot").count() >= 1
    # Each mount gets a table with 1 header + 1 data row. Two mounts
    # (full-tree + per-variable) → 4 rows total.
    rows_per_table = 2
    n_tables = page.locator(".peak-editor-ui table").count()
    assert page.locator(".peak-editor-ui tr").count() == n_tables * rows_per_table
    # Detect button rendered (one per mount).
    assert page.locator(".peak-editor-ui button", has_text="Detect").count() == n_tables
    context.close()


def test_declared_peaks_detect_button_populates_table(tmp_path, playwright_browser):
    """Click 'Detect peaks from reference' → table fills with the top
    detected peaks; leafState.params.peaks reflects the detected list."""
    import shutil as _shutil
    from jinja2 import Environment as _Env, FileSystemLoader as _Loader
    leaf = _leaf(
        path="/metrics/children/0", metric="dominant-frequency",
        variable="osc",
        params={"peaks": []},  # empty — user hasn't declared yet
    )
    # Trajectory must be long enough for the live FFT (D76) to find real
    # peaks. Use a 3-sinusoid sum at 1, 3, 5 Hz — Detect should find
    # exactly these in order.
    import math
    _n = 512
    _t_end = 4.0  # 4s window × 5 Hz max = 20 cycles (plenty)
    _times = [i * _t_end / (_n - 1) for i in range(_n)]
    _values = [
        (3.0 * math.sin(2 * math.pi * 1.0 * t)
         + 2.0 * math.sin(2 * math.pi * 3.0 * t)
         + 1.0 * math.sin(2 * math.pi * 5.0 * t))
        for t in _times
    ]
    leaf["spectrum"] = {
        "ref_freq": [], "ref_mag": [],
        "act_freq": [], "act_mag": [],
        "peaks_declared": [],
        "paired_peaks": [],
        "detected_reference_peaks_hz": [],
    }
    leaf["cli_authoritative"] = False
    tree = {
        "kind": "combinator", "combinator": "and", "path": "/metrics",
        "passed": False, "label": "and[1]", "children": [leaf],
    }
    traj = {
        "index": 1, "name": "osc",
        "act_time": _times, "act_values": _values,
        "ref_time": _times, "ref_values": _values,
    }
    ctx = {
        "model_id": "Fixture.DominantFreqEmpty",
        "n_passed": 0, "sim_failed": False,
        "last_run_at": 0, "last_run_str": "",
        "warnings": [], "key_stats": {}, "ref_info": [], "sim_params": [],
        "statistics_sections": [], "diagnostic_summaries": [], "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [], "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": {
            "osc": {"name": "osc", "trajectory": traj, "overlays": [],
                    "leaf_paths": ["/metrics/children/0"]},
        },
        "mode_schemas": {},
        "overlay_rows": [],
    }
    env = _Env(loader=_Loader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    _shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    context = playwright_browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False))
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")

    # Activate + click Detect.
    page.evaluate("activateLeaf(TREE_VIEW.children[0])")
    page.wait_for_function("document.querySelector('.peak-editor-ui')")
    assert page.evaluate("leafState['/metrics/children/0'].params.peaks.length") == 0
    page.locator(".peak-editor-ui button", has_text="Detect").first.click()
    # Should have populated with the top 3 detected peaks (live FFT picks
    # them within one bin of the authored 1, 3, 5 Hz).
    n = page.evaluate("leafState['/metrics/children/0'].params.peaks.length")
    assert n == 3
    freqs = page.evaluate(
        "leafState['/metrics/children/0'].params.peaks.map(p => p.freq)"
    )
    freqs_sorted = sorted(freqs)
    # Tolerance of 0.5 Hz covers FFT bin resolution.
    assert abs(freqs_sorted[0] - 1.0) < 0.5
    assert abs(freqs_sorted[1] - 3.0) < 0.5
    assert abs(freqs_sorted[2] - 5.0) < 0.5
    # Each seeded peak carries derived_from_window metadata since this
    # editor stamps it on detect; window was never set, so metadata is
    # absent (null window = no provenance to record).
    has_window_metadata = page.evaluate(
        "leafState['/metrics/children/0'].params.peaks.some(p => p.derived_from_window)"
    )
    # No window set → no metadata.
    assert has_window_metadata is False
    context.close()


def test_visibility_toggle_does_not_affect_scoring(rendered_page: Page):
    """The visibility toggle is plot-only — the pass pill stays whatever
    the scorer computed regardless of the checkbox state."""
    # Uncheck — pass pill still present + still showing 'pass' (nrmse
    # on h was passing in the fixture).
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header '
        '> input.node-visible'
    ).first.uncheck()
    pill = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header '
        '> .node-status'
    ).first
    assert pill.is_visible()
    assert "pass" in (pill.get_attribute("class") or "").lower()


def test_reset_does_not_dirty_structure(rendered_page: Page):
    """Reset is a value-revert, not a structural edit — structureDirty
    should stay false if it was false before."""
    assert rendered_page.evaluate("structureDirty") is False
    # Edit + reset.
    inp = rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] input[data-field="tolerance"]'
    ).first
    inp.fill("0.05")
    inp.dispatch_event("input")
    rendered_page.locator(
        '#nodes-full [data-path="/metrics/children/0"] > .node-header .node-btn-reset'
    ).first.click()
    assert rendered_page.evaluate("structureDirty") is False
