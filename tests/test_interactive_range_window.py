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
