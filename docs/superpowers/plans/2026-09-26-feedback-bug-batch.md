# Feedback Bug Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four defects the owner's first hands-on look at `dstf author` found that no later redesign step rebuilds: a criterion with a time window cannot be dragged; a new range / tube / scalar / dominant-frequency criterion arrives misconfigured; errors show raw exception text; the dominant-frequency spectrum squeezes real peaks into its left edge.

**Architecture:** Four independent fixes, one task each, then a gate over a copy of TRANSFORM. (1) The editable figure draws area shapes under lines, and a drag on anything that is not a handle snaps back silently; a drag on a bound's dotted `/outside` continuation is a drag on the bound. (2) A metric may supply a `seed` (a pure function of the reference / actual series) that makes a new leaf valid and sensible; the tool calls it when a criterion is added and when a criterion's metric is switched. (3) `PatchError`, `ValidationError` and `HandleEditError` carry a plain `reason` beside their located message; the tool shows the reason, never a class name, for the errors a user can cause. (4) The spectrum's x axis is ranged to the peaks, not to Nyquist.

**Tech Stack:** Python 3.10+, numpy, NiceGUI 3.16 (`[author]`; its plotly.js is **3.1.1** — measured `Plotly.version` in Chromium 2026-09-26; the "3.8.2" in `figures/plotly.py`'s docstring is a d3 version string inside the bundle), the report's vendored plotly.js 2.35.0, pytest, Playwright + Chromium, uv.

**Owner rulings this plan implements:** DSTF-060 (2026-09-26) — "These bugs get fixed whatever you choose" on the feedback board (https://claude.ai/artifact/88WhEgGHtSjT3pkiJTJ4Sm, settled block). Deferred to later steps because those steps rebuild the code: the "Add a criterion for x" dialog not opening its editor, the tree `+` wording and placement, the weighted-combinator hint (Test page step, Q3); rows waiting for the scan (Suite table step); the squeezed "t…" column (tolerance step, Q5).

**Evidence behind each fix (measured 2026-09-26, not argued):**
- Window drag: a real-mouse spike in Chromium over the tool's plotly.js 3.1.1 (`scratchpad/spike-window/`): today `layout.shapes` is `[range/max, range/max/outside, window]`; a drag on the bound inside the window moves the window rect, a drag outside it moves the `/outside` line — both then refused by `comparison/handles.py::_unnamed`. `layer: "below"` changes stacking but NOT what can be grabbed; per-shape `editable: false` has no effect under `edits.shapePosition`. Order `[window, outside, bound]` makes the bound win inside the window (V2b). The full-width `/outside` line covers the bound inside the window, so it must resolve to the bound.
- Misconfigured on add: `edit_tree.py:185` writes `{"metric", "variable"}` only; `edit_criteria.py:111` resets params to `{}` on a metric switch. range raises `RangeConfig requires at least one of min_value / max_value` (modes.py:584-587); tube `tube_width_mode must be 'band', 'rel', or 'abs'; got None` (algorithms.py:577-581); scalar requires a `quantity` (modes.py:654-658); dominant-frequency scores but always FAILs "no peaks declared" (algorithms.py:1607-1626). nrmse, points and event-timing are valid bare.
- Raw errors: the shell prefixes `type(e).__name__` at `authoring/shell/actions.py:209, 238, 388, 411` (and `plot_card.py:651`, `shell.py:139/247`, `runs.py:51`); `discovery/spec_update.py:156-157` wraps validator errors as `validator rejected the patched tree: <dotted path>: <message>`; `edit_on_plot.py:327-328` prefixes the JSON pointer.
- Spectrum: `figures/plotly.py::_layout` (~L662-664) sets only the `xaxis2` title, so Plotly autoranges 0..Nyquist, and Nyquist depends on the sample count, not the physics (TRANSFORM: axis to ~1.7e-3 Hz, peaks at ~1.2e-5 Hz).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash`, never `git checkout -- <path>` (to revert, copy the file aside and restore its bytes). Never push.
- Every shell command in the FOREGROUND with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&` in the same line. Assert any path variable non-empty before `cp`/`rm`.
- Test-first: write the test, run it, SEE it fail for the stated reason, then implement.
- Gates before each commit: the task's tests; before the last commit also `uv run pytest -m "not playwright" -q`, `uv run pytest -m playwright -q`, `uv run ruff check`, `uv run ruff format --check`, bare `uv run mypy`.
- Commit messages end with the session's attribution lines (the controller gives them).

## File map

| File | Change |
|---|---|
| `src/dstf/figures/plotly.py` | Task 1: area shapes first in `layout.shapes`; docstring version. Task 4: `xaxis2.range`. |
| `src/dstf/authoring/builtin/edit_on_plot.py` | Task 1: `/outside` resolves to its bound; a non-handle drag stages nothing. |
| `src/dstf/comparison/seeds.py` (new) | Task 2: `SeedData`, the four seeds, `seeded_leaf`. |
| `src/dstf/comparison/registry.py` | Task 2: `MetricDescriptor.seed`. |
| `src/dstf/comparison/builtins.py` | Task 2: register the seeds. |
| `src/dstf/authoring/builtin/edit_tree.py` | Task 2: seed on add. |
| `src/dstf/authoring/builtin/edit_criteria.py` | Task 2: seed on a metric switch. |
| `src/dstf/discovery/patch_apply.py`, `src/dstf/discovery/spec_update.py`, `src/dstf/comparison/validator.py`, `src/dstf/comparison/handles.py` | Task 3: `reason`; plain validator sentences. |
| `src/dstf/authoring/errors.py` (new) | Task 3: `user_text(e)`. |
| `src/dstf/authoring/shell/actions.py`, `plot_card.py`, `test_page.py` | Task 3: show `user_text(e)`. |
| `docs/extensibility.md`, `docs/decisions.md` | Task 5: the `seed` hook; D135. |

---

### Task 1: A windowed criterion drags; nothing that is not a handle swallows a drag

**Files:**
- Modify: `src/dstf/figures/plotly.py` (`_layout`, module docstring L13-18)
- Modify: `src/dstf/authoring/builtin/edit_on_plot.py` (`_act_handle`, ~L275-332)
- Test: `tests/test_figures.py`, `tests/test_edit_on_plot.py`, and one Playwright test (put it beside the existing browser test for on-plot editing — grep `tests/` for `playwright` and `edit_on_plot`/`shapePosition` to find it)

- [ ] **Step 1: Failing figure test — rects come before every other shape.** Build a `VariableView` with one `range` leaf that has `max_value` and a `window` (find the existing windowed-range fixture in `tests/test_figures.py`; if none, build the view the way the nearest range test does and add `"window": {"start": 2.0, "end": 8.0}` to the leaf). Assert, for BOTH `variable_figure(view)` and `variable_figure(view, editable=True)`:

```python
shapes = fig["layout"]["shapes"]
kinds = [s["type"] for s in shapes]
first_line = next(i for i, k in enumerate(kinds) if k != "rect")
assert all(k != "rect" for k in kinds[first_line:]), kinds  # every rect is first
names = [s.get("name") for s in shapes]
assert names.index(f"{leaf_path}/window") < names.index(f"{leaf_path}/range/max")
```

Run: `uv run pytest tests/test_figures.py -k window -q` — Expected: FAIL (the window rect is last).

- [ ] **Step 2: Implement.** In `_layout` (the one place both `variable_figure` and `plot_figure` hand their shapes to), stable-partition the shapes so every `type == "rect"` shape comes first, order otherwise kept:

```python
    # Area shapes under lines: under `edits.shapePosition` every shape takes
    # a drag and plotly.js has no per-shape switch that works (`layer` only
    # restacks — measured in Chromium on 3.1.1, 2026-09-26), so the one that
    # is on top wins. A window's or a box's rect drawn after a bound line
    # swallowed every drag on the bound inside it.
    shapes = [s for s in shapes if s.get("type") == "rect"] + [
        s for s in shapes if s.get("type") != "rect"
    ]
```

Before relying on it, grep `src/dstf/reporting/templates/` and `src/dstf/authoring/shell/` for anything that addresses a shape by INDEX (`shapes[`, `layout.shapes`) rather than by `name`; `figure_hook.py` maps `shapes[i]` to a name through the CURRENT figure, which stays correct. Report what you find; if the report's JS addresses shapes by index, stop and report.

Also correct the module docstring: "(measured on plotly.js 3.8.2, the build NiceGUI 3.16 bundles)" → "(measured on plotly.js 3.1.1, the build NiceGUI 3.16 bundles; the bundle's "3.8.2" string is d3's)".

Run the Step 1 test: PASS.

- [ ] **Step 3: Failing editor tests.** In `tests/test_edit_on_plot.py`, using the same session fixture its existing handle tests use, with a windowed `range` leaf open (`max_value: 0.9`, window 2..8):
  1. a drag event on shape name `<leaf>/range/max/outside` with `{"y0": 1.2, "y1": 1.2}` stages ONE `replace` of the leaf whose `max_value == 1.2`, and no error;
  2. a drag on `<leaf>/window` (any payload, e.g. `{"x0": 3.0, "x1": 9.0}`) stages NOTHING and raises nothing (the act returns `None`);
  3. the same for a `Box` primitive of a dominant-frequency leaf (`<leaf>/peaks/window/0`), if the fixture offers one cheaply; else skip this case and say so.

Run: `uv run pytest tests/test_edit_on_plot.py -k "outside or window" -q` — Expected: FAIL (1 raises "range/max/outside cannot be moved on the plot"; 2 raises "window cannot be moved on the plot").

- [ ] **Step 4: Implement** in `_act_handle`. Resolve the primitive FIRST, before the open-leaf check, so a non-handle shape of any leaf snaps back silently (the figure hook's `finally` redraw already snaps a shape back when nothing is staged):

```python
_OUTSIDE = "/outside"


def _handle_id(shape_name: str) -> str:
    """The primitive a dragged shape stands for: a bound's dotted
    continuation outside its window (`_hline`'s ``<id>/outside``, full
    width, so it also covers the bound inside the window) is the bound."""
    return shape_name[: -len(_OUTSIDE)] if shape_name.endswith(_OUTSIDE) else shape_name
```

and in `_act_handle`, after `if not handle: return None`:

```python
        handle = _handle_id(handle)
        paths = [p for p, _ in _leaf_paths(s, model_id, variable)]
        path = _leaf_path_of(handle, paths)
        if path is None:
            raise ValueError(f"{handle} names no leaf on {variable} of {model_id}")
        view = s.view(model_id, variable)  # full resolution, not the figure
        leaf = next((lv for lv in view.leaves if lv.path == path), None)
        primitive = next(
            (p for p in (leaf.geometry if leaf else ()) if p.id == handle), None
        )
        if primitive is None or getattr(primitive, "kind", None) != "handle":
            # A window, a box, a result line: drawn, not editable. The drag
            # snaps back; it is not an error the user made.
            return None
```

then keep the existing open-leaf check, tree lookup and `edit_handle` call, reusing `view` / `primitive` instead of computing them again inside the `try`. Keep the `except HandleEditError` re-wrap.

Run Step 3's tests: PASS. Run `uv run pytest tests/test_edit_on_plot.py tests/test_figures.py -q`: PASS (update any existing test that asserted the old "window cannot be moved" / "/outside cannot be moved" refusal: it now asserts no op and no error — list them in your report).

- [ ] **Step 5: A real-mouse browser test** (the spike showed dict-shape tests cannot see this). In the existing on-plot-editing Playwright test module, add a test that serves the tool on the fixture project with a windowed `range` leaf open, then with `page.mouse` (down, 8 intermediate moves, up):
  1. drags the bound line at a time INSIDE the window upward by ~40 px → the pending count rises by 1 and the editor's Max field changes; no error text in the editor's error slot;
  2. drags the dotted line OUTSIDE the window → same;
  3. drags the window interior away from any line → pending count unchanged, error slot empty.
Locate lines by computing pixel positions from the figure's axes (as the existing browser test does). Run: `uv run pytest -m playwright -k window -q`: PASS. Then revert Step 2's partition (copy aside, restore bytes after) and see case 1 FAIL — report it.

- [ ] **Step 6: Commit** `src/dstf/figures/plotly.py src/dstf/authoring/builtin/edit_on_plot.py` and the test files: "on-plot: a windowed criterion drags; a drag on what is not a handle snaps back".

---

### Task 2: A new criterion is valid when it is added

**Files:**
- Create: `src/dstf/comparison/seeds.py`
- Modify: `src/dstf/comparison/registry.py` (`MetricDescriptor`), `src/dstf/comparison/builtins.py` (registrations ~L137-213)
- Modify: `src/dstf/authoring/builtin/edit_tree.py:185`, `src/dstf/authoring/builtin/edit_criteria.py:109-113`
- Test: `tests/test_seeds.py` (new), `tests/test_builtin_capabilities.py`

- [ ] **Step 1: Failing seed tests** (`tests/test_seeds.py`). For a smooth reference `t = linspace(0, 10, 1001)`, `v = 300 + 50*sin(2*pi*0.5*t)`:

```python
import numpy as np
import pytest

from dstf.comparison.modes import resolve_mode  # modes.py:715
from dstf.comparison.seeds import SeedData, seeded_leaf

T = np.linspace(0.0, 10.0, 1001)
V = 300.0 + 50.0 * np.sin(2 * np.pi * 0.5 * T)
DATA = SeedData(ref=(T, V), act=(T, V))


@pytest.mark.parametrize("name", ["range", "tube", "scalar", "dominant-frequency"])
def test_a_seeded_leaf_resolves(name):
    leaf = seeded_leaf(name, "x", DATA)
    params = {k: v for k, v in leaf.items() if k not in ("metric", "variable")}
    resolve_mode({"mode": name, **params}, 1e-4)  # must not raise


def test_range_seed_brackets_the_data_with_a_margin():
    leaf = seeded_leaf("range", "x", DATA)
    assert leaf["min_value"] < V.min() and leaf["max_value"] > V.max()
    assert leaf["max_value"] - leaf["min_value"] <= 1.2 * (V.max() - V.min())


def test_dominant_frequency_seed_is_the_strongest_peak():
    leaf = seeded_leaf("dominant-frequency", "x", DATA)
    (peak,) = leaf["peaks"]
    assert peak["freq"] == pytest.approx(0.5, rel=0.05)
    assert peak["tolerance_mode"] == "rel"


def test_nrmse_points_event_timing_are_not_seeded():
    for name in ("nrmse", "points", "event-timing"):
        assert seeded_leaf(name, "x", DATA) == {"metric": name, "variable": "x"}


def test_no_data_leaves_the_bare_leaf():
    assert seeded_leaf("range", "x", SeedData(ref=None, act=None)) == {
        "metric": "range", "variable": "x"}
```

Adjust `resolve_mode`'s import and the override-dict shape to how `tree_eval._leaf_override_dict` builds it (grep it; the test must call the same path scoring uses). Also add: each seeded leaf, SCORED with the reference as both actual and reference through the comparator path the existing mode tests use, PASSES (grep `tests/test_modes*.py` / `tests/test_tree_eval.py` for a helper that scores one leaf). Run: FAIL (no module `seeds`).

- [ ] **Step 2: Implement `src/dstf/comparison/seeds.py`:**

```python
"""A new criterion's first values, from the data it will score (DSTF-060).

A leaf of `range`, `tube`, `scalar` or `dominant-frequency` with only a
`metric` and a `variable` is misconfigured or always fails, so the tool
seeds one when it is added or its metric is switched: a pure function of
the reference series (else the actual one) per metric, registered on its
`MetricDescriptor.seed`. Values are rounded to 6 significant digits: they
land in a spec file a person reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

#: The margin a seed leaves around the data, as a fraction of its span.
SEED_MARGIN = 0.05


@dataclass(frozen=True)
class SeedData:
    ref: tuple[np.ndarray, np.ndarray] | None
    act: tuple[np.ndarray, np.ndarray] | None

    def series(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The reference, else the actual; None when neither has a finite value."""
        for s in (self.ref, self.act):
            if s is not None and np.isfinite(s[1]).any():
                return s
        return None


def _round(x: float) -> float:
    return float(f"{float(x):.6g}")


def _span(v: np.ndarray) -> tuple[float, float, float]:
    finite = v[np.isfinite(v)]
    lo, hi = float(finite.min()), float(finite.max())
    span = hi - lo
    if span <= 0.0:
        span = abs(hi) or 1.0
    return lo, hi, span


def seed_range(data: SeedData) -> dict:
    s = data.series()
    if s is None:
        return {}
    lo, hi, span = _span(s[1])
    return {"min_value": _round(lo - SEED_MARGIN * span),
            "max_value": _round(hi + SEED_MARGIN * span)}


def seed_tube(data: SeedData) -> dict:
    s = data.series()
    if s is None:
        return {}
    _, _, span = _span(s[1])
    return {"tube_width_mode": "band", "tube_abs": _round(SEED_MARGIN * span)}


def seed_scalar(data: SeedData) -> dict:
    s = data.series()
    if s is None:
        return {}
    quantity = {"reduce": "final"}
    if data.ref is not None:
        return {"quantity": quantity, "tolerance_mode": "rel", "tolerance": 0.01}
    final = float(s[1][np.isfinite(s[1])][-1])
    half = SEED_MARGIN * (abs(final) or _span(s[1])[2])
    return {"quantity": quantity, "min_value": _round(final - half),
            "max_value": _round(final + half)}


def seed_dominant_frequency(data: SeedData) -> dict:
    s = data.series()
    if s is None:
        return {}
    from .algorithms import _compute_fft_spectrum, _find_top_n_peaks

    freqs, spec = _compute_fft_spectrum(*s)
    peaks = _find_top_n_peaks(freqs, spec, 1)
    if len(peaks) == 0:
        return {}
    f = float(peaks[0])
    bin_width = float(freqs[1] - freqs[0])
    # at least two bins wide: at low frequency a 5% window is narrower
    # than a bin, and matching needs a local maximum inside the window
    tol = max(SEED_MARGIN, 2.0 * bin_width / f)
    return {"peaks": [{"freq": _round(f), "tolerance": _round(tol),
                       "tolerance_mode": "rel"}]}


def seeded_leaf(metric: str, variable: str, data: SeedData) -> dict:
    """``{"metric", "variable"}`` plus the metric's seed, if it has one."""
    from .registry import metric as metric_descriptor

    leaf = {"metric": metric, "variable": variable}
    desc = metric_descriptor(metric)
    seed: Callable[[SeedData], dict] | None = getattr(desc, "seed", None)
    return {**leaf, **seed(data)} if seed is not None else leaf
```

Check `_compute_fft_spectrum`'s and `_find_top_n_peaks`'s real signatures and return shapes (algorithms.py:1296-1359) and adapt the two calls; if they are private names that another module must not import, make them public in `algorithms.py` in this commit (a rename tied to code you touch). (`registry.metric(name)` is the lookup, registry.py:140.) Add to `MetricDescriptor` (registry.py, after `drawn_by_default`):

```python
    #: A new leaf's first values from the data it will score (DSTF-060):
    #: the reference / actual series in, the params to add out; ``None`` for
    #: a metric valid bare (nrmse, points, event-timing). See :mod:`.seeds`.
    seed: Callable[[SeedData], dict] | None = None
```

and register `seed=seed_range` etc. in `builtins.py` for the four metrics. Run Step 1: PASS.

- [ ] **Step 3: Failing act tests** (`tests/test_builtin_capabilities.py`, beside the existing add tests ~L480-545): `EditTree.act` with `action="add", metric="range"` on the fixture stages an op whose value has `min_value` and `max_value` bracketing that variable's data; `metric="nrmse"` stages `{"metric": "nrmse", "variable": ...}` unchanged. And an `EditCriteria` metric switch to `range` on an existing leaf yields params with `min_value`/`max_value` (drive it the way the existing edit-criteria form tests do). Run: FAIL.

- [ ] **Step 4: Implement.** `edit_tree.py:185`:

```python
        if action == "add":
            leaf = seeded_leaf(
                inputs["metric"], inputs["variable"],
                _seed_data(s, model_id, inputs["variable"]),
            )
```

with a module helper (put it in `edit_tree.py`; `edit_criteria.py` imports it):

```python
def _seed_data(s: SessionView, model_id: str, variable: str) -> SeedData:
    """The series a new criterion on ``variable`` will score. A variable
    without a result gives none, and the leaf stays bare."""
    try:
        view = s.view(model_id, variable)
    except (KeyError, ValueError):
        return SeedData(ref=None, act=None)
    ref = (view.ref_time, view.ref_values) if view.ref_time is not None and view.ref_values is not None else None
    act = (view.act_time, view.act_values) if view.act_time is not None and view.act_values is not None else None
    return SeedData(ref=ref, act=act)
```

(check which exceptions `s.view` actually raises for an unknown variable / no result and catch exactly those). In `edit_criteria.py`'s `set_metric`: `state["params"] = {k: v for k, v in seeded_leaf(name, <the leaf's variable>, _seed_data(s, model_id, <variable>)).items() if k not in ("metric", "variable")}` and change the comment to "a new mode starts from its seed (DSTF-060), else its defaults". Update `_ADD_SENTENCE` (edit_tree.py:85-88) so it no longer says "the metric's defaults": "Added as the last child, with values from the data where the metric needs them." (the test at tests/test_builtin_capabilities.py:1170 changes with it). Run Step 3: PASS; `uv run pytest tests/test_builtin_capabilities.py tests/test_seeds.py -q`: PASS.

- [ ] **Step 5: Commit** "criteria: a new range, tube, scalar or dominant-frequency criterion is seeded from its data".

---

### Task 3: Errors read as sentences

**Files:**
- Modify: `src/dstf/discovery/patch_apply.py` (`PatchError`), `src/dstf/discovery/spec_update.py` (~L149-170), `src/dstf/comparison/validator.py` (the five templates, L68-167), `src/dstf/comparison/handles.py` (`HandleEditError`, `_refuse`, `_unnamed`), `src/dstf/authoring/builtin/edit_on_plot.py` (L293, 301, 327-328)
- Create: `src/dstf/authoring/errors.py`
- Modify: `src/dstf/authoring/shell/actions.py` (L209, 238, 388, 411), `plot_card.py:651`, `test_page.py:517-519, 535-536`
- Test: `tests/test_validator.py`, `tests/test_spec_update.py:168`, `tests/test_authoring_errors.py` (new), `tests/test_test_page.py` (L265, 472, 1912), `tests/test_capability_conformance.py:502-503`

- [ ] **Step 1: Failing tests.** `tests/test_authoring_errors.py`:

```python
from dstf.authoring.errors import user_text
from dstf.comparison.handles import HandleEditError
from dstf.discovery.patch_apply import PatchError


def test_a_patch_error_shows_its_reason_only():
    e = PatchError("validator rejected the patched tree: metrics: X", reason="X")
    assert user_text(e) == "X"


def test_a_handle_error_shows_its_reason_only():
    e = HandleEditError("/metrics/children/1: range/max: lower above upper",
                        reason="lower above upper")
    assert user_text(e) == "lower above upper"


def test_an_expected_error_has_no_class_name():
    assert user_text(ValueError("no result for x")) == "no result for x"


def test_an_unexpected_error_keeps_its_class_name():
    assert user_text(KeyError("k")) == "KeyError: 'k'"
```

And in `tests/test_validator.py`: the no-primary-anchor tree's error `.message` equals the new sentence (Step 2); `str(err)` still starts with the dotted path (the CLI keeps the location). And in `tests/test_spec_update.py`: the raised `PatchError` has `.reason` equal to the validator's `.message` (joined with "; " when several). Run: FAIL.

- [ ] **Step 2: Implement the `reason` fields and the plain sentences.**
  - `PatchError.__init__(self, message, *, reason=None)`: keep the existing located `str()`; store `self.reason = reason if reason is not None else message`. Where `apply_patch_to_document` re-raises with `patch_ops[i] (...)` (patch_apply.py:67-71), pass `reason=e.reason`.
  - `spec_update.py:156-157`: `raise PatchError(f"validator rejected the patched tree: {msg}", reason="; ".join(e.message for e in errors))`; likewise :149 and :170 pass the inner exception's plain text as `reason`.
  - `HandleEditError(message, *, reason=None)` the same way; `_refuse(edit, reason)` passes `reason=reason`; `_unnamed` passes `reason="this cannot be moved on the plot"`; `edit_on_plot.py:327-328` re-raises `HandleEditError(f"{path}: {e}", reason=e.reason)`; :293 becomes `HandleEditError(f"{handle} is not a handle of {open_leaf}", reason="Open that criterion to drag its lines.")`; :301 `reason=f"{name} has no on-plot handles"`.
  - `validator.py`, the five templates (keep `path` separate as today; only `message` changes):
    1. "Every tree needs at least one criterion scored against the primary baseline outside a 'warn': a tree of warnings alone can never fail."
    2. f"A 'weighted' node cannot mix lower-is-better and higher-is-better scores ({parts}); split them into separate weighted nodes."
    3. f"This criterion scores against {leaf.against!r}, which is not a baseline of this test (the primary or a soft check); check the name."
    4. f"This criterion scores against the companion {leaf.against!r}, and companions are only drawn; import it as a soft check (dstf import-baseline) to score against it."
    5. f"This criterion scores against the soft check {leaf.against!r}, which must sit under a 'warn' so it can only warn."
  - `src/dstf/authoring/errors.py`:

```python
"""What the tool says when an action fails.

The errors a person can cause — a patch the validator refuses, a drag a
criterion cannot take, a value out of range, a locked spec, a running job —
read as their plain reason; the location is already on the screen, beside
the row or the plot it came from. Anything else is a bug and keeps its class
name, so it can be reported (DSTF-060)."""

from __future__ import annotations

from ..comparison.handles import HandleEditError
from ..config import ConfigError
from ..discovery.patch_apply import PatchError
from .session import JobRunningError, LockedError

#: HandleEditError and ConfigError are ValueErrors; listed for the reader.
_EXPECTED: tuple[type[BaseException], ...] = (
    PatchError,
    HandleEditError,
    ConfigError,
    ValueError,
    LockedError,
    JobRunningError,
)


def user_text(e: BaseException) -> str:
    if isinstance(e, _EXPECTED):
        return str(getattr(e, "reason", None) or e)
    return f"{type(e).__name__}: {e}"
```

(`ConfigError` is `config.py:111`, `LockedError` / `JobRunningError` `authoring/session.py:183, 200`. If importing `session` here makes an import cycle with `actions.py`, report it and import the two classes lazily inside `user_text`.)
  - Replace every `f"{type(e).__name__}: {e}"` in `actions.py` (L209, 238, 388, 411) and `plot_card.py:651` with `user_text(e)`; `test_page.py:517-519, 535-536` `ui.notify(str(e))` → `ui.notify(user_text(e))`. Leave `shell.py:139/247` and `runs.py:51` as they are (a project that fails to open and a failed job are not a user's edit; say so in your report if you disagree).
  - Update the tests that pin the old prefix (tests/test_test_page.py:265, 472, 1912; tests/test_capability_conformance.py:502-503) to the new text.

Run Step 1 and `uv run pytest tests/test_validator.py tests/test_spec_update.py tests/test_authoring_errors.py tests/test_test_page.py tests/test_capability_conformance.py -q`: PASS. Check `dstf spec-update` output still names the location: `uv run pytest tests/ -k spec_update -q`.

- [ ] **Step 3: Commit** "errors: a user's own mistake reads as its reason; validator messages are sentences".

---

### Task 4: The spectrum is ranged to its peaks

**Files:**
- Modify: `src/dstf/figures/plotly.py` (`_layout`, ~L662-664)
- Test: `tests/test_figures.py` (beside the `_spectrum_view` tests ~L588-600)

- [ ] **Step 1: Failing test.** With the existing `_spectrum_view` (2 Hz sine, t 0..10 s, 1001 samples; Nyquist ≈ 50 Hz) and a declared peak at 2 Hz:

```python
def test_the_spectrum_is_ranged_to_its_peaks():
    fig = variable_figure(_spectrum_view(declared=[2.0]))  # adapt to the helper's signature
    lo, hi = fig["layout"]["xaxis2"]["range"]
    assert lo == 0.0
    assert 2.0 < hi <= 3 * 2.0 * 1.2   # the peak's window plus headroom, not Nyquist
    assert fig["layout"]["xaxis2"].get("uirevision") is not None
```

and one with no declared, matched or detected marker (a flat signal) asserting `"range" not in fig["layout"]["xaxis2"]` (autorange stays). Run: FAIL.

- [ ] **Step 2: Implement** in `_layout`, where `xaxis2` is set:

```python
#: How far past the highest peak of interest the spectrum's x axis runs.
SPECTRUM_HEADROOM = 3.0


def _spectrum_range(view: VariableView) -> list[float] | None:
    """[0, 3 x the highest declared / matched / detected peak (its window's
    upper edge where it has one)], clamped to Nyquist. Autoranged, the axis
    ran 0..Nyquist, and Nyquist follows the sample count, not the physics:
    TRANSFORM's peaks near 1.2e-5 Hz sat in the leftmost ~10 px of an axis
    to ~1.7e-3 Hz (2026-09-26)."""
    tops: list[float] = []
    nyquist = 0.0
    for leaf in view.leaves:
        for p in leaf.geometry:
            if getattr(p, "panel", None) != "spectrum":
                continue
            if isinstance(p, Series):
                nyquist = max(nyquist, float(np.nanmax(p.x)))
            elif isinstance(p, Marker):
                tops.append(float(p.x))
            elif isinstance(p, Box):
                tops.append(float(p.t1))
    if not tops:
        return None
    hi = SPECTRUM_HEADROOM * max(tops)
    return [0.0, min(hi, nyquist) if nyquist > 0 else hi]
```

(check the real attribute names of `Series`, `Marker`, `Box` in `comparison/geometry.py` — `panel`, `x`, `t1` are guesses; and that `np` is imported), then:

```python
    if view.has_spectrum:
        layout["yaxis"]["domain"] = list(TIME_DOMAIN)
        layout["xaxis2"] = {"title": {"text": "Frequency [Hz]"}, "anchor": "y2",
                            "uirevision": "keep"}
        rng = _spectrum_range(view)
        if rng is not None:
            layout["xaxis2"]["range"] = rng
```

Both tiers call `_layout`; `plot_figure` passes a `layout_view` carrying every view's leaves (~L938), so a multi-series plot ranges to all its peaks. Run: PASS; `uv run pytest tests/test_figures.py tests/test_plot_figure.py tests/test_figure_goldens.py tests/test_interactive_report.py -q`: PASS.

- [ ] **Step 3: Commit** "figures: the spectrum's x axis runs to its peaks, not to Nyquist".

---

### Task 5: Record it; gate over a copy of TRANSFORM

- [ ] **Step 1: Docs.** `docs/extensibility.md`: under the metric contract, one paragraph on `MetricDescriptor.seed` (what it receives, what it returns, that a metric valid bare leaves it `None`). `docs/decisions.md`: a new entry `D135 — A new criterion is valid when it is added; errors read as their reason; the editable figure lets only handles take a drag (2026-09-26)` with the four fixes, the spike's finding (layer restacks, does not stop a grab), and the reason field. Commit both.

- [ ] **Step 2: Full gates.** `uv run pytest -m "not playwright" -q`, `uv run pytest -m playwright -q`, `uv run ruff check`, `uv run ruff format --check`, bare `uv run mypy`.

- [ ] **Step 3: The TRANSFORM gate (a separate agent, over a COPY; the controller briefs it).** Count what the fixes DID, not only what stayed unchanged:
  - owner files unchanged: md5 of the real `test_spec.json` and a listing of the real reference dir before and after;
  - in Chromium, on a real TRANSFORM variable: add `range`, `tube`, `scalar`, `dominant-frequency` in turn (stage, then Discard) → count how many arrive "misconfigured" (expected 0) and read each pill;
  - a windowed range (type a window) → real-mouse drag of the bound inside and outside the window moves it; the window interior takes no drag and shows no error;
  - a dominant-frequency on the variable the owner tried (the reproduction used a TRANSFORM test with peaks near 1.2e-5 Hz — find it in the reproduction notes or pick a periodic variable) → the declared peak sits clear of the axis's left edge: report the axis range and the peak's pixel x;
  - a refused edit (wrap the root in `warn`) → the sentence shown, verbatim;
  - verdicts: `dstf compare` over the copy before and after — 330 PASS + 1 SIM_FAIL, unchanged;
  - a thumbnail of each state Read (never a full-res image).
