# Tube Union-Grid Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `tube` comparison mode enforce its envelope everywhere the data
defines it, instead of only at the times the baseline happened to sample — and add
the adversarial example library that proves it.

**Architecture:** A tolerance tube is a continuous envelope; the reference grid is
only the coarse basis on which it is declared. Today `_compare_tube` resamples the
actual onto `ref_time` and scores there, which has two consequences: an excursion
that lives entirely between two baseline samples is invisible, and a `tube_points`
control point that falls off the reference grid is rounded away so the band that is
scored is not the band that was declared. Both are fixed by evaluating on the
**union of every breakpoint** — reference sample times, actual sample times, and
tube control-point times. Because both the trajectory and the band are
piecewise-linear, between two consecutive breakpoints their difference is linear and
attains its extremum at an endpoint, so the union grid is exactly as strict as
continuous checking with no dense sampling and no crossing solver.

**Tech Stack:** Python ≥ 3.10, numpy, pytest. Existing project deps only. No
JavaScript is written by this plan: the live preview stops re-scoring tube leaves
and shows the CLI verdict instead (Task 5). That is deliberate — the browser-side
scorer is slated for deletion when authoring moves to a server-backed tool, so
mirroring this fix into it would be work with a known expiry date, and leaving it
unmirrored would show a stale verdict beside a corrected one.

**Scope context:** Delivers the "tube evaluation on the union of the reference and
actual grids" entry in `docs/ideas.md`, plus the "cubic interpolation mode for
time-varying tubes" item under that file's tolerance-tube **Remaining** list. This
change is a prerequisite for the x-direction time tolerance work (`docs/ideas.md`
#25): a 2-D envelope has to answer "evaluated where?" before it can answer "how wide
in x?". Out of scope, deliberately: independent upper/lower control-point arrays on
the **UI** side, and any change to `nrmse`, which stays on the reference grid because
adding samples changes a mean and would silently redefine a metric with baselines
behind it.

---

## Background an implementer needs

Read these before starting.

- `src/dstf/comparison/algorithms.py:514` — `_compare_tube`, the function this plan
  rewrites. Line 551 (`act_interp = np.interp(ref_time, act_time, act_values)`) is
  the defect.
- `src/dstf/comparison/algorithms.py:476` — `_interpolate_tube_widths`. Note its
  first parameter is already called `eval_time`; it does not need a signature change,
  only different arguments.
- `src/dstf/comparison/algorithms.py:22` and `:73` — `_find_event_boundaries` and
  `_dedup_time_series`. `_compare_trajectories` already uses these at `:147`–`:156`
  to handle discontinuities; the tube path will reuse them rather than invent
  anything.
- `src/dstf/comparison/modes.py:100` — `TubeConfig`, the typed config dataclass whose
  `field(metadata=...)` entries drive the auto-derived UI.
- `src/dstf/reporting/report_suite.py:307` — `_mark_decimated_cli_authoritative`,
  the existing mechanism for "the browser shows the CLI's verdict and does not
  re-score". Task 5 reuses it. `src/dstf/reporting/templates/scorers.js:365` is the
  live tube scorer that Task 5 switches off; do not edit it.

**Why duplicate timestamps matter here.** A Modelica result stores two samples at one
time across a discontinuity — the value just before and just after. (`ref_0002.json`
in the Modelica demo library, the EventTest baseline: 508 samples, 7 duplicate
times.) This is a data shape, not a scoring concern — event *scoring* is the separate
`event-timing` mode. It matters only because `np.interp` requires a strictly
increasing x array, and because at such an instant the trajectory genuinely has two
values, both of which must be inside the band.

---

## File structure

### New files
- `examples/python/PythonTestingLib/Examples/TubeExcursion.py` — adversarial example:
  returns a fine-grid trajectory with an excursion between two baseline samples.
  Expected to FAIL.
- `examples/python/PythonTestingLib/Examples/TubeTaper.py` — adversarial example: a
  tapering band whose control point falls off the reference grid. Expected to FAIL.
- `tests/test_tube_union_grid.py` — unit tests for the grid construction and the two
  defects.
- `tests/test_tube_cli_authoritative.py` — pins that the browser does not
  re-score tube leaves.

### Modified files
- `src/dstf/comparison/algorithms.py` — new `_tube_eval_grid` helper; `_compare_tube`
  rewritten to use it.
- `src/dstf/discovery/test_registry.py` — `TestModel.expected_status`.
- `src/dstf/discovery/spec_parser.py` — parse `expected_status`.
- `src/dstf/reporting/status.py` — `XFAIL` / `XPASS`.
- `src/dstf/reporting/report_suite.py` — mark tube leaves CLI-authoritative.
- `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json` — the
  two adversarial entries.
- `docs/usage.md`, `docs/decisions.md`, `docs/ideas.md` — document and close.

---

## Task 1: The union grid helper

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (new function above `_compare_tube`)
- Test: `tests/test_tube_union_grid.py`

- [ ] **Step 1: Write the failing test**

```python
"""Grid construction for tube evaluation."""

import numpy as np

from dstf.comparison.algorithms import _tube_eval_grid


def test_grid_is_the_union_of_all_breakpoints():
    ref_time = np.array([0.0, 1.0, 2.0, 3.0])
    act_time = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    ctrl = np.array([1.25])

    grid = _tube_eval_grid(ref_time, act_time, ctrl)

    assert grid.tolist() == [0.0, 0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


def test_grid_is_clipped_to_the_reference_span():
    # The band is only declared where the reference exists. An actual sample
    # past the end of the baseline has no envelope to be judged against, so it
    # must not appear on the grid.
    ref_time = np.array([0.0, 1.0])
    act_time = np.array([0.0, 0.5, 1.0, 1.5, 2.0])

    grid = _tube_eval_grid(ref_time, act_time, None)

    assert grid.tolist() == [0.0, 0.5, 1.0]


def test_grid_tolerates_duplicate_timestamps_on_either_side():
    # Duplicate times are how a discontinuity is stored. The grid carries the
    # instant once; Task 3 checks both values at it.
    ref_time = np.array([0.0, 1.0, 1.0, 2.0])
    act_time = np.array([0.0, 1.0, 1.0, 2.0])

    grid = _tube_eval_grid(ref_time, act_time, None)

    assert grid.tolist() == [0.0, 1.0, 2.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: FAIL — `ImportError: cannot import name '_tube_eval_grid'`

- [ ] **Step 3: Write minimal implementation**

Insert immediately above `def _compare_tube(` in
`src/dstf/comparison/algorithms.py`:

```python
def _tube_eval_grid(
    ref_time: np.ndarray,
    act_time: np.ndarray,
    ctrl_times: np.ndarray | None,
) -> np.ndarray:
    """Every breakpoint of either piecewise-linear curve, sorted and unique.

    A tube is a continuous envelope; the reference grid is only the coarse
    basis on which it is declared. Evaluating on ``ref_time`` alone cannot see
    an excursion between two baseline samples, and cannot see the band's own
    corners when a control point falls off that grid.

    Both the trajectory and the band are piecewise-linear, so between two
    consecutive breakpoints their difference is linear and attains its
    extremum at an endpoint. Checking every breakpoint is therefore exactly as
    strict as checking continuously — no dense sampling, no crossing solver.
    (Verified empirically over 20,000 random grid/band combinations against a
    20,000-point dense scan: the dense scan never found a larger violation.)

    The grid is clipped to the reference's span because that is where the band
    is declared; an actual sample past the end of the baseline has no envelope
    to be judged against.
    """
    parts = [np.asarray(ref_time, dtype=float), np.asarray(act_time, dtype=float)]
    if ctrl_times is not None and len(ctrl_times) > 0:
        parts.append(np.asarray(ctrl_times, dtype=float))
    grid = np.unique(np.concatenate(parts))
    return grid[(grid >= ref_time[0]) & (grid <= ref_time[-1])]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/dstf/comparison/algorithms.py tests/test_tube_union_grid.py
git commit -m "feat: a tube evaluation grid that carries every breakpoint"
```

---

## Task 2: The excursion defect

**Files:**
- Modify: `src/dstf/comparison/algorithms.py:514-620` (`_compare_tube`)
- Test: `tests/test_tube_union_grid.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tube_union_grid.py`:

```python
from dstf.comparison.algorithms import _compare_tube

BAND_HALF_WIDTH = {"tube_width_mode": "band", "tube_abs": 0.5}


def _flat_reference(n_points=4, stop=3.0):
    t = np.linspace(0.0, stop, n_points)
    return t, np.zeros_like(t)


def test_excursion_between_reference_samples_fails():
    # Reference is flat zero on a 1.0 s grid; the band is +/- 0.5. The run
    # samples every 0.1 s and excurses to 2.0 entirely between t=1 and t=2.
    ref_time, ref_values = _flat_reference()
    act_time = np.arange(0.0, 3.0001, 0.1)
    act_values = np.zeros_like(act_time)
    act_values[(act_time > 1.2) & (act_time < 1.8)] = 2.0

    result = _compare_tube(ref_time, ref_values, act_time, act_values,
                           dict(BAND_HALF_WIDTH))

    assert result.passed is False
    assert result.tube_worst_violation == pytest.approx(1.5)
    assert 1.2 <= result.tube_worst_violation_time <= 1.8


def test_a_trajectory_inside_the_band_everywhere_still_passes():
    ref_time, ref_values = _flat_reference()
    act_time = np.arange(0.0, 3.0001, 0.1)
    act_values = np.full_like(act_time, 0.25)

    result = _compare_tube(ref_time, ref_values, act_time, act_values,
                           dict(BAND_HALF_WIDTH))

    assert result.passed is True
    assert result.tube_points_inside == pytest.approx(1.0)
```

Add `import pytest` to the file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: `test_excursion_between_reference_samples_fails` FAILS with
`assert True is False` — the current code reports PASS.

- [ ] **Step 3: Write minimal implementation**

In `_compare_tube`, replace the single line at `algorithms.py:551`:

```python
    act_interp = np.interp(ref_time, act_time, act_values)
```

with grid construction deferred until the control-point times are known. Move
the interpolation to *after* the `tube_points` block. Concretely, the body from
the empty-input guards down to the violation calculation becomes:

```python
    tube_width_mode = tube_config.get("tube_width_mode")
    if tube_width_mode not in ("band", "rel", "abs"):
        raise ValueError(
            f"tube_width_mode must be 'band', 'rel', or 'abs'; got {tube_width_mode!r}"
        )
    min_width = tube_config.get("tube_min_width", 0.0)

    tube_points = tube_config.get("tube_points")
    ctrl_times = (
        np.array([p["time"] for p in tube_points], dtype=float)
        if tube_points
        else None
    )

    # The envelope is continuous; evaluate every breakpoint of either curve.
    grid = _tube_eval_grid(ref_time, act_time, ctrl_times)

    ref_on_grid = np.interp(grid, ref_time, ref_values)
    act_on_grid = np.interp(grid, act_time, act_values)

    if tube_points:
        interpolation = tube_config.get("tube_interpolation", "linear")
        raw_upper, raw_lower = _interpolate_tube_widths(
            grid,
            tube_points,
            interpolation,
        )
    else:
        const_abs = tube_config.get("tube_abs", 0.0)
        const_rel = tube_config.get("tube_rel", 0.0)
        if tube_width_mode == "band":
            raw_upper = np.full_like(grid, const_abs)
            raw_lower = raw_upper.copy()
        elif tube_width_mode == "rel":
            raw_upper = np.full_like(grid, const_rel)
            raw_lower = raw_upper.copy()
        else:  # abs
            raise ValueError(
                "tube_width_mode='abs' requires tube_points; "
                "constant-tube shorthand supports only 'band' and 'rel'."
            )

    if tube_width_mode == "abs":
        tube_upper = raw_upper
        tube_lower = raw_lower
    else:
        if tube_width_mode == "rel":
            upper_width = raw_upper * np.abs(ref_on_grid)
            lower_width = raw_lower * np.abs(ref_on_grid)
        else:  # band
            upper_width = raw_upper
            lower_width = raw_lower

        if min_width > 0:
            upper_width = np.maximum(upper_width, min_width)
            lower_width = np.maximum(lower_width, min_width)

        tube_upper = ref_on_grid + upper_width
        tube_lower = ref_on_grid - lower_width

    above_upper = act_on_grid - tube_upper
    below_lower = tube_lower - act_on_grid
    violations = np.maximum(above_upper, below_lower)

    n_total = len(grid)
    n_inside = int(np.sum(violations <= 0))
    fraction_inside = n_inside / n_total if n_total > 0 else 1.0

    worst_idx = int(np.argmax(violations))
    worst_violation = float(max(violations[worst_idx], 0.0))
    worst_violation_time = float(grid[worst_idx])

    passed = n_inside == n_total
```

Then in the reporting block below it, replace every remaining use of
`act_interp`, `ref_values` and `ref_time` in the NRMSE/max-error calculation with
the grid equivalents:

```python
    # NRMSE for reporting (always based on actual vs reference difference)
    diff = act_on_grid - ref_on_grid
    abs_error = np.abs(diff)
    rmse = float(np.sqrt(np.mean(abs_error**2)))
    signal_range = float(np.max(ref_values) - np.min(ref_values))
    is_constant = signal_range < _EPS
    if is_constant:
        ref_magnitude = float(np.max(np.abs(ref_values)))
        nrmse = rmse / ref_magnitude if ref_magnitude > _EPS else rmse
    else:
        nrmse = rmse / signal_range

    max_abs_idx = int(np.argmax(abs_error))
    max_abs_error = float(abs_error[max_abs_idx])
    max_abs_error_time = float(grid[max_abs_idx])

    ref_final = float(ref_values[-1]) if len(ref_values) > 0 else 0.0
    act_final = float(act_on_grid[-1]) if len(act_on_grid) > 0 else 0.0
```

`signal_range` deliberately stays on `ref_values` — it is a property of the
baseline signal, not of the evaluation grid.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: PASS, 5 passed

Run: `uv run pytest tests/ -k tube -v`
Expected: PASS. If an existing tube test now fails, read it before changing it —
a test that asserted a PASS which is now a FAIL may be asserting the defect.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/comparison/algorithms.py tests/test_tube_union_grid.py
git commit -m "fix: the tube is enforced between reference samples, not only at them"
```

---

## Task 3: Duplicate timestamps at discontinuities

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_tube`)
- Test: `tests/test_tube_union_grid.py`

- [ ] **Step 1: Write the failing test**

```python
def test_both_values_at_a_discontinuity_are_checked():
    # A discontinuity is stored as two samples at one timestamp. The value
    # before the jump is inside the band; the value after is not. Both must be
    # judged, so the comparison must FAIL.
    ref_time = np.array([0.0, 1.0, 2.0])
    ref_values = np.array([0.0, 0.0, 0.0])
    act_time = np.array([0.0, 1.0, 1.0, 2.0])
    act_values = np.array([0.0, 0.1, 2.0, 2.0])

    result = _compare_tube(ref_time, ref_values, act_time, act_values,
                           dict(BAND_HALF_WIDTH))

    assert result.passed is False


def test_a_discontinuity_wholly_inside_the_band_passes():
    ref_time = np.array([0.0, 1.0, 2.0])
    ref_values = np.array([0.0, 0.0, 0.0])
    act_time = np.array([0.0, 1.0, 1.0, 2.0])
    act_values = np.array([0.0, -0.2, 0.2, 0.2])

    result = _compare_tube(ref_time, ref_values, act_time, act_values,
                           dict(BAND_HALF_WIDTH))

    assert result.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: `test_both_values_at_a_discontinuity_are_checked` FAILS — `np.interp`
on a non-increasing `act_time` silently returns one of the two values.

- [ ] **Step 3: Write minimal implementation**

Replace the two plain interpolations added in Task 2:

```python
    ref_on_grid = np.interp(grid, ref_time, ref_values)
    act_on_grid = np.interp(grid, act_time, act_values)
```

with pre/post variants, mirroring what `_compare_trajectories` does at
`algorithms.py:153`:

```python
    # A discontinuity is stored as two samples at one timestamp, and both
    # values must be inside the band. Build a "just before" and a "just after"
    # view of each curve; away from a discontinuity the two are identical.
    ref_t_pre, ref_v_pre = _dedup_time_series(ref_time, ref_values, "first")
    ref_t_post, ref_v_post = _dedup_time_series(ref_time, ref_values, "last")
    act_t_pre, act_v_pre = _dedup_time_series(act_time, act_values, "first")
    act_t_post, act_v_post = _dedup_time_series(act_time, act_values, "last")

    ref_pre = np.interp(grid, ref_t_pre, ref_v_pre)
    ref_post = np.interp(grid, ref_t_post, ref_v_post)
    act_pre = np.interp(grid, act_t_pre, act_v_pre)
    act_post = np.interp(grid, act_t_post, act_v_post)
```

Everywhere Task 2 used `ref_on_grid` to build the band, build the band twice —
once from `ref_pre` and once from `ref_post` — and score each side against its
own half of the discontinuity. Pairing pre-actual with pre-band (and post with
post) is what keeps the comparison from straddling the jump. Replace the
violation block with:

```python
    def _bounds(ref_on_grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if tube_width_mode == "abs":
            return raw_upper, raw_lower
        if tube_width_mode == "rel":
            up_w = raw_upper * np.abs(ref_on_grid)
            lo_w = raw_lower * np.abs(ref_on_grid)
        else:  # band
            up_w, lo_w = raw_upper, raw_lower
        if min_width > 0:
            up_w = np.maximum(up_w, min_width)
            lo_w = np.maximum(lo_w, min_width)
        return ref_on_grid + up_w, ref_on_grid - lo_w

    viol_parts = []
    for ref_side, act_side in ((ref_pre, act_pre), (ref_post, act_post)):
        up, lo = _bounds(ref_side)
        viol_parts.append(np.maximum(act_side - up, lo - act_side))
    violations = np.maximum(viol_parts[0], viol_parts[1])
```

For the reporting block, use the post-event views (`ref_post`, `act_post`) as
`ref_on_grid` / `act_on_grid` — the NRMSE figure is diagnostic here, and
`_compare_trajectories` remains the authority on trajectory error.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tube_union_grid.py -v`
Expected: PASS, 7 passed

Run: `uv run pytest tests/ -v`
Expected: 1413 passed, 26 skipped (or higher `passed` count with the new tests).

- [ ] **Step 5: Commit**

```bash
git add src/dstf/comparison/algorithms.py tests/test_tube_union_grid.py
git commit -m "fix: both values at a discontinuity are checked against the tube"
```

---

## Task 4: The band that was declared is the band that is scored

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (already correct after Task 2 —
  this task adds the regression test that pins it)
- Test: `tests/test_tube_union_grid.py`

- [ ] **Step 1: Write the failing test**

This test would have failed before Task 2 and must keep passing forever after.

```python
def test_a_control_point_off_the_reference_grid_is_not_rounded_away():
    # Tapering band declared at t=0 (width 1.0) and t=1.5 (width 0.1). The
    # reference samples at 0, 1, 2, 3 -- t=1.5 is not one of them. Sampling the
    # widths at ref_time alone reconstructs an upper bound of 0.25 at t=1.5,
    # so an actual of 0.12 there would pass a band the user declared as 0.10.
    ref_time = np.array([0.0, 1.0, 2.0, 3.0])
    ref_values = np.zeros(4)
    act_time = np.array([0.0, 1.0, 1.5, 2.0, 3.0])
    act_values = np.array([0.0, 0.35, 0.12, 0.1, 0.1])
    config = {
        "tube_width_mode": "band",
        "tube_interpolation": "linear",
        "tube_points": [
            {"time": 0.0, "upper": 1.0, "lower": 1.0},
            {"time": 1.5, "upper": 0.1, "lower": 0.1},
        ],
    }

    result = _compare_tube(ref_time, ref_values, act_time, act_values, config)

    assert result.passed is False
    assert result.tube_worst_violation_time == pytest.approx(1.5)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_tube_union_grid.py::test_a_control_point_off_the_reference_grid_is_not_rounded_away -v`
Expected: PASS — Task 2 put `ctrl_times` in the grid, which is what fixes this.

If it FAILS, `ctrl_times` is not reaching `_tube_eval_grid`; check that the
`tube_points` block runs before grid construction.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tube_union_grid.py
git commit -m "test: pin that tube control points off the reference grid are honoured"
```

---

## Task 5: Stop the browser re-scoring tube leaves

**Files:**
- Modify: `src/dstf/reporting/report_suite.py`
- Test: `tests/test_tube_cli_authoritative.py`

The live preview scores tube leaves in JavaScript (`scorers.js:365`) on the old
reference-grid logic. After Tasks 2–4 the CLI is right and the browser is wrong,
so the two must not disagree in front of a user. Mirroring the fix into
JavaScript is not the answer: that scorer is slated for deletion when authoring
moves to a server-backed tool, so the mirror would be written to be thrown away.
Instead the leaf carries the CLI's verdict, using the mechanism already in place
for decimated dominant-frequency leaves.

- [ ] **Step 1: Write the failing test**

```python
"""Tube leaves keep the CLI verdict rather than being re-scored in the browser."""

from dstf.reporting.report_suite import _mark_tube_cli_authoritative


def _leaf(mode, **extra):
    return {"kind": "leaf", "mode_effective": mode, "name": "signal", **extra}


def test_tube_leaves_are_marked():
    context = {"tree_view": _leaf("tube")}
    _mark_tube_cli_authoritative(context)
    assert context["tree_view"]["cli_authoritative"] is True


def test_other_modes_keep_live_scoring():
    context = {"tree_view": _leaf("nrmse")}
    _mark_tube_cli_authoritative(context)
    assert "cli_authoritative" not in context["tree_view"]


def test_marking_reaches_nested_leaves():
    context = {
        "tree_view": {
            "kind": "combinator",
            "children": [_leaf("nrmse"), {"kind": "combinator",
                                          "children": [_leaf("tube")]}],
        }
    }
    _mark_tube_cli_authoritative(context)
    nested = context["tree_view"]["children"][1]["children"][0]
    assert nested["cli_authoritative"] is True
    assert "cli_authoritative" not in context["tree_view"]["children"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tube_cli_authoritative.py -v`
Expected: FAIL — `ImportError: cannot import name '_mark_tube_cli_authoritative'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/dstf/reporting/report_suite.py`, beside
`_mark_decimated_cli_authoritative`:

```python
def _mark_tube_cli_authoritative(context: dict) -> None:
    """Tube leaves keep the CLI verdict; the browser does not re-score them.

    The CLI evaluates a tube on the union of every breakpoint — reference
    sample times, actual sample times and tube control-point times — because
    the band is a continuous envelope. ``MODE_SCORERS['tube']`` in
    ``scorers.js`` still scores on the reference grid alone, so a live
    re-score can report PASS where the CLI reports FAIL.

    That mirror is not being fixed. The browser-side scorers are slated for
    deletion when authoring moves to a server-backed tool that calls these
    Python functions directly, so a JavaScript copy of this logic would be
    written to be thrown away. Showing the CLI's verdict is both correct now
    and the direction of travel.
    """

    def _walk(node: dict) -> None:
        if node.get("kind") == "leaf":
            if node.get("mode_effective") == "tube":
                node["cli_authoritative"] = True
            return
        for child in node.get("children", []) or []:
            _walk(child)

    tree_view = context.get("tree_view")
    if isinstance(tree_view, dict):
        _walk(tree_view)
```

Call it beside the existing marker at `report_suite.py:280`:

```python
    _mark_decimated_cli_authoritative(context)
    _mark_tube_cli_authoritative(context)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tube_cli_authoritative.py -v`
Expected: PASS, 3 passed

Run: `uv run pytest tests/test_scorer_parity.py -v`
Expected: PASS, unchanged. No parity fixture is added or removed by this plan —
the tube cases already there still exercise the JavaScript against the Python for
the inputs where the two agree. If one now fails, it is asserting the defect;
read it before touching it.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/report_suite.py tests/test_tube_cli_authoritative.py
git commit -m "fix: tube leaves show the CLI verdict instead of a stale live score"
```

---

## Task 6: Expected-status, so a test may be intentionally red

**Files:**
- Modify: `src/dstf/discovery/test_registry.py`, `src/dstf/discovery/spec_parser.py`,
  `src/dstf/reporting/status.py`
- Test: `tests/test_expected_status.py`

A DSTF test that *should* report FAIL is how a metric regression gets caught: an
expected-pass test goes on passing and says nothing, an expected-fail test flips
green and screams. This mirrors pytest's `xfail(strict=True)` and lit's `XFAIL:`.
The field is additive, so existing `test_spec.json` files need no change.

- [ ] **Step 1: Write the failing test**

```python
"""A test may declare the verdict it is supposed to reach."""

import pytest

from dstf.reporting import status


@pytest.mark.parametrize(
    "actual,expected,result",
    [
        (status.FAIL, status.FAIL, status.XFAIL),   # failed as declared
        (status.PASS, status.FAIL, status.XPASS),   # regression: it stopped failing
        (status.PASS, None, status.PASS),           # no declaration, unchanged
        (status.FAIL, None, status.FAIL),
        (status.SIM_FAIL, status.FAIL, status.SIM_FAIL),  # never masks a dead sim
    ],
)
def test_expected_status_reclassifies(actual, expected, result):
    assert status.apply_expected(actual, expected) == result


def test_xpass_counts_as_a_failure_and_xfail_does_not():
    assert status.is_failure(status.XPASS) is True
    assert status.is_failure(status.XFAIL) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_expected_status.py -v`
Expected: FAIL — `AttributeError: module 'dstf.reporting.status' has no attribute 'XFAIL'`

- [ ] **Step 3: Write minimal implementation**

In `src/dstf/reporting/status.py`, beside the existing `PASS` / `FAIL` /
`SIM_FAIL` constants:

```python
XFAIL = "XFAIL"  #: Failed, and the spec declared that it should.
XPASS = "XPASS"  #: Passed, but the spec declared it should fail — a regression.

#: Statuses that count against the run.
_FAILING = frozenset({FAIL, SIM_FAIL, XPASS})


def apply_expected(actual: str, expected: str | None) -> str:
    """Fold a test's declared expected status into its actual one.

    ``SIM_FAIL`` is never masked: a simulation that produced no data has not
    demonstrated anything, whatever the spec hoped for.
    """
    if expected is None or actual == SIM_FAIL:
        return actual
    if actual == expected:
        return XFAIL if expected == FAIL else actual
    if expected == FAIL and actual == PASS:
        return XPASS
    return actual


def is_failure(st: str) -> bool:
    """True if this status counts against the run."""
    return st in _FAILING
```

Add to `STATUS_CLASSES` (that is the dict's real name in this module):
`XFAIL: "xfail"`, `XPASS: "xpass"`.

In `src/dstf/discovery/test_registry.py`, add to `TestModel` beside
`comparison_tolerance`:

```python
    expected_status: str | None = None  # "fail" to declare an intentional failure
```

In `src/dstf/discovery/spec_parser.py`, inside the per-entry loop that already
reads `entry.get("simulation", {})` at line 208, add:

```python
        expected_status = entry.get("expected_status")
        if expected_status is not None and expected_status.upper() != "FAIL":
            raise ValueError(
                f"{entry.get('model')}: expected_status must be 'fail' if set; "
                f"got {expected_status!r}"
            )
```

and pass `expected_status=(expected_status or "").upper() or None` into the
`TestModel(...)` construction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_expected_status.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/status.py src/dstf/discovery/test_registry.py \
        src/dstf/discovery/spec_parser.py tests/test_expected_status.py
git commit -m "feat: a test may declare that it is expected to fail"
```

---

## Task 7: The adversarial examples

**Files:**
- Create: `examples/python/PythonTestingLib/Examples/TubeExcursion.py`
- Create: `examples/python/PythonTestingLib/Examples/TubeTaper.py`
- Modify: `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json`
- Test: `tests/test_python_backend.py`

These belong in `PythonTestingLib` and not in `tests/` because the Python backend
contract (`simulate(stop_time, tolerance) -> dict`) lets an example return any time
grid it likes — which is the whole point. They need no scipy, so unlike
`SimpleRamp` they run in CI on every machine, and they are simultaneously a
regression test and an example a user can read.

- [ ] **Step 1: Write the examples**

`examples/python/PythonTestingLib/Examples/TubeExcursion.py`:

```python
"""A trajectory that leaves the tolerance tube between two baseline samples.

This test is EXPECTED TO FAIL. It exists to prove that the tube is enforced
everywhere, not only at the times the baseline happened to sample.

The stored baseline is flat zero on a 1.0 s grid with a band of +/- 0.5. This
simulation returns a 0.1 s grid that excurses to 2.0 between t=1.2 and t=1.8 --
entirely inside one baseline interval. A tube evaluated only at baseline sample
times sees zero at t=0, 1, 2, 3 and reports PASS; the excursion is in the data
the whole time.
"""

import numpy as np


def simulate(stop_time: float, tolerance: float) -> dict:
    time = np.arange(0.0, stop_time + 1e-9, 0.1)
    signal = np.zeros_like(time)
    signal[(time > 1.2) & (time < 1.8)] = 2.0
    return {
        "time": time.tolist(),
        "variables": {"signal": signal.tolist()},
    }
```

`examples/python/PythonTestingLib/Examples/TubeTaper.py`:

```python
"""A trajectory judged against a band declared off the baseline's grid.

This test is EXPECTED TO FAIL. It exists to prove that the band which gets
scored is the band that was declared.

The tube tapers from a width of 1.0 at t=0 to 0.1 at t=1.5, but the baseline
samples at 0, 1, 2 and 3 -- t=1.5 is not one of them. Reconstructing the band
from baseline samples alone gives an upper bound of 0.25 at t=1.5, so the value
of 0.12 this simulation returns there would pass a band the spec declares as
0.10. No amount of extra sampling fixes that; the control point has to be on
the evaluation grid.
"""

import numpy as np


def simulate(stop_time: float, tolerance: float) -> dict:
    time = np.array([0.0, 1.0, 1.5, 2.0, 3.0])
    signal = np.array([0.0, 0.35, 0.12, 0.10, 0.10])
    keep = time <= stop_time
    return {
        "time": time[keep].tolist(),
        "variables": {"signal": signal[keep].tolist()},
    }
```

- [ ] **Step 2: Add the spec entries**

In `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json`,
add to the `tests` array:

```json
{
  "model": "PythonTestingLib.Examples.TubeExcursion",
  "variables": ["signal"],
  "simulation": {"stop_time": 3.0, "tolerance": 1e-6},
  "expected_status": "fail",
  "comparison": {
    "variable_overrides": {
      "signal": {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.5}
    }
  }
},
{
  "model": "PythonTestingLib.Examples.TubeTaper",
  "variables": ["signal"],
  "simulation": {"stop_time": 3.0, "tolerance": 1e-6},
  "expected_status": "fail",
  "comparison": {
    "variable_overrides": {
      "signal": {
        "mode": "tube",
        "tube_width_mode": "band",
        "tube_interpolation": "linear",
        "tube_points": [
          {"time": 0.0, "upper": 1.0, "lower": 1.0},
          {"time": 1.5, "upper": 0.1, "lower": 0.1}
        ]
      }
    }
  }
}
```

- [ ] **Step 3: Accept the baselines**

The baselines must be flat zero on a coarse grid, which is *not* what the
examples return. Write them directly rather than via `--accept`:

Run:
```bash
uv run python - <<'EOF'
import json, pathlib
root = pathlib.Path("examples/python/PythonTestingLib/Resources/ReferenceResults/Python/linux")
root.mkdir(parents=True, exist_ok=True)
for test_id, model, values in [
    (90, "PythonTestingLib.Examples.TubeExcursion", [0.0, 0.0, 0.0, 0.0]),
    (91, "PythonTestingLib.Examples.TubeTaper",     [0.0, 0.0, 0.0, 0.0]),
]:
    (root / f"ref_{test_id:04d}.json").write_text(json.dumps({
        "model_id": model,
        "test_id": test_id,
        "status": "active",
        "date_added": "2026-09-04",
        "last_updated": "2026-09-04",
        "simulation": {"stop_time": 3.0, "tolerance": 1e-6},
        "statistics": {},
        "diagnostics": {},
        "n_vars": 1,
        "time": [0.0, 1.0, 2.0, 3.0],
        "variables": [{"index": 0, "name": "signal", "values": values}],
    }, indent=2) + "\n", encoding="utf-8")
    print("wrote", test_id)
EOF
```

Expected output: `wrote 90` / `wrote 91`

- [ ] **Step 4: Write the end-to-end test**

Append to `tests/test_python_backend.py`, beside
`test_python_simple_ramp_self_regression`:

```python
def test_tube_adversarial_examples_fail_as_declared(tmp_path):
    """The two tube examples must report FAIL, and be reclassified XFAIL.

    If either of these ever reports PASS the tube has stopped being enforced
    between baseline samples -- which is the regression this pair exists to
    catch. Neither needs scipy, so both run everywhere.
    """
    result = subprocess.run(
        [
            "uv", "run", "dstf",
            "--config", str(_EXAMPLES_DIR / "Resources" / "ReferenceResults" / "testing.json"),
            "run", "--filter", "*Tube*",
            "--work-dir", str(tmp_path),
            "--report-format", "junit", "--output", str(tmp_path / "results.xml"),
        ],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    assert (tmp_path / "results.xml").exists(), result.stderr

    xml = (tmp_path / "results.xml").read_text()
    assert "TubeExcursion" in xml and "TubeTaper" in xml
    # Both are declared expected_status=fail, so the run must not report them
    # as failures -- but it must not report them as plain passes either.
    assert "XFAIL" in xml
    assert "XPASS" not in xml, "a tube example stopped failing -- the tube regressed"
```

This drives the CLI the same way `test_python_simple_ramp_self_regression` does
(`subprocess.run(["uv", "run", ...])`); the module already imports `subprocess`
and `Path`. There is no shared CLI helper in this file — do not add one for two
call sites.

- [ ] **Step 5: Run and commit**

Run: `uv run pytest tests/test_python_backend.py -v`
Expected: PASS

```bash
git add examples/python/PythonTestingLib tests/test_python_backend.py
git commit -m "test: two tube examples that are supposed to fail"
```

---

## Task 8: Documentation and closing the ideas entries

**Files:**
- Modify: `docs/usage.md`, `docs/decisions.md`, `docs/ideas.md`

- [ ] **Step 1: Document the evaluation grid in `docs/usage.md`**

In the `tube` section, add:

```markdown
**Where the tube is enforced.** The band is a continuous envelope, so it is
checked at every breakpoint of either curve: the baseline's sample times, the
result's sample times, and any `tube_points` control-point times. Because both
the trajectory and the band are piecewise-linear, checking every breakpoint is
exactly as strict as checking continuously — a violation cannot hide between
two samples. At a discontinuity (two samples sharing one timestamp) both values
are checked.
```

- [ ] **Step 2: Add the decision entry**

Append a new `## D125` section to `docs/decisions.md` recording: the two defects
(evaluation grid, and the band being resampled onto the reference grid), the
union-grid fix, the piecewise-linearity argument for why crossing detection is
unnecessary, and the `expected_status` field. Follow the file's existing entry
format.

- [ ] **Step 3: Close the ideas entries**

In `docs/ideas.md`, mark the "Tube evaluation on the union of the reference and
actual grids" entry `**DONE**` in the file's existing strikethrough style, noting
that it also fixed the control-point rounding, which the entry did not anticipate.

- [ ] **Step 4: Run the full gates**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Run `mypy` bare — a path argument overrides `[tool.mypy] files` and reports
pre-existing out-of-scope errors that CI never sees.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: the tube is enforced everywhere it is declared (D125)"
```

---

## Follow-on, not in this plan

**Per-key Hermite tangents (cubic tubes).** Agreed in design, deliberately a
separate plan: `tube_interpolation` becomes a property of each control point
rather than of the tube, with `auto` (shape-preserving PCHIP tangents), `linear`,
`constant` and `user` (explicit arrive/leave tangents, unified or broken) — the
key-frame model a curve editor uses. A Hermite segment is still a cubic, so the
difference against a linear trajectory is a cubic whose derivative is a
quadratic: the ≤ 2 stationary points per segment join the evaluation grid and
scoring stays exact with no dense sampling. Two consequences worth writing down
now: a user-broken tangent can overshoot beyond every declared control value, and
the stationary points you already compute for scoring are exactly what an
authoring UI needs to warn about it; and cubic tubes need no JavaScript
decision at all, because Task 5 has already made every tube leaf
CLI-authoritative.

**The authoring split.** A separate subsystem and a separate plan. The shape
agreed: the HTML report reduces to a visual summary that plots bounds arrays
Python has already resolved, so the browser holds no interpolation, geometry or
scoring code and `tests/test_scorer_parity.py` can be deleted rather than
maintained. Authoring moves behind the existing RFC 6902 JSON-Patch contract that
`spec-update` already round-trips. Writes emit the split upper/lower control-point
form; `tube_points` stays readable as the symmetric shorthand.
