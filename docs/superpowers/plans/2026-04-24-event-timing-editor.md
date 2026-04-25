# Event-Timing HTML Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a declared-events editor to the interactive HTML reporter for the `event-timing` comparison mode — a table UI where users can author expected event instants (with per-event tolerances) and populate the table via "🔍 Detect events" with a "Detect from: Reference | Actual" dropdown. Mirrors the dominant-frequency declared-peaks editor pattern (D75/D76).

**Architecture:** Two coordinated layers. (1) Python: new `events: Optional[list[dict]]` field on `EventTimingConfig`. When None → existing auto-detect behavior (unchanged). When set → use the declared list as the ref-side event set, pair each declared event with the nearest actual-side auto-detected event within the declared event's own tolerance. Per-event `tolerance` falls back to the leaf's `time_tolerance`. (2) JavaScript: new `MODE_PLOT_EDITORS['event-timing']` IIFE — declared-events table in the leaf's editor slot with `+ add event` / `🔍 Detect events` / per-row numeric inputs / per-row delete. No live JS pass/fail rescoring; event-timing stays CLI-authoritative for pass/fail per `cli_authoritative` flag.

**Tech Stack:** Python 3.10+, JavaScript (ES2017-ish, no build step), pytest, pytest-playwright, Plotly.js 2.x. No new deps.

**Scope (Medium):** Table + detect + numeric editing per row. NO draggable plot markers (Full scope — deferred). NO live JS scorer (CLI-authoritative). NO changes to existing auto-detect path when `events` is unset (no regression risk for existing event-timing tests).

**Pre-known plan corrections from prior tasks** (apply inline wherever the pattern appears):
1. Playwright test imports: `from test_interactive_playwright import (...)` — NOT `from tests.test_interactive_playwright`.
2. `leafState` is a script-scope const in interactive.js, NOT `window.leafState`.
3. Global tree variable is `TREE_VIEW`, NOT `SPEC_TREE`.
4. `initLeafState()` merges `leaf.params` with `leaf.mode_values` (mode_values wins); fixture params overrides need BOTH.
5. Don't rely on specific linspace-grid values — use piecewise-constant or hand-tuned trajectories for deterministic tests.

**Out of scope (per user roadmap):**
- Draggable diamond markers on the trajectory plot (Full scope).
- Live JS pass/fail rescoring (stays CLI-authoritative).
- Final-only point-based redesign (roadmap #4).
- Broad tech-debt review (roadmap #6).

---

## File structure

### Modified (Python)
- `src/dstf/comparison/modes.py` — add `events` field to `EventTimingConfig`, thread through `EventTimingMode.compare`.
- `src/dstf/comparison/comparator.py` — update `_compare_event_timing` to accept declared events and match each to nearest actual-side event within its tolerance.
- `src/dstf/reporting/ui/mode_controls.py` — no schema changes needed (`events` field serializes to `passthrough` automatically, same as dom-frequency's `peaks`). Confirm during Task 3.

### Modified (JavaScript)
- `src/dstf/reporting/templates/interactive.js` — new `MODE_PLOT_EDITORS['event-timing']` IIFE (~150 lines), slotted between range editor (ends line ~1604) and dom-frequency editor (starts line 1605). Sibling to the existing three.

### New tests
- Additions to `tests/test_event_and_freq_modes.py` — CLI-side declared-events scoring tests.
- Additions to `tests/test_export_schema.py` — assert the new `events` field serializes via passthrough.
- New file `tests/test_interactive_event_timing.py` — Playwright tests for the editor UI.

### Untouched
- `MODE_SCORERS` (event-timing stays absent — CLI-authoritative).
- `MODE_PLOT_CONTRIBUTIONS['event-timing']` (the vertical-line overlay on the trajectory plot stays as-is).
- `_detectEvents` helper at interactive.js:740 (already correct; editor uses it).
- `test_interactive_playwright.py` fixtures (extend in place for event-timing tests).

---

## Pre-flight

Before Task 1, confirm starting state:

```bash
git log --oneline -1
uv run pytest -q
```

Expected: `959a92d` (last range-fix commit) at HEAD or later, 776 passed + 0 skipped, 0 failures. If the suite isn't clean, stop and report.

---

## Task 1: Python — `events` field + declared-events scoring path

**Goal:** Extend `EventTimingConfig` and `_compare_event_timing` so a declared list of events drives scoring when present. Existing auto-detect path stays unchanged (no regression). Add CLI-side tests.

**Files:**
- Modify: `src/dstf/comparison/modes.py` — `EventTimingConfig` (lines 199-223), `EventTimingMode.compare` (lines 316-321)
- Modify: `src/dstf/comparison/comparator.py` — `_compare_event_timing` (lines 542-591)
- Modify: `tests/test_event_and_freq_modes.py` — append declared-events test class

- [ ] **Step 1: Write the failing CLI tests first**

Append to `tests/test_event_and_freq_modes.py` (after the existing `TestEventTimingMode` class):

```python
class TestEventTimingDeclaredEvents:
    """Declared-events semantics: user supplies the reference-side event
    list explicitly; each declared event matches against the nearest
    actual-side auto-detected event within its own tolerance.
    """

    def test_declared_events_match_when_actual_within_tolerance(self):
        # Two declared events at t=1.0 and t=2.0. Actual has events at
        # t=1.005 and t=1.998 (both within 0.01 tolerance). PASS.
        ref_t = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])  # no events in ref
        act_t = np.array([0.0, 0.5, 1.005, 1.005, 1.5, 1.998, 1.998, 2.5])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(
            time_tolerance=0.01,
            events=[{"time": 1.0}, {"time": 2.0}],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["ref_event_count"] == 2  # from declared
        assert result.diagnostics["act_event_count"] == 2
        assert result.diagnostics["max_time_delta"] < 0.01

    def test_declared_events_fail_when_actual_missing(self):
        # Two declared events; actual has only one matching event.
        ref_t = np.array([0.0, 1.0, 2.0])
        act_t = np.array([0.0, 0.999, 0.999, 2.5])  # event at ~1.0 matches; no event at ~2.0
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(
            time_tolerance=0.01,
            events=[{"time": 1.0}, {"time": 2.0}],
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["ref_event_count"] == 2
        assert result.diagnostics["act_event_count"] == 1

    def test_declared_events_per_event_tolerance_overrides_global(self):
        # Declared event at t=1.0 with a wide per-event tolerance (0.5)
        # wins over the global strict tolerance (0.01). Actual event at
        # t=1.3 matches only with the per-event override.
        ref_t = np.array([0.0, 1.0, 2.0])
        act_t = np.array([0.0, 1.3, 1.3, 2.0])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(
            time_tolerance=0.01,
            events=[{"time": 1.0, "tolerance": 0.5}],
            count_must_match=False,  # actual has one event, declared has one
        ))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["max_time_delta"] == pytest.approx(0.3, abs=1e-9)

    def test_declared_events_empty_list_passes_with_empty_actual(self):
        # Degenerate: declared = [] (user says "no events expected"),
        # actual also has no events → PASS.
        ref_t = np.array([0.0, 0.5, 1.0])
        act_t = np.array([0.0, 0.5, 1.0])
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(events=[]))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert result.passed
        assert result.diagnostics["ref_event_count"] == 0
        assert result.diagnostics["act_event_count"] == 0

    def test_declared_events_empty_list_fails_with_events_in_actual(self):
        # Declared = [] but actual has events → FAIL (unexpected events).
        ref_t = np.array([0.0, 0.5, 1.0])
        act_t = np.array([0.0, 0.5, 0.5, 1.0])  # event at 0.5
        ref_v = np.zeros_like(ref_t)
        act_v = np.zeros_like(act_t)
        mode = EventTimingMode(EventTimingConfig(events=[]))
        result = mode.compare(ref_t, ref_v, act_t, act_v)
        assert not result.passed
        assert result.diagnostics["act_event_count"] == 1
```

- [ ] **Step 2: Run the new tests — all 5 must FAIL**

```bash
uv run pytest tests/test_event_and_freq_modes.py::TestEventTimingDeclaredEvents -v
```

Expected: 5 FAIL with `TypeError: __init__() got an unexpected keyword argument 'events'` (the field doesn't exist yet).

- [ ] **Step 3: Add the `events` field to `EventTimingConfig`**

In `src/dstf/comparison/modes.py`, find `EventTimingConfig` at line 199. The existing class has `time_tolerance` and `count_must_match`. Add a new `events` field after `count_must_match`. Replace the entire class with:

```python
@dataclass(frozen=True)
class EventTimingConfig:
    """Configuration for event-timing comparison (4.C.1).

    When ``events`` is None (default), both reference and actual event
    instants are auto-detected from duplicate-time samples (Modelica
    convention) and paired by index. When ``events`` is provided, the
    declared list becomes the authoritative reference-side event set —
    the actual signal is still auto-detected, but each declared event
    must find a nearest actual event within its own tolerance window.
    Mirrors the dominant-frequency declared-peaks semantics (D75).
    """
    time_tolerance: float = field(
        default=1e-3,
        metadata={
            "label": "Time tolerance (s)",
            "help": (
                "Default max time-shift between paired reference/actual "
                "events. Per-event overrides in the declared ``events`` "
                "list take precedence when present."
            ),
        },
    )
    count_must_match: bool = field(
        default=True,
        metadata={
            "label": "Event counts must match",
            "help": (
                "If checked, reference and actual must fire the same number "
                "of events. Unchecked allows pairs-that-exist comparisons "
                "even when extra/missing events appear."
            ),
        },
    )
    events: Optional[list[dict]] = field(
        default=None,
        metadata={
            "label": "Declared events",
            "help": (
                "Declared reference-side events. Each entry has a ``time`` "
                "(seconds) and an optional ``tolerance`` (seconds; falls "
                "back to the leaf's ``time_tolerance`` if omitted). When "
                "None, events are auto-detected from duplicate-time samples "
                "in ``ref_time``. Authored via the table editor in the "
                "interactive HTML reporter."
            ),
        },
    )
```

- [ ] **Step 4: Thread `events` through `EventTimingMode.compare`**

In the same file at line 316, replace `EventTimingMode.compare`:

```python
    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_event_timing(
            ref_time, act_time,
            time_tolerance=self.config.time_tolerance,
            count_must_match=self.config.count_must_match,
            declared_events=self.config.events,
        )
```

- [ ] **Step 5: Update `_compare_event_timing` to handle declared events**

In `src/dstf/comparison/comparator.py` at line 542, replace the entire `_compare_event_timing` function with:

```python
def _compare_event_timing(
    ref_time: np.ndarray,
    act_time: np.ndarray,
    time_tolerance: float = 1e-3,
    count_must_match: bool = True,
    declared_events: Optional[list[dict]] = None,
) -> VariableComparison:
    """Compare event instants between reference and actual signals (4.C.1).

    Two paths:

    * ``declared_events is None`` (default): events are auto-detected
      from duplicate-time samples in BOTH arrays, then paired by index.
      Pass when ``count_must_match`` is satisfied and every pair's time-
      delta is within ``time_tolerance``.

    * ``declared_events is not None``: the declared list IS the reference
      event set. The actual signal is still auto-detected; each declared
      event claims the nearest actual event within its own tolerance
      (``event["tolerance"]`` if set, else ``time_tolerance``). An
      unclaimed declared event fails. Unclaimed actual events only fail
      if ``count_must_match`` is set (and declared count != actual count).

    The score (``nrmse`` field — repurposed for reporting uniformity) is
    the max event-time delta across matched pairs.
    """
    act_boundaries = _find_event_boundaries(act_time)
    act_events = [float(act_time[b[0]]) for b in act_boundaries]

    if declared_events is None:
        # Legacy auto-detect path — unchanged behavior.
        ref_boundaries = _find_event_boundaries(ref_time)
        ref_events = [float(ref_time[b[0]]) for b in ref_boundaries]

        n = min(len(ref_events), len(act_events))
        max_delta = 0.0
        delta_at = 0.0
        for i in range(n):
            d = abs(ref_events[i] - act_events[i])
            if d > max_delta:
                max_delta = d
                delta_at = ref_events[i]

        counts_match = len(ref_events) == len(act_events)
        passed = max_delta <= time_tolerance and (counts_match or not count_must_match)

        return VariableComparison(
            index=0, name="", passed=passed,
            nrmse=max_delta, rmse=max_delta, signal_range=0.0,
            max_abs_error=max_delta, max_abs_error_time=delta_at,
            reference_final=float("nan"), actual_final=float("nan"),
            mode="event-timing",
            diagnostics={
                "ref_event_count": len(ref_events),
                "act_event_count": len(act_events),
                "max_time_delta": max_delta,
                "time_tolerance": time_tolerance,
                "counts_match": counts_match,
            },
        )

    # Declared-events path.
    declared_count = len(declared_events)
    actual_count = len(act_events)
    counts_match = declared_count == actual_count
    max_delta = 0.0
    delta_at = 0.0
    # Track which actual events have been claimed so we don't double-match.
    claimed = [False] * actual_count
    all_matched = True
    for e in declared_events:
        target = float(e["time"])
        tol = float(e.get("tolerance") if e.get("tolerance") is not None else time_tolerance)
        # Find nearest unclaimed actual event within tolerance.
        best_idx = -1
        best_d = float("inf")
        for j, at in enumerate(act_events):
            if claimed[j]:
                continue
            d = abs(at - target)
            if d <= tol and d < best_d:
                best_d = d
                best_idx = j
        if best_idx < 0:
            all_matched = False
            continue
        claimed[best_idx] = True
        if best_d > max_delta:
            max_delta = best_d
            delta_at = target

    passed = all_matched and (counts_match or not count_must_match)

    return VariableComparison(
        index=0, name="", passed=passed,
        nrmse=max_delta, rmse=max_delta, signal_range=0.0,
        max_abs_error=max_delta, max_abs_error_time=delta_at,
        reference_final=float("nan"), actual_final=float("nan"),
        mode="event-timing",
        diagnostics={
            "ref_event_count": declared_count,
            "act_event_count": actual_count,
            "max_time_delta": max_delta,
            "time_tolerance": time_tolerance,
            "counts_match": counts_match,
        },
    )
```

Also make sure `from typing import Optional` is imported at the top of comparator.py (probably already present — skip if so; add if missing).

- [ ] **Step 6: Re-run the 5 new tests; all must PASS**

```bash
uv run pytest tests/test_event_and_freq_modes.py::TestEventTimingDeclaredEvents -v
```

Expected: 5 PASS.

- [ ] **Step 7: Run the full event-timing test class and full suite**

```bash
uv run pytest tests/test_event_and_freq_modes.py -v
uv run pytest -q
```

Expected: all existing `TestEventTimingMode` tests still PASS (no regression on auto-detect path). Full suite: 781 passed (776 previous + 5 new) + 0 skipped, 0 failures.

- [ ] **Step 8: Commit**

```bash
git status --short
git add src/dstf/comparison/modes.py src/dstf/comparison/comparator.py tests/test_event_and_freq_modes.py
git commit -m "$(cat <<'EOF'
feat(event-timing): declared-events scoring path in CLI

Add `events: Optional[list[dict]]` field to EventTimingConfig. When
None (default), the auto-detect + pair-by-index path is unchanged —
no regression for existing event-timing tests. When set, the declared
list is the authoritative reference-side event set; each declared
event claims the nearest actual-side auto-detected event within its
own tolerance (falls back to time_tolerance). Per-event tolerance
lets users specify "event N can drift more than others."

Mirrors dominant-frequency's declared-peaks semantics: explicit user-
authored list wins over auto-detection when provided.

Five new CLI tests cover: basic declared match, missing actual,
per-event tolerance override, empty-declared+empty-actual, empty-
declared+populated-actual.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Schema + UI plumbing — confirm `events` passthrough, keep `has_plot_editor=False`

**Goal:** The auto-derived schema needs to emit `events` as a `passthrough` field (same as dom-frequency's `peaks`). This is automatic via `mode_controls._field_type()` which falls back to `passthrough` for `list[dict]` types — so no code changes expected. This task is mostly verification + a test to lock it in. Mark event-timing as editor-enabled in the registration (same as dom-frequency; the editor mounts in the leaf's .node-editor slot and doesn't require the "plot editor" flag).

**Files:**
- Test: `tests/test_export_schema.py` — append an assertion that `event-timing` schema includes `events` as passthrough
- Test: `tests/test_mode_controls.py` — append a render test confirming `events` field doesn't blow up

- [ ] **Step 1: Read the existing schema-export test to mirror its pattern**

```bash
grep -n "peaks\|dominant-frequency\|passthrough" tests/test_export_schema.py | head -20
```

Note: find the test that asserts dominant-frequency's `peaks` field is passthrough. Mirror the structure for event-timing's `events`.

- [ ] **Step 2: Write the failing schema test**

Append to `tests/test_export_schema.py`:

```python
def test_event_timing_schema_includes_events_as_passthrough():
    """The declared-events field must export as a passthrough type so
    the interactive HTML gets a raw-JSON fallback renderer *and* can be
    overridden by the JS-side MODE_PLOT_EDITORS table UI."""
    from dstf.reporting.ui.mode_controls import emit_mode_schemas
    schemas = emit_mode_schemas()
    event_timing = schemas.get("event-timing")
    assert event_timing is not None, "event-timing schema missing"
    fields = {f["name"]: f for f in event_timing.get("fields", [])}
    assert "events" in fields, (
        "event-timing should export an 'events' field (declared events "
        "for the reporter's table editor)."
    )
    assert fields["events"]["type"] == "passthrough", (
        f"events should be passthrough (list[dict]), got {fields['events']['type']}"
    )
```

- [ ] **Step 3: Run — likely PASSES already (auto-derive handles it)**

```bash
uv run pytest tests/test_export_schema.py::test_event_timing_schema_includes_events_as_passthrough -v
```

If PASSES: great, the auto-derive already works. Proceed to Step 4.

If FAILS: the schema export needs a manual hint. Open `src/dstf/reporting/ui/mode_controls.py` and find `_field_type()` (around line 90-130). For `Optional[list[dict]]`, the code should fall through to `"passthrough"`. If it doesn't, that's a real bug — investigate before adding a workaround.

- [ ] **Step 4: Write + run a mode-controls render test**

Append to `tests/test_mode_controls.py`:

```python
def test_event_timing_render_html_includes_passthrough_events():
    """render_schema_html should emit a textarea for the events field
    (standard passthrough fallback). The JS-side MODE_PLOT_EDITORS
    table editor overlays this when the leaf is activated; the
    textarea is the fallback when JS fails to load."""
    from dstf.comparison.modes import EventTimingConfig
    from dstf.reporting.ui.mode_controls import (
        derive_schema, render_schema_html,
    )
    schema = derive_schema(EventTimingConfig, mode="event-timing")
    html = render_schema_html(schema, values={
        "time_tolerance": 1e-3,
        "count_must_match": True,
        "events": None,
    })
    assert 'data-field="time_tolerance"' in html
    assert 'data-field="count_must_match"' in html
    # Passthrough field emits a textarea; events should be there.
    assert 'data-field="events"' in html
    assert 'data-passthrough="true"' in html
```

Run:

```bash
uv run pytest tests/test_mode_controls.py::test_event_timing_render_html_includes_passthrough_events -v
```

Expected: PASS. If FAIL on the `data-field="events"` assertion, the auto-derive must be failing to emit the field at all — check that `EventTimingConfig.events` has the `@dataclass` `field(...)` decorator and that `_field_type` returns `"passthrough"` for `Optional[list[dict]]`.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: 783 passed (781 previous + 2 new). No regressions.

- [ ] **Step 6: Commit**

```bash
git add tests/test_export_schema.py tests/test_mode_controls.py
git commit -m "$(cat <<'EOF'
test(event-timing): lock in passthrough schema export for events field

Two tests: one asserts event-timing's schema emits 'events' as
passthrough (matches dom-frequency's peaks), one asserts the rendered
HTML includes the passthrough textarea fallback. Belt-and-suspenders
so future changes to the auto-derive rules don't silently break the
editor's integration surface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: JS editor scaffold — table + add + delete (no detect yet)

**Goal:** The smallest vertical slice of the editor: when a user clicks an event-timing leaf, a table shows in the leaf's .node-editor slot. Rows have `time` and `tolerance` number inputs plus a delete button. Below the table: `+ add event`. No detect button yet (Task 4). No match column yet (Task 4). This task gets the editor mounting lifecycle + table rendering proven first.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — add `MODE_PLOT_EDITORS['event-timing']` IIFE, between range editor (ends ~1604) and dominant-frequency editor (starts 1605)
- Test: `tests/test_interactive_event_timing.py` (new file)

- [ ] **Step 1: Create the test file with the failing scaffold test**

Create `tests/test_interactive_event_timing.py` with:

```python
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
```

- [ ] **Step 2: Run — expect FAILs for the right reason**

```bash
uv run pytest tests/test_interactive_event_timing.py -v -k "mounts or renders or add or delete"
```

Expected: all 4 tests FAIL. The failures should be about `.event-timing-editor` elements not existing (because the editor doesn't exist yet). If they fail with errors like `leafState is not defined` or `TREE_VIEW is not defined`, that's a test-side plan bug — NOT a missing-editor problem — fix per the pre-known corrections at the top of this plan.

- [ ] **Step 3: Add the `MODE_PLOT_EDITORS['event-timing']` IIFE**

Open `src/dstf/reporting/templates/interactive.js`. Find the end of the range editor IIFE (around line 1604: `})();` or similar). Between that and the start of `MODE_PLOT_EDITORS['dominant-frequency'] = (function() {` at line 1605, insert the new event-timing editor:

```javascript
// Event-timing editor — declared-events table in the leaf slot.
// Event-timing is CLI-authoritative for pass/fail (event pairing is
// non-trivial and lives in Python). The editor lets users AUTHOR the
// declared-events list: add rows, edit time + tolerance, delete rows,
// and (Task 4) auto-detect from the reference or actual signals.
// The table UI mirrors dominant-frequency's declared-peaks editor
// pattern; we don't share code with dom-frequency yet — if the overlap
// ends up >=70% after both ship, a shared helper can be extracted as
// a follow-up.
MODE_PLOT_EDITORS['event-timing'] = (function() {

  // --- state helpers -----------------------------------------------------
  function getEvents(leaf) {
    const st = leafState[leaf.path] || {};
    const p = st.params || (st.params = {});
    if (!Array.isArray(p.events)) p.events = [];
    return p.events;
  }

  function getGlobalTolerance(leaf) {
    const st = leafState[leaf.path] || {};
    const v = Number((st.params || {}).time_tolerance);
    return Number.isFinite(v) && v > 0 ? v : 1e-3;
  }

  function sortEvents(leaf) {
    const evs = getEvents(leaf);
    evs.sort((a, b) => Number(a.time) - Number(b.time));
  }

  // --- table rendering ---------------------------------------------------
  const mountedByLeaf = new WeakMap();

  function mount(container, leaf, commit) {
    const root = document.createElement('div');
    root.className = 'event-timing-editor';
    container.appendChild(root);
    mountedByLeaf.set(leaf, { root });
    refreshEditor(leaf, commit);
  }

  function unmount(container) {
    const el = container.querySelector('.event-timing-editor');
    if (el) el.remove();
  }

  function refreshEditor(leaf, commit) {
    const m = mountedByLeaf.get(leaf);
    if (!m) return;
    renderTable(m.root, leaf, commit);
  }

  function renderTable(root, leaf, commit) {
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);

    root.innerHTML = '';

    // Table.
    const table = document.createElement('table');
    table.className = 'event-table';
    const thead = document.createElement('thead');
    thead.innerHTML = (
      '<tr>'
      + '<th>Time (s)</th>'
      + '<th>Tolerance (s)</th>'
      + '<th></th>'
      + '</tr>'
    );
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    evs.forEach((ev, i) => {
      const tr = document.createElement('tr');
      // Time input.
      const timeTd = document.createElement('td');
      const timeInput = document.createElement('input');
      timeInput.type = 'number';
      timeInput.step = 'any';
      timeInput.value = ev.time != null ? String(ev.time) : '';
      timeInput.addEventListener('change', () => {
        const n = Number(timeInput.value);
        if (Number.isFinite(n)) ev.time = n;
        sortEvents(leaf);
        refreshEditor(leaf, commit);
        commit();
      });
      timeTd.appendChild(timeInput);
      tr.appendChild(timeTd);
      // Tolerance input (placeholder shows the global fallback).
      const tolTd = document.createElement('td');
      const tolInput = document.createElement('input');
      tolInput.type = 'number';
      tolInput.step = 'any';
      tolInput.placeholder = String(globalTol);
      tolInput.value = ev.tolerance != null ? String(ev.tolerance) : '';
      tolInput.addEventListener('change', () => {
        const raw = tolInput.value.trim();
        if (raw === '') {
          delete ev.tolerance;
        } else {
          const n = Number(raw);
          if (Number.isFinite(n) && n > 0) ev.tolerance = n;
        }
        commit();
      });
      tolTd.appendChild(tolInput);
      tr.appendChild(tolTd);
      // Delete button.
      const delTd = document.createElement('td');
      const delBtn = document.createElement('button');
      delBtn.className = 'row-delete';
      delBtn.textContent = '✕';
      delBtn.title = 'Remove this event';
      delBtn.addEventListener('click', () => {
        evs.splice(i, 1);
        refreshEditor(leaf, commit);
        commit();
      });
      delTd.appendChild(delBtn);
      tr.appendChild(delTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    root.appendChild(table);

    // Button row — Task 3 only has the add button. Detect button
    // arrives in Task 4.
    const btnRow = document.createElement('div');
    btnRow.className = 'event-editor-buttons';
    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add event';
    addBtn.addEventListener('click', () => {
      // Seed new event time from the last + 0.5s, or 0 if empty.
      const seedTime = evs.length ? Number(evs[evs.length - 1].time) + 0.5 : 0;
      evs.push({ time: seedTime });
      sortEvents(leaf);
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(addBtn);
    root.appendChild(btnRow);
  }

  // --- editor lifecycle (MODE_PLOT_EDITORS contract) ---------------------
  return {
    activate(leaf, plotEl, commit) {
      // The leaf's editor slot is a .node-editor <div> the core
      // already cleared for us. Find it via the leaf's DOM anchor.
      const anchor = document.querySelector(
        `[data-path="${escapeSelector(leaf.path)}"] .node-editor`
      );
      if (!anchor) return;
      mount(anchor, leaf, commit);
    },
    deactivate(leaf, _plotEl) {
      const anchor = document.querySelector(
        `[data-path="${escapeSelector(leaf.path)}"] .node-editor`
      );
      if (anchor) unmount(anchor);
      mountedByLeaf.delete(leaf);
    },
  };
})();
```

Note: `escapeSelector` is an existing helper in interactive.js — verify by `grep -n "function escapeSelector" src/dstf/reporting/templates/interactive.js`. If not present, use a simple template-string selector instead.

- [ ] **Step 4: Re-run the 4 tests; all must PASS**

```bash
uv run pytest tests/test_interactive_event_timing.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Full suite**

```bash
uv run pytest -q
```

Expected: 787 passed (783 previous + 4 new), 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_event_timing.py
git commit -m "$(cat <<'EOF'
feat(reporter): event-timing editor scaffold (table + add + delete)

New MODE_PLOT_EDITORS['event-timing'] IIFE. Mounts a declared-events
table in the leaf's .node-editor slot when the leaf is activated. Each
row: time input, tolerance input (placeholder shows global fallback),
delete button. Below the table: "+ add event" button.

No detect button yet (Task 4). No live match column (Task 4). Pass/
fail stays CLI-authoritative — MODE_SCORERS['event-timing'] remains
intentionally absent.

Four Playwright tests: editor mounts on click, existing events render
as rows, add appends a row + updates leafState, delete removes the
right row + updates leafState.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: JS editor — Detect button + source dropdown + live match column

**Goal:** Round out the Medium scope. Add the "🔍 Detect events" button with a "Detect from: Reference | Actual" dropdown that scans the chosen trajectory for duplicate-time samples and populates the table. Add a "Match" column to each row showing the nearest actual-side event's delta (live — via auto-detection, does NOT substitute for CLI pass/fail).

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — extend the event-timing editor IIFE added in Task 3
- Test: `tests/test_interactive_event_timing.py` — append detect + match-column tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_interactive_event_timing.py`:

```python
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
    # Select Actual in the dropdown, then Detect.
    page.locator(
        '[data-path="/metrics/children/0"] .event-timing-editor '
        'select.detect-source-select'
    ).select_option('act')
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
```

- [ ] **Step 2: Run — expect FAILs**

```bash
uv run pytest tests/test_interactive_event_timing.py -v -k "detect or match_column"
```

Expected: 4 FAIL. The first two because `button.detect-events-btn` doesn't exist yet. The last two because `.match-cell` isn't rendered.

- [ ] **Step 3: Extend the event-timing editor IIFE**

In `src/dstf/reporting/templates/interactive.js`, find the `MODE_PLOT_EDITORS['event-timing']` IIFE added in Task 3. Make these modifications:

**(a)** Inside the IIFE (near `getGlobalTolerance`), add:

```javascript
  function getDetectSource(leaf) {
    const st = leafState[leaf.path] || {};
    return st.event_detect_source === 'act' ? 'act' : 'ref';
  }

  function setDetectSource(leaf, src) {
    const st = leafState[leaf.path] || (leafState[leaf.path] = {});
    st.event_detect_source = src === 'act' ? 'act' : 'ref';
  }

  function getTrajectory(leaf) {
    return (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
  }

  // Evaluate live match status for each declared event against the
  // ACTUAL signal's auto-detected events. Does NOT substitute for CLI
  // pass/fail — this is live feedback only. Same pairing rule as the
  // Python scorer: nearest unclaimed actual event within the declared
  // tolerance wins.
  function evaluateLiveMatches(leaf) {
    const traj = getTrajectory(leaf);
    const actEvents = _detectEvents(traj.act_time || []);
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);
    const claimed = new Array(actEvents.length).fill(false);
    return evs.map(ev => {
      const target = Number(ev.time);
      const tol = ev.tolerance != null ? Number(ev.tolerance) : globalTol;
      let bestIdx = -1;
      let bestD = Infinity;
      for (let j = 0; j < actEvents.length; j++) {
        if (claimed[j]) continue;
        const d = Math.abs(actEvents[j] - target);
        if (d <= tol && d < bestD) {
          bestD = d;
          bestIdx = j;
        }
      }
      if (bestIdx < 0) return { matched: false, delta: null, at: null };
      claimed[bestIdx] = true;
      return { matched: true, delta: bestD, at: actEvents[bestIdx] };
    });
  }
```

**(b)** Modify `renderTable` to:
  - Add a "Match" column header and cell
  - Add the Detect source dropdown + Detect button to the button row

Replace the entire `renderTable` function body from Task 3 with:

```javascript
  function renderTable(root, leaf, commit) {
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);
    const matches = evaluateLiveMatches(leaf);

    root.innerHTML = '';

    // Table.
    const table = document.createElement('table');
    table.className = 'event-table';
    const thead = document.createElement('thead');
    thead.innerHTML = (
      '<tr>'
      + '<th>Time (s)</th>'
      + '<th>Tolerance (s)</th>'
      + '<th>Match (live)</th>'
      + '<th></th>'
      + '</tr>'
    );
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    evs.forEach((ev, i) => {
      const tr = document.createElement('tr');
      // Time input.
      const timeTd = document.createElement('td');
      const timeInput = document.createElement('input');
      timeInput.type = 'number';
      timeInput.step = 'any';
      timeInput.value = ev.time != null ? String(ev.time) : '';
      timeInput.addEventListener('change', () => {
        const n = Number(timeInput.value);
        if (Number.isFinite(n)) ev.time = n;
        sortEvents(leaf);
        refreshEditor(leaf, commit);
        commit();
      });
      timeTd.appendChild(timeInput);
      tr.appendChild(timeTd);
      // Tolerance input.
      const tolTd = document.createElement('td');
      const tolInput = document.createElement('input');
      tolInput.type = 'number';
      tolInput.step = 'any';
      tolInput.placeholder = String(globalTol);
      tolInput.value = ev.tolerance != null ? String(ev.tolerance) : '';
      tolInput.addEventListener('change', () => {
        const raw = tolInput.value.trim();
        if (raw === '') {
          delete ev.tolerance;
        } else {
          const n = Number(raw);
          if (Number.isFinite(n) && n > 0) ev.tolerance = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      tolTd.appendChild(tolInput);
      tr.appendChild(tolTd);
      // Match column (live).
      const matchTd = document.createElement('td');
      matchTd.className = 'match-cell';
      const m = matches[i];
      if (m.matched) {
        matchTd.innerHTML = (
          '<span style="color:#2e7d32">✓ matched</span> '
          + `@ t=${m.at.toPrecision(4)} (Δ=${m.delta.toPrecision(2)})`
        );
      } else {
        matchTd.innerHTML = (
          '<span style="color:#c62828">✕ unmatched</span>'
        );
      }
      tr.appendChild(matchTd);
      // Delete button.
      const delTd = document.createElement('td');
      const delBtn = document.createElement('button');
      delBtn.className = 'row-delete';
      delBtn.textContent = '✕';
      delBtn.title = 'Remove this event';
      delBtn.addEventListener('click', () => {
        evs.splice(i, 1);
        refreshEditor(leaf, commit);
        commit();
      });
      delTd.appendChild(delBtn);
      tr.appendChild(delTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    root.appendChild(table);

    // Button row: add + (new) detect with source dropdown.
    const btnRow = document.createElement('div');
    btnRow.className = 'event-editor-buttons';

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add event';
    addBtn.addEventListener('click', () => {
      const seedTime = evs.length ? Number(evs[evs.length - 1].time) + 0.5 : 0;
      evs.push({ time: seedTime });
      sortEvents(leaf);
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(addBtn);

    // Source dropdown — which signal to scan for auto-detected events.
    const sourceLabel = document.createElement('label');
    sourceLabel.className = 'editor-hint';
    sourceLabel.style.marginLeft = '1em';
    sourceLabel.textContent = 'Detect from: ';
    sourceLabel.title = (
      'Which signal to scan for duplicate-time samples when you click '
      + 'Detect. Reference = pick up events from the saved baseline; '
      + 'Actual = pick up events from this run (useful on a fresh test '
      + 'with no baseline).'
    );
    const sourceSel = document.createElement('select');
    sourceSel.className = 'detect-source-select';
    for (const [val, txt] of [['ref', 'Reference'], ['act', 'Actual']]) {
      const opt = document.createElement('option');
      opt.value = val; opt.textContent = txt;
      if (getDetectSource(leaf) === val) opt.selected = true;
      sourceSel.appendChild(opt);
    }
    sourceSel.addEventListener('change', () => {
      setDetectSource(leaf, sourceSel.value);
    });
    sourceLabel.appendChild(sourceSel);
    btnRow.appendChild(sourceLabel);

    const detectBtn = document.createElement('button');
    detectBtn.className = 'node-btn detect-events-btn';
    detectBtn.textContent = '🔍 Detect events';
    detectBtn.title = (
      'Replace the declared-events list with duplicate-time samples '
      + "auto-detected on the selected source signal. Uses Modelica's "
      + 'convention: two consecutive samples at the same t flag a '
      + 'solver event.'
    );
    detectBtn.addEventListener('click', () => {
      const traj = getTrajectory(leaf);
      const source = getDetectSource(leaf);
      const times = source === 'act' ? (traj.act_time || []) : (traj.ref_time || []);
      const detected = _detectEvents(times);
      const globalTol = getGlobalTolerance(leaf);
      const seed = detected.map(t => ({ time: Number(t), tolerance: globalTol }));
      leafState[leaf.path].params.events = seed;
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(detectBtn);

    root.appendChild(btnRow);
  }
```

- [ ] **Step 4: Re-run tests — 4 new must PASS**

```bash
uv run pytest tests/test_interactive_event_timing.py -v -k "detect or match_column"
```

Expected: 4 PASS. If `test_event_timing_detect_populates_from_reference` fails because the detected events aren't `[1.0, 2.0]` exactly but something like `[0.8, 1.7]`, check that the source dropdown defaulted to `ref` (it should — see `getDetectSource` fallback).

- [ ] **Step 5: Full suite**

```bash
uv run pytest -q
```

Expected: 791 passed (787 previous + 4 new), 0 failures. The existing Task 3 tests (mount, render, add, delete) must still PASS — the refactored `renderTable` changes column count but not structure; `tbody tr` rows still work.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js tests/test_interactive_event_timing.py
git commit -m "$(cat <<'EOF'
feat(reporter): event-timing editor — Detect button + match column

Add three pieces to the Task-3 scaffold:
  - Detect source dropdown (Reference | Actual, default Reference).
  - "🔍 Detect events" button — scans the chosen source signal via
    _detectEvents for duplicate-time samples and seeds the declared-
    events list with each one (tolerance = global default).
  - Live "Match" column — each declared event shows whether the nearest
    actual-side auto-detected event falls within the declared
    tolerance. Does NOT substitute for CLI pass/fail (pairing algorithm
    stays Python-side); this is feedback-only while editing.

Four new Playwright tests: detect-from-ref, detect-from-actual,
matched-indicator, unmatched-indicator.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Overlap audit + documentation

**Goal:** Measure overlap between the new event-timing editor and the existing dominant-frequency declared-peaks editor. If >=70%, note the shared-helper extraction as a follow-up in `docs/ideas.md`. If <70%, keep them parallel. Also document the new event-timing feature — add a D82 decisions entry + update SESSION_HANDOFF.

**Files:**
- Read-only: `src/dstf/reporting/templates/interactive.js` (both editors)
- Modify: `docs/decisions.md` (append D82)
- Modify: `docs/SESSION_HANDOFF.md` (add naming + feature note)
- Modify: `docs/ideas.md` (maybe-append extraction follow-up)

- [ ] **Step 1: Measure overlap manually**

Identify the two editor functions in `src/dstf/reporting/templates/interactive.js`:

```bash
grep -n "^MODE_PLOT_EDITORS\['event-timing'\]\|^MODE_PLOT_EDITORS\['dominant-frequency'\]" \
    src/dstf/reporting/templates/interactive.js
```

For each editor, count approximate lines of:
- Table rendering (thead/tbody/rows) — lines between `renderTable` bodies
- Detect button + source dropdown
- Add button
- Delete button per row
- Match/live-eval column
- Lifecycle (activate/deactivate)

Group these into "shared-looking code" (table shell, button row with detect, source dropdown, add button, row delete) vs "metric-specific" (dom-freq has spectrum subplot + PointPlotEditor; event-timing only has the trajectory-plot overlay from MODE_PLOT_CONTRIBUTIONS).

Report the estimate in the Step-5 commit message. Threshold: if shared-looking code is >= 70% of either editor's total (measured generously), note the extraction follow-up. If it's less, leave as-is.

- [ ] **Step 2: Append D82 decision entry**

Append to `docs/decisions.md`:

```markdown

## D82: Event-timing HTML editor (Medium scope)

- **What**: Added a declared-events editor for the ``event-timing``
  comparison mode to the interactive HTML reporter. Users can author
  a list of expected event instants with per-event tolerances via a
  table UI in the leaf's editor slot, including a "🔍 Detect events"
  button that scans the chosen signal (Reference or Actual) for
  duplicate-time samples and populates the table. The Python scorer
  gained an ``events: Optional[list[dict]]`` field on
  ``EventTimingConfig`` — when set, declared events become the
  authoritative reference-side event set; each one claims the nearest
  actual-side auto-detected event within its own tolerance.
- **Why**: Event-timing was the last remaining mode without a
  dedicated UI surface. The user's roadmap item #3 asked for parity
  with the dominant-frequency declared-peaks editor (D75/D76).
- **Scope: Medium** — table + add + delete + detect + live match
  column. Deliberately NOT included: draggable diamond markers on
  the trajectory plot (Full scope; deferred until someone heavily
  uses it) and a live JS pass/fail rescorer (stays CLI-authoritative
  because event pairing is non-trivial).
- **Semantic change (declared-events path)**: When ``events`` is
  None (default), the existing auto-detect + pair-by-index path is
  unchanged — no regression for existing event-timing tests. When
  set, the algorithm switches to "each declared event claims the
  nearest actual event within its own tolerance", matching
  dominant-frequency's declared-peaks semantics.
- **Validation**: 5 new Python tests + 8 new Playwright tests.
  Full suite goes from 776 → 791 passed. 0 regressions.

### Rejected alternatives

- **Live JS pass/fail rescorer**. Rejected: event-pairing is complex
  enough (nearest-neighbor + tolerance-per-event + count_must_match)
  that porting it would add latent bugs and require keeping two
  implementations in sync. Current UX: users edit events in the
  browser, export the patch, rerun CLI for authoritative results.
- **Draggable diamond markers via PointPlotEditor**. Deferred.
  Could fit cleanly since ``_pointEditor`` already handles tube's
  two-axis drags; events would only need one-axis (time) drags. Skip
  until a user hits a concrete workflow where the numeric inputs feel
  slow.
- **Shared helper with dom-frequency peaks editor upfront**.
  Rejected per the plan's YAGNI guard — wrote both fresh, audited
  overlap afterward (see ideas.md follow-up).
```

- [ ] **Step 3: Update SESSION_HANDOFF**

Open `docs/SESSION_HANDOFF.md`. Find the "Reporter-as-IDE" block (near the end of the "Current architecture" section — describes the six modes' editor surface). Update the event-timing entry:

Find the line mentioning event-timing staying CLI-authoritative without a live editor. Replace the relevant bullet with:

```markdown
* **event-timing**: CLI-authoritative for pass/fail (event pairing
  stays Python-side). D82 added a declared-events table editor in
  the leaf slot — table + add + delete + "🔍 Detect events" with
  Reference/Actual source dropdown + live "Match" column showing
  nearest actual-side auto-detected event and its tolerance status.
  No draggable plot markers (Full-scope, deferred).
```

Also update the test count near the top of the file from whatever it says (was "776 tests passing + 0 skipped") to the new number (expected 791+0).

- [ ] **Step 4: Append extraction follow-up to ideas.md (if overlap ≥ 70%)**

Only do this step if Step 1's overlap measurement came out at 70% or higher. Append to `docs/ideas.md`'s priority matrix a new row:

```markdown
| N+1 | Extract shared "declared-items table editor" helper | L | Low | Dom-frequency's declared-peaks editor and event-timing's declared-events editor (D82) share ~N% of structure (table render, source dropdown, detect button, add/delete). Extract a factory: `createDeclaredItemsEditor({getItems, itemToRow, onAdd, onDetect, renderMatch})` — both editors become ~30-line call sites. Follow-up; YAGNI-deferred until the second editor shipped so the factory shape is concrete. |
```

If overlap is under 70%, SKIP this step and note the overlap % in the commit message instead — documenting why we did NOT extract.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions.md docs/SESSION_HANDOFF.md
# add ideas.md only if step 4 modified it:
git diff --name-only HEAD | grep -q "docs/ideas.md" && git add docs/ideas.md || true
git commit -m "$(cat <<'EOF'
docs: D82 event-timing HTML editor

decisions.md D82 covers motivation (last mode without a UI surface),
scope (Medium — table + detect + match column; no draggable markers,
no live scorer), semantic change (declared-events path vs auto-
detect path), rejected alternatives.

SESSION_HANDOFF.md gains a clarification on the event-timing mode
surface now that it has a dedicated editor, and the backend count
stays at 5.

Overlap audit: dom-frequency peaks editor vs event-timing events
editor overlap is [NN]% — [extracted to a shared helper | left
parallel because below 70% threshold].

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Replace `[NN]` with the actual overlap percentage and `[...]` with the chosen path before committing.)

---

## Rollback plan

If Task 3 or 4's editor integration breaks existing Playwright tests in a way that can't be fixed quickly:

```bash
# After Task 2 (Python side) — no UI code yet, safe boundary.
git log --oneline
git reset --hard <Task-2-commit>
```

Then reconsider the JS-editor approach. The Python side from Tasks 1-2 stands on its own — users can author declared events via `test_spec.json` directly until the UI catches up.

---

## Scope reminders

**This plan does:**
- Add `events` field to `EventTimingConfig` + declared-events scoring path in `_compare_event_timing`.
- Add `MODE_PLOT_EDITORS['event-timing']` with table + add + delete + detect + source dropdown + live match column.
- Add Python + Playwright tests covering the full flow.
- Document D82 + optionally note shared-helper extraction follow-up.

**This plan does NOT do:**
- Draggable diamond markers on the trajectory plot (Full scope).
- Live JS pass/fail rescorer (CLI stays authoritative).
- Refactor dom-frequency editor (preserve historical code; extract later if overlap warrants).
- Change auto-detect behavior when `events` is unset.
- Touch MODE_SCORERS (event-timing stays absent).
- Touch MODE_PLOT_CONTRIBUTIONS (the vertical-line overlay stays as-is).

If a reviewer pushes to expand scope, the answer is "not in this plan — log as a follow-up in ideas.md."
