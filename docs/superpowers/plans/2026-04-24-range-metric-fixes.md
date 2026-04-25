# Range Metric + Cross-Metric Window Consistency Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in DSTF's interactive HTML reporter (range FAIL-despite-in-bounds, bound lines not respecting window, Plotly y-range stuck after bound edit) and unify window handling across all 6 comparison modes' JS live scorers + plot decorators so cross-metric consistency is guaranteed.

**Architecture:** Extract a single `_sliceLeafTrajectory(leaf, traj)` helper that reads `leafState[leaf.path].window` and returns window-clipped trajectory arrays. Thread it through every JS live scorer and plot decorator that should be window-aware. Dominant-frequency already does this via `_sliceToWindow` — use it as the template. After all fixes, the cross-metric consistency table reads YES in every cell (except event-timing which stays CLI-authoritative by design).

**Tech Stack:** JavaScript (ES2017-ish, no build step — lives in `src/dstf/reporting/templates/interactive.js`), Plotly.js 2.x, Python 3.10+ (fixtures + Playwright drivers), pytest + pytest-playwright. No new deps.

**Root-cause investigation** (Phase 1 of systematic-debugging, already complete): See conversation history. Summary:
- Bug 1 confirmed at `interactive.js:122-133` — range scorer iterates full `traj.act_values` with no window clip.
- Bug 2 confirmed at `interactive.js:318-341` — range plot shapes use `xref:'paper', x0:0, x1:1` (full plot width).
- Bug 3 hypothesis only — `uirevision:'keep'` on yaxis at `2303/2314` may be preserving auto-range from prior shape extents. Needs browser instrumentation to nail.
- Cross-cutting: only dom-frequency is window-aware end-to-end in JS. nrmse + final-only use CLI-computed `leaf.nrmse` / `leaf.max_abs_error` so window edits silently no-op; tube + range iterate full trajectories.

**User decisions locked:**
- Bug 2 visual: **dual-style** — gray line segments outside window, red inside window (option b).
- Silent no-op fix: **live-port** nrmse + final-only JS scorers (not the stale-badge half-measure). All metrics become window-aware end-to-end in JS after this plan.

**Out of scope (per the user's broader roadmap):**
- Final-only point-based redesign (roadmap #4 — separate design discussion).
- Event-timing HTML editor (roadmap #3).
- Baseline-free NO_REF short-circuit (roadmap #5).
- Broad technical-debt / file-structure review (roadmap #6).

---

## File structure

### Modified (all JS; no Python package changes)
- `src/dstf/reporting/templates/interactive.js` — add helper, update scorers, update plot decorators, add Bug 3 instrumentation + fix attempt

### New
- `tests/test_interactive_range_window.py` — Playwright tests for Bug 1, Bug 2, Bug 3, and the cross-metric window-edit sweep

### Untouched
- `src/dstf/comparison/**` — CLI scorers already correct (they use `_slice_window` at `tree_eval.py:248-254`)
- All other tests

---

## Pre-flight

Before Task 1, ensure Playwright is installed and the browser binary is available. The CLAUDE memory notes a venv-drift caveat — `uv run` may pick the wrong Python. If Playwright tests skip unexpectedly, verify:

```bash
uv run which python
uv run python -c "import playwright; print(playwright.__version__)"
uv run playwright install chromium  # only if not already installed
```

Also confirm the starting state is green:

```bash
git log --oneline -1
uv run pytest -q
```

Expected: `78bd4da` (or later post-rename commit) at HEAD, 762 passed + 0 skipped, 0 failures. If the suite isn't clean, stop and report.

---

## Task 1: Extract `_sliceLeafTrajectory` helper (infrastructure, no behavior change)

**Goal:** Introduce the single shared helper. This task does NOT fix any user-visible bug yet — it's the foundation for Tasks 2-4. Zero behavior change; full suite still green. Establishing the infrastructure first per the user's standardization request — so when we fix range in Task 2, tube in Task 3, etc., they all go through one code path.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` (add helper near the existing `_sliceToWindow` around line 515)
- Test: `tests/test_interactive_range_window.py` (new file, helper-only tests)

- [ ] **Step 1: Create the test file with a helper-unit test**

Create `tests/test_interactive_range_window.py` with this initial content:

```python
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

from tests.test_interactive_playwright import (
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
            window.leafState = window.leafState || {};
            window.leafState[leaf.path] = window.leafState[leaf.path] || {};
            window.leafState[leaf.path].window = {};
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
            window.leafState = window.leafState || {};
            window.leafState[leaf.path] = window.leafState[leaf.path] || {};
            window.leafState[leaf.path].window = {start: 1.0, end: 2.0};
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
            window.leafState = window.leafState || {};
            window.leafState[leaf.path] = window.leafState[leaf.path] || {};
            window.leafState[leaf.path].window = {start: 2.0};
            const out = _sliceLeafTrajectory(leaf, traj);
            return {refT0: out.refTime[0], refTN: out.refTime[out.refTime.length - 1]};
        }
    """)
    page.close()
    assert result["refT0"] >= 2.0
    assert result["refTN"] == 3.0  # unbounded on upper end → include trace end
```

- [ ] **Step 2: Run the tests — they must FAIL because the helper doesn't exist yet**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_slice_leaf_trajectory"
```

Expected: 3 FAIL with JavaScript errors like "ReferenceError: _sliceLeafTrajectory is not defined" (visible in the Playwright evaluation result).

If the tests SKIP (Playwright not available), follow the pre-flight instructions to install Playwright, then re-run.

- [ ] **Step 3: Add the helper to interactive.js**

Open `src/dstf/reporting/templates/interactive.js`. Find `_sliceToWindow` (around line 515). Immediately after it (before `_computeFftSpectrum`), insert the new helper:

```javascript
function _sliceLeafTrajectory(leaf, traj) {
  // Read the leaf's current window (from leafState) and clip every
  // trajectory array to it. Returns {refTime, refValues, actTime,
  // actValues} — same shape as the raw trajectory, just windowed.
  //
  // Used by every MODE_SCORERS / MODE_PLOT_CONTRIBUTIONS entry that
  // needs window-awareness. Centralizing here so bugs in window
  // handling get fixed in ONE place, not six.
  //
  // If no window is set (both endpoints null/unset), returns the
  // trajectory unchanged — zero-cost fast path.
  const state = leafState[leaf.path] || {};
  const w = state.window || {};
  const s = w.start, e = w.end;
  const refTime = traj.ref_time || [];
  const refValues = traj.ref_values || [];
  const actTime = traj.act_time || [];
  const actValues = traj.act_values || [];
  const refSliced = _sliceToWindow(refTime, refValues, s, e);
  const actSliced = _sliceToWindow(actTime, actValues, s, e);
  return {
    refTime: refSliced.time,
    refValues: refSliced.values,
    actTime: actSliced.time,
    actValues: actSliced.values,
  };
}
```

- [ ] **Step 4: Re-run the tests; all 3 must PASS**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_slice_leaf_trajectory"
```

Expected: 3 PASS.

- [ ] **Step 5: Run the full suite to confirm zero behavior change**

```bash
uv run pytest -q
```

Expected: 765 passed + 0 skipped (762 previous + 3 new). If existing tests fail, the helper introduction broke something — investigate rather than paper over.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
feat(reporter): extract _sliceLeafTrajectory helper (window infra)

Add a shared JS helper that reads leafState[leaf.path].window and
returns the leaf's trajectory clipped to {start, end}. Zero call
sites yet — infrastructure for the range / tube / nrmse / final-only
window-awareness fixes in the next tasks.

Centralizing the window lookup + clip here means we can audit and
patch window handling in ONE place rather than six scattered call
sites. Mirrors the path _sliceToWindow already uses in the
dominant-frequency scorer.

Three helper-unit tests via Playwright verify: no-window passthrough,
inclusive [start, end] clipping, and open-ended (start-only) windows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Apply `_sliceLeafTrajectory` to range + tube JS scorers

**Goal:** Close Bug 1 (range FAIL despite in-bounds) by routing the range scorer through the helper. Apply the same fix to the tube scorer — which has the identical class of bug but hasn't been reported yet (the user would hit it the first time they set a window on a tube leaf). Both fixes are the same pattern, committed together because they're one conceptual change: "JS scorers for bound-check modes respect window."

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — `MODE_SCORERS['range']` (lines 122-133) and `MODE_SCORERS['tube']` (lines 134-182)
- Test: `tests/test_interactive_range_window.py` (append)

- [ ] **Step 1: Write the failing tests first**

Append to `tests/test_interactive_range_window.py`:

```python
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
    range_leaf = ctx["tree_view"]["children"][1]
    range_leaf["params"] = {"min_value": min_value, "max_value": max_value}
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
    from tests.test_interactive_playwright import _JS_SRC, _TEMPLATE_DIR
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
            const passMap = recomputePassStates(SPEC_TREE);
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
            const passMap = recomputePassStates(SPEC_TREE);
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
    """Tube leaf on variable 'v'. ref is 1 + sin(t), act is ref + 0.01
    everywhere. With tube_rel=0.005 (too tight for the 0.01 offset):
    - Full trajectory: FAIL (act > ref + 0.5% everywhere).
    - Narrow window where ref ≈ 2 (π/2): 0.005 * 2 = 0.01 ≥ offset → PASS.
    Regression guard: scorer must distinguish these by respecting window.
    """
    ctx = _fixture_context()
    # Tube leaf is at /metrics/children/2/children/0.
    tube_leaf = ctx["tree_view"]["children"][2]["children"][0]
    tube_leaf["params"] = {"tube_rel": 0.005}
    tube_leaf["window"] = {"start": 1.47, "end": 1.67}  # ~π/2 on fixture's t-grid
    tube_leaf["window_values"] = {"start": 1.47, "end": 1.67}
    # Fixture's t-grid is linspace(0, 3, 50) — so 1.47 to 1.67 picks a
    # cluster of points where sin(t) ≈ 1 (π/2 = 1.5708). At those points
    # ref = 1 + sin(t) ≈ 2, and tube_rel * |ref| ≈ 0.01 which equals the
    # act offset exactly — so the in-window check PASSES with tolerance.
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result_windowed = page.evaluate("""
        () => recomputePassStates(SPEC_TREE)['/metrics/children/2/children/0']
    """)
    page.close()
    assert result_windowed is True, (
        "Tube scorer should PASS in the windowed region around π/2 where "
        "ref ≈ 2 makes tube_rel*|ref| wide enough. If this fails, the "
        "tube scorer is still iterating full ref_time."
    )
```

- [ ] **Step 2: Run the new tests — expect FAIL**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_scorer or test_tube_scorer" 2>&1 | tail -30
```

Expected: `test_range_scorer_respects_window_passes_when_bounds_ok_in_window` FAILS (scorer currently ignores window). `test_tube_scorer_respects_window` FAILS for the same reason. `test_range_scorer_fails_when_in_window_violates_bounds` may PASS (whole trace violates, in-window also violates). That's fine — it's the regression guard.

- [ ] **Step 3: Update `MODE_SCORERS['range']` to use the helper**

In `src/dstf/reporting/templates/interactive.js`, find `MODE_SCORERS['range']` at line 122. Replace:

```javascript
  'range': (leaf) => {
    const p = (leafState[leaf.path] || {}).params || {};
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const values = traj.act_values || [];
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    for (const v of values) {
      if (mn !== null && v < mn) return false;
      if (mx !== null && v > mx) return false;
    }
    return true;
  },
```

With:

```javascript
  'range': (leaf) => {
    const p = (leafState[leaf.path] || {}).params || {};
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { actValues } = _sliceLeafTrajectory(leaf, traj);
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    for (const v of actValues) {
      if (mn !== null && v < mn) return false;
      if (mx !== null && v > mx) return false;
    }
    return true;
  },
```

- [ ] **Step 4: Update `MODE_SCORERS['tube']` to use the helper**

Find `MODE_SCORERS['tube']` at line 134. Locate the inner loop (the two places it references `traj.ref_time` / `traj.ref_values` / `traj.act_time` / `traj.act_values`). Replace:

```javascript
  'tube': (leaf) => {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    if (!traj.ref_time || !traj.ref_time.length) return !!leaf.passed;
    const p = (leafState[leaf.path] || {}).params || {};
    const rel = Number(p.tube_rel || 0);
    const abs = Number(p.tube_abs || 0);
    const minW = Number(p.tube_min_width || 0);
    const mode = p.tube_width_mode;
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];

    let widthsUpper, widthsLower;
    if (points.length > 0) {
      const normalized = points.map(pt => ({
        time: Number(pt.time ?? 0),
        upper: Number(pt.upper ?? pt.abs ?? pt.rel ?? 0),
        lower: Number(pt.lower ?? pt.abs ?? pt.rel ?? 0),
      })).sort((a, b) => a.time - b.time);
      const interp = (t, key) => {
        if (t <= normalized[0].time) return normalized[0][key];
        if (t >= normalized[normalized.length - 1].time) return normalized[normalized.length - 1][key];
        for (let i = 1; i < normalized.length; i++) {
          if (normalized[i].time >= t) {
            const f = (t - normalized[i - 1].time) / (normalized[i].time - normalized[i - 1].time);
            return normalized[i - 1][key] + f * (normalized[i][key] - normalized[i - 1][key]);
          }
        }
        return normalized[normalized.length - 1][key];
      };
      widthsUpper = traj.ref_time.map(t => Math.max(minW, interp(t, 'upper')));
      widthsLower = traj.ref_time.map(t => Math.max(minW, interp(t, 'lower')));
    } else {
      const w = traj.ref_values.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      widthsUpper = w;
      widthsLower = w;
    }

    const refTime = traj.ref_time;
    const refValues = traj.ref_values;
    for (let i = 0; i < refTime.length; i++) {
      const actV = _interpLinear(traj.act_time, traj.act_values, refTime[i]);
      if (actV > refValues[i] + widthsUpper[i]) return false;
      if (actV < refValues[i] - widthsLower[i]) return false;
    }
    return true;
  },
```

With:

```javascript
  'tube': (leaf) => {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    if (!traj.ref_time || !traj.ref_time.length) return !!leaf.passed;
    const p = (leafState[leaf.path] || {}).params || {};
    const rel = Number(p.tube_rel || 0);
    const abs = Number(p.tube_abs || 0);
    const minW = Number(p.tube_min_width || 0);
    const mode = p.tube_width_mode;
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];

    // Window-clip both ref and act before iterating. Act interp uses
    // the full-trajectory act arrays (so we interpolate onto windowed
    // refTime grid, but get correct values even if the nearest act
    // samples are just outside the window).
    const { refTime, refValues } = _sliceLeafTrajectory(leaf, traj);
    const actTimeFull = traj.act_time || [];
    const actValuesFull = traj.act_values || [];

    let widthsUpper, widthsLower;
    if (points.length > 0) {
      const normalized = points.map(pt => ({
        time: Number(pt.time ?? 0),
        upper: Number(pt.upper ?? pt.abs ?? pt.rel ?? 0),
        lower: Number(pt.lower ?? pt.abs ?? pt.rel ?? 0),
      })).sort((a, b) => a.time - b.time);
      const interp = (t, key) => {
        if (t <= normalized[0].time) return normalized[0][key];
        if (t >= normalized[normalized.length - 1].time) return normalized[normalized.length - 1][key];
        for (let i = 1; i < normalized.length; i++) {
          if (normalized[i].time >= t) {
            const f = (t - normalized[i - 1].time) / (normalized[i].time - normalized[i - 1].time);
            return normalized[i - 1][key] + f * (normalized[i][key] - normalized[i - 1][key]);
          }
        }
        return normalized[normalized.length - 1][key];
      };
      widthsUpper = refTime.map(t => Math.max(minW, interp(t, 'upper')));
      widthsLower = refTime.map(t => Math.max(minW, interp(t, 'lower')));
    } else {
      const w = refValues.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      widthsUpper = w;
      widthsLower = w;
    }

    for (let i = 0; i < refTime.length; i++) {
      const actV = _interpLinear(actTimeFull, actValuesFull, refTime[i]);
      if (actV > refValues[i] + widthsUpper[i]) return false;
      if (actV < refValues[i] - widthsLower[i]) return false;
    }
    return true;
  },
```

- [ ] **Step 5: Re-run the tests — all must PASS**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_scorer or test_tube_scorer"
```

Expected: 3 PASS.

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest -q
```

Expected: 768 passed (765 previous + 3 new), 0 failures.

If existing Playwright tests fail — particularly anything that exercises `recomputePassStates` with an unwindowed leaf — the helper's fast-path for empty windows may have broken. Investigate.

- [ ] **Step 7: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
fix(reporter): range + tube JS scorers respect leaf window

Bug 1 (range FAIL despite in-bounds): MODE_SCORERS['range'] iterated
traj.act_values directly, ignoring the leaf's window. Users who set
bounds tighter than the full trace's extent and then windowed to a
region where bounds ARE satisfied saw FAIL in the browser pill even
though the CLI (which pre-slices via tree_eval.py:_slice_window)
would have reported PASS on rerun.

Same class of bug was latent in MODE_SCORERS['tube'] — tube never hit
in the wild because users hadn't tried tube + window yet, but the
fix is identical: route the inner loop through the new
_sliceLeafTrajectory helper.

Three new Playwright tests: range-passes-in-window, range-fails-in-
window (regression guard), tube-passes-in-window.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Live-port NRMSE + final-only JS scorers (close silent no-op)

**Goal:** Currently nrmse and final-only use `leaf.nrmse` / `leaf.max_abs_error` — CLI-computed values baked in at report-generation time. Window edits in the browser have NO effect on the pill because the precomputed value doesn't change. This task live-ports both computations to JS using the new helper so the pill updates with every window edit, matching dominant-frequency's UX.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — `MODE_SCORERS['nrmse']` (lines 114-117), `MODE_SCORERS['final-only']` (lines 118-121)
- Test: `tests/test_interactive_range_window.py` (append)

- [ ] **Step 1: Write failing tests first**

Append to `tests/test_interactive_range_window.py`:

```python
# ---------------------------------------------------------------------------
# Task 3: nrmse + final-only live scorers respect window
# ---------------------------------------------------------------------------

def test_final_only_window_edits_rescore_in_browser(tmp_path, playwright_browser):
    """Final-only scorer should use the windowed final value, not the
    full-trace final. Fixture: variable 'h' ramp from 1.0 down to 0.1
    over [0, 3]. Window [0, 1]: ref_final = 0.7, act_final = 0.7
    (matched to ref for this test). Tolerance = 0.05.
    - No window: final-only uses leaf.max_abs_error (CLI-precomputed ~0).
    - Window [0, 1] (still matched): live-scorer sees last-in-window
      act vs last-in-window ref, delta = 0, PASS.
    - Window [0, 1] with act shifted +0.1: delta = 0.1 > 0.05, FAIL.
    """
    ctx = _fixture_context()
    # Rewire the nrmse leaf on 'h' to final-only for this test.
    ctx["tree_view"]["children"][0]["metric"] = "final-only"
    ctx["tree_view"]["children"][0]["mode_effective"] = "final-only"
    ctx["tree_view"]["children"][0]["params"] = {"tolerance": 0.05}
    ctx["tree_view"]["children"][0]["window"] = {"start": 0.0, "end": 1.0}
    ctx["tree_view"]["children"][0]["window_values"] = {"start": 0.0, "end": 1.0}
    # Offset act by +0.1 — makes the windowed final-value delta 0.1.
    traj = ctx["variables_by_name"]["h"]["trajectory"]
    traj["act_values"] = [v + 0.1 for v in traj["ref_values"]]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    result = page.evaluate("""
        () => recomputePassStates(SPEC_TREE)['/metrics/children/0']
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
    # Tailor the trajectory: ref and act identical in [1, 3], very
    # different in [0, 1].
    import numpy as np
    t = np.linspace(0, 3, 50).tolist()
    ref = [1 - 0.3 * x for x in t]
    act = [(r + 0.5) if x < 1.0 else r for x, r in zip(t, ref)]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": t, "act_values": act,
        "ref_time": t, "ref_values": ref,
    }
    ctx["trajectories"] = [ctx["variables_by_name"][k]["trajectory"]
                           for k in ctx["variables_by_name"]]
    # First render with window [0, 1] — expect FAIL.
    ctx["tree_view"]["children"][0]["window"] = {"start": 0.0, "end": 1.0}
    ctx["tree_view"]["children"][0]["window_values"] = {"start": 0.0, "end": 1.0}
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Scorer should FAIL with the high-offset region included.
    result_fail = page.evaluate(
        "() => recomputePassStates(SPEC_TREE)['/metrics/children/0']"
    )
    # Now move the window to [1, 3] by direct leafState mutation + rescore.
    result_pass = page.evaluate("""
        () => {
            leafState['/metrics/children/0'].window = {start: 1.0, end: 3.0};
            return recomputePassStates(SPEC_TREE)['/metrics/children/0'];
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
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_final_only or test_nrmse_window" 2>&1 | tail -20
```

Expected: both tests FAIL. final-only's assertion fails because the current scorer uses `leaf.max_abs_error` (0 in the fixture). nrmse's assertion fails because it uses precomputed `leaf.nrmse`.

- [ ] **Step 3: Replace `MODE_SCORERS['nrmse']`**

In `interactive.js`, find `MODE_SCORERS['nrmse']` at line 114. Replace:

```javascript
  'nrmse': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    return leaf.nrmse < tol;
  },
```

With:

```javascript
  'nrmse': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    if (refTime.length < 2) {
      // Not enough windowed points to compute a meaningful NRMSE —
      // fall through to the CLI value. Covers the case where the
      // user narrows the window below the sampling rate.
      return leaf.nrmse < tol;
    }
    // NRMSE = sqrt(mean((act - ref)^2)) / (max(ref) - min(ref)).
    // Interpolate act onto ref's time grid so we score on a shared
    // time axis (matches the CLI's signal-range normalization).
    let sq = 0;
    for (let i = 0; i < refTime.length; i++) {
      const aV = _interpLinear(actTime, actValues, refTime[i]);
      const d = aV - refValues[i];
      sq += d * d;
    }
    const rmse = Math.sqrt(sq / refTime.length);
    let refMin = refValues[0], refMax = refValues[0];
    for (const v of refValues) {
      if (v < refMin) refMin = v;
      if (v > refMax) refMax = v;
    }
    const range = refMax - refMin;
    // Zero-range (flat reference) → use RMSE directly, same convention
    // as the CLI's _compare_trajectories degenerate-signal handling.
    const nrmse = range > 0 ? rmse / range : rmse;
    return nrmse < tol;
  },
```

- [ ] **Step 4: Replace `MODE_SCORERS['final-only']`**

Find `MODE_SCORERS['final-only']` at line 118 (after the nrmse replacement, now around line 118+). Replace:

```javascript
  'final-only': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    return leaf.max_abs_error < tol;
  },
```

With:

```javascript
  'final-only': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    if (!refTime.length || !actTime.length) {
      // No samples in window → no final value to compare. Match CLI's
      // behavior (falls through to the cached max_abs_error).
      return leaf.max_abs_error < tol;
    }
    const refFinal = refValues[refValues.length - 1];
    const actFinal = actValues[actValues.length - 1];
    return Math.abs(actFinal - refFinal) < tol;
  },
```

- [ ] **Step 5: Re-run tests — both must PASS**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_final_only or test_nrmse_window"
```

Expected: 2 PASS.

- [ ] **Step 6: Full suite**

```bash
uv run pytest -q
```

Expected: 770 passed (768 previous + 2 new), 0 failures. Prior tests may exercise nrmse / final-only with zero window — the helper's fast-path must make the new scorers produce identical answers to `leaf.nrmse` / `leaf.max_abs_error` in that case. If any existing test fails, check whether the fast-path falls through correctly on unwindowed leaves.

- [ ] **Step 7: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
fix(reporter): live-port nrmse + final-only JS scorers

Close the silent no-op: previously, window edits on nrmse + final-only
leaves had zero effect on the pill because the scorer read
leaf.nrmse / leaf.max_abs_error (CLI values frozen at report time).

Now both scorers use _sliceLeafTrajectory to get windowed arrays and
recompute in the browser. Matches dom-frequency's live-port UX — after
this commit, every mode except event-timing is window-aware end-to-end
in JS. Event-timing stays CLI-authoritative by design (event pairing
is non-trivial).

Fallback to CLI values when window produces < 2 samples (nrmse) or
0 samples (final-only) — matches the CLI's degenerate-case handling.

Two Playwright tests: final-only offset fails on windowed comparison,
nrmse flips pass/fail based on which region the window covers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Range plot contribution — dual-style gray-outside / red-inside (Bug 2)

**Goal:** Fix Bug 2 per the user's visual preference. When a window is set on a range leaf, emit THREE line segments per bound: gray from trace start → window start, red from window start → window end, gray from window end → trace end. When no window, keep the single full-width red line.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — `MODE_PLOT_CONTRIBUTIONS['range']` (lines 318-341)
- Test: `tests/test_interactive_range_window.py` (append)

- [ ] **Step 1: Write a failing test**

Append:

```python
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
            const leaf = SPEC_TREE.children[1];  // the range leaf
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
            const leaf = SPEC_TREE.children[1];
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
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_plot_dual_style"
```

Expected: FAIL. Assertions like `windowed["total"] == 6` will fail because the current code always emits 2 shapes regardless of window.

- [ ] **Step 3: Update `MODE_PLOT_CONTRIBUTIONS['range']`**

In `interactive.js`, find `MODE_PLOT_CONTRIBUTIONS['range']` at line 318. Replace the entire entry with:

```javascript
  'range': (leaf, traj) => {
    const p = leafState[leaf.path] ? leafState[leaf.path].params : {};
    const state = leafState[leaf.path] || {};
    const w = state.window || {};
    const hasWindow = (w.start != null && w.start !== ''
                     && w.end != null && w.end !== '');

    // Build one bound's shapes — returns an array of 1 or 3 line shapes.
    // When no window: a single full-width (paper-coord) red dashed line.
    // When windowed: three segments in data coords — gray pre-window,
    // red in-window, gray post-window. Named shapes let
    // MODE_PLOT_EDITORS['range'] match plotly_relayout drag events back
    // to the right params field by path — only the in-window RED segment
    // is draggable (so users interact with the authoritative segment).
    const buildBoundShapes = (yVal, nameSuffix) => {
      if (!Number.isFinite(yVal)) return [];
      if (!hasWindow) {
        return [{
          type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#f44336', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}:${leaf.path}`,
        }];
      }
      // Window-aware: three segments, all in data coords so they sit
      // under the right regions of the x axis.
      const refTime = traj.ref_time || traj.act_time || [];
      const tStart = refTime.length ? refTime[0] : 0;
      const tEnd = refTime.length ? refTime[refTime.length - 1] : 1;
      const wStart = Number(w.start);
      const wEnd = Number(w.end);
      return [
        // Pre-window gray segment.
        {
          type: 'line', xref: 'x', x0: tStart, x1: wStart, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#9e9e9e', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}_pre:${leaf.path}`,
        },
        // In-window red segment — the authoritative one.
        {
          type: 'line', xref: 'x', x0: wStart, x1: wEnd, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#f44336', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}:${leaf.path}`,
        },
        // Post-window gray segment.
        {
          type: 'line', xref: 'x', x0: wEnd, x1: tEnd, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#9e9e9e', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}_post:${leaf.path}`,
        },
      ];
    };

    const shapes = [];
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    if (mn !== null) shapes.push(...buildBoundShapes(mn, 'min'));
    if (mx !== null) shapes.push(...buildBoundShapes(mx, 'max'));
    return { traces: [], shapes };
  },
```

- [ ] **Step 4: Re-run the dual-style test — must PASS**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_plot_dual_style"
```

Expected: PASS.

- [ ] **Step 5: Full suite**

```bash
uv run pytest -q
```

Expected: 771 passed (770 previous + 1 new). Check whether any existing range-related tests exercise the plot contribution directly — they may need updating if they inspected shape count under new behavior. Read the failure message carefully; if the test was exercising the OLD 2-shape baseline without a window, it should still pass (unwindowed path unchanged). If a test used to assert shape.xref === 'paper' after setting a window, that test needs its expectations updated — but per the plan's scope, we shouldn't have any such pre-existing tests. If we do, flag and stop.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
fix(reporter): range plot bound lines respect window (Bug 2)

Dual-style: when a window is set, emit three line segments per bound
— gray outside window, red inside — all in data coords (xref='x').
When no window, fall back to the single full-width paper-coord red
line (preserves the pre-fix look for the common case).

Named shapes: only the in-window red segment keeps the legacy name
pattern `range_{min,max}:path` — that's what MODE_PLOT_EDITORS['range']
matches on for shape-drag events. Gray pre/post segments get distinct
suffixes (`_pre`, `_post`) so they're not accidentally targeted.

Playwright test asserts shape count (2 unwindowed, 6 windowed) plus
color distribution (4 gray + 2 red for the windowed case).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Bug 3 — instrument + first-attempt fix for Plotly y-range stuck

**Goal:** Bug 3's root cause is a hypothesis, not a confirmed defect. Use Playwright to capture the yaxis.range before, during, and after a bound-edit sequence (0.5 → 5.0 → 0.5) and assert the correct behavior. Apply a targeted fix (explicit `Plotly.relayout(el, {'yaxis.autorange': true})` after any bound commit) that matches the most likely mechanism. If the captured data shows a different mechanism, the test will fail with enough info for a follow-up rather than silently succeeding with the wrong fix.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — add autorange reset in the range bound-commit flow (around line 2140 where editors commit bound edits) and in any tube-point-commit flow that extends the shape area (for consistency)
- Test: `tests/test_interactive_range_window.py` (append)

- [ ] **Step 1: Write the characterization test first**

Append:

```python
# ---------------------------------------------------------------------------
# Task 5: Bug 3 — plot y-range resets after bound-edit sequence
# ---------------------------------------------------------------------------

def test_range_yaxis_resets_after_bound_contract(tmp_path, playwright_browser):
    """Sequence: render with max_value=0.5 → edit to 5.0 → edit back
    to 0.5. The y-axis range after the final edit should reflect the
    trajectory extent (~[-0.01, 1.1] per fixture bounds), NOT the
    historical max_value=5.0 extent.
    """
    ctx = _fixture_context()
    # Set a narrow max_value so the initial plot range is tight.
    ctx["tree_view"]["children"][1]["params"] = {
        "min_value": -0.01, "max_value": 0.5,
    }
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Wait for initial plot render.
    page.wait_for_function("document.querySelector('.plotly') !== null")
    # Sequence: 0.5 → 5.0 → 0.5 via direct leafState mutation + commit.
    final_range = page.evaluate("""
        () => {
            const leaf = SPEC_TREE.children[1];
            // Step 1 (starting): max_value=0.5 (already set). Record the
            // starting yaxis range.
            const el = document.querySelector('[data-variable="h"] .plotly');
            // Step 2: push to 5.0 and commit.
            leafState[leaf.path].params.max_value = 5.0;
            commit(leaf);
            // Step 3: contract back to 0.5 and commit.
            leafState[leaf.path].params.max_value = 0.5;
            commit(leaf);
            // Return the current yaxis.range after the second commit.
            return el && el.layout && el.layout.yaxis
                ? el.layout.yaxis.range.slice()
                : null;
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
```

- [ ] **Step 2: Run — either it FAILS (Bug 3 confirmed, need fix) or PASSES (Bug 3 doesn't reproduce here — need different reproduction). Report which.**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_yaxis_resets"
```

If FAIL: proceed to Step 3 (apply the fix). Note the actual `upper` value from the assertion message — that's your characterization of the bug.

If PASS: Bug 3 doesn't reproduce via direct leafState mutation + commit. The reproduction likely requires interactive shape-drag (Plotly relayout events). STOP and report to the user — they'll need to provide a more detailed reproduction sequence before we apply a speculative fix. Do NOT apply the fix blindly; systematic-debugging Phase 3 says "If you don't understand X, say so" — this is that case.

- [ ] **Step 3 (only if Step 2 FAILED): Apply the autorange-reset fix**

Find the `commit(leaf)` function in `interactive.js`. It's called after any state mutation that should trigger re-render. Grep for its definition:

```bash
grep -n "^function commit\|^  function commit" src/dstf/reporting/templates/interactive.js
```

Find the line where it triggers plot re-render (likely via `renderAllPlots()` or `renderVariable()`). Just before that re-render call, add:

```javascript
  // Bug 3 fix: after any bound/shape edit commits, force yaxis
  // autorange so the range contracts when bounds move back inward.
  // Without this, `uirevision: 'keep'` preserves whatever axis extent
  // was established when a wider shape was in place — stale.
  // Range-mode shapes don't themselves contribute to autorange
  // calculation (Plotly only autoranges on traces), BUT user mouse
  // interaction that happened while the wider shape was visible does,
  // and uirevision locks that in.
  try {
    document.querySelectorAll('.plotly-graph-div').forEach(el => {
      if (el && el._mt_plotted && typeof Plotly !== 'undefined') {
        Plotly.relayout(el, { 'yaxis.autorange': true });
      }
    });
  } catch (e) {
    // Non-fatal; the UI continues with whatever range Plotly decides.
    console.warn('yaxis autorange reset failed:', e);
  }
```

(If `commit()` isn't the right place — e.g., if the plot-render happens in a separate function called only for the active variable — adapt: put the reset inside the per-variable render function, just before the `Plotly.react` call around line 2321. The key is: every bound edit must walk through this code path.)

- [ ] **Step 4: Re-run the test — must PASS**

```bash
uv run pytest tests/test_interactive_range_window.py -v -k "test_range_yaxis_resets"
```

Expected: PASS. If it still FAILs, the autorange reset isn't hitting the right element or `uirevision` is overriding it. Investigate: try `{'yaxis.autorange': true, 'yaxis.uirevision': Date.now()}` (force a new UI revision) in place of just `autorange: true`. Per systematic-debugging Phase 4.5: if this is the 3rd fix attempt and still broken, STOP and question whether `uirevision: 'keep'` is the right policy.

- [ ] **Step 5: Full suite**

```bash
uv run pytest -q
```

Expected: 772 passed (771 previous + 1 new). The autorange reset fires on every commit — verify that existing tests with manually-zoomed axes still pass (if any). Zoom-preservation was the reason for `uirevision: 'keep'`; the fix should only force autorange when it was already autoranging, not override user zoom. Read test failures carefully; if the tube-drag tests fail because autorange-reset now fires during tube drags, the fix is too broad and needs scoping to range-only commits.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
fix(reporter): reset yaxis autorange on bound-edit commit (Bug 3)

Bug 3 hypothesis confirmed via characterization test: after a 0.5 →
5.0 → 0.5 bound-edit sequence, yaxis.range stayed stuck at the
historical upper extent instead of contracting to match the now-
narrower bound. Root cause: uirevision='keep' preserves axis state
across Plotly.react calls, including state that was adopted to fit
a temporarily-wider shape.

Fix: in commit(), force yaxis.autorange:true via Plotly.relayout on
every plotted graph div. Autorange computes from traces (which
haven't changed extent), so this restores the trace-based range
whenever bounds contract. User-initiated zoom is preserved
separately by Plotly's own zoom-event handling — uirevision still
protects that.

Playwright characterization test asserts the final yaxis upper is
<= 1.5 (trace extent + padding) after the 0.5 → 5.0 → 0.5 sequence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Adjust the commit message if Step 2's test actually PASSED — in that case the task doesn't commit a fix; document the finding and stop.)

---

## Task 6: Cross-metric window-edit sweep — Playwright regression matrix

**Goal:** Lock in the cross-metric consistency guarantee with a parameterized Playwright matrix. For each metric × window configuration, assert that the scorer re-runs and produces the expected pass/fail. This is the test that catches any future regression where a new contributor adds a 7th metric without making it window-aware — the test fixture iterates all modes and fails if any mode's pill is stale after a window edit.

**Files:**
- Test: `tests/test_interactive_range_window.py` (append)

- [ ] **Step 1: Add the sweep test**

Append:

```python
# ---------------------------------------------------------------------------
# Task 6: cross-metric window-edit sweep (regression matrix)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metric,initial_pass,after_narrow_pass", [
    # (metric_name, pass_with_no_window, pass_with_narrow_window_at_pi/2)
    # Sine wave trajectory; each mode's config is chosen so the full
    # trace and the narrow window give DIFFERENT pass states.
    ("nrmse", False, True),   # full-range NRMSE big; in-window NRMSE = 0
    ("final-only", True, True),  # symmetric match; both pass
    ("range", False, True),   # full trace violates; in-window ok
    ("tube", False, True),    # tight tube fails full trace; ok in window
])
def test_window_edit_rescores_every_mode(
    tmp_path, playwright_browser, metric, initial_pass, after_narrow_pass,
):
    """Matrix test: for each window-aware mode, toggling window must
    update the pass pill. Guards against a future contributor adding
    a new metric that reads pre-computed CLI values instead of
    re-scoring on the windowed arrays.
    """
    import numpy as np
    ctx = _fixture_context()
    # Replace the primary leaf with one of the parameterized metric.
    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = metric
    leaf["mode_effective"] = metric
    leaf["variable"] = "h"
    if metric == "nrmse" or metric == "final-only":
        leaf["params"] = {"tolerance": 0.02}
    elif metric == "range":
        leaf["params"] = {"min_value": -0.1, "max_value": 0.1}
    elif metric == "tube":
        leaf["params"] = {"tube_rel": 0.005}
    leaf["window"] = {}
    leaf["window_values"] = {}
    # Trajectory: for nrmse/final-only, act = ref exactly (always pass).
    # Actually override per-metric to make initial_pass match:
    t = np.linspace(0, 6.283, 100).tolist()
    if metric == "nrmse":
        # Ref sine, act sine+0.3 to make full-range NRMSE > 0.02;
        # in narrow window at π/2 act+ref both ≈ 1 → small offset → low NRMSE
        # BUT we want in-window to PASS so pick act = ref in a small band
        # and offset outside. That's easier:
        ref = [float(np.sin(x)) for x in t]
        act = [r if 1.47 < x < 1.67 else r + 0.3 for r, x in zip(ref, t)]
    elif metric == "final-only":
        ref = [float(np.sin(x)) for x in t]
        act = ref[:]
    elif metric == "range":
        ref = [float(np.sin(x)) for x in t]
        act = ref[:]
    else:  # tube
        ref = [1.0 + float(np.sin(x)) for x in t]
        act = [r + 0.01 for r in ref]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": t, "act_values": act,
        "ref_time": t, "ref_values": ref,
    }
    ctx["trajectories"] = [ctx["variables_by_name"][k]["trajectory"]
                           for k in ctx["variables_by_name"]]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    # Evaluate pass with no window, then apply the narrow window and
    # re-score. Both re-scores must execute the JS-side scorer.
    results = page.evaluate("""
        () => {
            const leaf = SPEC_TREE.children[0];
            const initial = recomputePassStates(SPEC_TREE)[leaf.path];
            leafState[leaf.path].window = {start: 1.47, end: 1.67};
            const narrow = recomputePassStates(SPEC_TREE)[leaf.path];
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
```

- [ ] **Step 2: Run — must PASS for all 4 parameterizations**

```bash
uv run pytest tests/test_interactive_range_window.py::test_window_edit_rescores_every_mode -v
```

Expected: 4 PASS (one per metric). If any specific metric fails, the fix in Tasks 2 or 3 may not cover all code paths — go back and check. Dom-frequency is not in the matrix because it already had its own tests via prior work; add a fifth parameterization if you want defense-in-depth coverage.

- [ ] **Step 3: Full suite**

```bash
uv run pytest -q
```

Expected: 776 passed (772 previous + 4 parameterized), 0 failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
test(reporter): cross-metric window-edit sweep regression matrix

Parameterized Playwright test: for each of nrmse / final-only /
range / tube, assert that applying a narrow window flips the pass
pill to the expected state. Guards against future contributors
adding a 7th metric that reads CLI-precomputed values instead of
re-scoring on windowed arrays.

Event-timing and dom-frequency are excluded: event-timing is
CLI-authoritative by design; dom-frequency already has live-port
tests from D75/D76.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Rollback plan

Unlikely to need, but if anything goes catastrophically wrong mid-plan:

```bash
git log --oneline 78bd4da..HEAD
git reset --hard 78bd4da  # or whichever commit represents your pre-plan state
```

Then diagnose before re-attempting.

---

## Scope reminders

**This plan does:**
- Extract `_sliceLeafTrajectory` helper.
- Fix range JS scorer (Bug 1).
- Fix latent tube JS scorer bug.
- Live-port nrmse + final-only JS scorers (silent no-op fix).
- Dual-style range plot bound lines (Bug 2).
- Instrument + fix Plotly y-range autorange on bound edit (Bug 3).
- Add Playwright regression matrix for cross-metric window handling.

**This plan does NOT do:**
- Touch any CLI / Python scoring code — the CLI is already correct.
- Redesign final-only into point-based mode (roadmap #4).
- Build an event-timing HTML editor (roadmap #3).
- Short-circuit NO_REF for baseline-free modes (roadmap #5).
- Refactor file / method structure (roadmap #6).
- Live-port event-timing (CLI-authoritative by design).
- Change the tube editor's `_resolveAllBoundsOnGrid` or `PointPlotEditor` internals.

If a reviewer pushes to expand scope, the answer is "not in this plan — log it as a follow-up in ideas.md."
