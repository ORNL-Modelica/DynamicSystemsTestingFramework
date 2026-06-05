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
    _fixture_context,
    _leaf,
    _render_report,
    playwright_browser,
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
    tmp_path,
    playwright_browser,
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
        "index": 1,
        "name": "h",
        "act_time": t,
        "act_values": vals,
        "ref_time": t,
        "ref_values": vals,
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"] for k in ctx["variables_by_name"]
    ]
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
    tmp_path,
    playwright_browser,
):
    """Sine wave [0, 2π] has |x| up to 1.0 overall but |x| < 0.1 near π.
    With bounds [-0.1, 0.1] and window around π: in-window ✓, out-of-
    window ✗. Scorer must respect window → PASS.
    """
    ctx = _context_with_windowed_range(
        window_start=3.04,
        window_end=3.24,  # ~π ± 0.1 rad
        min_value=-0.1,
        max_value=0.1,
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
    tmp_path,
    playwright_browser,
):
    """Regression guard: narrow window that crosses π/2 (where sin = 1).
    With bounds [-0.1, 0.1] and window around π/2: in-window ✗.
    Scorer must FAIL — ensures we didn't break the fail-path.
    """
    ctx = _context_with_windowed_range(
        window_start=1.47,
        window_end=1.67,  # ~π/2 ± 0.1 rad (sin ≈ 1)
        min_value=-0.1,
        max_value=0.1,
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
        "index": 1,
        "name": "v",
        "act_time": t,
        "act_values": act,
        "ref_time": t,
        "ref_values": ref,
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"] for k in ctx["variables_by_name"]
    ]
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


# ---------------------------------------------------------------------------
# Task 3: nrmse + final-only live scorers respect window
# ---------------------------------------------------------------------------


def test_default_points_window_edits_rescore_in_browser(tmp_path, playwright_browser):
    """Final-only scorer should use the windowed final value, not the
    full-trace final. With a +0.1 offset on act and tolerance 0.05, the
    windowed scorer should FAIL (delta = 0.1 > 0.05). Before the live-port
    it would PASS because it used CLI-precomputed leaf.max_abs_error.
    """
    ctx = _fixture_context()
    # Rewire the nrmse leaf on 'h' to points (implicit-final mode) for
    # this test — empty/null points list triggers the points scorer's
    # final-value fallback, which is the behavior we want to exercise.
    ctx["tree_view"]["children"][0]["metric"] = "points"
    ctx["tree_view"]["children"][0]["mode_effective"] = "points"
    ctx["tree_view"]["children"][0]["params"] = {"tolerance": 0.05}
    ctx["tree_view"]["children"][0]["mode_values"] = {"tolerance": 0.05}
    ctx["tree_view"]["children"][0]["window"] = {"start": 0.0, "end": 1.0}
    ctx["tree_view"]["children"][0]["window_values"] = {"start": 0.0, "end": 1.0}
    # Offset act by +0.1 — makes the windowed final-value delta 0.1.
    traj = ctx["variables_by_name"]["h"]["trajectory"]
    traj["act_values"] = [v + 0.1 for v in traj["ref_values"]]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert result is False, (
        "Final-only live scorer with a 0.1 offset should FAIL at "
        "tolerance 0.05. If PASS, the scorer is still using the "
        "CLI-precomputed leaf.max_abs_error instead of re-deriving "
        "from the windowed arrays."
    )


def test_nrmse_window_edits_rescore_in_browser(tmp_path, playwright_browser):
    """NRMSE live scorer should recompute over the windowed arrays.
    Fixture: variable 'h' with a large act-offset only in [0, 1] (the
    narrow window), zero offset in [1, 3] (the rest).
    - Window [0, 1]: in-window NRMSE ≈ large → FAIL.
    - Window [1, 3]: in-window NRMSE ≈ 0 → PASS.
    Same leaf config, different window → opposite pill states.
    """
    ctx = _fixture_context()
    # NRMSE leaf is /metrics/children/0 on variable 'h'.
    ctx["tree_view"]["children"][0]["params"] = {"tolerance": 0.02}
    ctx["tree_view"]["children"][0]["mode_values"] = {"tolerance": 0.02}
    # Tailor the trajectory: ref and act identical in [1, 3], very
    # different in [0, 1].
    import numpy as np

    t = np.linspace(0, 3, 50).tolist()
    ref = [1 - 0.3 * x for x in t]
    act = [(r + 0.5) if x < 1.0 else r for x, r in zip(t, ref)]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1,
        "name": "h",
        "act_time": t,
        "act_values": act,
        "ref_time": t,
        "ref_values": ref,
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"] for k in ctx["variables_by_name"]
    ]
    # First render with window [0, 1] — expect FAIL.
    ctx["tree_view"]["children"][0]["window"] = {"start": 0.0, "end": 1.0}
    ctx["tree_view"]["children"][0]["window_values"] = {"start": 0.0, "end": 1.0}
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Scorer should FAIL with the high-offset region included.
    result_fail = page.evaluate(
        "() => recomputePassStates(TREE_VIEW)['/metrics/children/0']"
    )
    # Now move the window to [1, 3] by direct leafState mutation + rescore.
    result_pass = page.evaluate("""
        () => {
            leafState['/metrics/children/0'].window = {start: 1.0, end: 3.0};
            return recomputePassStates(TREE_VIEW)['/metrics/children/0'];
        }
    """)
    page.close()
    assert result_fail is False, (
        "NRMSE scorer with window [0,1] covering the 0.5 offset region "
        "should FAIL. If PASS, scorer is still using leaf.nrmse."
    )
    assert result_pass is True, (
        "NRMSE scorer with window [1,3] covering the zero-offset region "
        "should PASS. If FAIL, scorer is still using the full-trajectory "
        "NRMSE."
    )


# ---------------------------------------------------------------------------
# Task 4: Range plot decorator respects window (dual-style gray/red)
# ---------------------------------------------------------------------------


def test_range_plot_dual_style_when_window_set(tmp_path, playwright_browser):
    """When a range leaf has both window endpoints, the plot contribution
    should emit SIX shapes per bound-pair (gray outside + red inside ×
    min + max = 6), each with explicit x coordinates. Without a window,
    it should emit TWO (one min line + one max line, full-width in
    paper coords) — the pre-fix baseline.
    """
    ctx = _fixture_context()
    # Unwindowed range leaf — baseline count check.
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    baseline = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[1];  // the range leaf
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            const contrib = MODE_PLOT_CONTRIBUTIONS['range'](leaf, traj);
            return {
                shapes: contrib.shapes.length,
                firstShapeXref: contrib.shapes[0] && contrib.shapes[0].xref,
            };
        }
    """)
    page.close()
    assert baseline["shapes"] == 2, (
        "Baseline (no window): range should emit 2 shapes (min line + "
        "max line), full-width in paper coords."
    )
    assert baseline["firstShapeXref"] == "paper"

    # Now with a window — expect 6 shapes (3 segments per bound × 2 bounds).
    ctx["tree_view"]["children"][1]["window"] = {"start": 1.0, "end": 2.0}
    ctx["tree_view"]["children"][1]["window_values"] = {"start": 1.0, "end": 2.0}
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    windowed = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[1];
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            const contrib = MODE_PLOT_CONTRIBUTIONS['range'](leaf, traj);
            // Group shapes by dashed-line color so the test can assert
            // gray-outside / red-inside without being brittle about
            // exact shape ordering.
            const colors = contrib.shapes.map(s => (s.line || {}).color);
            const xrefs = contrib.shapes.map(s => s.xref);
            return {
                total: contrib.shapes.length,
                grayCount: colors.filter(c => c === '#9e9e9e').length,
                redCount: colors.filter(c => c === '#f44336').length,
                allXAxis: xrefs.every(xr => xr === 'x'),
            };
        }
    """)
    page.close()
    assert windowed["total"] == 6, (
        "Windowed range should emit 6 shapes: 3 segments per bound "
        "(pre-window gray + in-window red + post-window gray) × 2 bounds."
    )
    assert windowed["grayCount"] == 4, "4 gray segments (2 per bound × 2)"
    assert windowed["redCount"] == 2, "2 red in-window segments (1 per bound)"
    assert windowed["allXAxis"], (
        "All segments must use xref='x' with explicit time coords so "
        "they actually land in window coordinates — not 'paper'."
    )


def test_range_emits_invisible_trace_anchors_for_autorange(
    tmp_path,
    playwright_browser,
):
    """Plotly autorange ignores shape coordinates — only trace data
    drives the y-axis reset range. To make double-click reset include
    the declared min/max bounds, the range plot contribution must emit
    one invisible scatter point per declared bound at the bound's
    y-value. Without these anchors, an out-of-window max_value produces
    a double-click reset that snaps to the trajectory and hides the
    bound.
    """
    ctx = _fixture_context()
    # Bound far above the trajectory's [-0.01, 1.1] envelope so the
    # autorange difference is observable. min_value left at -0.01.
    ctx["tree_view"]["children"][1]["params"] = {
        "min_value": -0.01,
        "max_value": 5.0,
    }
    ctx["tree_view"]["children"][1]["mode_values"] = {
        "min_value": -0.01,
        "max_value": 5.0,
    }
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[1];
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            const contrib = MODE_PLOT_CONTRIBUTIONS['range'](leaf, traj);
            const ys = (contrib.traces || [])
                .flatMap(t => Array.isArray(t.y) ? t.y : []);
            const opacities = (contrib.traces || [])
                .map(t => (t.marker || {}).color);
            return {
                traceCount: (contrib.traces || []).length,
                ys,
                opacities,
            };
        }
    """)
    page.close()
    assert result["traceCount"] == 2, (
        "Expected 2 invisible trace anchors (one per declared bound). "
        f"Got {result['traceCount']}."
    )
    assert -0.01 in result["ys"] and 5.0 in result["ys"], (
        f"Anchor y-values should include both bounds. Got {result['ys']}."
    )
    assert all("rgba(0,0,0,0)" in c for c in result["opacities"]), (
        "Anchor markers must be fully transparent so they don't render. "
        f"Got marker colors: {result['opacities']}."
    )


# ---------------------------------------------------------------------------
# Task 5: Bug 3 — plot y-range resets after bound-edit sequence
# ---------------------------------------------------------------------------


def test_range_yaxis_resets_after_bound_contract(tmp_path, playwright_browser):
    """Sequence: render with max_value=0.5 -> edit to 5.0 -> edit back
    to 0.5. The y-axis range after the final edit should reflect the
    trajectory extent (~[-0.01, 1.1] per fixture bounds), NOT the
    historical max_value=5.0 extent.

    Note: ``commit()`` in interactive.js is a local closure inside
    ``activateLeaf``; it isn't globally reachable. Drive the same
    re-render path by calling ``renderVariablePlot`` directly (plus
    ``refreshPassStates`` + ``updateExport`` for parity with commit()).
    """
    ctx = _fixture_context()
    # Set a narrow max_value so the initial plot range is tight.
    # Override both params and mode_values — mode_values wins on init.
    ctx["tree_view"]["children"][1]["params"] = {
        "min_value": -0.01,
        "max_value": 0.5,
    }
    ctx["tree_view"]["children"][1]["mode_values"] = {
        "min_value": -0.01,
        "max_value": 0.5,
    }
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Wait for the initial plot render driven by DOMContentLoaded.
    page.wait_for_function(
        "() => { const el = document.getElementById('plot-0');"
        "        return el && el._mt_plotted === true; }"
    )
    final_range = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[1];  // range leaf on 'h'
            const idx = VARIABLE_INDEX[leaf.variable];
            const el = document.getElementById(`plot-${idx}`);
            // Step 1 (starting): max_value=0.5 (already set).
            // Step 2: push to 5.0 and re-render (mirrors commit()).
            leafState[leaf.path].params.max_value = 5.0;
            renderVariablePlot(leaf.variable, idx);
            refreshPassStates();
            updateExport();
            // Step 3: contract back to 0.5 and re-render.
            leafState[leaf.path].params.max_value = 0.5;
            renderVariablePlot(leaf.variable, idx);
            refreshPassStates();
            updateExport();
            // Prefer _fullLayout.yaxis.range — Plotly populates it even
            // when autorange is true, while el.layout.yaxis.range may be
            // undefined for autoranged plots.
            const fullY = el && el._fullLayout && el._fullLayout.yaxis;
            if (fullY && Array.isArray(fullY.range)) return fullY.range.slice();
            if (el && el.layout && el.layout.yaxis && Array.isArray(el.layout.yaxis.range))
                return el.layout.yaxis.range.slice();
            return null;
        }
    """)
    page.close()
    assert final_range is not None, "Plot did not render"
    upper = final_range[1]
    # After contracting bounds back to 0.5, the yaxis upper should NOT
    # still be stuck at ~5 (or even 1.5+). Trace max is 1.0; bound at
    # 0.5. A reasonable autorange is <= 1.5 (trace + breathing room).
    assert upper <= 1.5, (
        f"yaxis upper stuck at {upper} after contracting max_value from "
        f"5.0 back to 0.5. Expected <= 1.5 (trace extent + small "
        f"Plotly padding). Likely root cause: uirevision='keep' on "
        f"yaxis preserving the extended range from when max_value=5 was "
        f"a shape that pushed autorange."
    )


# ---------------------------------------------------------------------------
# Task 6: cross-metric window-edit sweep (regression matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric,initial_pass,after_narrow_pass",
    [
        # (metric_name, pass_with_no_window, pass_with_narrow_window_at_pi/2)
        # Each fixture is shaped so the full trace and the narrow window
        # [1.47, 1.67] give DIFFERENT pass states (except final-only, where
        # the point is that toggling the window does NOT flip a passing
        # scorer — guards against false failures leaking in).
        ("nrmse", False, True),  # full-range NRMSE big; in-window NRMSE = 0
        ("points", True, True),  # final values agree; both pass
        ("range", False, True),  # out-of-window violates; in-window ok
        ("tube", False, True),  # tight tube fails full trace; ok in window
    ],
)
def test_window_edit_rescores_every_mode(
    tmp_path,
    playwright_browser,
    metric,
    initial_pass,
    after_narrow_pass,
):
    """Matrix test: for each window-aware mode, toggling the window must
    update the pass pill. Guards against a future contributor adding a
    new metric that reads pre-computed CLI values instead of re-scoring
    on the windowed arrays.

    Event-timing and dominant-frequency are excluded: event-timing is
    CLI-authoritative by design; dominant-frequency has dedicated
    live-port tests from D75/D76.

    Fixture shapes are piecewise (not pure sine) so each metric gets
    clean pass/fail margins without sampling-grid float noise. The
    plan's pure-sine fixtures land marginal tube-width values on the
    same order as the offset (0.01 ≥ 0.01), which flips the narrow
    tube pill on a strict > comparison; piecewise ref = 1 vs 2 gives
    the scorer a clear 2× margin to decide on.
    """
    import numpy as np

    ctx = _fixture_context()
    # Rewire the primary leaf to the parameterized metric on variable 'h'.
    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = metric
    leaf["mode_effective"] = metric
    leaf["variable"] = "h"
    # Per-metric params. Override both `params` and `mode_values` —
    # leafState initializes from mode_values merged over params, so if
    # we only set params the default mode_values wins.
    if metric == "nrmse":
        p = {"tolerance": 0.02}
    elif metric == "points":
        p = {"tolerance": 0.02}
    elif metric == "range":
        p = {"min_value": -0.1, "max_value": 0.1}
    else:  # tube
        p = {"tube_rel": 0.005}
    leaf["params"] = dict(p)
    leaf["mode_values"] = dict(p)
    # No window initially; applied via leafState mutation after load.
    leaf["window"] = {}
    leaf["window_values"] = {}

    # Piecewise trajectories — the window [1.47, 1.67] covers indices
    # 24/25/26 on linspace(0, 6.283, 100). "In-window" means x in that
    # band; "out-of-window" means everywhere else. Each metric gets a
    # fixture where the narrow window makes the decision flip cleanly,
    # with a 2×+ margin to absorb any sampling-grid float noise.
    t = np.linspace(0, 6.283, 100).tolist()
    in_window = lambda x: 1.47 < x < 1.67
    if metric == "nrmse":
        # Ref = sin; act = ref in window, ref+0.3 outside. Full NRMSE
        # ~0.15 > 0.02 (fail); windowed NRMSE = 0 (pass).
        ref = [float(np.sin(x)) for x in t]
        act = [r if in_window(x) else r + 0.3 for x, r in zip(t, ref)]
    elif metric == "points":
        # Ref = act = sin — symmetric match; final delta = 0 < 0.02
        # both windowed and full. This param exists to guard against
        # a regression that makes points (implicit final-only) FAIL
        # under any window change (false negatives are as harmful as
        # false positives).
        ref = [float(np.sin(x)) for x in t]
        act = ref[:]
    elif metric == "range":
        # Act = 0 in window, 1 outside. Bounds [-0.1, 0.1]:
        # full trace has act=1 > 0.1 (fail); windowed act=0 ok (pass).
        # Set ref = act so the trajectory is consistent (range doesn't
        # consult ref for bounds, but overlays use it).
        act = [0.0 if in_window(x) else 1.0 for x in t]
        ref = act[:]
    else:  # tube
        # Ref = 2 in window, 1 outside. Act = ref + 0.008 everywhere.
        # With tube_rel=0.005:
        #   outside window: width = 0.005*1 = 0.005 < offset 0.008 → FAIL.
        #   inside window:  width = 0.005*2 = 0.010 > offset 0.008 → PASS.
        # 25% margin both ways — float-noise robust.
        ref = [2.0 if in_window(x) else 1.0 for x in t]
        act = [r + 0.008 for r in ref]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1,
        "name": "h",
        "act_time": t,
        "act_values": act,
        "ref_time": t,
        "ref_values": ref,
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"] for k in ctx["variables_by_name"]
    ]

    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Evaluate pass with no window, then apply the narrow window and
    # re-score. Both scores must execute the JS-side scorer.
    # Uses TREE_VIEW (script-scope global, not SPEC_TREE) and
    # leafState (script-scope const, not window.leafState) per the
    # actual JS module shape.
    results = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const initial = recomputePassStates(TREE_VIEW)[leaf.path];
            leafState[leaf.path].window = {start: 1.47, end: 1.67};
            const narrow = recomputePassStates(TREE_VIEW)[leaf.path];
            return {initial, narrow};
        }
    """)
    page.close()
    assert results["initial"] is initial_pass, (
        f"[{metric}] Full-trace pass expected={initial_pass}, "
        f"got={results['initial']}. Scorer may not be reading params "
        f"from the expected location."
    )
    assert results["narrow"] is after_narrow_pass, (
        f"[{metric}] Narrow-window pass expected={after_narrow_pass}, "
        f"got={results['narrow']}. Scorer is not respecting window — "
        f"either _sliceLeafTrajectory isn't wired into this metric's "
        f"scorer, or the scorer falls back to CLI value before "
        f"consulting the helper."
    )
