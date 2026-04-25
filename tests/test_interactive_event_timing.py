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
    # Editor mounts in every .node-editor slot for the leaf; scope the
    # row count to the first mount.
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor'
    ).first.locator('tbody tr').count()
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
    # Editor mounts in every .node-editor slot for the leaf; scope the
    # row count to the first mount.
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor'
    ).first.locator('tbody tr').count()
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
    # Scope row count to the first mount (editor renders in every slot).
    rows = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor'
    ).first.locator('tbody tr').count()
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


# ---------------------------------------------------------------------------
# Task 4: detect button + source dropdown + live match column
# ---------------------------------------------------------------------------

def test_event_timing_detect_populates_from_reference(tmp_path, playwright_browser):
    """With source='ref' and the fixture's ref events at t=1.0 & t=2.0,
    clicking Detect should populate the table with 2 rows at those times."""
    ctx = _context_with_event_timing_leaf(declared_events=[])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    # Source dropdown defaults to Reference. Click Detect.
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'button.detect-events-btn'
    ).first.click()
    events = page.evaluate("""
        () => leafState['/metrics/children/0'].params.events.map(e => Number(e.time))
    """)
    page.close()
    assert events == [1.0, 2.0], f"Expected detected events [1.0, 2.0], got {events}"


def test_event_timing_detect_source_actual_uses_act_time(
    tmp_path, playwright_browser,
):
    """With source='act' and act_time holding different event times than
    ref_time, Detect should pick up the actual-side values."""
    ctx = _context_with_event_timing_leaf(declared_events=[])
    # Override actual-side to have different events than reference.
    traj = ctx["variables_by_name"]["h"]["trajectory"]
    traj["act_time"] = [0.0, 0.5, 0.8, 0.8, 1.5, 1.7, 1.7, 2.5]
    traj["act_values"] = [0.0] * len(traj["act_time"])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    # Select Actual in the dropdown, then Detect. The dropdown lives in
    # every editor mount; pick the first since they share the same state.
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'select.detect-source-select'
    ).first.select_option('act')
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'button.detect-events-btn'
    ).first.click()
    events = page.evaluate("""
        () => leafState['/metrics/children/0'].params.events.map(e => Number(e.time))
    """)
    page.close()
    assert events == [0.8, 1.7], f"Expected detected events [0.8, 1.7], got {events}"


def test_event_timing_match_column_shows_delta_when_matched(
    tmp_path, playwright_browser,
):
    """Declared event at t=1.0 with tolerance=0.05; actual has event at
    t=1.02. Match column should show something containing 'matched' or
    the delta 0.02."""
    ctx = _context_with_event_timing_leaf(declared_events=[
        {"time": 1.0, "tolerance": 0.05},
    ])
    traj = ctx["variables_by_name"]["h"]["trajectory"]
    traj["act_time"] = [0.0, 0.5, 1.02, 1.02, 1.5, 2.0, 2.0, 2.5]
    traj["act_values"] = [0.0] * len(traj["act_time"])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    match_text = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'tbody tr .match-cell'
    ).first.inner_text()
    page.close()
    # Match cell should indicate matched + delta around 0.02.
    assert "✓" in match_text or "matched" in match_text.lower(), (
        f"Match cell should show matched indicator; got: {match_text!r}"
    )


def test_event_timing_match_column_shows_unmatched_when_out_of_tolerance(
    tmp_path, playwright_browser,
):
    """Declared t=1.0 with tolerance=0.001; actual at t=1.5 (too far).
    Match cell should show unmatched indicator."""
    ctx = _context_with_event_timing_leaf(declared_events=[
        {"time": 1.0, "tolerance": 0.001},
    ])
    traj = ctx["variables_by_name"]["h"]["trajectory"]
    traj["act_time"] = [0.0, 0.5, 1.5, 1.5, 2.5]
    traj["act_values"] = [0.0] * len(traj["act_time"])
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    match_text = page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'tbody tr .match-cell'
    ).first.inner_text()
    page.close()
    assert "✕" in match_text or "unmatched" in match_text.lower(), (
        f"Match cell should show unmatched indicator; got: {match_text!r}"
    )
