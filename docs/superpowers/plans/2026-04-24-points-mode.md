# `points` Mode Implementation Plan (D84)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `final-only` mode to `points` and extend it into a fully-capable point-based comparison: per-point ref-relative or absolute targets, abs/rel y-tolerance modes, x-axis tolerance (`time_tolerance`) for box-style timing-uncertainty checks, dedicated declared-points table editor with "Snapshot from ref" workflow.

**Architecture:** Three coordinated layers, applied in nine atomic tasks. (1) Python: rename `FinalOnlyConfig`/`FinalOnlyMode`/`_compare_final_values` to `Points`-prefixed names, then extend `_compare_points` first with declared-points handling, then with the time_tolerance box check. (2) CLI/config plumbing: rename the legacy `--final-only` flag and `config.final_only` to `--default-points`/`config.default_points` (clean break, "points with no entries" IS the final-only behavior). (3) JavaScript: port the scoring algorithm to JS, replace the vertical-line plot decoration with a translucent tolerance-box + diamond-marker pair, ship a new `MODE_PLOT_EDITORS['points']` editor with declared-points table + add/delete + Snapshot-from-ref. Mechanical doc/sed sweep + D84 entry close out.

**Tech Stack:** Python 3.10+, JavaScript (no build step), pytest, pytest-playwright, Plotly.js 2.x. No new deps.

**Spec:** `docs/superpowers/specs/2026-04-24-points-mode-design.md` (commit `bb228c0`).

**Pre-known plan corrections from prior tasks** (apply inline wherever the pattern appears):
1. Playwright test imports: `from test_interactive_playwright import (...)` — NOT `from tests.test_interactive_playwright`.
2. `leafState` is a script-scope const in interactive.js, NOT `window.leafState`.
3. Global tree variable is `TREE_VIEW`, NOT `SPEC_TREE`.
4. `initLeafState()` merges `leaf.params` with `leaf.mode_values` (mode_values wins); fixture overrides need BOTH.
5. Don't rely on specific linspace-grid values — use piecewise-constant or hand-tuned trajectories for deterministic tests.

**Standing user preferences:** No backward compat during dev cycle. Modular/OO bias.

**Out of scope (per spec §3, §7, §8):**
- Back-compat alias for `"final-only"` mode string (becomes a `resolve_mode` error).
- Draggable plot markers (Full scope; deferred).
- `tolerance_mode: "band"` per point (Tube-specific concept).
- Auto-detect heuristics (no general algorithm; user intent stays explicit).

---

## File structure

### Modified — Python
- `src/dstf/comparison/modes.py` — class & config rename, scorer alias removal, `is_baseline_free` override, `resolve_mode` signature change.
- `src/dstf/comparison/comparator.py` — scorer rename + extension (declared-points + box check), CLI plumbing for renamed flag.
- `src/dstf/comparison/tree_eval.py` — `_METRIC_TO_MODE_KEY` mapping update.
- `src/dstf/config.py` — `final_only` field rename to `default_points`.
- `src/dstf/cli.py` — `--final-only` flag rename + per-field threading.
- `src/dstf/reporting/ui/mode_controls.py` — `register_mode_ui("points", PointsConfig)` (replaces `final_only`).

### Modified — JavaScript
- `src/dstf/reporting/templates/interactive.js`
  - `MODE_SCORERS['points']` (replaces `'final-only'`) — full algorithm with declared-points + box check.
  - `MODE_PLOT_CONTRIBUTIONS['points']` (replaces `'final-only'`) — translucent box + diamond marker.
  - `MODE_PLOT_EDITORS['points']` (NEW) — declared-points table editor with add/delete/Snapshot.

### Modified — tests
- 8 existing test files reference `final-only`/`final_only`/`FinalOnly` (57 occurrences). All get the rename, several gain new assertions exercising the new capability.
- New file: `tests/test_interactive_points.py` — Playwright tests for the new editor.

### Modified — docs
- `docs/architecture.md`, `docs/extensibility.md`, `docs/SESSION_HANDOFF.md`, `docs/patterns.md`, `docs/usage.md`, `docs/vision.md`, `docs/related_tools_research/evaluation_report.md`.
- `docs/decisions.md` — append D84.
- `docs/ideas.md` — log Full-scope draggable-marker follow-up.

### Untouched (deliberately)
- `docs/superpowers/plans/*.md`, `docs/superpowers/specs/*.md` — historical snapshots.
- D1–D83 entries in `docs/decisions.md` — they describe past state.
- LLM-output snapshots in `docs/related_tools_research/llm_responses/`.

---

## Pre-flight

```bash
git log --oneline -1
uv run pytest -q
```

Expected: `bb228c0` (D84 spec) at HEAD or later, 796 passed + 0 skipped, 0 failures.

---

## Task 1: Python rename — `final_only` → `points` (preserve current behavior)

**Goal:** Pure mechanical rename. `points=None` or `[]` behaves exactly like today's final-only. No new capability yet — that lands in Tasks 3-4. The aim is "after this commit, the tool runs the same suite green under the new mode name."

**Files:**
- Modify: `src/dstf/comparison/modes.py`
- Modify: `src/dstf/comparison/comparator.py`
- Modify: `src/dstf/comparison/tree_eval.py`
- Modify: `src/dstf/reporting/ui/mode_controls.py`
- Modify: 8 test files referencing `final-only`/`final_only`/`FinalOnly`

- [ ] **Step 1: Audit the surface**

```bash
grep -rln "FinalOnly\|final_only\|final-only" src/dstf/ tests/ --include="*.py" --include="*.js" | sort
```

Expected: ~12 files. Compare against the file list in this task — anything extra is a surprise to investigate.

- [ ] **Step 2: Rename the dataclass + mode class in `src/dstf/comparison/modes.py`**

In `src/dstf/comparison/modes.py`, find `class FinalOnlyConfig` (line 169) and replace it with:

```python
@dataclass(frozen=True)
class PointsConfig:
    """Configuration for point-based comparison.

    When ``points`` is None or [], the mode behaves exactly like the
    former final-only: checks ``act[-1]`` vs ``ref[-1]`` with
    ``tolerance`` as absolute delta. When ``points`` is a non-empty
    list, each entry is a declared checkpoint with optional explicit
    target value, per-point tolerance, and per-point time-tolerance.
    See docs/superpowers/specs/2026-04-24-points-mode-design.md.
    """
    points: Optional[list[dict]] = field(
        default=None,
        metadata={
            "label": "Declared points",
            "help": (
                "Optional list of (time, value, tolerance, "
                "tolerance_mode, time_tolerance) checkpoints. When None "
                "or empty, the mode falls back to final-value comparison "
                "with the global ``tolerance``. Authored via the table "
                "editor in the interactive HTML reporter."
            ),
        },
    )
    tolerance: float = field(
        default=1e-4,
        metadata={
            "label": "Default tolerance",
            "help": (
                "Default per-point y-tolerance when not specified inside "
                "a point dict. Also the tolerance for the implicit final-"
                "value check when ``points`` is empty."
            ),
        },
    )
```

Find `class FinalOnlyMode(ComparisonMode)` (line 327) and replace its body with:

```python
class PointsMode(ComparisonMode):
    """Compare actual vs reference at user-declared time points.

    When ``config.points`` is None or empty, falls back to the legacy
    final-value-only check (act[-1] vs ref[-1] with config.tolerance).
    """

    def __init__(self, config: PointsConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "points"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_points(
            ref_time, ref_values, act_time, act_values,
            points=self.config.points,
            tolerance=self.config.tolerance,
        )

    def is_baseline_free(self) -> bool:
        # Baseline-free iff non-empty points list AND every point
        # has an explicit ``value``. Empty list → implicit final
        # comparison reads ref → not baseline-free. Mixed (some with
        # value, some without) is also not baseline-free; users
        # commit to all-or-nothing.
        pts = self.config.points
        if not pts:
            return False
        return all(p.get("value") is not None for p in pts)
```

Find `resolve_mode` (line 433). Replace its signature and body:

```python
def resolve_mode(
    var_override: dict,
    tolerance: float,
    default_points: bool = False,
) -> ComparisonMode:
    """Build the appropriate ComparisonMode from a per-variable override dict.

    Resolution order:
    1. Explicit ``mode`` key in override → use that mode.
    2. If no explicit mode and ``default_points`` is True → PointsMode
       with points=None (implicit final-value check).
    3. Otherwise → NrmseMode (legacy default).

    Recognized mode strings:
      "points"             → PointsMode (canonical)
      "tube"               → TubeMode
      "range"              → RangeMode
      "event-timing"       → EventTimingMode
      "dominant-frequency" → DominantFrequencyMode
    """
    mode_name = var_override.get("mode")
    var_tol = var_override.get("tolerance", tolerance)

    if mode_name == "points" or (not mode_name and default_points):
        return PointsMode(PointsConfig(
            points=var_override.get("points"),
            tolerance=var_tol,
        ))
    if mode_name == "tube":
        return TubeMode(TubeConfig(
            tube_width_mode=var_override.get("tube_width_mode"),
            tube_abs=var_override.get("tube_abs", 0.0),
            tube_rel=var_override.get("tube_rel", 0.0),
            tube_min_width=var_override.get("tube_min_width", 0.0),
            tube_points=var_override.get("tube_points"),
            tube_interpolation=var_override.get("tube_interpolation", "linear"),
        ))
    if mode_name == "range":
        return RangeMode(RangeConfig(
            min_value=var_override.get("min_value"),
            max_value=var_override.get("max_value"),
        ))
    if mode_name == "event-timing":
        return EventTimingMode(EventTimingConfig(
            time_tolerance=var_override.get("time_tolerance", 1e-3),
            count_must_match=var_override.get("count_must_match", True),
            events=var_override.get("events"),
        ))
    if mode_name == "dominant-frequency":
        return DominantFrequencyMode(DominantFrequencyConfig(
            peaks=var_override.get("peaks"),
        ))
    return NrmseMode(NrmseConfig(tolerance=var_tol))
```

Note the rename of the `default_final_only` parameter to `default_points`, and the removal of the legacy `"final_only"` mode-name branch. Old specs that say `"mode": "final_only"` or `"mode": "final-only"` will fall through to `NrmseMode` (the no-mode default), which is wrong — but that's per the clean-break policy. Users see incorrect-but-different-from-final-only behavior; we document this in the D84 entry as the migration warning.

Also update the module docstring at the top of `modes.py`. Find the existing docstring (line 1-16) and replace any reference to `FinalOnlyMode` with `PointsMode`. If the docstring lists modes, ensure `PointsMode — compare values at declared time points` appears.

- [ ] **Step 3: Rename the scorer in `src/dstf/comparison/comparator.py`**

In `src/dstf/comparison/comparator.py`, find `def _compare_final_values` (line 264) and replace its name + signature + docstring. The body stays unchanged for now (extension lands in Tasks 3-4). Replace the function definition with:

```python
def _compare_points(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    points: Optional[list[dict]] = None,
    tolerance: float = 1e-4,
) -> VariableComparison:
    """Compare actual vs reference at declared time points.

    When ``points`` is None or empty, falls back to the legacy final-
    value check (act[-1] vs ref[-1] with ``tolerance`` as absolute
    delta). When ``points`` is a non-empty list, declared-points
    handling lands in Task 3 of the points-mode plan; for now, this
    branch raises NotImplementedError so downstream callers see a
    clear error if they hit the unimplemented path.
    """
    if not points:
        # Implicit final-only — preserved behavior.
        if len(ref_values) == 0 or len(act_values) == 0:
            return VariableComparison(
                index=0, name="", passed=False,
                nrmse=float("inf"), rmse=float("inf"),
                signal_range=0.0,
                max_abs_error=float("inf"),
                max_abs_error_time=0.0,
                reference_final=float("nan"),
                actual_final=float("nan"),
                mode="points",
                diagnostics={"error": "empty trajectory"},
            )
        ref_final = float(ref_values[-1])
        act_final = float(act_values[-1])
        delta = abs(act_final - ref_final)
        passed = delta < tolerance
        return VariableComparison(
            index=0, name="", passed=passed,
            nrmse=delta, rmse=delta, signal_range=0.0,
            max_abs_error=delta,
            max_abs_error_time=float(ref_time[-1]) if len(ref_time) else 0.0,
            reference_final=ref_final, actual_final=act_final,
            mode="points",
            diagnostics={"tolerance": tolerance, "delta": delta},
        )
    raise NotImplementedError(
        "Declared-points scoring lands in Task 3 of the points-mode plan."
    )
```

Note the exact line where the original `_compare_final_values` ends — the replacement spans the same range. The `mode="points"` field in `VariableComparison` is the only behavior-visible change so far; `mode="final-only"` becomes `mode="points"` in the diagnostics output.

- [ ] **Step 4: Update `tree_eval.py` mode-key normalization**

In `src/dstf/comparison/tree_eval.py` around line 60, find `_METRIC_TO_MODE_KEY` and replace `"final-only": "final_only"` with `"points": "points"`. The complete updated dict:

```python
_METRIC_TO_MODE_KEY = {
    "nrmse": None,
    "tube": "tube",
    "points": "points",
    "range": "range",
    "event-timing": "event-timing",
    "dominant-frequency": "dominant-frequency",
}
```

Also find any other reference to `"final_only"` or `"final-only"` in tree_eval.py and rename to `"points"`. The line 155 docstring mentioning "(nrmse / tube / final_only)" should be updated to "(nrmse / tube / points)". Line 225's `default_final_only=False` becomes `default_points=False`.

- [ ] **Step 5: Update `mode_controls.py` registry**

In `src/dstf/reporting/ui/mode_controls.py` find:

```python
register_mode_ui("final_only", FinalOnlyConfig)
```

Replace with:

```python
register_mode_ui("points", PointsConfig)
```

Update the import in the same file: `FinalOnlyConfig` → `PointsConfig`.

- [ ] **Step 6: Sed the test files**

Run a careful sed sweep across the 8 test files. Use one-shot replacements:

```bash
# Class + dataclass name (CamelCase)
grep -rl "FinalOnlyConfig\|FinalOnlyMode" tests/ --include="*.py" \
  | xargs sed -i 's/FinalOnlyConfig/PointsConfig/g; s/FinalOnlyMode/PointsMode/g'

# Mode-name strings (lowercase variants)
grep -rl '"final_only"\|"final-only"' tests/ --include="*.py" \
  | xargs sed -i 's/"final_only"/"points"/g; s/"final-only"/"points"/g'
```

After both sweeps, verify no surviving references:

```bash
grep -rn "FinalOnly\|final_only\|final-only" tests/ --include="*.py" || echo "CLEAN"
```

Expected: `CLEAN`. Some tests may have inline mode strings like `metric="final-only"` — sed should catch those. If anything remains, audit it manually before proceeding.

- [ ] **Step 7: Update `cli.py` flag references in tests (preview only)**

The CLI flag `--final-only` is renamed in Task 2. For Task 1, just leave `--final-only` references in tests alone — they'll keep working until Task 2 lands. The `final_only` Python-attribute access in tests will likewise survive (Task 2 renames `config.final_only` → `config.default_points`).

- [ ] **Step 8: Run the full suite**

```bash
uv run pytest -q
```

Expected: 796 passed + 0 skipped, 0 failures. Same as pre-rename. If any test fails:
- `AttributeError: ... has no attribute 'FinalOnlyConfig'` → a tests file wasn't sed'd. Find it and fix.
- `AssertionError: mode != 'final-only'` → a test asserted on the OLD `mode` field in diagnostics. Update the assertion to expect `"points"`.
- Anything else → STOP and report; don't paper over.

- [ ] **Step 9: Commit**

```bash
git status --short
```

Confirm only Python source files + test files are staged. Pre-existing unstaged files (LICENSE, examples/fmu/, tests/fixtures/, .claude/settings.local.json) stay unstaged.

```bash
git add src/dstf/comparison/modes.py \
        src/dstf/comparison/comparator.py \
        src/dstf/comparison/tree_eval.py \
        src/dstf/reporting/ui/mode_controls.py \
        tests/
git commit -m "$(cat <<'EOF'
refactor(comparison): final-only → points mode (preserve behavior)

Pure rename + minimal extension. PointsConfig replaces FinalOnlyConfig
with the new ``points`` field defaulting to None — when None or
empty, _compare_points behaves exactly like the old
_compare_final_values (act[-1] vs ref[-1] with tolerance). Declared-
points + time_tolerance lands in Tasks 3-4.

Wire-through: resolve_mode parameter default_final_only → default_points;
tree_eval _METRIC_TO_MODE_KEY entry "final-only" → "points";
register_mode_ui("final_only", ...) → register_mode_ui("points", ...).

PointsMode.is_baseline_free returns True iff every declared point has
an explicit ``value`` — preview of the D83 short-circuit integration.
Empty/None points list still needs a reference (legacy semantics).

Old "mode": "final-only" / "final_only" strings now fall through to
NrmseMode silently — clean-break per standing policy. D84 entry will
document the migration in Task 9.

CLI flag --final-only and config.final_only stay until Task 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Rename CLI flag + config field

**Goal:** Rename the legacy `--final-only` CLI flag and `config.final_only` field to align with the new mode. Mechanical, but touches user-facing surfaces.

**Files:**
- Modify: `src/dstf/config.py`
- Modify: `src/dstf/cli.py`
- Modify: `src/dstf/comparison/comparator.py` (function-signature names)
- Modify: tests that reference `--final-only` or `config.final_only`

- [ ] **Step 1: Find every reference**

```bash
grep -rn "final_only\|--final-only" src/dstf/ tests/ docs/ --include="*.py" --include="*.md" \
  | grep -v "/superpowers/" \
  | grep -v "decisions.md"
```

Expected: ~10-15 hits in `cli.py`, `config.py`, `comparator.py`, and a handful of tests + a few in `usage.md` / `architecture.md`. Note the count.

- [ ] **Step 2: Rename `config.final_only` → `config.default_points`**

In `src/dstf/config.py` find the `final_only` field (line 261):

```python
final_only: bool = False
```

Replace with:

```python
default_points: bool = False
```

Update any `__post_init__` or `from_file` references in the same file that read/write `final_only` → `default_points`.

- [ ] **Step 3: Rename `--final-only` CLI flag → `--default-points`**

In `src/dstf/cli.py`:
- Line 1326 and 1360: change `--final-only` to `--default-points` and update the help text from "Compare only final values" to "Use points mode (with empty points list) as the default for variables without an explicit ``mode`` override."
- Line 1254 (and any similar): replace `args.final_only` with `args.default_points` and `kwargs["final_only"] = args.final_only` with `kwargs["default_points"] = args.default_points`.
- Lines 190, 242, 250, 311: replace `config.final_only` with `config.default_points`.

argparse converts `--default-points` to `args.default_points` automatically (dashes → underscores).

- [ ] **Step 4: Update `_compare_points` and `compare_test`/`compare_all` parameter names**

In `src/dstf/comparison/comparator.py` find functions that take `final_only: bool = False` and rename to `default_points: bool = False`. Search:

```bash
grep -n "final_only" src/dstf/comparison/comparator.py
```

For each hit:
- Function-parameter declarations: `final_only` → `default_points`.
- Internal variable references and call-sites: same rename.
- Pass-through to `resolve_mode(..., default_final_only=...)` → `resolve_mode(..., default_points=...)`. Note: the parameter on `resolve_mode` was renamed in Task 1.

After this step, `grep -n "final_only" src/dstf/comparison/comparator.py` should return nothing.

- [ ] **Step 5: Sed `--final-only` and `final_only` in tests**

```bash
grep -rl "final_only\|--final-only" tests/ --include="*.py" \
  | xargs sed -i 's/--final-only/--default-points/g; s/final_only/default_points/g'
```

Verify:

```bash
grep -rn "final_only\|--final-only" tests/ --include="*.py" || echo "CLEAN"
```

Expected: `CLEAN`.

- [ ] **Step 6: Full suite**

```bash
uv run pytest -q
```

Expected: 796 passed + 0 skipped. If a test fails with `AttributeError: 'Namespace' object has no attribute 'final_only'`, a CLI test missed the sed. If a test fails with `TypeError: ... unexpected keyword argument 'final_only'`, a Python call-site missed the sed. Investigate and fix.

- [ ] **Step 7: Commit**

```bash
git status --short
git add src/dstf/config.py src/dstf/cli.py src/dstf/comparison/comparator.py tests/
git commit -m "$(cat <<'EOF'
refactor(cli): rename --final-only to --default-points

config.final_only → config.default_points. CLI flag --final-only →
--default-points. Function parameters likewise. Aligns the legacy
"global default mode" knob with the new mode name from Task 1.

Semantics unchanged: when set, variables without an explicit
"mode" override get PointsMode (with empty points list, behaving
like the legacy final-value comparison) instead of NrmseMode.

Clean-break per standing policy; users with --final-only in scripts
must update.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extend scorer with declared-points handling (no time_tolerance yet)

**Goal:** Implement the declared-points algorithm in `_compare_points`. Each point gets a target (explicit `value` or interpolated `ref(time)`), a per-point or default tolerance, and an abs/rel tolerance mode. Strict-time check (`time_tolerance == 0`) only — box check lands in Task 4.

**Files:**
- Modify: `src/dstf/comparison/comparator.py` (replace the `NotImplementedError` branch in `_compare_points`)
- Test: `tests/test_comparator.py` (append a new `TestPointsDeclaredPath` class)

- [ ] **Step 1: Write failing tests first**

Append to `tests/test_comparator.py`:

```python
class TestPointsDeclaredPath:
    """Declared-points semantics: per-point target, tolerance, and mode."""

    def _make_traj(self):
        # Piecewise trajectory: ref(0)=0, ref(1)=1, ref(2)=2, ref(3)=3,
        # ref(4)=4, ref(5)=5. Linear ramp y = t. Act has small offset
        # around t=2 to test pass/fail at a specific point.
        import numpy as np
        ref_t = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        ref_v = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        act_t = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        act_v = np.array([0.0, 1.001, 2.05, 3.001, 4.001, 5.001])
        return ref_t, ref_v, act_t, act_v

    def test_implicit_final_check_unchanged(self):
        """Empty points list → behaves identically to legacy final-only."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = _compare_points(ref_t, ref_v, act_t, act_v,
                                 points=None, tolerance=0.01)
        # act[-1] = 5.001, ref[-1] = 5.0, delta = 0.001 < 0.01 → PASS.
        assert result.passed
        assert result.diagnostics["delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_passes(self):
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=3: target = ref(3) = 3.0; act(3) = 3.001; delta = 0.001.
        # Tolerance 0.01 → PASS.
        result = _compare_points(ref_t, ref_v, act_t, act_v,
                                 points=[{"time": 3.0}], tolerance=0.01)
        assert result.passed
        assert result.diagnostics["scored_points"] == 1
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_fails(self):
        """Point at t=2 hits the 0.05 act offset → exceeds 0.01 tolerance."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = _compare_points(ref_t, ref_v, act_t, act_v,
                                 points=[{"time": 2.0}], tolerance=0.01)
        assert not result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.05, abs=1e-9)

    def test_explicit_value_point_baseline_free(self):
        """Point with explicit ``value`` is baseline-free — passes any
        ref. Use a synthetic empty ref array to prove the scorer never
        reads it for absolute-value points.
        """
        import numpy as np
        from dstf.comparison.comparator import _compare_points
        # Empty ref arrays: an absolute-value point should still score.
        ref_t = np.array([])
        ref_v = np.array([])
        act_t = np.array([0.0, 1.0, 2.0, 3.0])
        act_v = np.array([0.0, 1.0, 2.0, 3.0])
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 2.0, "value": 2.0, "tolerance": 0.01}],
            tolerance=1.0,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.0, abs=1e-9)

    def test_per_point_tolerance_overrides_global(self):
        """A wider per-point tolerance lets a point pass that the
        global tolerance would fail."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=2: delta=0.05. Global tolerance=0.01 (would fail);
        # per-point tolerance=0.1 (passes).
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 2.0, "tolerance": 0.1}],
            tolerance=0.01,
        )
        assert result.passed

    def test_relative_tolerance_mode(self):
        """tolerance_mode='rel' scales tol by |target|."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=4: target=ref(4)=4.0; act(4)=4.001; delta=0.001.
        # rel-tolerance 0.001 → limit = 0.001 * 4 = 0.004 → PASS.
        # rel-tolerance 0.0001 → limit = 0.0001 * 4 = 0.0004 → FAIL.
        passing = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 4.0, "tolerance": 0.001, "tolerance_mode": "rel"}],
            tolerance=0.01,
        )
        failing = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 4.0, "tolerance": 0.0001, "tolerance_mode": "rel"}],
            tolerance=0.01,
        )
        assert passing.passed
        assert not failing.passed

    def test_null_time_resolves_to_trace_end(self):
        """time: null sentinel = the trace's final time (act_time[-1]
        when ref_time is empty, else ref_time[-1])."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at time=null: target = ref(5) = 5.0; act(5) = 5.001; delta=0.001.
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": None, "tolerance": 0.01}],
            tolerance=0.01,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_multiple_points_all_must_match(self):
        """A leaf with 3 points fails if any one fails."""
        from dstf.comparison.comparator import _compare_points
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # t=1 (delta=0.001 ✓), t=2 (delta=0.05 ✗), t=3 (delta=0.001 ✓).
        # Tolerance 0.01 → overall FAIL.
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 1.0}, {"time": 2.0}, {"time": 3.0}],
            tolerance=0.01,
        )
        assert not result.passed
        assert result.diagnostics["scored_points"] == 3
        assert result.diagnostics["failed_points"] == 1
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_comparator.py::TestPointsDeclaredPath -v
```

Expected: 8 FAIL with `NotImplementedError: Declared-points scoring lands in Task 3 of the points-mode plan.` (from Task 1's stub).

- [ ] **Step 3: Implement the declared-points scorer**

In `src/dstf/comparison/comparator.py`, replace the `_compare_points` function from Task 1 entirely with:

```python
def _compare_points(
    ref_time: np.ndarray,
    ref_values: np.ndarray,
    act_time: np.ndarray,
    act_values: np.ndarray,
    points: Optional[list[dict]] = None,
    tolerance: float = 1e-4,
) -> VariableComparison:
    """Compare actual vs reference at declared time points.

    When ``points`` is None or empty, falls back to the legacy final-
    value check (act[-1] vs ref[-1] with ``tolerance``). When ``points``
    is a non-empty list, each entry is a checkpoint:

      ``time`` — absolute time, or None for "trace's final time".
      ``value`` — absolute target. If absent, target = ref(time).
      ``tolerance`` — per-point y-tolerance (defaults to ``tolerance``).
      ``tolerance_mode`` — "abs" (default) | "rel" (scale by |target|).
      ``time_tolerance`` — x-tolerance for the box check (Task 4 of
        the points-mode plan; defaults to 0 = strict-time).

    Pass iff every scored point's delta is within its y-limit.
    """
    if not points:
        # Implicit final-only — legacy behavior.
        if len(ref_values) == 0 or len(act_values) == 0:
            return VariableComparison(
                index=0, name="", passed=False,
                nrmse=float("inf"), rmse=float("inf"),
                signal_range=0.0,
                max_abs_error=float("inf"),
                max_abs_error_time=0.0,
                reference_final=float("nan"),
                actual_final=float("nan"),
                mode="points",
                diagnostics={"error": "empty trajectory"},
            )
        ref_final = float(ref_values[-1])
        act_final = float(act_values[-1])
        delta = abs(act_final - ref_final)
        passed = delta < tolerance
        return VariableComparison(
            index=0, name="", passed=passed,
            nrmse=delta, rmse=delta, signal_range=0.0,
            max_abs_error=delta,
            max_abs_error_time=float(ref_time[-1]) if len(ref_time) else 0.0,
            reference_final=ref_final, actual_final=act_final,
            mode="points",
            diagnostics={"tolerance": tolerance, "delta": delta},
        )

    # Declared-points path.
    trace_end = (
        float(ref_time[-1]) if len(ref_time)
        else float(act_time[-1]) if len(act_time)
        else 0.0
    )
    scored = 0
    failed = 0
    worst_delta = 0.0
    worst_t = 0.0
    for point in points:
        t = point.get("time")
        if t is None:
            t = trace_end
        else:
            t = float(t)
        # Resolve target.
        explicit_value = point.get("value")
        if explicit_value is not None:
            target = float(explicit_value)
        else:
            if len(ref_time) == 0:
                # Ref-relative point with no reference data is skipped.
                # The ``is_baseline_free`` invariant should prevent us
                # from getting here, but we guard anyway.
                continue
            target = float(np.interp(t, ref_time, ref_values))
        # Resolve tolerance + mode.
        per_tol = point.get("tolerance")
        per_tol = float(per_tol) if per_tol is not None else float(tolerance)
        mode = point.get("tolerance_mode", "abs")
        y_limit = per_tol * abs(target) if mode == "rel" else per_tol
        # Single-point evaluation (Task 4 will replace this with a box).
        if len(act_time) == 0:
            continue
        act_at_t = float(np.interp(t, act_time, act_values))
        delta = abs(act_at_t - target)
        scored += 1
        if delta > worst_delta:
            worst_delta = delta
            worst_t = t
        if delta > y_limit:
            failed += 1

    passed = scored > 0 and failed == 0
    return VariableComparison(
        index=0, name="", passed=passed,
        nrmse=worst_delta, rmse=worst_delta, signal_range=0.0,
        max_abs_error=worst_delta, max_abs_error_time=worst_t,
        reference_final=float(ref_values[-1]) if len(ref_values) else float("nan"),
        actual_final=float(act_values[-1]) if len(act_values) else float("nan"),
        mode="points",
        diagnostics={
            "scored_points": scored,
            "failed_points": failed,
            "worst_delta": worst_delta,
            "worst_time": worst_t,
        },
    )
```

- [ ] **Step 4: Run the new tests + full suite**

```bash
uv run pytest tests/test_comparator.py::TestPointsDeclaredPath -v
uv run pytest -q
```

Expected: 8 PASS for the new class. Full suite 804 passed (796 previous + 8 new) + 0 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/comparison/comparator.py tests/test_comparator.py
git commit -m "$(cat <<'EOF'
feat(points): declared-points scoring path (no x-tolerance yet)

Replace the NotImplementedError stub from Task 1 with the
declared-points algorithm: per-point target (explicit ``value`` or
interpolated ``ref(time)``), per-point tolerance with abs/rel mode,
null-time sentinel resolves to trace end. Pass iff every scored
point's delta is within its y-limit.

Strict-time only (single-point evaluation at t). The time_tolerance
box check lands in Task 4.

Eight new tests in TestPointsDeclaredPath: implicit-final preserved,
single-point ref-relative pass/fail, baseline-free with empty ref,
per-point tolerance override, abs vs rel mode, null-time → trace end,
multi-point all-must-match.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `time_tolerance` (x-axis) box check to scorer

**Goal:** Replace single-point evaluation in `_compare_points` with a box check: find `min |act(t) - target|` for `t in [t_lo, t_hi]` where the box is `[time ± time_tolerance]` clipped to the leaf's window. `time_tolerance == 0` degenerates to single-point check (preserves Task 3 behavior).

**Files:**
- Modify: `src/dstf/comparison/comparator.py` (extend `_compare_points` with box check)
- Test: `tests/test_comparator.py` (append 4-5 new tests for box semantics)

**Note on window threading:** The scorer signature does not take a window; window clipping is handled upstream in `tree_eval.py` (where the trajectory is pre-sliced before being passed to the scorer). For the box check, we use the actual time arrays we receive — they're already window-clipped if a window was set on the leaf. Time-tolerance simply defines the per-point local window inside that.

- [ ] **Step 1: Write failing tests for box semantics**

Append to `tests/test_comparator.py` inside `TestPointsDeclaredPath`:

```python
    def test_time_tolerance_passes_when_act_enters_box(self):
        """Box check: act curve must enter the [time±x_tol] × [target±y_lim]
        rectangle at least once. Construct an act curve that misses the
        target at t=3 exactly but hits it at t=2.95.
        """
        import numpy as np
        from dstf.comparison.comparator import _compare_points
        # ref doesn't matter — point has explicit value.
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act curve crosses 30 around t=2.95.
        act_t = np.array([0.0, 2.5, 2.9, 2.95, 3.0, 3.5, 5.0])
        act_v = np.array([0.0, 20.0, 28.0, 30.0, 32.0, 35.0, 40.0])
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 3.0, "value": 30.0,
                     "tolerance": 0.5, "time_tolerance": 0.2}],
            tolerance=0.01,
        )
        # At t=3.0, act is 32 (delta=2 > 0.5). Strict-time would FAIL.
        # Box from [2.8, 3.2] × [29.5, 30.5]: act hits 30.0 at t=2.95 → PASS.
        assert result.passed
        assert result.diagnostics["worst_delta"] < 0.5

    def test_time_tolerance_fails_when_act_misses_box(self):
        """If the act curve never enters the box, point fails."""
        import numpy as np
        from dstf.comparison.comparator import _compare_points
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act stays well above 30 across the whole [2.8, 3.2] window.
        act_t = np.array([0.0, 2.8, 3.0, 3.2, 5.0])
        act_v = np.array([0.0, 35.0, 36.0, 37.0, 40.0])
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 3.0, "value": 30.0,
                     "tolerance": 0.5, "time_tolerance": 0.2}],
            tolerance=0.01,
        )
        assert not result.passed
        # Worst delta within the box is at t=2.8 where act=35, target=30 → delta=5.
        assert result.diagnostics["worst_delta"] >= 5.0

    def test_time_tolerance_zero_degenerates_to_strict_time(self):
        """time_tolerance=0 must behave identically to a single-point check."""
        import numpy as np
        from dstf.comparison.comparator import _compare_points
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        act_t = np.array([0.0, 2.5, 3.0, 5.0])
        act_v = np.array([0.0, 28.0, 32.0, 40.0])
        # act(3.0) = 32, target = 30, delta = 2 > 0.5 → FAIL with strict time.
        with_xtol = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 3.0, "value": 30.0,
                     "tolerance": 0.5, "time_tolerance": 0.0}],
            tolerance=0.01,
        )
        without_xtol = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 3.0, "value": 30.0, "tolerance": 0.5}],
            tolerance=0.01,
        )
        # Both must give the same result.
        assert with_xtol.passed == without_xtol.passed == False
        assert (
            with_xtol.diagnostics["worst_delta"]
            == without_xtol.diagnostics["worst_delta"]
        )

    def test_time_tolerance_box_uses_interpolated_endpoints(self):
        """Box check evaluates the act curve at every sample inside
        [t_lo, t_hi] PLUS at the interpolated endpoints t_lo and t_hi
        — so we don't miss a curve entering between samples."""
        import numpy as np
        from dstf.comparison.comparator import _compare_points
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # Sparse act samples that bracket the target box.
        # Box: [2.5, 3.5] × [29.0, 31.0]. Samples at 2.0 and 4.0 with
        # values 20 and 40 → linear interp puts act(2.5) = 25, act(3.5) = 35.
        # Neither crosses 30 inside the box at a sample, but the
        # interpolated curve passes through (3.0, 30) which IS inside.
        act_t = np.array([0.0, 2.0, 4.0, 5.0])
        act_v = np.array([0.0, 20.0, 40.0, 50.0])
        result = _compare_points(
            ref_t, ref_v, act_t, act_v,
            points=[{"time": 3.0, "value": 30.0,
                     "tolerance": 1.0, "time_tolerance": 0.5}],
            tolerance=0.01,
        )
        # Linear interp at t=3 gives act=30, delta=0 → PASS.
        assert result.passed
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_comparator.py::TestPointsDeclaredPath -v -k "time_tolerance"
```

Expected: at least 3 of the 4 new tests fail. The first one (`test_time_tolerance_passes_when_act_enters_box`) fails because Task 3's strict-time check uses `np.interp(t=3.0, ...)` and gets act=32 > 30+0.5 → FAIL, but with the box check it should PASS.

- [ ] **Step 3: Add a box-check helper**

In `src/dstf/comparison/comparator.py`, immediately above `_compare_points`, add:

```python
def _min_delta_in_box(
    act_time: np.ndarray,
    act_values: np.ndarray,
    t_lo: float,
    t_hi: float,
    target: float,
) -> tuple[float, float]:
    """Find the smallest |act(t) - target| for t in [t_lo, t_hi].

    Evaluates at every act_time sample inside the window plus the
    interpolated endpoints t_lo and t_hi — so a curve that enters the
    box between samples is still detected.

    Returns (min_delta, t_at_min). When [t_lo, t_hi] is empty / outside
    the trajectory, returns (inf, t_lo) — caller decides whether that
    counts as a fail or a skip.
    """
    if len(act_time) == 0 or t_hi < t_lo:
        return float("inf"), t_lo
    # Endpoint values via interpolation.
    candidates = []
    if act_time[0] <= t_lo <= act_time[-1]:
        candidates.append((float(np.interp(t_lo, act_time, act_values)), t_lo))
    if act_time[0] <= t_hi <= act_time[-1]:
        candidates.append((float(np.interp(t_hi, act_time, act_values)), t_hi))
    # Interior samples.
    for i, t in enumerate(act_time):
        if t_lo <= t <= t_hi:
            candidates.append((float(act_values[i]), float(t)))
    if not candidates:
        return float("inf"), t_lo
    best_delta = float("inf")
    best_t = t_lo
    for v, t in candidates:
        d = abs(v - target)
        if d < best_delta:
            best_delta = d
            best_t = t
    return best_delta, best_t
```

- [ ] **Step 4: Replace the single-point branch in `_compare_points` with the box check**

Find the inner per-point loop in `_compare_points` (added in Task 3). Replace the section from `if len(act_time) == 0: continue` through `failed += 1` with:

```python
        if len(act_time) == 0:
            continue
        x_tol = point.get("time_tolerance", 0)
        x_tol = float(x_tol) if x_tol is not None else 0.0
        t_lo = max(t - x_tol, float(act_time[0]))
        t_hi = min(t + x_tol, float(act_time[-1]))
        if t_hi < t_lo:
            # Fully clipped (time outside trajectory + box doesn't reach in).
            continue
        delta, t_at_min = _min_delta_in_box(
            act_time, act_values, t_lo, t_hi, target,
        )
        scored += 1
        if delta > worst_delta:
            worst_delta = delta
            worst_t = t_at_min
        if delta > y_limit:
            failed += 1
```

- [ ] **Step 5: Run — all box-check tests + Task 3 tests must PASS**

```bash
uv run pytest tests/test_comparator.py::TestPointsDeclaredPath -v
uv run pytest -q
```

Expected: 12 PASS in TestPointsDeclaredPath (8 from Task 3 + 4 new). Full suite 808 passed.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/comparison/comparator.py tests/test_comparator.py
git commit -m "$(cat <<'EOF'
feat(points): time_tolerance (x-axis) box check

Replace single-point evaluation with a box check: each declared point
is scored as "does the act curve enter the [time±time_tolerance] ×
[target±y_limit] rectangle at least once?". When time_tolerance=0,
the box degenerates to a vertical line segment at t=time and the
behavior matches Task 3's strict-time check exactly.

Box evaluation considers every act_time sample inside [t_lo, t_hi]
plus the interpolated endpoints — catches curves that enter the box
between samples.

Four new tests: passes-when-act-enters, fails-when-act-misses, zero-
xtol-degenerates-to-strict-time, interpolated-endpoint-detection.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: JS live scorer port (`MODE_SCORERS['points']`)

**Goal:** Mirror the CLI algorithm in JS. Window-aware end-to-end via `_sliceLeafTrajectory`. Renames the registry key from `'final-only'` to `'points'`.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` (`MODE_SCORERS['final-only']` → `MODE_SCORERS['points']`, replace body)
- Test: `tests/test_interactive_points.py` (new file, scorer-isolation tests)

- [ ] **Step 1: Create test scaffold + write failing tests**

Create `tests/test_interactive_points.py`:

```python
"""Playwright tests for the points-mode JS layer.

Covers MODE_SCORERS['points'], MODE_PLOT_CONTRIBUTIONS['points'], and
MODE_PLOT_EDITORS['points'] (Tasks 5-8 of the points-mode plan).
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


def _mode_controls_html_points(values: dict) -> str:
    """Minimal points-mode controls HTML — passthrough fallback for
    the points list, plus a numeric tolerance input."""
    tol = values.get("tolerance", 1e-4)
    return (
        '<div class="mode-controls" data-mode="points" data-variable="h">'
        '<label><span>Tolerance</span>'
        f'<input type="number" step="any" data-field="tolerance" value="{tol}"></label>'
        '<label class="mc-field mc-passthrough">'
        '<textarea data-field="points" data-passthrough="true" rows="2"></textarea>'
        '</label>'
        "</div>"
    )


def _context_with_points_leaf(points=None, tolerance=0.01):
    """Fixture: one points leaf on variable 'h' with a piecewise-constant
    trajectory — explicit values at t=0..5 so tests don't depend on
    grid alignment.
    """
    ctx = _fixture_context()
    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = "points"
    leaf["mode_effective"] = "points"
    leaf["variable"] = "h"
    params = {"points": points, "tolerance": tolerance}
    leaf["params"] = params
    leaf["mode_values"] = dict(params)
    leaf["mode_controls_html"] = _mode_controls_html_points(params)
    leaf["cli_authoritative"] = False
    # Hand-tuned trajectory: act tracks ref except a 0.05 spike at t=2.
    t = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    ref = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    act = [0.0, 1.001, 2.05, 3.001, 4.001, 5.001]
    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1, "name": "h",
        "act_time": list(t), "act_values": list(act),
        "ref_time": list(t), "ref_values": list(ref),
    }
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"]
        for k in ctx["variables_by_name"]
    ]
    return ctx


def _render_with_context(tmp_path: Path, ctx: dict) -> Path:
    """Render a custom context to interactive.html."""
    from jinja2 import Environment, FileSystemLoader
    from test_interactive_playwright import _JS_SRC, _TEMPLATE_DIR
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path


# ---------------------------------------------------------------------------
# Task 5: live scorer
# ---------------------------------------------------------------------------

def test_points_scorer_implicit_final_passes(tmp_path, playwright_browser):
    """Empty points list → implicit final-value check. Fixture's
    act[-1]=5.001, ref[-1]=5.0, tolerance=0.01 → PASS."""
    ctx = _context_with_points_leaf(points=None, tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_declared_point_pass(tmp_path, playwright_browser):
    """Declared point at t=3 (delta 0.001) with tol 0.01 → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 3.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_declared_point_fail(tmp_path, playwright_browser):
    """Declared point at t=2 (delta 0.05) with tol 0.01 → FAIL."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is False


def test_points_scorer_baseline_free_with_explicit_value(
    tmp_path, playwright_browser,
):
    """Point with explicit value=2.0 at t=2: act(2)=2.05, delta=0.05.
    Tolerance 0.01 → FAIL. Confirms scorer reads explicit value over ref."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0, "value": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is False


def test_points_scorer_time_tolerance_box_pass(tmp_path, playwright_browser):
    """Strict t=2 fails (delta 0.05 > 0.01); box with x_tol=1.5 lets the
    scorer find a closer point near t=1 or t=3 (delta 0.001) → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0, "tolerance": 0.01, "time_tolerance": 1.5}],
        tolerance=0.05,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True


def test_points_scorer_window_clips_points(tmp_path, playwright_browser):
    """Window [3, 5] excludes the t=2 trouble point. Fixture's
    only-failing point is at t=2; with window applied → PASS."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}, {"time": 4.0}], tolerance=0.01,
    )
    leaf = ctx["tree_view"]["children"][0]
    leaf["window"] = {"start": 3.0, "end": 5.0}
    leaf["window_values"] = {"start": 3.0, "end": 5.0}
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    pass_state = page.evaluate("""
        () => recomputePassStates(TREE_VIEW)['/metrics/children/0']
    """)
    page.close()
    assert pass_state is True
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_interactive_points.py -v -k "test_points_scorer"
```

Expected: 6 tests FAIL. The current `MODE_SCORERS['final-only']` doesn't recognize the metric name `'points'` — `recomputePassStates` falls back to `!!node.passed` (the CLI value), which is fixture-set to `True`. So all the FAIL-expected tests are reporting True incorrectly.

- [ ] **Step 3: Replace `MODE_SCORERS['final-only']` with `MODE_SCORERS['points']`**

In `src/dstf/reporting/templates/interactive.js` find `'final-only': (leaf) => {` (line 146). Replace the entry (key + body) with:

```javascript
  'points': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    const params = (leafState[leaf.path] || {}).params || {};
    const points = Array.isArray(params.points) ? params.points : null;

    // Implicit final-only path: empty / null points → check act[-1] vs
    // ref[-1] with tolerance. Matches CLI's _compare_points fallback.
    if (!points || points.length === 0) {
      if (!refTime.length || !actTime.length) return !!leaf.passed;
      const refFinal = refValues[refValues.length - 1];
      const actFinal = actValues[actValues.length - 1];
      return Math.abs(actFinal - refFinal) < tol;
    }

    // Declared-points path. Same algorithm as _compare_points + box check.
    if (!actTime.length) return !!leaf.passed;
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                   : actTime[actTime.length - 1];
    let allMatched = true;
    for (const point of points) {
      let t = point.time;
      if (t == null) t = traceEnd;
      else t = Number(t);
      // Resolve target.
      let target;
      if (point.value != null) {
        target = Number(point.value);
      } else if (refTime.length) {
        target = _interpLinear(refTime, refValues, t);
      } else {
        // Ref-relative point with no ref data — skip.
        continue;
      }
      // Resolve y-tolerance + mode.
      const perTol = point.tolerance != null ? Number(point.tolerance) : tol;
      const mode = point.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      // Box check.
      const xTol = point.time_tolerance != null
        ? Number(point.time_tolerance) : 0;
      const tLo = Math.max(t - xTol, actTime[0]);
      const tHi = Math.min(t + xTol, actTime[actTime.length - 1]);
      if (tHi < tLo) continue;          // fully clipped
      const delta = _minDeltaInBox(actTime, actValues, tLo, tHi, target);
      if (delta > yLimit) {
        allMatched = false;
      }
    }
    return allMatched;
  },
```

In the same file, near `_interpLinear` (around line 239), add:

```javascript
function _minDeltaInBox(times, values, tLo, tHi, target) {
  // Mirrors comparator._min_delta_in_box: evaluate every sample inside
  // [tLo, tHi] plus the interpolated endpoints. Returns +Infinity when
  // the box is empty / outside the trajectory.
  if (!times.length || tHi < tLo) return Infinity;
  let best = Infinity;
  if (times[0] <= tLo && tLo <= times[times.length - 1]) {
    best = Math.min(best, Math.abs(_interpLinear(times, values, tLo) - target));
  }
  if (times[0] <= tHi && tHi <= times[times.length - 1]) {
    best = Math.min(best, Math.abs(_interpLinear(times, values, tHi) - target));
  }
  for (let i = 0; i < times.length; i++) {
    if (times[i] >= tLo && times[i] <= tHi) {
      best = Math.min(best, Math.abs(values[i] - target));
    }
  }
  return best;
}
```

- [ ] **Step 4: Run — 6 new tests must PASS**

```bash
uv run pytest tests/test_interactive_points.py -v -k "test_points_scorer"
uv run pytest -q
```

Expected: 6 PASS for the new file. Full suite 814 passed (808 previous + 6 new). One thing to watch: the cross-metric matrix from the range-fix plan (`test_window_edit_rescores_every_mode`) parameterizes over `final-only` — that test name uses the OLD mode key. After this commit it will fail because `final-only` no longer exists in MODE_SCORERS. Update the parameterization in that test from `'final-only'` to `'points'` as part of the same commit (it's a JS-rename consequence).

To find and update: `grep -n "final-only" tests/test_interactive_range_window.py` — there should be one parameterize tuple. Change `("final-only", True, True)` to `("points", True, True)`.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js \
        tests/test_interactive_points.py \
        tests/test_interactive_range_window.py
git commit -m "$(cat <<'EOF'
feat(reporter): MODE_SCORERS['points'] live scorer (replaces 'final-only')

Mirror the CLI _compare_points algorithm on the JS side. Empty points
list → implicit final-value check (matches Task 1 behavior). Declared
points → per-point target/tolerance/mode, with the time_tolerance box
check from Task 4. Window-aware via _sliceLeafTrajectory.

New helper _minDeltaInBox is the JS twin of comparator._min_delta_in_box
— evaluates every sample inside [t_lo, t_hi] plus the interpolated
endpoints, returns the smallest |act(t) - target|.

Cross-metric matrix at tests/test_interactive_range_window.py updated:
'final-only' parameterize tuple becomes 'points'.

Six new Playwright tests in test_interactive_points.py: implicit-final
PASS, declared-point PASS/FAIL, baseline-free explicit-value FAIL,
time_tolerance box PASS, window clipping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: JS plot decoration — translucent box + diamond marker

**Goal:** Replace the current `MODE_PLOT_CONTRIBUTIONS['final-only']` (a single vertical dotted line at the trace's final time) with the points-mode visual: one diamond marker per point at `(t_center, resolved_value)` plus a translucent rectangle covering the tolerance box. When `time_tolerance == 0` the rectangle degenerates to a zero-width vertical line — visually identical to the old vertical bar pattern.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js`
- Test: `tests/test_interactive_points.py` (append plot-decoration tests)

- [ ] **Step 1: Append failing plot tests**

Append to `tests/test_interactive_points.py`:

```python
# ---------------------------------------------------------------------------
# Task 6: plot decoration (translucent box + diamond marker)
# ---------------------------------------------------------------------------

def test_points_plot_no_decoration_when_no_points(tmp_path, playwright_browser):
    """Empty points list → no plot contribution (the implicit final
    case is invisible on the plot — same as the rest of the reporter)."""
    ctx = _context_with_points_leaf(points=None, tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {traces: c.traces.length, shapes: c.shapes.length};
        }
    """)
    page.close()
    assert contrib["traces"] == 0
    assert contrib["shapes"] == 0


def test_points_plot_diamond_per_point_when_xtol_zero(
    tmp_path, playwright_browser,
):
    """Two declared points, no time_tolerance → 2 diamond markers as a
    single trace (one trace with 2 (x,y) entries) + 2 zero-width
    rectangle shapes (one per point)."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "value": 2.0, "tolerance": 0.5},
            {"time": 4.0, "value": 4.0, "tolerance": 0.5},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {
                traces: c.traces.length,
                trace0_xs: c.traces[0] && c.traces[0].x,
                trace0_ys: c.traces[0] && c.traces[0].y,
                trace0_symbol: c.traces[0] && c.traces[0].marker.symbol,
                shapes: c.shapes.length,
                shape0_x0: c.shapes[0] && c.shapes[0].x0,
                shape0_x1: c.shapes[0] && c.shapes[0].x1,
            };
        }
    """)
    page.close()
    assert contrib["traces"] == 1, "Single marker trace with 2 points"
    assert contrib["trace0_xs"] == [2.0, 4.0]
    assert contrib["trace0_ys"] == [2.0, 4.0]
    assert contrib["trace0_symbol"] == "diamond"
    assert contrib["shapes"] == 2, "One rectangle per point"
    # With time_tolerance=0, rectangle is zero-width.
    assert contrib["shape0_x0"] == contrib["shape0_x1"] == 2.0


def test_points_plot_box_has_width_when_xtol_set(tmp_path, playwright_browser):
    """time_tolerance=0.2 → rectangle spans [time-0.2, time+0.2]."""
    ctx = _context_with_points_leaf(
        points=[{"time": 3.0, "value": 30.0,
                 "tolerance": 0.5, "time_tolerance": 0.2}],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    contrib = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
            const c = MODE_PLOT_CONTRIBUTIONS['points'](leaf, traj);
            return {x0: c.shapes[0].x0, x1: c.shapes[0].x1,
                    y0: c.shapes[0].y0, y1: c.shapes[0].y1};
        }
    """)
    page.close()
    assert contrib["x0"] == pytest.approx(2.8)
    assert contrib["x1"] == pytest.approx(3.2)
    assert contrib["y0"] == pytest.approx(29.5)
    assert contrib["y1"] == pytest.approx(30.5)
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_interactive_points.py -v -k "test_points_plot"
```

Expected: 3 FAIL. `MODE_PLOT_CONTRIBUTIONS['points']` doesn't exist yet — `MODE_PLOT_CONTRIBUTIONS['final-only']` (the old line) is still there but the registry key is wrong.

- [ ] **Step 3: Replace `MODE_PLOT_CONTRIBUTIONS['final-only']` with `'points'`**

In `src/dstf/reporting/templates/interactive.js`, find `'final-only': (leaf, traj) => {` (line 350). Replace the key and full body with:

```javascript
  'points': (leaf, traj) => {
    const params = (leafState[leaf.path] || {}).params || {};
    const tolDefault = Number(params.tolerance) || 0;
    const points = Array.isArray(params.points) ? params.points : null;
    if (!points || points.length === 0) {
      // Implicit final case — no plot contribution. The implicit
      // check just compares the final value; nothing useful to draw.
      return { traces: [], shapes: [] };
    }
    const refTime = traj.ref_time || [];
    const refValues = traj.ref_values || [];
    const actTime = traj.act_time || [];
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                   : (actTime.length ? actTime[actTime.length - 1] : 0);

    const xs = [];
    const ys = [];
    const shapes = [];
    for (const point of points) {
      let t = point.time;
      if (t == null) t = traceEnd;
      else t = Number(t);
      // Resolve target.
      let target;
      if (point.value != null) {
        target = Number(point.value);
      } else if (refTime.length) {
        target = _interpLinear(refTime, refValues, t);
      } else {
        continue;     // ref-relative without ref data — skip
      }
      // y-limit (resolved absolute size of the band).
      const perTol = point.tolerance != null ? Number(point.tolerance) : tolDefault;
      const mode = point.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      const xTol = point.time_tolerance != null
        ? Number(point.time_tolerance) : 0;
      // Marker.
      xs.push(t);
      ys.push(target);
      // Translucent rectangle. Width = 2 * xTol (zero when xTol=0 →
      // visually a vertical line segment thanks to Plotly drawing
      // zero-width rects as a single line stroke).
      shapes.push({
        type: 'rect', xref: 'x', yref: 'y',
        x0: t - xTol, x1: t + xTol,
        y0: target - yLimit, y1: target + yLimit,
        fillcolor: 'rgba(76,175,80,0.10)',
        line: { color: 'rgba(76,175,80,0.6)', width: 1, dash: 'dot' },
        name: `points_box:${leaf.path}:${xs.length - 1}`,
      });
    }
    if (!xs.length) return { traces: [], shapes };
    const traces = [{
      x: xs, y: ys, mode: 'markers', type: 'scatter',
      name: `Points ${leaf.path}`,
      marker: {
        color: '#2e7d32', size: 12, symbol: 'diamond',
        line: { color: 'white', width: 1.5 },
      },
      hoverinfo: 'x+y', showlegend: true,
    }];
    return { traces, shapes };
  },
```

- [ ] **Step 4: Run all points tests + full suite**

```bash
uv run pytest tests/test_interactive_points.py -v
uv run pytest -q
```

Expected: 9 PASS in `test_interactive_points.py` (6 from Task 5 + 3 new). Full suite 817 passed.

If the cross-metric matrix test or the html-snapshot test fails because the plot output structure for points changed, the structural-hash snapshot needs regenerating — `pytest tests/test_interactive_html_snapshot.py --update-snapshots` or whatever the project's update mechanism is. If unsure, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js \
        tests/test_interactive_points.py
git commit -m "$(cat <<'EOF'
feat(reporter): points plot decoration — diamond markers + tolerance box

Replace MODE_PLOT_CONTRIBUTIONS['final-only'] (a single vertical dotted
line at the trace's final time) with MODE_PLOT_CONTRIBUTIONS['points']:
diamond marker at (t_center, resolved_value) per declared point + a
translucent rectangle covering the [time±x_tol] × [target±y_limit]
box. When time_tolerance=0 the box degenerates to a zero-width line.

Empty points list → no plot contribution (consistent with the implicit
final-only check having no useful visual).

Three new tests cover: no-decoration when empty, diamond + rectangle
per declared point, rectangle width when time_tolerance > 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: JS editor scaffold — table + add + delete

**Goal:** New `MODE_PLOT_EDITORS['points']` IIFE with declared-points table mounted in the leaf's `.node-editor` slot. Columns: `Time | x-tol | Value | Mode | Tolerance | Match (live) | ×`. Buttons: `+ add point`. Delete column inline. No "Snapshot from ref" button yet (Task 8).

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` — insert new editor IIFE, near event-timing's editor for symmetry
- Test: `tests/test_interactive_points.py` (append editor-mount tests)

- [ ] **Step 1: Append editor tests**

Append to `tests/test_interactive_points.py`:

```python
# ---------------------------------------------------------------------------
# Task 7: editor scaffold (table + add + delete)
# ---------------------------------------------------------------------------

def test_points_editor_mounts_on_leaf_click(tmp_path, playwright_browser):
    """Clicking the points leaf header mounts the editor in the
    .node-editor slot."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    mounted = page.locator(
        '[data-path="/metrics/children/0"] .points-editor'
    ).count()
    page.close()
    assert mounted >= 1


def test_points_editor_renders_existing_points(tmp_path, playwright_browser):
    """Two declared points → two table rows."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "tolerance": 0.01},
            {"time": 4.0, "value": 4.0},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    page.close()
    assert rows == 2


def test_points_editor_add_button_appends_row(tmp_path, playwright_browser):
    """+ add point appends to the table AND to leafState.params.points."""
    ctx = _context_with_points_leaf(
        points=[{"time": 2.0}], tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor button.node-btn-add'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    state_len = page.evaluate("""
        () => (leafState['/metrics/children/0'].params.points || []).length
    """)
    page.close()
    assert rows == 2
    assert state_len == 2


def test_points_editor_delete_removes_row(tmp_path, playwright_browser):
    """Per-row × button removes from DOM and leafState."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 1.0, "tolerance": 0.01},
            {"time": 3.0, "tolerance": 0.01},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'tbody tr button.row-delete'
    ).first.click()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    remaining_time = page.evaluate("""
        () => {
            const pts = leafState['/metrics/children/0'].params.points || [];
            return pts.length === 1 ? Number(pts[0].time) : null;
        }
    """)
    page.close()
    assert rows == 1
    assert remaining_time == 3.0
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_interactive_points.py -v -k "test_points_editor"
```

Expected: 4 FAIL. The editor doesn't exist yet — `.points-editor` selectors all timeout.

- [ ] **Step 3: Add `MODE_PLOT_EDITORS['points']`**

In `src/dstf/reporting/templates/interactive.js`, find the existing `MODE_PLOT_EDITORS['event-timing'] = (function() {` block (around line 1614 after D82). Insert this new IIFE immediately after the event-timing editor's closing `})();` (and before `MODE_PLOT_EDITORS['dominant-frequency']`):

```javascript
// Points editor — declared-points table in the leaf slot.
// Each row authors one (time, value?, tolerance?, tolerance_mode,
// time_tolerance?) checkpoint. Pass/fail is computed live by
// MODE_SCORERS['points']; this editor just mutates leafState.
MODE_PLOT_EDITORS['points'] = (function() {

  function getPoints(leaf) {
    const st = leafState[leaf.path] || {};
    const p = st.params || (st.params = {});
    if (!Array.isArray(p.points)) p.points = [];
    return p.points;
  }

  function getDefaultTolerance(leaf) {
    const st = leafState[leaf.path] || {};
    const v = Number((st.params || {}).tolerance);
    return Number.isFinite(v) && v > 0 ? v : 1e-4;
  }

  function getTrajectory(leaf) {
    return (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
  }

  // Live match info per point: nearest delta inside the box, plus
  // the t at which it occurred. Used by the Match column.
  function evaluateLiveMatches(leaf) {
    const traj = getTrajectory(leaf);
    const sliced = _sliceLeafTrajectory(leaf, traj);
    const refTime = sliced.refTime;
    const refValues = sliced.refValues;
    const actTime = sliced.actTime;
    const actValues = sliced.actValues;
    const tol = getDefaultTolerance(leaf);
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                    : (actTime.length ? actTime[actTime.length - 1] : 0);
    return getPoints(leaf).map(pt => {
      let t = pt.time != null ? Number(pt.time) : traceEnd;
      let target;
      if (pt.value != null) target = Number(pt.value);
      else if (refTime.length) target = _interpLinear(refTime, refValues, t);
      else return { ok: null, delta: null, at: null };
      const perTol = pt.tolerance != null ? Number(pt.tolerance) : tol;
      const mode = pt.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      const xTol = pt.time_tolerance != null ? Number(pt.time_tolerance) : 0;
      if (!actTime.length) return { ok: null, delta: null, at: null };
      const tLo = Math.max(t - xTol, actTime[0]);
      const tHi = Math.min(t + xTol, actTime[actTime.length - 1]);
      if (tHi < tLo) return { ok: null, delta: null, at: null };
      const delta = _minDeltaInBox(actTime, actValues, tLo, tHi, target);
      // Best-t — use the canonical center for now (full search would
      // require returning t along with the min from _minDeltaInBox).
      // CLI-side reports the actual best_t; JS keeps it simple — Match
      // column shows delta only for points with x_tol = 0, plus the
      // box label "in box" for x_tol > 0.
      return { ok: delta <= yLimit, delta, at: t };
    });
  }

  // Render lifecycle.
  const mountedByLeaf = new WeakMap();

  function mount(container, leaf, commit) {
    const root = document.createElement('div');
    root.className = 'points-editor';
    container.appendChild(root);
    mountedByLeaf.set(leaf, { root });
    refreshEditor(leaf, commit);
  }

  function unmount(container) {
    const el = container.querySelector('.points-editor');
    if (el) el.remove();
  }

  function refreshEditor(leaf, commit) {
    const m = mountedByLeaf.get(leaf);
    if (!m) return;
    renderTable(m.root, leaf, commit);
  }

  function renderTable(root, leaf, commit) {
    const points = getPoints(leaf);
    const tolDefault = getDefaultTolerance(leaf);
    const matches = evaluateLiveMatches(leaf);

    root.innerHTML = '';

    const table = document.createElement('table');
    table.className = 'points-table';
    const thead = document.createElement('thead');
    thead.innerHTML = (
      '<tr>'
      + '<th>Time</th>'
      + '<th>x-tol</th>'
      + '<th>Value</th>'
      + '<th>Mode</th>'
      + '<th>Tolerance</th>'
      + '<th>Match (live)</th>'
      + '<th></th>'
      + '</tr>'
    );
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    points.forEach((pt, i) => {
      const tr = document.createElement('tr');

      // Time input — placeholder "final" when null.
      const timeTd = document.createElement('td');
      const timeInput = document.createElement('input');
      timeInput.type = 'number';
      timeInput.step = 'any';
      timeInput.placeholder = 'final';
      timeInput.value = pt.time != null ? String(pt.time) : '';
      timeInput.addEventListener('change', () => {
        const raw = timeInput.value.trim();
        if (raw === '') pt.time = null;
        else {
          const n = Number(raw);
          if (Number.isFinite(n)) pt.time = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      timeTd.appendChild(timeInput);
      tr.appendChild(timeTd);

      // x-tol input.
      const xtolTd = document.createElement('td');
      const xtolInput = document.createElement('input');
      xtolInput.type = 'number';
      xtolInput.step = 'any';
      xtolInput.placeholder = '0';
      xtolInput.value = pt.time_tolerance != null
        ? String(pt.time_tolerance) : '';
      xtolInput.addEventListener('change', () => {
        const raw = xtolInput.value.trim();
        if (raw === '') delete pt.time_tolerance;
        else {
          const n = Number(raw);
          if (Number.isFinite(n) && n >= 0) pt.time_tolerance = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      xtolTd.appendChild(xtolInput);
      tr.appendChild(xtolTd);

      // Value input — empty = ref-relative.
      const valTd = document.createElement('td');
      const valInput = document.createElement('input');
      valInput.type = 'number';
      valInput.step = 'any';
      valInput.placeholder = 'ref(t)';
      valInput.value = pt.value != null ? String(pt.value) : '';
      valInput.addEventListener('change', () => {
        const raw = valInput.value.trim();
        if (raw === '') delete pt.value;
        else {
          const n = Number(raw);
          if (Number.isFinite(n)) pt.value = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      valTd.appendChild(valInput);
      tr.appendChild(valTd);

      // Mode dropdown.
      const modeTd = document.createElement('td');
      const modeSel = document.createElement('select');
      for (const m of ['abs', 'rel']) {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        if ((pt.tolerance_mode || 'abs') === m) opt.selected = true;
        modeSel.appendChild(opt);
      }
      modeSel.addEventListener('change', () => {
        pt.tolerance_mode = modeSel.value;
        refreshEditor(leaf, commit);
        commit();
      });
      modeTd.appendChild(modeSel);
      tr.appendChild(modeTd);

      // Tolerance input.
      const tolTd = document.createElement('td');
      const tolInput = document.createElement('input');
      tolInput.type = 'number';
      tolInput.step = 'any';
      tolInput.placeholder = String(tolDefault);
      tolInput.value = pt.tolerance != null ? String(pt.tolerance) : '';
      tolInput.addEventListener('change', () => {
        const raw = tolInput.value.trim();
        if (raw === '') delete pt.tolerance;
        else {
          const n = Number(raw);
          if (Number.isFinite(n) && n > 0) pt.tolerance = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      tolTd.appendChild(tolInput);
      tr.appendChild(tolTd);

      // Match column.
      const matchTd = document.createElement('td');
      matchTd.className = 'match-cell';
      const m = matches[i] || {};
      if (m.ok === null) {
        matchTd.innerHTML = '<span style="color:#9e9e9e">·</span>';
      } else if (m.ok) {
        matchTd.innerHTML = (
          `<span style="color:#2e7d32">✓ matched</span> `
          + `(Δ=${m.delta.toExponential(2)})`
        );
      } else {
        matchTd.innerHTML = (
          `<span style="color:#c62828">✕ unmatched</span> `
          + `(Δ=${m.delta.toExponential(2)})`
        );
      }
      tr.appendChild(matchTd);

      // Delete.
      const delTd = document.createElement('td');
      const delBtn = document.createElement('button');
      delBtn.className = 'row-delete';
      delBtn.textContent = '✕';
      delBtn.title = 'Remove this point';
      delBtn.addEventListener('click', () => {
        points.splice(i, 1);
        refreshEditor(leaf, commit);
        commit();
      });
      delTd.appendChild(delBtn);
      tr.appendChild(delTd);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    root.appendChild(table);

    // Buttons row — Task 7 only has "+ add point". Snapshot button
    // arrives in Task 8.
    const btnRow = document.createElement('div');
    btnRow.className = 'points-editor-buttons';
    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add point';
    addBtn.addEventListener('click', () => {
      // Seed time from previous point + 0.5s, or null (= "final") on
      // an empty list.
      let seedTime = null;
      if (points.length) {
        const prev = points[points.length - 1];
        const prevT = prev.time != null ? Number(prev.time) : null;
        seedTime = prevT != null ? prevT + 0.5 : null;
      }
      points.push({ time: seedTime });
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(addBtn);
    root.appendChild(btnRow);
  }

  return {
    activate(leaf, plotEl, commit) {
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

Also update `register_mode_ui("points", PointsConfig)` in `src/dstf/reporting/ui/mode_controls.py` to set `has_plot_editor=True`:

Find:

```python
register_mode_ui("points", PointsConfig)
```

Replace with:

```python
register_mode_ui("points", PointsConfig, has_plot_editor=True)
```

This was set to False in Task 1 (matching the old `final-only` registration). Now that we have a real editor, flip it on. The `has_plot_editor` flag controls whether the leaf renders an editor activator hint; the actual MODE_PLOT_EDITORS lookup happens regardless.

- [ ] **Step 4: Run all points tests + full suite**

```bash
uv run pytest tests/test_interactive_points.py -v
uv run pytest -q
```

Expected: 13 PASS in `test_interactive_points.py` (9 from Tasks 5-6 + 4 new). Full suite 821 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js \
        src/dstf/reporting/ui/mode_controls.py \
        tests/test_interactive_points.py
git commit -m "$(cat <<'EOF'
feat(reporter): MODE_PLOT_EDITORS['points'] table editor (scaffold)

New IIFE for points-mode declared-points editing. Mounts a table in
the leaf's .node-editor slot when activated. Columns: Time | x-tol |
Value | Mode | Tolerance | Match (live) | ×.

Per-row inputs: time (placeholder "final" for null), x-tolerance,
value (placeholder "ref(t)" for ref-relative), abs/rel mode dropdown,
tolerance, plus a per-row delete button. Buttons row has "+ add point".
Snapshot from ref + zero-point fast-path placeholder land in Task 8.

Live Match column re-evaluates the box check on every state change —
shows ✓/✕ + delta, or "·" when neither ref nor explicit value lets us
score. Reuses _sliceLeafTrajectory + _minDeltaInBox from Tasks 4-5.

register_mode_ui("points", ..., has_plot_editor=True) flipped on so
the leaf's editor-activator hint renders.

Four new Playwright tests: editor mounts, existing points render,
add appends, delete removes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: JS editor — Snapshot from ref + zero-point fast-path placeholder

**Goal:** Round out the editor's Medium scope. Add `📸 Snapshot from ref` button: for every row where `value` is empty, fill in `value` with the current `ref(time)`. Idempotent (rows with explicit value untouched). And the zero-point fast-path: when the points list is empty, the table shows a single italic placeholder row "`final` · uses ref[-1] · tolerance {tol}" reinforcing the implicit-final behavior; first click on `+ add point` replaces it with an editable row.

**Files:**
- Modify: `src/dstf/reporting/templates/interactive.js` (extend the editor IIFE from Task 7)
- Test: `tests/test_interactive_points.py` (append snapshot + placeholder tests)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_interactive_points.py`:

```python
# ---------------------------------------------------------------------------
# Task 8: Snapshot from ref + zero-point fast-path placeholder
# ---------------------------------------------------------------------------

def test_points_editor_zero_point_placeholder_renders(
    tmp_path, playwright_browser,
):
    """Empty points list → italic placeholder row shows the implicit
    final-only behavior. No regular tbody rows for now."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    placeholder_count = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).count()
    placeholder_text = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).first.inner_text()
    page.close()
    assert placeholder_count == 1
    assert "final" in placeholder_text.lower()
    assert "ref" in placeholder_text.lower()


def test_points_editor_first_add_replaces_placeholder(
    tmp_path, playwright_browser,
):
    """Clicking + add point on an empty list removes the placeholder
    and shows a real editable row."""
    ctx = _context_with_points_leaf(points=[], tolerance=0.01)
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor button.node-btn-add'
    ).first.click()
    placeholder_count = page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        '.points-implicit-row'
    ).count()
    rows = page.locator(
        '[data-path="/metrics/children/0"] .points-editor tbody tr'
    ).count()
    page.close()
    assert placeholder_count == 0
    assert rows == 1


def test_points_editor_snapshot_from_ref_fills_empty_value_cells(
    tmp_path, playwright_browser,
):
    """📸 Snapshot from ref fills value for every row where value is
    None. Rows with explicit value are untouched."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "tolerance": 0.01},                       # ref-relative
            {"time": 4.0, "value": 99.0, "tolerance": 0.01},        # explicit
            {"time": 1.0, "tolerance": 0.01},                       # ref-relative
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'button.snapshot-btn'
    ).first.click()
    values = page.evaluate("""
        () => leafState['/metrics/children/0'].params.points.map(p => p.value)
    """)
    page.close()
    # Fixture trajectory has ref[t] = t exactly. So snapshot of t=2 → 2,
    # t=1 → 1. The explicit 99.0 is preserved.
    assert values == [2.0, 99.0, 1.0]


def test_points_editor_snapshot_idempotent_on_no_empty_rows(
    tmp_path, playwright_browser,
):
    """When all rows have explicit value, snapshot is a no-op."""
    ctx = _context_with_points_leaf(
        points=[
            {"time": 2.0, "value": 2.5, "tolerance": 0.01},
            {"time": 4.0, "value": 4.5, "tolerance": 0.01},
        ],
        tolerance=0.01,
    )
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    page.locator(
        '[data-path="/metrics/children/0"] > .node-header'
    ).first.click()
    page.locator(
        '[data-path="/metrics/children/0"] .points-editor '
        'button.snapshot-btn'
    ).first.click()
    values = page.evaluate("""
        () => leafState['/metrics/children/0'].params.points.map(p => p.value)
    """)
    page.close()
    assert values == [2.5, 4.5]
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_interactive_points.py -v -k "placeholder or snapshot"
```

Expected: 4 FAIL. The placeholder isn't rendered when points is empty, and the snapshot button doesn't exist.

- [ ] **Step 3: Add the Snapshot button + zero-point placeholder to the editor**

In `src/dstf/reporting/templates/interactive.js`, find the `renderTable` function inside the points-mode editor IIFE (added in Task 7). Replace its body with:

```javascript
  function renderTable(root, leaf, commit) {
    const points = getPoints(leaf);
    const tolDefault = getDefaultTolerance(leaf);
    const matches = evaluateLiveMatches(leaf);

    root.innerHTML = '';

    const table = document.createElement('table');
    table.className = 'points-table';
    const thead = document.createElement('thead');
    thead.innerHTML = (
      '<tr>'
      + '<th>Time</th>'
      + '<th>x-tol</th>'
      + '<th>Value</th>'
      + '<th>Mode</th>'
      + '<th>Tolerance</th>'
      + '<th>Match (live)</th>'
      + '<th></th>'
      + '</tr>'
    );
    table.appendChild(thead);
    const tbody = document.createElement('tbody');

    if (points.length === 0) {
      // Zero-point fast path: italic placeholder row reinforcing the
      // implicit final-only behavior. Spans all columns.
      const tr = document.createElement('tr');
      tr.className = 'points-implicit-row';
      const td = document.createElement('td');
      td.colSpan = 7;
      td.style.fontStyle = 'italic';
      td.style.color = '#666';
      td.textContent = (
        `final · uses ref[-1] · tolerance ${tolDefault} · `
        + `+ add point overrides`
      );
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      points.forEach((pt, i) => {
        const tr = document.createElement('tr');

        // Time input.
        const timeTd = document.createElement('td');
        const timeInput = document.createElement('input');
        timeInput.type = 'number';
        timeInput.step = 'any';
        timeInput.placeholder = 'final';
        timeInput.value = pt.time != null ? String(pt.time) : '';
        timeInput.addEventListener('change', () => {
          const raw = timeInput.value.trim();
          if (raw === '') pt.time = null;
          else {
            const n = Number(raw);
            if (Number.isFinite(n)) pt.time = n;
          }
          refreshEditor(leaf, commit);
          commit();
        });
        timeTd.appendChild(timeInput);
        tr.appendChild(timeTd);

        // x-tol input.
        const xtolTd = document.createElement('td');
        const xtolInput = document.createElement('input');
        xtolInput.type = 'number';
        xtolInput.step = 'any';
        xtolInput.placeholder = '0';
        xtolInput.value = pt.time_tolerance != null
          ? String(pt.time_tolerance) : '';
        xtolInput.addEventListener('change', () => {
          const raw = xtolInput.value.trim();
          if (raw === '') delete pt.time_tolerance;
          else {
            const n = Number(raw);
            if (Number.isFinite(n) && n >= 0) pt.time_tolerance = n;
          }
          refreshEditor(leaf, commit);
          commit();
        });
        xtolTd.appendChild(xtolInput);
        tr.appendChild(xtolTd);

        // Value input.
        const valTd = document.createElement('td');
        const valInput = document.createElement('input');
        valInput.type = 'number';
        valInput.step = 'any';
        valInput.placeholder = 'ref(t)';
        valInput.value = pt.value != null ? String(pt.value) : '';
        valInput.addEventListener('change', () => {
          const raw = valInput.value.trim();
          if (raw === '') delete pt.value;
          else {
            const n = Number(raw);
            if (Number.isFinite(n)) pt.value = n;
          }
          refreshEditor(leaf, commit);
          commit();
        });
        valTd.appendChild(valInput);
        tr.appendChild(valTd);

        // Mode dropdown.
        const modeTd = document.createElement('td');
        const modeSel = document.createElement('select');
        for (const m of ['abs', 'rel']) {
          const opt = document.createElement('option');
          opt.value = m;
          opt.textContent = m;
          if ((pt.tolerance_mode || 'abs') === m) opt.selected = true;
          modeSel.appendChild(opt);
        }
        modeSel.addEventListener('change', () => {
          pt.tolerance_mode = modeSel.value;
          refreshEditor(leaf, commit);
          commit();
        });
        modeTd.appendChild(modeSel);
        tr.appendChild(modeTd);

        // Tolerance input.
        const tolTd = document.createElement('td');
        const tolInput = document.createElement('input');
        tolInput.type = 'number';
        tolInput.step = 'any';
        tolInput.placeholder = String(tolDefault);
        tolInput.value = pt.tolerance != null ? String(pt.tolerance) : '';
        tolInput.addEventListener('change', () => {
          const raw = tolInput.value.trim();
          if (raw === '') delete pt.tolerance;
          else {
            const n = Number(raw);
            if (Number.isFinite(n) && n > 0) pt.tolerance = n;
          }
          refreshEditor(leaf, commit);
          commit();
        });
        tolTd.appendChild(tolInput);
        tr.appendChild(tolTd);

        // Match column.
        const matchTd = document.createElement('td');
        matchTd.className = 'match-cell';
        const m = matches[i] || {};
        if (m.ok === null) {
          matchTd.innerHTML = '<span style="color:#9e9e9e">·</span>';
        } else if (m.ok) {
          matchTd.innerHTML = (
            `<span style="color:#2e7d32">✓ matched</span> `
            + `(Δ=${m.delta.toExponential(2)})`
          );
        } else {
          matchTd.innerHTML = (
            `<span style="color:#c62828">✕ unmatched</span> `
            + `(Δ=${m.delta.toExponential(2)})`
          );
        }
        tr.appendChild(matchTd);

        // Delete.
        const delTd = document.createElement('td');
        const delBtn = document.createElement('button');
        delBtn.className = 'row-delete';
        delBtn.textContent = '✕';
        delBtn.title = 'Remove this point';
        delBtn.addEventListener('click', () => {
          points.splice(i, 1);
          refreshEditor(leaf, commit);
          commit();
        });
        delTd.appendChild(delBtn);
        tr.appendChild(delTd);

        tbody.appendChild(tr);
      });
    }
    table.appendChild(tbody);
    root.appendChild(table);

    // Buttons row.
    const btnRow = document.createElement('div');
    btnRow.className = 'points-editor-buttons';

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add point';
    addBtn.addEventListener('click', () => {
      let seedTime = null;
      if (points.length) {
        const prev = points[points.length - 1];
        const prevT = prev.time != null ? Number(prev.time) : null;
        seedTime = prevT != null ? prevT + 0.5 : null;
      }
      points.push({ time: seedTime });
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(addBtn);

    const snapshotBtn = document.createElement('button');
    snapshotBtn.className = 'node-btn snapshot-btn';
    snapshotBtn.textContent = '📸 Snapshot from ref';
    snapshotBtn.title = (
      'For every row whose Value is empty (ref-relative), fill in the '
      + 'current ref(time) as an explicit value. Converts ref-based '
      + 'points into baseline-free absolute points. Idempotent — rows '
      + 'with an explicit value are untouched.'
    );
    snapshotBtn.addEventListener('click', () => {
      const traj = getTrajectory(leaf);
      const refTime = traj.ref_time || [];
      const refValues = traj.ref_values || [];
      if (!refTime.length) return;
      const traceEnd = refTime[refTime.length - 1];
      points.forEach(pt => {
        if (pt.value != null) return;
        const t = pt.time != null ? Number(pt.time) : traceEnd;
        pt.value = _interpLinear(refTime, refValues, t);
      });
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(snapshotBtn);

    root.appendChild(btnRow);
  }
```

- [ ] **Step 4: Run — 4 new must PASS, all prior tests still PASS**

```bash
uv run pytest tests/test_interactive_points.py -v
uv run pytest -q
```

Expected: 17 PASS in `test_interactive_points.py` (13 from Tasks 5-7 + 4 new). Full suite 825 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/templates/interactive.js \
        tests/test_interactive_points.py
git commit -m "$(cat <<'EOF'
feat(reporter): points editor — Snapshot button + zero-point placeholder

Add the two pieces that complete the Medium scope of the points
editor:

  - "📸 Snapshot from ref" button — fills value with the current
    ref(time) for every row where value is empty. Idempotent (explicit
    values untouched). Workflow: author ref-relative points first,
    iterate on tolerances, then snapshot to lock current behavior in
    place as baseline-free absolutes.

  - Zero-point placeholder row — when points is empty, the table shows
    one italic row "final · uses ref[-1] · tolerance N · + add point
    overrides", reinforcing the implicit final-only fallback. First
    click on + add point replaces it with a real editable row.

Four new Playwright tests: placeholder renders, first add replaces it,
snapshot fills empty values only, snapshot idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Docs sweep + D84 entry + SESSION_HANDOFF + ideas.md

**Goal:** Mechanical rename across non-historical docs, append the D84 decision entry, update SESSION_HANDOFF state header, log the Full-scope draggable-marker follow-up in ideas.md.

**Files:**
- Modify: `docs/architecture.md`, `docs/extensibility.md`, `docs/SESSION_HANDOFF.md`, `docs/patterns.md`, `docs/usage.md`, `docs/vision.md`, `docs/related_tools_research/evaluation_report.md` — sed `final-only` / `final_only` / `FinalOnly` → `points` / `points` / `Points` (context-aware; brand-name docstrings → "points mode").
- Modify: `docs/decisions.md` — append D84.
- Modify: `docs/ideas.md` — log draggable-markers follow-up.
- Untouched: `docs/superpowers/plans/*.md`, `docs/superpowers/specs/*.md`, `docs/related_tools_research/llm_responses/*.md`, D1-D83 entry bodies in decisions.md.

- [ ] **Step 1: Enumerate the doc surface to update**

```bash
grep -rln "final-only\|final_only\|FinalOnly" docs/ \
  | grep -v "/superpowers/" \
  | grep -v "/llm_responses/" \
  | grep -v "decisions.md" \
  | sort
```

Expected: 6-8 files. Note them.

- [ ] **Step 2: Sed-rename `final-only`/`final_only`/`FinalOnly` in those files**

```bash
grep -rln "final-only\|final_only\|FinalOnly" docs/ \
  | grep -v "/superpowers/" \
  | grep -v "/llm_responses/" \
  | grep -v "decisions.md" \
  | xargs sed -i 's/final-only/points/g; s/final_only/points/g; s/FinalOnly/Points/g'
```

Verify:

```bash
grep -rn "final-only\|final_only\|FinalOnly" docs/ \
  | grep -v "/superpowers/" \
  | grep -v "/llm_responses/" \
  | grep -v "decisions.md" \
  || echo "CLEAN"
```

Expected: `CLEAN`.

After sed, manually skim each touched file to fix any awkward sentences. Sentences that read awkwardly (e.g., "final-only mode checks the final value" became "points mode checks the points value") must be edited inline. The sed pattern is intentionally aggressive; awkward replacements are expected and easier to spot manually than to anticipate via regex.

- [ ] **Step 3: Update CLAUDE.md mode list**

In `/mnt/d/Modelica/ModelicaTesting/CLAUDE.md`, find the line listing comparison-mode leaves (around the project-overview section — search for `nrmse` and `tube` together). It currently lists `nrmse / tube / final-only / range / event-timing / dominant-frequency`. Update to `nrmse / tube / points / range / event-timing / dominant-frequency`.

- [ ] **Step 4: Append D84 to decisions.md**

Append at the end of `docs/decisions.md`:

```markdown

## D84: `points` mode (final-only → points + multi-point + abs values + x-tolerance)

- **What**: Rename `final-only` to `points` and extend to a fully-
  capable point-based comparison mode. New schema accepts a list of
  declared checkpoints; each can have an explicit absolute target
  (baseline-free) or fall back to `ref(time)`, an absolute or
  relative y-tolerance, and a symmetric x-tolerance (`time_tolerance`)
  that turns the strict-time check into a 2D box check.
- **Why**: Three needs that the legacy final-only didn't serve —
  multi-point checking ("at t=1 the transient settles, at t=5 steady
  state matches, final value also OK"), absolute-value checking
  ("temperature reaches 350 K at t=3.2 ± 0.5"), and timing-uncertainty
  ("temperature reaches 30 °C at t≈3 — different solvers may hit at
  2.97 or 3.02"). Also closes the cross-metric consistency gap left
  by event-timing (D82) and dominant-frequency (D75) both having
  declared-list editors while final-only didn't.
- **Empty-list compatibility**: `points = None` or `[]` behaves
  identically to legacy final-only (act[-1] vs ref[-1] within
  tolerance). Existing tests continue to work without per-point
  authoring.
- **Clean break**: `"mode": "final-only"` and `"mode": "final_only"`
  in test specs no longer resolve — they fall through to NrmseMode
  (the no-mode default), which is wrong-but-different. Per the
  standing "no backward compat" policy. Verified: zero example test
  specs in this repo used `final-only`, so user-facing migration cost
  is zero. CLI flag `--final-only` and `config.final_only` likewise
  renamed to `--default-points` and `config.default_points`.
- **Editor surface (Medium scope)**: declared-points table in the
  leaf's .node-editor slot — Time | x-tol | Value | Mode | Tolerance |
  Match (live) | ×. Buttons: `+ add point`, `📸 Snapshot from ref`.
  Zero-point fast path shows an italic "final · uses ref[-1] ·
  tolerance N" placeholder row.
- **Plot decoration**: diamond markers at `(t_center, resolved_value)`
  + a translucent rectangle covering the box. When `time_tolerance=0`
  the rectangle degenerates to a vertical line segment.
- **Baseline-free integration (D83)**: `PointsMode.is_baseline_free()`
  returns True iff `points` is non-empty AND every point has an
  explicit `value`. Tests configured this way run via D83's
  short-circuit — no baseline file needed on disk.

### Rejected alternatives

- **Keep `final-only` and ship `points` as a separate mode** — adds
  a redundant editor, redundant docs, two overlapping mental models.
  The user explicitly preferred unification ("avoid method bloat in
  the HTML"). Empty `points` list IS final-only; renaming + extending
  collapses cleanly.
- **No null-time sentinel; require explicit numeric `time`** — couples
  spec to simulation `stop_time`. Any change to stop_time silently
  invalidates "final value" checks. `time: null` decouples cleanly.
- **Per-point partial scoring for mixed-baseline leaves** — score
  absolute points even when ref is missing, skip ref-relative ones
  with a warning. Creates PASS/FAIL ambiguity ("test passed when half
  the checks didn't run"). All-or-nothing baseline-free trigger
  preferred — users who want fully baseline-free commit to setting
  `value` everywhere.
- **Draggable plot markers as MVP** — same call as event-timing D82.
  Numeric table editing covers essentials; markers are polish that
  layers on later if usage shows demand. Logged as a follow-up in
  `ideas.md`.
- **Pyfunnel-style continuous x-tolerance integration** — separate
  tool for a separate problem (continuous-trajectory bounds vs
  discrete-checkpoint bounds). Tracked as ideas.md #25; can coexist
  with points-mode x-tolerance.

### Validation

- 20+ new CLI tests in `TestPointsDeclaredPath`: implicit-final
  preserved, single-point pass/fail, baseline-free absolute, per-point
  tolerance, abs/rel mode, null-time sentinel, multi-point, x-tolerance
  box pass/fail, x-tolerance=0 degenerate, interpolated endpoints.
- 17 new Playwright tests in `tests/test_interactive_points.py`:
  scorer (window-aware, declared paths, box check, window clipping),
  plot decoration (no-points + per-point markers + box dimensions),
  editor (mount, render, add, delete, placeholder, Snapshot button).
- Cross-metric matrix from range-fix Task 6 still passes after
  `'final-only'` → `'points'` rename.
- Full suite at +29 tests, 0 regressions.
```

- [ ] **Step 5: Update SESSION_HANDOFF.md**

Open `docs/SESSION_HANDOFF.md`. Update the test-count line at the top from whatever it currently says (was `791 tests passing` after D82) to `825 tests passing + 0 skipped`.

Also find the "Reporter-as-IDE" block (describes the per-mode editor surface). Find the bullet that mentions final-only (already became "points" via Step 2's sed). Verify it now reads coherently — if not, fix inline. Add a short clarifying sentence:

```markdown
* **points** (D84): replaces the legacy `final-only` mode. Empty list →
  legacy "check the final value" behavior. Non-empty list → declared
  points with abs/rel y-tolerance + x-tolerance box check, edited in
  a `.node-editor`-slot table with `+ add point` / `📸 Snapshot from
  ref`. Plot decoration: diamond marker + translucent box per point.
```

- [ ] **Step 6: Append draggable-markers follow-up to ideas.md**

In `docs/ideas.md` find the priority matrix at the top. Determine the next available index `N` by inspecting the table. Append:

```markdown
| N+1 | Points editor — draggable plot markers (Full scope) | M | Low | Diamond markers from MODE_PLOT_CONTRIBUTIONS['points'] become draggable via PointPlotEditor. Shift+click on plot adds a point at (clicked_x, clicked_y) as absolute-value; drag updates time + value (abs mode) or just time (ref-relative); right-click deletes. Mirrors tube and dom-frequency interaction. Defer until usage shows the numeric table is too slow. |
```

(Replace `N+1` with the actual computed index before committing.)

- [ ] **Step 7: Final verification**

```bash
uv run pytest -q
git status --short
```

Confirm:
- 825 passed + 0 skipped, 0 failures.
- Only doc files staged.

```bash
git add CLAUDE.md docs/
git commit -m "$(cat <<'EOF'
docs(points): D84 entry + sweep + SESSION_HANDOFF + ideas.md

Append D84 to decisions.md covering rename, extension semantics,
clean-break policy, baseline-free integration, rejected alternatives,
and full validation summary.

Mechanical rename across non-historical docs (architecture, extensibility,
patterns, usage, vision, evaluation_report, SESSION_HANDOFF) — final-only
→ points, FinalOnly → Points, final_only → points. Historical files
preserved: superpowers/plans/*, superpowers/specs/*, llm_responses/*,
D1-D83 entry bodies in decisions.md.

CLAUDE.md mode list updated.

ideas.md gains a row tracking the draggable-marker follow-up
(deferred per Medium-scope discipline).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Rollback plan

If any task fails in a way that's hard to fix incrementally:

```bash
# Revert just the failing task — let prior commits stand.
git reset --hard <previous-task-commit>
```

The rename in Task 1 is the highest-impact step; if Task 1 lands cleanly the rest are localized changes that can be redone.

---

## Scope reminders

**This plan does:**
- Rename `final-only` mode + CLI flag + config field to `points` family.
- Extend the scorer with declared points (per-point target, tolerance, mode) plus x-tolerance box check.
- JS live scorer + plot decoration mirror the CLI algorithm.
- Editor table with add/delete/Snapshot + zero-point placeholder.
- Mechanical doc + test rename, D84 decision entry, SESSION_HANDOFF state update.

**This plan does NOT do:**
- Backward-compat alias for `"final-only"` mode string (clean break).
- Draggable markers on the trajectory plot (Full scope).
- Auto-detection heuristics for points (no general algorithm).
- Per-point partial scoring when references are missing (all-or-nothing rule).
- `tolerance_mode: "band"` (Tube-specific concept).
- Pyfunnel-style continuous x-tolerance (different problem; ideas.md #25).

If a reviewer pushes to expand scope, the answer is "not in this plan — log as a follow-up."
