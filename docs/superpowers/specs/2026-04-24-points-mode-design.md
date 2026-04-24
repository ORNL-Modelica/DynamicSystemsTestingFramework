# `points` Mode — Design

**Status**: Design — pending approval
**Replaces**: `final-only` mode (clean break; no back-compat alias)
**Date**: 2026-04-24
**Depends on**: D83 (baseline-free short-circuit)
**Adjacent**: D82 (event-timing declared events), D75/D76 (dominant-frequency declared peaks)

---

## 1. Summary

Rename and extend `final-only` into a more capable `points` mode. Today's `final-only` supports exactly one check (actual final value vs reference final value, single tolerance). The new `points` mode generalizes this: a user-authored list of checkpoints, each with an optional explicit target value (making it baseline-free) and a choice of absolute or relative tolerance. The default configuration (empty points list) behaves identically to today's `final-only`, so the mental model "check the final value" stays the low-friction path. Multi-point tests, absolute-value sanity checks, and reference-relative spot-checks all collapse to one mode.

## 2. Motivation

Three user needs that `final-only` doesn't currently serve:

1. **Multi-point checking**. Tests that need to verify values at several specific times (e.g., "at t=1.0 the transient has settled, at t=5.0 the steady state matches, and the final value is still within tolerance") require three separate leaves today, with no unified structure.
2. **Absolute-value checking**. "The peak temperature reaches exactly 350 K ± 1 K at t=3.2" can't be expressed without a reference baseline — `final-only` always compares against ref. A dedicated "expected value" escape hatch is needed for engineering-spec-driven tests, and it enables baseline-free tests (per D83).
3. **Cross-metric consistency**. `event-timing` (D82) and `dominant-frequency` (D75) both support the "declared list of user-authored checks" pattern. `final-only` was the outlier with no corresponding story. A `points` mode with a declared-points list gives the same pattern everywhere.

## 3. Scope

### In scope
- New `PointsMode` + `PointsConfig` replacing `FinalOnlyMode` + `FinalOnlyConfig`.
- CLI scorer for the declared-points path.
- JS live scorer (window-aware, per-point target resolution).
- Plot decoration: diamond markers + vertical tolerance bands at declared point times.
- Editor UI: declared-points table with per-row inputs, `+ add point`, `📸 Snapshot from ref`.
- Mechanical rename of `final-only` throughout source, tests, docs.

### Out of scope
- Back-compat alias (`"mode": "final-only"` becomes an error — user's standing "no backward compat" policy).
- Draggable markers on the trajectory plot (Full scope — deferred; same decision as event-timing D82).
- Auto-populating points from ref-events or similar heuristics (nothing general-purpose to auto-detect — points are user intent).
- Point-specific `tolerance_mode: "band"` (Tube-specific concept; overkill for discrete points).

## 4. Design

### 4.1 Mode renaming — clean break

- Python: `FinalOnlyConfig` → `PointsConfig`; `FinalOnlyMode` → `PointsMode`; `_compare_final_values` → `_compare_points`.
- Registry key: `"final_only"` → `"points"`. The `resolve_mode` helper accepts `"points"` as the canonical name.
- Old strings `"final-only"` and `"final_only"` in spec JSON become unrecognized — user gets a clear error (preferred over a silent rename). Clean-break policy covers this.
- Test suite, docs, example libraries updated in lockstep. Verification step: grep for any surviving `final_only`/`final-only`/`FinalOnly` references post-implementation.

### 4.2 Config schema

```python
@dataclass(frozen=True)
class PointsConfig:
    """Configuration for point-based comparison.

    When ``points`` is None or [], the mode behaves exactly like the
    former final-only: checks ``act[-1]`` vs ``ref[-1]`` with
    ``tolerance`` as absolute delta. When ``points`` is a non-empty
    list, each entry is a declared checkpoint.
    """
    points: Optional[list[dict]] = None
    tolerance: float = 1e-4  # default per-point tolerance when not specified
```

Per-point dict shape:

```python
{
    "time": float | None,             # Null sentinel = "the trace's final time"
    "value": float | None,            # Optional; if omitted, read interpolated ref(time)
    "tolerance": float | None,        # Optional y-tolerance; falls back to config.tolerance
    "tolerance_mode": "abs" | "rel",  # Optional; default "abs"
    "time_tolerance": float | None,   # Optional x-tolerance (symmetric); default 0 = strict-time check
}
```

Semantics:
- `time: null` means "whatever the trace's final time is" — decouples the spec from simulation `stop_time`.
- `value: null` and missing-`value` key are treated identically. Both mean "target is `ref(time)`" (interpolated linearly). Baseline-dependent.
- `value` set to a number → the target is the literal number. Baseline-free for this point.
- `tolerance_mode: "abs"` → y-tolerance is a literal absolute delta: `y_limit = tolerance`.
- `tolerance_mode: "rel"` → y-tolerance scales with target: `y_limit = tolerance * |target|`.
- `time_tolerance: 0` (default) → strict-time check at `t = time`. `time_tolerance > 0` → relaxed-time check: "act curve must enter the tolerance box `[time - time_tolerance, time + time_tolerance] × [target - y_limit, target + y_limit]` at least once." Covers solver-timing drift (e.g., "temperature hits 30°C at t ≈ 3" where different solvers may hit it at 2.97 or 3.02 — both pass).
- Target resolves at `time` (the box center). `time_tolerance` only expands *where in time* the act curve has to satisfy the y-check; it does not re-evaluate `ref(t)` across the time window.

### 4.3 Window semantics

Points whose `time` falls outside the leaf's `[window.start, window.end]` are ignored by the scorer. Matches NRMSE / range / tube / event-timing behavior. The null-time sentinel resolves against the windowed trace's final time, not the full trace's — so "final" means "end of the window" when a window is set. When `time_tolerance > 0`, the box is clipped to the leaf's window: `[max(time - time_tolerance, window.start), min(time + time_tolerance, window.end)]`. Fully-clipped (window excludes the box entirely) → point is ignored same as if `time` itself were outside.

### 4.4 Baseline-free trigger

`PointsMode.is_baseline_free()` returns True iff `points` is a non-empty list AND every point in it has an explicit `value`. The implicit-final case (`points=None` or `[]`) reads from ref and is never baseline-free. Mixed points (some with `value`, some without) are never baseline-free either — all-or-nothing. Users who want a fully baseline-free test commit to setting `value` on every point.

This integrates with D83's short-circuit: a test whose leaves are all `points`-with-explicit-values needs no baseline; the comparator runs the scorer on actuals only.

### 4.5 CLI scoring algorithm

```
def compare(ref_time, ref_values, act_time, act_values, config):
    if not config.points:
        # Implicit final-only.
        target = ref_values[-1]
        delta = abs(act_values[-1] - target)
        return pass if delta < config.tolerance else fail
    for point in config.points:
        t_center = resolve_time(point, trace_end=ref_time[-1] or act_time[-1])
        x_tol = point.get("time_tolerance", 0)
        t_lo = max(t_center - x_tol, window.start_or_-inf)
        t_hi = min(t_center + x_tol, window.end_or_+inf)
        if t_hi < t_lo: continue                 # fully-clipped by window
        target = point["value"] if "value" in point else interp(ref_time, ref_values, t_center)
        tol = point.get("tolerance", config.tolerance)
        mode = point.get("tolerance_mode", "abs")
        y_limit = tol * abs(target) if mode == "rel" else tol
        # Find the smallest |act(t) - target| for t in [t_lo, t_hi].
        # When x_tol == 0 this degenerates to a single-point check.
        best_delta = min_over_window(act_time, act_values, t_lo, t_hi, target)
        if best_delta > y_limit: mark point as failed
    pass if all scored points matched else fail
```

`min_over_window` computes `min |act(t_i) - target|` for samples `t_i` inside `[t_lo, t_hi]`, plus the interpolated endpoints `act(t_lo)` and `act(t_hi)` to catch cases where the act curve enters the box between samples. When `x_tol == 0`, `t_lo == t_hi` and the window has only the interpolated value at `t_center` → reduces to the current strict-time check.

Reported scalar (`nrmse` field, reused for reporting uniformity): max `best_delta` across all scored points. `max_abs_error` / `max_abs_error_time` track the worst point (with `time` = the `t` inside the box that minimized delta — not necessarily the point's center `t_center`).

### 4.6 JS live scorer

Mirrors the CLI algorithm using the existing `_sliceLeafTrajectory` helper (D83) + `_interpLinear`. Same logic, same window semantics, same tolerance-mode math. Window-aware end-to-end on the JS side (matches the cross-metric consistency the range-fix plan established).

### 4.7 Plot decoration

For each point whose box intersects the leaf's window, emit:

- A diamond marker at `(t_center, resolved_value)` where `resolved_value = point["value"]` if set, else `ref(t_center)`. Signals the user's declared target. Color: green if live-match passes, red otherwise. Matches dom-frequency's peak-marker visual.
- A translucent tolerance box at `[t_center - time_tolerance, t_center + time_tolerance] × [resolved_value - y_limit, resolved_value + y_limit]`. Fill color matches the marker (light green on match, light red on fail) at low opacity. When `time_tolerance == 0` the box degenerates to a vertical line segment — same as a zero-width rectangle, visually identical to the "vertical tolerance bar" pattern.

**Key principle**: plot always shows resolved absolute values, table always shows raw config. A point configured as `{tolerance_mode: "rel", tolerance: 0.05, time_tolerance: 0.1}` with `ref(t_center) = 2.0` renders as a marker at y=2.0 inside a box spanning `[t_center ± 0.1] × [2.0 ± 0.1]` in data coords. The config stays `tolerance: 0.05, tolerance_mode: "rel", time_tolerance: 0.1`. Mirror of tube's approach (`_resolvePoint` computes absolute upper/lower for rendering).

### 4.8 Editor UI

**Location**: leaf's `.node-editor` slot (same slot event-timing and dom-frequency use).

**Components**:
1. **Table** — columns `Time | x-tol | Value | Mode | Tolerance | Match (live) | ×`.
   - `Time`: number input. Renders the word `final` as a placeholder when `time: null`. Typing a number sets `time` to that number; clearing the input (empty string on blur) resets to `time: null`.
   - `x-tol`: number input for `time_tolerance`; empty or `0` → strict-time check. Placeholder shows `0`.
   - `Value`: number input; empty → ref-relative.
   - `Mode`: `abs / rel` dropdown, default `abs`.
   - `Tolerance`: number input; placeholder shows the leaf's global `tolerance`.
   - `Match (live)`: `✓ matched (Δ=0.002 at t=2.97)` — delta + the time inside the box where the match occurred. Fail form: `✕ unmatched (best Δ=0.5 at t=3.0 > 0.1)`.
   - `×`: per-row delete button.
2. **Buttons row**:
   - `+ add point` — seed time from the previous point + 0.5s, or 0 if empty.
   - `📸 Snapshot from ref` — for every row where `value` is empty, fill `value` with the current `ref(time)`. Converts a ref-based test into a baseline-free absolute-value test in one click. Idempotent (points with explicit `value` untouched).

**Zero-point fast path**: when the points list is empty (implicit final-only), the table shows a single italic placeholder row "`final` · uses ref[-1] · tolerance {tol}" with no inputs, reinforcing that adding points overrides the implicit final check. First click on `+ add point` replaces the placeholder with an editable row seeded at `time: null`.

### 4.9 Data flow — concrete examples

**Legacy final-only test**:
```json
{"mode": "points", "tolerance": 1e-3}
```
Identical behavior to old `{"mode": "final-only", "tolerance": 1e-3}`.

**Multi-point ref-relative test**:
```json
{
  "mode": "points",
  "tolerance": 1e-3,
  "points": [
    {"time": 1.0},
    {"time": 5.0, "tolerance": 0.01},
    {"time": null, "tolerance_mode": "rel", "tolerance": 0.05}
  ]
}
```
Three checks: strict ref-match at t=1.0, looser ref-match at t=5.0, final value within 5% of ref[-1].

**Fully baseline-free test**:
```json
{
  "mode": "points",
  "points": [
    {"time": 2.0, "value": 10.0, "tolerance": 0.5},
    {"time": null, "value": 0.0, "tolerance": 1.0}
  ]
}
```
No reference needed; runs via D83's short-circuit. Users express "the peak reaches 10 at t=2, system returns to 0 at end" purely from spec.

**Timing-uncertainty test**:
```json
{
  "mode": "points",
  "points": [
    {"time": 3.0, "value": 30.0, "tolerance": 0.5, "time_tolerance": 0.2}
  ]
}
```
"Temperature reaches 30 °C somewhere between t=2.8 and t=3.2, within ±0.5 °C of the target." Covers solver-timing drift without forcing the act curve to hit exactly at t=3.0.

## 5. Migration plan

### 5.1 User-facing migration
- **Zero test-spec files currently use `"mode": "final-only"`** (verified via grep of `examples/`). No user-authored JSON needs rewriting.
- Clean break: old mode name is simply gone. If someone has a `final-only` spec outside this repo, they get a `resolve_mode` error pointing to `points`.

### 5.2 Source migration
- Rename class names in `src/dstf/comparison/modes.py`.
- Rename scorer in `src/dstf/comparison/comparator.py`.
- Update registry key in `src/dstf/reporting/ui/mode_controls.py` (`register_mode_ui("points", PointsConfig)`).
- Update JS `MODE_SCORERS['final-only']` → `MODE_SCORERS['points']` (and the keys of `MODE_PLOT_CONTRIBUTIONS` / `MODE_PLOT_EDITORS`).
- Mechanical sed for `final-only` / `final_only` / `FinalOnly` across Python + JS + docs — excluding historical decision entries (D1–D83) which stay as-written.

### 5.3 Test migration
- 8 test files currently reference `final-only`. Each gets the mode name replaced and, where useful, gains at least one new assertion exercising the multi-point / absolute-value path so the new capability is regression-protected.
- Keep existing single-tolerance tests (they become "points with empty list" tests — still run, same expected results).

## 6. Testing

Planned test coverage (specified in the implementation plan):

**CLI**:
- Single-implicit-point behavior matches old final-only exactly (regression guard).
- Multi-point ref-relative passes when all points within tol.
- Multi-point with one failing point → FAIL.
- Absolute-value point passes with no reference (baseline-free path).
- Mixed abs+rel tolerance_mode per point.
- Null-time sentinel resolves to trace end.
- Window clipping: points outside window ignored.
- Baseline-free trigger: `is_baseline_free()` True only when all points have explicit `value`.
- `time_tolerance > 0` passes when the act curve enters the box somewhere inside; fails when the act curve stays entirely outside the box.
- `time_tolerance = 0` behaves identically to current strict-time check (regression guard for the default case).
- `time_tolerance` box clipped by leaf window: point with center inside window but box partially outside → clipped box still scores; point with box fully outside window → ignored.

**Reporter JS / Playwright**:
- Table renders existing points on leaf activation.
- `+ add point` appends a row with null-time default.
- `📸 Snapshot from ref` fills in empty `value` cells with `ref(time)`.
- Per-row delete removes from `leafState` + refreshes plot.
- Live-match column flips on tolerance edit.
- Plot markers at resolved absolute y; tolerance-mode `rel` renders band as fraction of |target|.
- `time_tolerance > 0` renders the 2D tolerance box with correct width; `time_tolerance = 0` degenerates to vertical line segment.
- Window edit triggers rescore (cross-metric consistency lock from range-fix Task 6).

## 7. Rejected alternatives

**Keep `final-only` alongside a new `points` mode.** Creates redundant code, redundant editors, redundant docs, and two overlapping mental models. User's explicit preference: avoid method bloat in the HTML. Unification is the cleaner path.

**Require explicit numeric `time` (no null-sentinel)**. Simpler config schema but couples test-spec `time` values to simulation `stop_time`. Any change to `stop_time` silently invalidates the "final value" check. Null-sentinel decouples cleanly.

**Per-point partial scoring for mixed-baseline leaves.** "Score the absolute points even when ref is missing, skip the ref-relative ones with a warning." Creates PASS/FAIL ambiguity — users get "test passed" when half the declared checks didn't run. Rejected in favor of the all-or-nothing rule: a leaf with any ref-dependent point needs a baseline, period. Users who want fully baseline-free must commit.

**Draggable markers on trajectory plot as MVP.** Skipping per the same rationale as event-timing D82: numeric table editing covers the essentials; draggable markers are polish that can layer on later if usage shows demand. Avoids ~1 day of interaction code + the `PointPlotEditor` wiring complexity.

## 8. Follow-ups (explicit non-goals)

- **Draggable markers + Shift+click-to-add** (Full scope upgrade) — log in `ideas.md` for a future session.
- **Tolerance mode `"band"` per point** (Tube-style literal y-offsets) — no identified use case; skip unless requested.
- **Auto-detect via heuristic** (e.g., "points where the signal settles") — no general-purpose algorithm; user intent stays explicit.
- **Cross-point weighted scoring** — weighted tree combinator already handles cross-leaf weighting; per-point weights inside one leaf would duplicate the mechanism.
