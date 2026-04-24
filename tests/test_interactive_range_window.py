"""Playwright tests for range metric + cross-metric window consistency.

Covers:
- `_sliceLeafTrajectory` helper correctness (Task 1)
- Range JS scorer respects window (Bug 1, Task 2)
- Tube JS scorer respects window (latent bug, Task 3)
- NRMSE + final-only live-port window-aware (Task 4)
- Range plot dual-style gray/red lines (Bug 2, Task 5)
- Plot y-range autorange reset on bound edit (Bug 3, Task 6)

Pattern mirrors tests/test_interactive_playwright.py — the _render_report
helper + fixture context are imported from there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page

from test_interactive_playwright import (  # noqa: E402
    _fixture_context, _leaf, _render_report, playwright_browser,
)


# ---------------------------------------------------------------------------
# Task 1: _sliceLeafTrajectory helper
# ---------------------------------------------------------------------------

def test_slice_leaf_trajectory_no_window_returns_full(tmp_path, playwright_browser):
    """With no window set, the helper returns trajectory arrays unchanged."""
    html_path = _render_report(tmp_path)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const leaf = {path: '/metrics/children/0', variable: 'h'};
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            leafState[leaf.path] = leafState[leaf.path] || {};
            leafState[leaf.path].window = {};
            const out = _sliceLeafTrajectory(leaf, traj);
            return {
                refLen: out.refTime.length,
                actLen: out.actTime.length,
                refT0: out.refTime[0],
                refTN: out.refTime[out.refTime.length - 1],
            };
        }
    """)
    page.close()
    # Fixture traj for 'h' is linspace(0, 3, 50) — 50 points total.
    assert result["refLen"] == 50
    assert result["actLen"] == 50
    assert result["refT0"] == 0.0
    assert result["refTN"] == 3.0


def test_slice_leaf_trajectory_with_window_clips_both_arrays(
    tmp_path, playwright_browser,
):
    """Setting window.start/end clips both ref and act arrays inclusively."""
    html_path = _render_report(tmp_path)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const leaf = {path: '/metrics/children/0', variable: 'h'};
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            leafState[leaf.path] = leafState[leaf.path] || {};
            leafState[leaf.path].window = {start: 1.0, end: 2.0};
            const out = _sliceLeafTrajectory(leaf, traj);
            return {
                refT0: out.refTime[0],
                refTN: out.refTime[out.refTime.length - 1],
                actT0: out.actTime[0],
                actTN: out.actTime[out.actTime.length - 1],
                refVals0: out.refValues[0],
                refValsN: out.refValues[out.refValues.length - 1],
            };
        }
    """)
    page.close()
    # Both endpoints inclusive; the linspace(0,3,50) grid has points at
    # roughly 1.0 and 2.0 (actually 0.980, 1.041, ..., 2.020 — so the
    # clip picks up points >= 1.0 and <= 2.0).
    assert result["refT0"] >= 1.0
    assert result["refTN"] <= 2.0
    assert result["actT0"] >= 1.0
    assert result["actTN"] <= 2.0
    # Ref values on variable 'h' are 1 - 0.3*t; at t=1.0 → 0.7, at t=2.0 → 0.4.
    assert 0.35 < result["refValsN"] < 0.45
    assert 0.65 < result["refVals0"] < 0.75


def test_slice_leaf_trajectory_open_ended_window(tmp_path, playwright_browser):
    """Only setting window.start (no end) clips from below; end unbounded."""
    html_path = _render_report(tmp_path)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const leaf = {path: '/metrics/children/0', variable: 'h'};
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            leafState[leaf.path] = leafState[leaf.path] || {};
            leafState[leaf.path].window = {start: 2.0};
            const out = _sliceLeafTrajectory(leaf, traj);
            return {refT0: out.refTime[0], refTN: out.refTime[out.refTime.length - 1]};
        }
    """)
    page.close()
    assert result["refT0"] >= 2.0
    assert result["refTN"] == 3.0  # unbounded on upper end → include trace end


# ---------------------------------------------------------------------------
# Task 2: range + tube JS scorers respect window
# ---------------------------------------------------------------------------

def _context_with_windowed_range(window_start, window_end, min_value, max_value):
    """Custom fixture: range leaf on variable 'h' with a narrow window,
    and a trajectory that VIOLATES bounds OUTSIDE the window but stays
    WITHIN bounds inside. If the scorer respects window: PASS. If not: FAIL.
    """
    ctx = _fixture_context()
    # Override the range leaf's params + window.
    # Note: leafState initializes from mode_values merged over params, so we
    # must override both or mode_values wins.
    range_leaf = ctx["tree_view"]["children"][1]
    range_leaf["params"] = {"min_value": min_value, "max_value": max_value}
    range_leaf["mode_values"] = {"min_value": min_value, "max_value": max_value}
    range_leaf["window"] = {"start": window_start, "end": window_end}
    range_leaf["window_values"] = {"start": window_start, "end": window_end}
    range_leaf["window_controls_html"] = (
        '<div class="window-controls" data-variable="h">'
        f'<input type="number" step="any" data-field="window_start" value="{window_start}">'
        f'<input type="number" step="any" data-field="window_end" value="{window_end}">'
        "</div>"
    )
    # Override h's trajectory: sine wave with amplitude 1 but narrow
    # window at the zero-crossing where |x| < 0.1.
    import numpy as np
    t = np.linspace(0, 6.283, 100).tolist()  # [0, 2π]
    vals = [float(np.sin(x)) for x in t]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": t, "act_values": vals,
        "ref_time": t, "ref_values": vals,
    }
    ctx["trajectories"] = [ctx["variables_by_name"][k]["trajectory"]
                           for k in ctx["variables_by_name"]]
    return ctx


def _render_with_context(tmp_path, ctx):
    """Like _render_report, but accepts a custom context."""
    import shutil
    from jinja2 import Environment, FileSystemLoader
    from test_interactive_playwright import _JS_SRC, _TEMPLATE_DIR
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path


def test_range_scorer_respects_window_passes_when_bounds_ok_in_window(
    tmp_path, playwright_browser,
):
    """Sine wave [0, 2π] has |x| up to 1.0 overall but |x| < 0.1 near π.
    With bounds [-0.1, 0.1] and window around π: in-window ✓, out-of-
    window ✗. Scorer must respect window → PASS.
    """
    ctx = _context_with_windowed_range(
        window_start=3.04, window_end=3.24,  # ~π ± 0.1 rad
        min_value=-0.1, max_value=0.1,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const passMap = recomputePassStates(TREE_VIEW);
            return passMap['/metrics/children/1'];  // the range leaf
        }
    """)
    page.close()
    assert result is True, (
        "Range scorer should PASS when bounds are satisfied within the "
        "window, even though the full trace has |x| > bounds outside. "
        "If this fails, MODE_SCORERS['range'] is still iterating the "
        "full trajectory instead of the window-clipped subset."
    )


def test_range_scorer_fails_when_in_window_violates_bounds(
    tmp_path, playwright_browser,
):
    """Regression guard: narrow window that crosses π/2 (where sin = 1).
    With bounds [-0.1, 0.1] and window around π/2: in-window ✗.
    Scorer must FAIL — ensures we didn't break the fail-path.
    """
    ctx = _context_with_windowed_range(
        window_start=1.47, window_end=1.67,  # ~π/2 ± 0.1 rad (sin ≈ 1)
        min_value=-0.1, max_value=0.1,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const passMap = recomputePassStates(TREE_VIEW);
            return passMap['/metrics/children/1'];
        }
    """)
    page.close()
    assert result is False, (
        "Range scorer should FAIL when the windowed portion of the "
        "trajectory exceeds bounds. This test guards the fail-path so "
        "we don't accidentally make the scorer too permissive."
    )


def test_tube_scorer_respects_window(tmp_path, playwright_browser):
    """Tube leaf on variable 'v'. Custom trajectory: ref=1 outside window,
    ref=2 inside window; act = ref + 0.008 everywhere. With tube_rel=0.005:
    - Outside window: 0.005 * 1 = 0.005 < 0.008 → FAIL.
    - Inside window: 0.005 * 2 = 0.010 > 0.008 → PASS.
    Regression guard: scorer must distinguish these by respecting window.
    """
    ctx = _fixture_context()
    # Tube leaf is at /metrics/children/2/children/0.
    # Note: leafState initializes from mode_values merged over params, so we
    # must override both or the default mode_values (tube_rel=0.05) wins.
    tube_leaf = ctx["tree_view"]["children"][2]["children"][0]
    tube_leaf["params"] = {"tube_rel": 0.005}
    tube_leaf["mode_values"] = {"tube_rel": 0.005}
    tube_leaf["window"] = {"start": 1.0, "end": 2.0}
    tube_leaf["window_values"] = {"start": 1.0, "end": 2.0}
    # Override v's trajectory: ref = 2 inside window [1.0, 2.0], 1 outside.
    # act = ref + 0.008 everywhere. With tube_rel=0.005:
    # outside (ref=1) → width=0.005 < 0.008 offset → FAIL;
    # inside (ref=2) → width=0.010 > 0.008 offset → PASS.
    import numpy as np
    t = np.linspace(0, 3, 50).tolist()
    ref = [2.0 if 1.0 <= x <= 2.0 else 1.0 for x in t]
    act = [r + 0.008 for r in ref]
    ctx["variables_by_name"]["v"]["trajectory"] = {
        "index": 1, "name": "v",
        "act_time": t, "act_values": act,
        "ref_time": t, "ref_values": ref,
    }
    ctx["trajectories"] = [ctx["variables_by_name"][k]["trajectory"]
                           for k in ctx["variables_by_name"]]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result_windowed = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/2/children/0']
    """)
    page.close()
    assert result_windowed is True, (
        "Tube scorer should PASS in the windowed region around π/2 where "
        "ref ≈ 2 makes tube_rel*|ref| wide enough. If this fails, the "
        "tube scorer is still iterating full ref_time."
    )
