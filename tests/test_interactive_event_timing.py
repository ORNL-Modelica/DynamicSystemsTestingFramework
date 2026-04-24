"""Playwright tests for the event-timing declared-events editor.

Covers the Medium-scope editor (D82): table + add + delete + detect.
Event-timing stays CLI-authoritative for pass/fail — no live JS
scorer. Users edit declared events in the browser, export the patch,
and rerun the CLI for authoritative results.

Pattern mirrors tests/test_interactive_range_window.py — the shared
fixture + render helpers come from test_interactive_playwright.py.
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


def _mode_controls_html_event(values: dict) -> str:
    """Minimal event-timing mode controls. Mirrors what mode_controls.py's
    render_schema_html would emit — just enough so the editor's slot
    mounts correctly.
    """
    tol = values.get("time_tolerance", 1e-3)
    cm = "checked" if values.get("count_must_match", True) else ""
    return (
        '<div class="mode-controls" data-mode="event-timing" data-variable="h">'
        '<label><span>Time tolerance</span>'
        f'<input type="number" step="any" data-field="time_tolerance" value="{tol}"></label>'
        f'<label><span>Counts must match</span>'
        f'<input type="checkbox" data-field="count_must_match" {cm}></label>'
        '<label class="mc-field mc-passthrough">'
        '<textarea data-field="events" data-passthrough="true" rows="2"></textarea>'
        '</label>'
        "</div>"
    )


def _context_with_event_timing_leaf(declared_events=None):
    """Fixture: one event-timing leaf on variable 'h' with a trajectory
    containing events at t=1.0 and t=2.0 (duplicate-time samples).
    """
    ctx = _fixture_context()
    # Override the first leaf to be event-timing.
    et_leaf = ctx["tree_view"]["children"][0]
    et_leaf["metric"] = "event-timing"
    et_leaf["mode_effective"] = "event-timing"
    et_leaf["variable"] = "h"
    params = {
        "time_tolerance": 0.01,
        "count_must_match": True,
        "events": declared_events,
    }
    et_leaf["params"] = params
    et_leaf["mode_values"] = dict(params)
    et_leaf["mode_controls_html"] = _mode_controls_html_event(params)
    et_leaf["cli_authoritative"] = True
    # Override h's trajectory to have duplicate-time events.
    t = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0]
    v = [0.0] * len(t)
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": list(t), "act_values": list(v),
        "ref_time": list(t), "ref_values": list(v),
    }
    ctx["trajectories"] = [ctx["variables_by_name"][k]["trajectory"]
                           for k in ctx["variables_by_name"]]
    return ctx


def _render_with_context(tmp_path: Path, ctx: dict) -> Path:
    """Like _render_report, but accepts a custom context."""
    from jinja2 import Environment, FileSystemLoader
    from test_interactive_playwright import _JS_SRC, _TEMPLATE_DIR
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path


# ---------------------------------------------------------------------------
# Task 3: table + add + delete
# ---------------------------------------------------------------------------

def test_event_timing_editor_mounts_on_leaf_click(tmp_path, playwright_browser):
    """Clicking the event-timing leaf in the tree should mount the
    editor in its .node-editor slot — the editor-specific container
    must be present after click."""
    ctx = _context_with_event_timing_leaf(declared_events=[])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Click the event-timing leaf's header to activate it.
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    # Editor slot should contain our event-editor container.
    mounted = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor'
    ).count()
    page.close()
    assert mounted >= 1, (
        "Event-timing editor container didn't mount after leaf click. "
        "MODE_PLOT_EDITORS['event-timing'] may not be defined or "
        "activate() isn't injecting the expected DOM class."
    )


def test_event_timing_editor_renders_existing_declared_events(
    tmp_path, playwright_browser,
):
    """Fixture starts with two declared events; the table should show
    two rows after the editor mounts."""
    ctx = _context_with_event_timing_leaf(declared_events=[
        {"time": 1.0, "tolerance": 0.01},
        {"time": 2.0},  # fallback tolerance
    ])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor tbody tr'
    ).count()
    page.close()
    assert rows == 2, f"Expected 2 rows in declared-events table, got {rows}"


def test_event_timing_editor_add_button_appends_row(
    tmp_path, playwright_browser,
):
    """Clicking '+ add event' should append a row and update leafState."""
    ctx = _context_with_event_timing_leaf(declared_events=[
        {"time": 1.0, "tolerance": 0.01},
    ])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'button.node-btn-add'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor tbody tr'
    ).count()
    events_len = page.evaluate("""
        () => (leafState['/metrics/children/0'].params.events || []).length
    """)
    page.close()
    assert rows == 2
    assert events_len == 2, (
        "Add button updated DOM but not leafState; click handler "
        "must mutate the events array AND refresh the table."
    )


def test_event_timing_editor_delete_button_removes_row(
    tmp_path, playwright_browser,
):
    """Each row has a delete button that removes it from the table AND
    from leafState.params.events."""
    ctx = _context_with_event_timing_leaf(declared_events=[
        {"time": 1.0, "tolerance": 0.01},
        {"time": 2.0, "tolerance": 0.02},
    ])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    # Click the first row's delete button.
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'tbody tr button.row-delete'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor tbody tr'
    ).count()
    remaining_time = page.evaluate("""
        () => {
            const evs = leafState['/metrics/children/0'].params.events || [];
            return evs.length === 1 ? Number(evs[0].time) : null;
        }
    """)
    page.close()
    assert rows == 1
    assert remaining_time == 2.0, (
        "Delete removed the wrong row (or didn't update leafState)."
    )
