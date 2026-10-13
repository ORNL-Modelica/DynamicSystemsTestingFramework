# Geometry and Figures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every comparison mode returns the geometry it scored against as data, one Python module turns that data into a Plotly figure dict, and the HTML report's first paint comes from that dict instead of from JavaScript re-derivation.

**Architecture:** Sub-project 1 of `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.2, §4.3, §5 step 1). Primitives (`Band`, `Box`, `Marker`, `HLine`, `VLine`, `Series`, `Window`) live next to the modes in `comparison/geometry.py`; each `_compare_*` in `algorithms.py` attaches them to its `VariableComparison`; the evaluator stamps every leaf with its RFC 6902 path and prefixes primitive ids with it. A new `figures/` package builds a library-neutral view per variable and renders it to a Plotly dict with a registry of builders keyed on primitive type. The report embeds those dicts; `plots.js` paints from them on load and falls back to its existing JavaScript path on the first edit. The JavaScript scorers and editors are NOT touched in this sub-project — they go in sub-project 2.

**Tech Stack:** Python 3.10+, numpy, dataclasses (`kw_only`), Jinja2, the vendored plotly.js already in `reporting/templates/vendor/`, pytest, Playwright (gated by `importorskip`).

---

## Conventions for every task

- Run commands from the repo root with `uv run ...`. The suite is `uv run pytest -m "not playwright"`; browser tests are `uv run pytest -m playwright` and skip cleanly without Playwright.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check` (run `uv run ruff format` to fix), `uv run mypy` **bare, no path** (a path argument overrides the scoped file list and reports errors that are not yours), and the affected tests.
- Commit messages end with the attribution trailer the session's instructions specify (Co-Authored-By and Claude-Session lines). Nothing is pushed.
- Done-checks are predicates over the end state, never counts.
- `docs/ideas.md`, `docs/decisions.md` and `.soul/` are NOT touched by this plan. D125 lands in sub-project 2.
- One deviation from the spec's step-1 wording, decided here: `_extract_mode_values` and the spectrum gate in `context_builder.py` are **kept** in this sub-project because the JavaScript forms and the editor-slot spectrum subplot they feed still exist until sub-project 2 deletes them. Only `_leaf_score_display` goes now. The end state the spec describes is unchanged.

## File structure

| Path | Responsibility |
|---|---|
| `src/dstf/comparison/geometry.py` (new) | The seven primitives, `Primitive.prefixed`. No imports from the rest of `comparison`. |
| `src/dstf/comparison/types.py` | `VariableComparison.geometry`; `ScoreSummary`. |
| `src/dstf/comparison/algorithms.py` | Each `_compare_*` emits its geometry; small `_*_geometry` helpers beside them. |
| `src/dstf/comparison/metric_tree.py` | `stamp_leaf_path`; `implicit_and_tree` stamps `/metrics/children/<i>`. |
| `src/dstf/comparison/tree_eval.py` | `evaluate_spec` threads the path; `_evaluate_leaf` adds `Window`; `leaf_comparisons_by_path`. |
| `src/dstf/comparison/summaries.py` (new) | Per-mode `ScoreSummary` functions (transplanted from `_leaf_score_display`) and the default. |
| `src/dstf/comparison/registry.py` | `MetricDescriptor.summarize`; the `summarize(vc)` dispatcher. |
| `src/dstf/comparison/builtins.py` | Passes each mode's summarize function. |
| `src/dstf/figures/__init__.py` (new) | Package docstring. |
| `src/dstf/figures/decimate.py` (moved from `reporting/decimate.py`) | `lttb`, `decimate_pair`, plus `decimate_band`. |
| `src/dstf/figures/view.py` (new) | `VariableView`, `LeafView`, `OverlayView`, `build_view`, `error_series`, `decimate_view`. |
| `src/dstf/figures/plotly.py` (new) | `variable_figure`, `TRACE_BUILDERS`, `PLOT_CONFIG`. Knows no mode name. |
| `src/dstf/reporting/context_builder.py` | Uses `summarize`; leaf lookup by path replaces the positional zip. |
| `src/dstf/reporting/report_suite.py` | `_build_figures`; decimate import path; tombstone comment removed. |
| `src/dstf/reporting/templates/interactive.html`, `scorers.js`, `plots.js`, `export.js` | Figure dicts marshalled; first paint from them. |
| `src/dstf/cli/review.py` | `_print_detail` reads `summarize`; stale matplotlib string removed. |
| `pyproject.toml`, `uv.lock` | `matplotlib` dependency removed. |
| `tests/test_geometry.py` (new), `tests/test_figures.py` (new), `tests/test_summaries.py` (new), `tests/test_interactive_figures.py` (new) | New coverage. |
| `tests/test_decimate.py`, `tests/test_tree_eval.py`, `tests/test_reporters.py`, `tests/test_scalar_metric.py`, `tests/test_report_suite.py`, `tests/golden/*.hash` | Updated. |

---

### Task 1: Primitives, `VariableComparison.geometry`, `ScoreSummary`

**Files:**
- Create: `src/dstf/comparison/geometry.py`
- Modify: `src/dstf/comparison/types.py`
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_geometry.py`:

```python
"""Geometry primitives: what a mode scored against, as drawable data."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.geometry import Band, Box, Marker, Window
from dstf.comparison.types import ScoreSummary, VariableComparison


def _vc(**kw) -> VariableComparison:
    base = dict(
        index=0, name="x", passed=True, nrmse=0.0, rmse=0.0, signal_range=1.0,
        max_abs_error=0.0, max_abs_error_time=0.0, reference_final=0.0, actual_final=0.0,
    )
    base.update(kw)
    return VariableComparison(**base)


class TestPrimitives:
    def test_prefixed_returns_a_copy_with_the_leaf_path(self):
        m = Marker(id="points/target/0", kind="handle", x=1.0, y=2.0)
        p = m.prefixed("/metrics/children/1")
        assert p.id == "/metrics/children/1/points/target/0"
        assert p.kind == "handle" and p.x == 1.0 and p.panel == "time"
        assert m.id == "points/target/0"  # frozen: the original is untouched

    def test_box_open_height_and_window_open_ends_are_none(self):
        b = Box(id="events/window/0", t0=0.9, t1=1.1, y0=None, y1=None)
        assert b.y0 is None and b.y1 is None and b.passed is None
        w = Window(id="window", start=None, end=5.0)
        assert w.start is None and w.end == 5.0

    def test_band_holds_arrays_and_defaults_to_the_time_panel(self):
        t = np.array([0.0, 1.0])
        b = Band(id="tube/band", time=t, lower=t - 1, upper=t + 1)
        assert b.panel == "time" and b.kind == "result"
        np.testing.assert_array_equal(b.upper, [1.0, 2.0])

    def test_kind_and_panel_are_keyword_only(self):
        with pytest.raises(TypeError):
            Marker("points/target/0", 1.0, 2.0)  # type: ignore[misc]


class TestResultTypes:
    def test_variable_comparison_geometry_defaults_empty(self):
        assert _vc().geometry == ()

    def test_score_summary_is_frozen(self):
        s = ScoreSummary(display="NRMSE 1e-3", criterion="NRMSE 1e-3 vs 1e-4 → FAIL")
        with pytest.raises(AttributeError):
            s.display = "x"  # type: ignore[misc]
        assert s.score is None and s.score_label == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dstf.comparison.geometry'`

- [ ] **Step 3: Create the primitives module**

Create `src/dstf/comparison/geometry.py`:

```python
"""Geometry primitives: what a comparison mode scored against, as data.

Each :class:`~.types.VariableComparison` carries the primitives its mode
built while scoring — the tube band on the grid it was enforced on, the
checkpoint boxes, the declared bounds, the detected events, the spectra.
They are the single source every figure draws from (``dstf.figures``), so a
mode cannot draw one envelope and score another.

The vocabulary is deliberately small. A new kind of drawing is a new class
here plus one builder in ``dstf.figures.plotly``; that is cheaper than
smuggling a shape into :class:`Series`, which is the failure this file's
size guards against.

Ids are addresses. A mode emits LOCAL ids (``tube/band``, ``tube/upper/3``,
``points/target/0``) — it does not know its leaf — and the evaluator
prefixes each with the leaf's RFC 6902 path (``/metrics/children/2``)
through :meth:`Primitive.prefixed`, so a figure handle and a patch op share
one name. ``kind`` is a free label the figure builder styles on:
``"result"`` (what was computed), ``"handle"`` (the resolved position of an
authored parameter — a control point, a bound, a declared peak), ``"ref"`` /
``"act"`` (which curve an event or spectrum belongs to), ``"matched"`` /
``"detected"`` (peak roles), ``"error"`` (the view's error series).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

Panel = Literal["time", "spectrum"]


@dataclass(frozen=True, kw_only=True, eq=False)
class Primitive:
    id: str
    kind: str = "result"
    panel: Panel = "time"

    def prefixed(self, prefix: str) -> Primitive:
        """A copy whose id is ``<prefix>/<id>`` — the evaluator's stamp."""
        return replace(self, id=f"{prefix}/{self.id}")


@dataclass(frozen=True, kw_only=True, eq=False)
class Band(Primitive):
    """An envelope: ``lower[i] <= y <= upper[i]`` at ``time[i]``."""

    time: np.ndarray
    lower: np.ndarray
    upper: np.ndarray


@dataclass(frozen=True, kw_only=True, eq=False)
class Box(Primitive):
    """A rectangle ``[t0, t1] × [y0, y1]``. ``y0``/``y1`` of ``None`` span the panel."""

    t0: float
    t1: float
    y0: float | None
    y1: float | None
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class Marker(Primitive):
    x: float
    y: float
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class HLine(Primitive):
    value: float
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class VLine(Primitive):
    time: float


@dataclass(frozen=True, kw_only=True, eq=False)
class Series(Primitive):
    x: np.ndarray
    y: np.ndarray


@dataclass(frozen=True, kw_only=True, eq=False)
class Window(Primitive):
    """The leaf's time window; either end ``None`` means open on that side."""

    start: float | None
    end: float | None
```

- [ ] **Step 4: Add `geometry` and `ScoreSummary` to `types.py`**

In `src/dstf/comparison/types.py`, extend the `TYPE_CHECKING` block and the dataclass:

```python
if TYPE_CHECKING:
    # Annotation-only — runtime import is inside compare_test() in
    # comparator.py to break the comparator <-> metric_tree cycle.
    from .geometry import Primitive
    from .metric_tree import MetricResult
```

At the end of `VariableComparison` (after the `diagnostics` field):

```python
    # What the mode scored against, as drawable data (see geometry.py).
    # Ids are local until the evaluator prefixes them with the leaf path.
    geometry: tuple[Primitive, ...] = ()
```

Immediately after the `VariableComparison` class, add:

```python
@dataclass(frozen=True)
class ScoreSummary:
    """One leaf's verdict in words: the pill text and the criterion sentence.

    Produced per mode by :mod:`.summaries` and dispatched through the
    metric descriptor, so the report's tree view and the terminal review
    loop read the same mode-aware text.
    """

    display: str
    criterion: str
    score: float | None = None
    score_label: str = ""
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: 6 passed

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/geometry.py src/dstf/comparison/types.py tests/test_geometry.py
git commit -m "feat: geometry primitives — what a mode scored against, as data"
```

---

### Task 2: Tube geometry — the band on the union grid, plus handles

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_tube`, ~line 640; new helper after `_tube_eval_grid`)
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py`:

```python
import random

from dstf.comparison.algorithms import _compare_tube, _tube_bounds


def _random_tube_case(rng: random.Random):
    """A case drawn to REACH the regimes the exactness claim excludes:
    rel widths, min-width floors, constant interpolation, control points
    off the reference grid. A generator that only produces band+linear
    would verify the claim in the one regime where it holds."""
    n_ref = rng.randint(3, 12)
    ref_time = np.array(sorted(rng.sample(range(0, 100), n_ref)), dtype=float) / 10.0
    ref_values = np.array([rng.uniform(-2.0, 2.0) for _ in ref_time])
    act_time = np.array(sorted(rng.uniform(ref_time[0], ref_time[-1]) for _ in range(rng.randint(3, 15))))
    act_values = np.array([rng.uniform(-3.0, 3.0) for _ in act_time])
    mode = rng.choice(["band", "rel", "abs"])
    cfg: dict = {
        "tube_width_mode": mode,
        "tube_min_width": rng.choice([0.0, 0.3]),
        "tube_interpolation": rng.choice(["linear", "constant"]),
    }
    if mode == "abs" or rng.random() < 0.5:
        pts = []
        for _ in range(rng.randint(1, 4)):
            t = rng.uniform(ref_time[0], ref_time[-1])
            if mode == "abs":
                lo = rng.uniform(-3.0, 0.0)
                pts.append({"time": t, "upper": lo + rng.uniform(0.5, 3.0), "lower": lo})
            else:
                pts.append({"time": t, "upper": rng.uniform(0.0, 1.0), "lower": rng.uniform(0.0, 1.0)})
        cfg["tube_points"] = pts
    else:
        cfg["tube_abs"] = rng.uniform(0.0, 1.0)
        cfg["tube_rel"] = rng.uniform(0.0, 0.5)
    return ref_time, ref_values, act_time, act_values, cfg


class TestTubeGeometry:
    def test_band_is_the_scored_envelope_and_the_verdict_follows_it(self):
        rng = random.Random(20260908)
        failing_cases = 0
        for _ in range(400):
            ref_t, ref_v, act_t, act_v, cfg = _random_tube_case(rng)
            vc = _compare_tube(ref_t, ref_v, act_t, act_v, cfg)
            band = next(p for p in vc.geometry if isinstance(p, Band))
            assert band.id == "tube/band" and band.kind == "result"
            # The band IS _tube_bounds on the band's own grid — nothing re-derived.
            up, lo = _tube_bounds(band.time, np.interp(band.time, ref_t, ref_v), cfg)
            np.testing.assert_allclose(band.upper, up)
            np.testing.assert_allclose(band.lower, lo)
            act_on_grid = np.interp(band.time, act_t, act_v)
            inside = (act_on_grid >= band.lower - 1e-12) & (act_on_grid <= band.upper + 1e-12)
            assert bool(inside.all()) == vc.passed
            if not vc.passed:
                i = int(np.argmin(np.abs(band.time - vc.tube_worst_violation_time)))
                assert not inside[i], "the reported worst violation lies inside the band"
                failing_cases += 1
        assert failing_cases > 50, "generator never reached the failing regime"

    def test_handles_sit_on_the_resolved_band_edges_in_spec_order(self):
        ref_t = np.array([0.0, 1.0, 2.0, 3.0])
        ref_v = np.array([1.0, 2.0, 1.0, 2.0])
        pts = [  # unsorted on purpose: ids follow SPEC order, not time order
            {"time": 2.5, "upper": 0.2, "lower": 0.1},
            {"time": 0.5, "upper": 0.4, "lower": 0.4},
        ]
        vc = _compare_tube(ref_t, ref_v, ref_t, ref_v, {"tube_width_mode": "rel", "tube_points": pts})
        handles = {p.id: p for p in vc.geometry if isinstance(p, Marker)}
        assert set(handles) == {"tube/upper/0", "tube/lower/0", "tube/upper/1", "tube/lower/1"}
        assert all(h.kind == "handle" for h in handles.values())
        band = next(p for p in vc.geometry if isinstance(p, Band))
        assert handles["tube/upper/0"].x == 2.5
        assert handles["tube/upper/0"].y == pytest.approx(float(np.interp(2.5, band.time, band.upper)))
        assert handles["tube/lower/1"].x == 0.5
        assert handles["tube/lower/1"].y == pytest.approx(float(np.interp(0.5, band.time, band.lower)))

    def test_no_data_sentinel_has_no_geometry(self):
        vc = _compare_tube(np.array([]), np.array([]), np.array([0.0]), np.array([1.0]), {"tube_width_mode": "band"})
        assert vc.geometry == () and not vc.passed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py::TestTubeGeometry -v`
Expected: FAIL with `StopIteration` (no `Band` in `vc.geometry`)

- [ ] **Step 3: Emit the geometry from `_compare_tube`**

In `src/dstf/comparison/algorithms.py`, add the import near the top (after the numpy import, alongside the existing `from .types import ...`):

```python
from .geometry import Band, Box, HLine, Marker, Primitive, Series, VLine
```

Add this helper directly after `_tube_eval_grid`:

```python
def _tube_geometry(
    grid: np.ndarray,
    tube_lower: np.ndarray,
    tube_upper: np.ndarray,
    tube_points: list[dict] | None,
) -> tuple[Primitive, ...]:
    """The band as scored, plus one handle pair per authored control point.

    Handles sit at the RESOLVED band edge at the control point's time — for
    a ``rel`` tube that is ``ref ± rel·|ref|`` there, not the authored
    fraction — so an editor drags what the user sees. Control-point times
    are on the grid by construction (``_tube_eval_grid``), so the lookup is
    exact. Ids follow the spec's array order: ``tube/upper/3`` addresses
    ``tube_points/3`` in a patch.
    """
    out: list[Primitive] = [
        Band(id="tube/band", time=grid, lower=tube_lower, upper=tube_upper)
    ]
    for i, pt in enumerate(tube_points or []):
        t = float(pt["time"])
        if t < grid[0] or t > grid[-1]:
            continue  # off the reference span: nothing is scored there
        out.append(
            Marker(id=f"tube/upper/{i}", kind="handle", x=t, y=float(np.interp(t, grid, tube_upper)))
        )
        out.append(
            Marker(id=f"tube/lower/{i}", kind="handle", x=t, y=float(np.interp(t, grid, tube_lower)))
        )
    return tuple(out)
```

In `_compare_tube`'s final `return VariableComparison(...)`, add the geometry after `tube_worst_violation_time=worst_violation_time,`:

```python
        tube_worst_violation_time=worst_violation_time,
        geometry=_tube_geometry(grid, tube_lower, tube_upper, tube_points),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py tests/test_tube_union_grid.py tests/test_comparator.py -q`
Expected: all pass

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/algorithms.py tests/test_geometry.py
git commit -m "feat: tube returns the band it scored on, and a handle per control point"
```

---

### Task 3: Points geometry — targets and boxes

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_points`, ~lines 306–472)
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py`:

```python
from dstf.comparison.algorithms import _compare_points


class TestPointsGeometry:
    def test_boxes_contain_passed_points_and_exclude_failed_ones(self):
        ref_t = np.linspace(0.0, 10.0, 101)
        ref_v = np.sin(ref_t)
        act_v = ref_v + 0.05 * (ref_t > 5.0)  # off by 0.05 after t=5
        pts = [
            {"time": 2.0, "tolerance": 0.01},
            {"time": 8.0, "tolerance": 0.01},
            {"time": 9.0, "tolerance": 0.1, "time_tolerance": 0.2},
        ]
        vc = _compare_points(ref_t, ref_v, ref_t, act_v, points=pts)
        boxes = {p.id: p for p in vc.geometry if isinstance(p, Box)}
        targets = {p.id: p for p in vc.geometry if isinstance(p, Marker)}
        assert set(boxes) == {"points/box/0", "points/box/1", "points/box/2"}
        assert set(targets) == {"points/target/0", "points/target/1", "points/target/2"}
        assert all(m.kind == "handle" for m in targets.values())
        assert targets["points/target/0"].passed is True
        assert targets["points/target/1"].passed is False and boxes["points/box/1"].passed is False
        assert targets["points/target/2"].passed is True
        # Geometry agrees with the verdict: the actual at a failed point is outside its box.
        b = boxes["points/box/1"]
        a = float(np.interp(8.0, ref_t, act_v))
        assert not (b.y0 <= a <= b.y1)
        assert (boxes["points/box/2"].t0, boxes["points/box/2"].t1) == (pytest.approx(8.8), pytest.approx(9.2))
        assert vc.passed is False

    def test_an_unresolvable_point_has_no_geometry(self):
        ref_t = np.array([0.0, 1.0])
        vc = _compare_points(ref_t, ref_t, ref_t, ref_t, points=[{"time": 5.0}])
        assert vc.geometry == () and not vc.passed

    def test_final_only_emits_one_checkpoint_at_the_trace_end(self):
        ref_t = np.array([0.0, 1.0])
        ref_v = np.array([0.0, 1.0])
        vc = _compare_points(ref_t, ref_v, ref_t, np.array([0.0, 1.0005]), points=None, tolerance=1e-3)
        assert [p.id for p in vc.geometry] == ["points/target/final", "points/box/final"]
        box = vc.geometry[1]
        assert isinstance(box, Box)
        assert (box.t0, box.t1) == (1.0, 1.0)
        assert box.y0 == pytest.approx(0.999) and box.y1 == pytest.approx(1.001)
        assert vc.passed and box.passed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py::TestPointsGeometry -v`
Expected: 2 FAIL (empty geometry), 1 pass (`test_an_unresolvable_point_has_no_geometry`)

- [ ] **Step 3: Emit the geometry from `_compare_points`**

In the legacy branch of `_compare_points` (the `if not points:` block), change the second `return VariableComparison(` — the one with `diagnostics={"tolerance": tolerance, "delta": delta}` — to:

```python
        t_final = float(ref_time[-1]) if len(ref_time) else 0.0
        return VariableComparison(
            index=0,
            name="",
            passed=passed,
            nrmse=delta,
            rmse=delta,
            signal_range=0.0,
            max_abs_error=delta,
            max_abs_error_time=t_final,
            reference_final=ref_final,
            actual_final=act_final,
            mode="points",
            diagnostics={"tolerance": tolerance, "delta": delta},
            geometry=(
                Marker(id="points/target/final", kind="handle", x=t_final, y=ref_final, passed=passed),
                Box(
                    id="points/box/final",
                    kind="handle",
                    t0=t_final,
                    t1=t_final,
                    y0=ref_final - tolerance,
                    y1=ref_final + tolerance,
                    passed=passed,
                ),
            ),
        )
```

(`max_abs_error_time` was `float(ref_time[-1]) if len(ref_time) else 0.0` — the same value, now named.)

In the declared-points path, before `point_failures: list[dict] = []` add:

```python
    geometry: list[Primitive] = []
```

Change the loop header `for point in points:` to `for i, point in enumerate(points):`, and replace the tail of the loop body from `scored += 1` onward with:

```python
        scored += 1
        if delta > worst_delta:
            worst_delta = delta
            worst_t = t_at_min
        point_passed = delta <= y_limit
        geometry.append(
            Marker(id=f"points/target/{i}", kind="handle", x=t, y=target, passed=point_passed)
        )
        geometry.append(
            Box(
                id=f"points/box/{i}",
                kind="handle",
                t0=t - x_tol,
                t1=t + x_tol,
                y0=target - y_limit,
                y1=target + y_limit,
                passed=point_passed,
            )
        )
        if not point_passed:
            failed += 1
            point_failures.append(
                {"time": t, "reason": f"delta {delta:.6g} exceeds limit {y_limit:.6g}"}
            )
```

In the final `return VariableComparison(` of the declared path, add after the `diagnostics={...}` argument:

```python
        geometry=tuple(geometry),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py tests/test_comparator.py -q -k "points or Points"`
Expected: all pass

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/algorithms.py tests/test_geometry.py
git commit -m "feat: points returns its checkpoint targets and tolerance boxes"
```

---

### Task 4: Range and scalar geometry — bounds as lines

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_range` ~line 750; `_compare_scalar` ~line 869)
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py`:

```python
from dstf.comparison.algorithms import _compare_range, _compare_scalar


class TestRangeGeometry:
    def test_each_declared_bound_is_a_handle_with_its_own_verdict(self):
        t = np.linspace(0.0, 1.0, 11)
        v = np.linspace(0.0, 2.0, 11)  # exceeds max=1.5, never below min=-1
        vc = _compare_range(t, v, min_value=-1.0, max_value=1.5)
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"range/min", "range/max"}
        assert lines["range/min"].value == -1.0 and lines["range/min"].passed is True
        assert lines["range/max"].value == 1.5 and lines["range/max"].passed is False
        assert all(line.kind == "handle" for line in lines.values())
        assert not vc.passed

    def test_an_absent_bound_emits_nothing(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_range(t, t, min_value=None, max_value=5.0)
        assert [p.id for p in vc.geometry] == ["range/max"]


class TestScalarGeometry:
    def test_bounds_form_draws_actual_and_both_bounds(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_scalar(
            t, t, t, t * 3.0, reduction="max", tolerance=0.1, tolerance_mode="abs",
            min_value=0.0, max_value=2.0,
        )
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"scalar/actual", "scalar/min", "scalar/max"}
        assert lines["scalar/actual"].value == pytest.approx(3.0)
        assert lines["scalar/actual"].kind == "act" and lines["scalar/actual"].passed is False
        assert lines["scalar/max"].kind == "handle" and lines["scalar/max"].value == 2.0

    def test_baseline_form_draws_actual_and_reference(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_scalar(
            t, t, t, t * 1.01, reduction="final", tolerance=0.05, tolerance_mode="rel",
            min_value=None, max_value=None,
        )
        lines = {p.id: p for p in vc.geometry if isinstance(p, HLine)}
        assert set(lines) == {"scalar/actual", "scalar/reference"}
        assert lines["scalar/reference"].kind == "ref" and lines["scalar/reference"].value == pytest.approx(1.0)
        assert lines["scalar/actual"].passed is True and vc.passed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py::TestRangeGeometry tests/test_geometry.py::TestScalarGeometry -v`
Expected: 4 FAIL (empty geometry or wrong id set)

- [ ] **Step 3: Emit the geometry**

In `_compare_range`'s `return VariableComparison(`, add after `diagnostics={"min_value": min_value, "max_value": max_value},`:

```python
        geometry=tuple(
            HLine(id=f"range/{label}", kind="handle", value=float(bound), passed=bool(np.all(viol <= 0)))
            for label, bound, viol in (("min", min_value, below), ("max", max_value, above))
            if bound is not None
        ),
```

Add this helper directly before `_compare_scalar`:

```python
def _scalar_geometry(
    act_scalar: float,
    min_value: float | None,
    max_value: float | None,
    *,
    ref_scalar: float | None,
    passed: bool,
) -> tuple[Primitive, ...]:
    """The scalar and what it was judged against, as horizontal lines.

    A scalar leaf on a statistics path has no trajectory at all; these
    lines are then the whole figure.
    """
    out: list[Primitive] = [HLine(id="scalar/actual", kind="act", value=float(act_scalar), passed=passed)]
    if ref_scalar is not None:
        out.append(HLine(id="scalar/reference", kind="ref", value=float(ref_scalar)))
    if min_value is not None:
        out.append(HLine(id="scalar/min", kind="handle", value=float(min_value)))
    if max_value is not None:
        out.append(HLine(id="scalar/max", kind="handle", value=float(max_value)))
    return tuple(out)
```

In `_compare_scalar`'s bounds branch (the `return VariableComparison(` inside `if min_value is not None or max_value is not None:`), add after `diagnostics=diagnostics,`:

```python
            geometry=_scalar_geometry(
                act_scalar, min_value, max_value, ref_scalar=None, passed=violation <= 0.0
            ),
```

In the baseline branch's final `return VariableComparison(`, add after `diagnostics=diagnostics,`:

```python
        geometry=_scalar_geometry(
            act_scalar, None, None, ref_scalar=ref_scalar, passed=bool(score <= tolerance)
        ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py tests/test_scalar_metric.py tests/test_comparator.py -q`
Expected: all pass

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/algorithms.py tests/test_geometry.py
git commit -m "feat: range and scalar return their bounds as lines — scalar gets a drawing"
```

---

### Task 5: Event-timing geometry — events and tolerance windows

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_event_timing`, ~lines 968–1095)
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py`:

```python
from dstf.comparison.algorithms import _compare_event_timing, _find_event_boundaries


class TestEventTimingGeometry:
    def test_auto_detected_events_become_lines_and_matched_windows(self):
        ref_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])       # one event at t=1.0
        act_t = np.array([0.0, 0.5, 1.0005, 1.0005, 1.5, 2.0])  # reproduced 0.5 ms late
        vc = _compare_event_timing(ref_t, act_t, time_tolerance=1e-3)
        ids = {p.id: p for p in vc.geometry}
        assert set(ids) == {"events/ref/0", "events/window/0", "events/act/0"}
        assert ids["events/ref/0"].kind == "ref" and ids["events/ref/0"].time == 1.0
        assert ids["events/act/0"].kind == "act" and ids["events/act/0"].time == pytest.approx(1.0005)
        w = ids["events/window/0"]
        assert isinstance(w, Box) and (w.y0, w.y1) == (None, None)
        assert (w.t0, w.t1) == (pytest.approx(0.999), pytest.approx(1.001)) and w.passed is True
        assert vc.passed
        # Every VLine coincides with a detected boundary.
        assert [ids["events/act/0"].time] == [float(act_t[b[0]]) for b in _find_event_boundaries(act_t)]

    def test_declared_events_mark_the_unmatched_window_as_failed(self):
        ref_t = np.linspace(0.0, 2.0, 5)
        act_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        vc = _compare_event_timing(
            ref_t, act_t, time_tolerance=1e-3,
            declared_events=[{"time": 1.0}, {"time": 1.8, "tolerance": 0.05}],
        )
        windows = {p.id: p for p in vc.geometry if isinstance(p, Box)}
        assert windows["events/window/0"].passed is True
        assert windows["events/window/1"].passed is False
        assert (windows["events/window/1"].t0, windows["events/window/1"].t1) == (pytest.approx(1.75), pytest.approx(1.85))
        assert [p.id for p in vc.geometry if isinstance(p, VLine) and p.kind == "ref"] == ["events/ref/0", "events/ref/1"]
        assert not vc.passed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py::TestEventTimingGeometry -v`
Expected: 2 FAIL (empty geometry)

- [ ] **Step 3: Emit the geometry**

Add this helper directly before `_compare_event_timing`:

```python
def _event_geometry(
    ref_events: list[float],
    act_events: list[float],
    tolerances: list[float],
    matched: list[bool],
) -> tuple[Primitive, ...]:
    """Reference (or declared) events with their tolerance windows, and the
    actual events. Windows span the panel height (``y0``/``y1`` ``None``)."""
    out: list[Primitive] = []
    for i, (t, tol, ok) in enumerate(zip(ref_events, tolerances, matched)):
        out.append(VLine(id=f"events/ref/{i}", kind="ref", time=float(t)))
        out.append(
            Box(
                id=f"events/window/{i}",
                kind="handle",
                t0=float(t - tol),
                t1=float(t + tol),
                y0=None,
                y1=None,
                passed=bool(ok),
            )
        )
    for j, t in enumerate(act_events):
        out.append(VLine(id=f"events/act/{j}", kind="act", time=float(t)))
    return tuple(out)
```

In the auto-detect path of `_compare_event_timing`, change the `return VariableComparison(` to include, after its `diagnostics={...}` block:

```python
            geometry=_event_geometry(
                ref_events,
                act_events,
                [time_tolerance] * len(ref_events),
                [
                    i < n and abs(ref_events[i] - act_events[i]) <= time_tolerance
                    for i in range(len(ref_events))
                ],
            ),
```

In the declared path, replace the window-building loop and matching loop with a version that remembers which declared event matched:

```python
    claimed = [False] * actual_count
    matched_flags = [False] * declared_count
    all_matched = True
    windows = []
    declared_times: list[float] = []
    declared_tols: list[float] = []
    for k, e in enumerate(declared_events):
        target = float(e["time"])
        _tol = e.get("tolerance")
        tol = float(_tol if _tol is not None else time_tolerance)
        declared_times.append(target)
        declared_tols.append(tol)
        windows.append((target - tol, target + tol, target, k))
    windows.sort(key=lambda w: w[1])
    for lo, hi, target, k in windows:
        best_idx = -1
        for j, at in enumerate(act_events):
            if not claimed[j] and lo <= at <= hi:
                best_idx = j
                break  # act_events is time-sorted → first hit is smallest
        if best_idx < 0:
            all_matched = False
            continue
        claimed[best_idx] = True
        matched_flags[k] = True
        d = abs(act_events[best_idx] - target)
        if d > max_delta:
            max_delta = d
            delta_at = target
```

and add to that path's `return VariableComparison(`, after `diagnostics={...}`:

```python
        geometry=_event_geometry(declared_times, act_events, declared_tols, matched_flags),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py tests/test_event_and_freq_modes.py -q`
Expected: all pass

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/algorithms.py tests/test_geometry.py
git commit -m "feat: event-timing returns its events and tolerance windows"
```

---

### Task 6: Dominant-frequency geometry — spectra and peaks

**Files:**
- Modify: `src/dstf/comparison/algorithms.py` (`_compare_dominant_frequency`, ~lines 1221–1443)
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py`:

```python
from dstf.comparison.algorithms import _compare_dominant_frequency


class TestDominantFrequencyGeometry:
    def _signal(self, hz: float):
        t = np.linspace(0.0, 10.0, 1001)
        return t, np.sin(2 * np.pi * hz * t)

    def test_spectra_and_peaks_land_on_the_spectrum_panel(self):
        t, ref = self._signal(2.0)
        _, act = self._signal(2.0)
        vc = _compare_dominant_frequency(
            t, ref, t, act,
            peaks=[{"freq": 2.0, "tolerance": 0.05}, {"freq": 5.0, "tolerance": 0.02}],
        )
        prims = {p.id: p for p in vc.geometry}
        assert all(p.panel == "spectrum" for p in vc.geometry)
        assert isinstance(prims["spectrum/ref"], Series) and prims["spectrum/ref"].kind == "ref"
        assert isinstance(prims["spectrum/act"], Series) and prims["spectrum/act"].kind == "act"
        assert prims["peaks/declared/0"].kind == "handle" and prims["peaks/declared/0"].x == 2.0
        assert prims["peaks/declared/0"].passed is True
        assert prims["peaks/matched/0"].kind == "matched"
        assert prims["peaks/matched/0"].x == pytest.approx(2.0, abs=0.05)
        assert prims["peaks/declared/1"].passed is False and "peaks/matched/1" not in prims
        w = prims["peaks/window/1"]
        assert isinstance(w, Box) and (w.t0, w.t1) == (pytest.approx(4.9), pytest.approx(5.1))
        assert any(p.id.startswith("peaks/detected/") and p.kind == "detected" for p in vc.geometry)
        # Every matched marker is one of the paired peaks.
        paired = {p["matched_hz"] for p in vc.diagnostics["paired_peaks"] if p["matched_hz"] is not None}
        assert {p.x for p in vc.geometry if p.kind == "matched"} == paired
        assert not vc.passed

    def test_no_declared_peaks_still_draws_the_spectra(self):
        t, ref = self._signal(2.0)
        vc = _compare_dominant_frequency(t, ref, t, ref, peaks=None)
        ids = [p.id for p in vc.geometry]
        assert "spectrum/ref" in ids and "spectrum/act" in ids
        assert not any(i.startswith("peaks/declared/") for i in ids)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py::TestDominantFrequencyGeometry -v`
Expected: 2 FAIL (`KeyError: 'spectrum/ref'`)

- [ ] **Step 3: Emit the geometry**

Add this helper directly before `_compare_dominant_frequency`:

```python
def _frequency_geometry(
    ref_f: list[float],
    ref_m: list[float],
    act_f: list[float],
    act_m: list[float],
    act_freqs: np.ndarray,
    act_spectrum: np.ndarray,
    detected_ref_peaks: list[tuple[float, float]],
    peaks: list[dict],
    paired: list[dict],
) -> tuple[Primitive, ...]:
    """Both spectra, the reference's detected peaks, and per declared peak
    its handle, its acceptance window and (when found) its match. All on
    the spectrum panel. The embedded (capped) spectra are what is drawn; the
    full ``act_freqs``/``act_spectrum`` only place markers on the curve."""
    out: list[Primitive] = []
    if len(ref_f):
        out.append(Series(id="spectrum/ref", kind="ref", panel="spectrum", x=np.asarray(ref_f), y=np.asarray(ref_m)))
    out.append(Series(id="spectrum/act", kind="act", panel="spectrum", x=np.asarray(act_f), y=np.asarray(act_m)))
    for k, (f, amp) in enumerate(detected_ref_peaks):
        out.append(Marker(id=f"peaks/detected/{k}", kind="detected", panel="spectrum", x=float(f), y=float(amp)))

    def _amp_at(freq: float) -> float:
        return float(np.interp(freq, act_freqs, act_spectrum)) if len(act_freqs) else 0.0

    for i, (declared, pair) in enumerate(zip(peaks, paired)):
        f_decl = float(declared.get("freq", 0.0))
        tol = float(declared.get("tolerance", 0.01))
        if declared.get("tolerance_mode", "rel") == "rel":
            lo, hi = f_decl * (1.0 - tol), f_decl * (1.0 + tol)
        else:
            lo, hi = f_decl - tol, f_decl + tol
        ok = bool(pair["passed"])
        out.append(Marker(id=f"peaks/declared/{i}", kind="handle", panel="spectrum", x=f_decl, y=_amp_at(f_decl), passed=ok))
        out.append(Box(id=f"peaks/window/{i}", kind="handle", panel="spectrum", t0=lo, t1=hi, y0=None, y1=None, passed=ok))
        if pair.get("matched_hz") is not None:
            mh = float(pair["matched_hz"])
            out.append(Marker(id=f"peaks/matched/{i}", kind="matched", panel="spectrum", x=mh, y=_amp_at(mh), passed=True))
    return tuple(out)
```

In the no-peaks `return VariableComparison(` (the one whose diagnostics carry `"reason": ("no peaks declared — ...")`), add after the `diagnostics={...}` block:

```python
            geometry=_frequency_geometry(
                ref_f_embed, ref_m_embed, act_f_embed, act_m_embed,
                act_freqs, act_spectrum, detected_ref_peaks, [], [],
            ),
```

In the final `return VariableComparison(` of the declared path, add after `diagnostics={...}`:

```python
        geometry=_frequency_geometry(
            ref_f_embed, ref_m_embed, act_f_embed, act_m_embed,
            act_freqs, act_spectrum, detected_ref_peaks, peaks, paired,
        ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py tests/test_event_and_freq_modes.py -q`
Expected: all pass

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/algorithms.py tests/test_geometry.py
git commit -m "feat: dominant-frequency returns its spectra, peaks and acceptance windows"
```

---

### Task 7: The evaluator stamps leaf paths, prefixes ids, adds the window

**Files:**
- Modify: `src/dstf/comparison/metric_tree.py` (`implicit_and_tree` ~line 298; new `stamp_leaf_path`)
- Modify: `src/dstf/comparison/tree_eval.py` (`evaluate_spec` ~line 69; `_evaluate_leaf` ~line 335; new `leaf_comparisons_by_path`)
- Test: `tests/test_tree_eval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tree_eval.py`:

```python
from dstf.comparison.geometry import Band, Window
from dstf.comparison.metric_tree import implicit_and_tree
from dstf.comparison.tree_eval import leaf_comparisons_by_path
from dstf.comparison.tree_spec import collect_leaf_paths, synthesize_implicit_tree


class TestLeafPathStamping:
    def _eval(self, spec_raw: dict):
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        spec = parse_metric_tree(spec_raw)
        return evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            baselines=_primary({"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}}),
            base_tolerance=1e-4,
        )

    def test_leaves_carry_their_path_and_prefixed_geometry(self):
        tree = self._eval(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.1},
                    {"metric": "tube", "variable": "x", "tube_width_mode": "band", "tube_abs": 0.01},
                ],
            }
        )
        by_path = leaf_comparisons_by_path(tree)
        assert set(by_path) == {"/metrics/children/0", "/metrics/children/1"}
        assert tree.children[1].diagnostics["leaf_path"] == "/metrics/children/1"
        band = next(p for p in by_path["/metrics/children/1"].geometry if isinstance(p, Band))
        assert band.id == "/metrics/children/1/tube/band"

    def test_a_windowed_leaf_gets_a_window_primitive_with_open_ends_allowed(self):
        tree = self._eval({"metric": "nrmse", "variable": "x", "tolerance": 0.1, "window": {"start": 0.2}})
        vc = leaf_comparisons_by_path(tree)["/metrics"]
        window = next(p for p in vc.geometry if isinstance(p, Window))
        assert window.id == "/metrics/window"
        assert window.start == 0.2 and window.end is None

    def test_an_unwindowed_leaf_has_no_window_primitive(self):
        tree = self._eval({"metric": "nrmse", "variable": "x", "tolerance": 0.1})
        vc = leaf_comparisons_by_path(tree)["/metrics"]
        assert not any(isinstance(p, Window) for p in vc.geometry)

    def test_a_config_error_leaf_still_carries_its_path(self):
        tree = self._eval({"metric": "tube", "variable": "x", "tube_width_mode": "nonsense"})
        assert tree.diagnostics["leaf_path"] == "/metrics"
        assert "/metrics" in leaf_comparisons_by_path(tree)

    def test_implicit_tree_paths_agree_with_the_synthesized_spec(self):
        from dstf.comparison.algorithms import _compare_tube

        t, ref_vals = _linear()
        vcs = []
        for i, name in enumerate(["a", "b"]):
            vc = _compare_tube(t, ref_vals, t, ref_vals, {"tube_width_mode": "band", "tube_abs": 0.1})
            vc.index, vc.name = i + 1, name
            vcs.append(vc)
        tree = implicit_and_tree(vcs)
        stamped = list(leaf_comparisons_by_path(tree))
        synthesized = collect_leaf_paths(synthesize_implicit_tree(["a", "b"]))
        assert stamped == synthesized == ["/metrics/children/0", "/metrics/children/1"]
        assert vcs[1].geometry[0].id == "/metrics/children/1/tube/band"

    def test_stamping_is_idempotent(self):
        from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
        from dstf.comparison.algorithms import _compare_tube

        t, ref_vals = _linear()
        vc = _compare_tube(t, ref_vals, t, ref_vals, {"tube_width_mode": "band", "tube_abs": 0.1})
        leaf = leaf_from_variable(vc)
        stamp_leaf_path(leaf, "/metrics")
        stamp_leaf_path(leaf, "/metrics")
        assert vc.geometry[0].id == "/metrics/tube/band"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tree_eval.py::TestLeafPathStamping -v`
Expected: FAIL with `ImportError: cannot import name 'leaf_comparisons_by_path'`

- [ ] **Step 3: Add `stamp_leaf_path` and stamp the implicit tree**

In `src/dstf/comparison/metric_tree.py`, add after `leaf_from_variable`:

```python
def stamp_leaf_path(leaf: MetricResult, path: str) -> None:
    """Address a leaf result by its RFC 6902 path — the same path a patch op uses.

    Prefixes the stashed ``VariableComparison``'s geometry ids with the path
    so a figure handle and a spec edit share one name. Idempotent: a mode
    emits local ids, and a stamped id starts with ``/``.
    """
    leaf.diagnostics["leaf_path"] = path
    vc = leaf.diagnostics.get("variable")
    if vc is not None:
        vc.geometry = tuple(
            p if p.id.startswith("/") else p.prefixed(path) for p in vc.geometry
        )
```

Replace the body of `implicit_and_tree`:

```python
def implicit_and_tree(variables: list[VariableComparison]) -> MetricResult:
    """Build the implicit flat-AND tree from a list of per-variable comparisons.

    This is the degenerate MetricTree that matches the current pass/fail
    semantics exactly: a test passes iff every variable passes. Leaves are
    stamped ``/metrics/children/<i>`` — the paths
    :func:`~.tree_spec.synthesize_implicit_tree` produces for the same list,
    so the report can look leaves up by path on both evaluation routes.
    """
    leaves = []
    for i, vc in enumerate(variables):
        leaf = leaf_from_variable(vc)
        stamp_leaf_path(leaf, f"/metrics/children/{i}")
        leaves.append(leaf)
    return AndCombinator().combine(leaves)
```

- [ ] **Step 4: Thread the path through `evaluate_spec`; add the window; add the lookup**

In `src/dstf/comparison/tree_eval.py`, import at the top alongside the existing `metric_tree` imports:

```python
from .geometry import Window
from .metric_tree import stamp_leaf_path
```

(Keep whatever the file already imports from `.metric_tree`; add `stamp_leaf_path` to that line.)

Change `evaluate_spec`'s signature and body:

```python
def evaluate_spec(
    spec: SpecNode,
    var_results_by_name: dict[str, VariableResult],
    baselines: dict[str, BaselineView],
    base_tolerance: float,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
    scalars: ScalarSources | None = None,
    path: str = "/metrics",
) -> MetricResult:
```

Add to the docstring's end: ``` ``path`` is this node's RFC 6902 path; leaves are stamped with it (``stamp_leaf_path``) so results address the spec, not a position in a flat list. ```

Replace the body after the docstring:

```python
    sources = scalars if scalars is not None else ScalarSources()
    if isinstance(spec, LeafSpec):
        result = _evaluate_leaf(
            spec, var_results_by_name, baselines, base_tolerance, dataset_kind, sources
        )
        stamp_leaf_path(result, path)
        return result

    children = [
        evaluate_spec(
            c,
            var_results_by_name,
            baselines,
            base_tolerance,
            dataset_kind,
            sources,
            path=f"{path}/children/{i}",
        )
        for i, c in enumerate(spec.children)
    ]
    return _build_combinator(spec).combine(children)
```

In `_evaluate_leaf`, after `vc.tolerance_used = tolerance` (just before `leaf_result = leaf_from_variable(vc)`), add:

```python
    if windowed:
        # The window is a leaf property, not a mode's: the evaluator owns it.
        vc.geometry = vc.geometry + (
            Window(id="window", start=leaf.window_start, end=leaf.window_end),
        )
```

Add after `collect_leaf_variables`:

```python
def leaf_comparisons_by_path(tree: MetricResult) -> dict[str, VariableComparison]:
    """``{leaf_path: VariableComparison}`` for every stamped leaf, in tree order.

    The report and the figure layer look leaves up here, by the path a patch
    op would use — never by position in a flat list.
    """
    out: dict[str, VariableComparison] = {}

    def _walk(node: MetricResult) -> None:
        if not node.children:
            vc = node.diagnostics.get("variable")
            path = node.diagnostics.get("leaf_path")
            if vc is not None and path is not None:
                out[path] = vc
            return
        for child in node.children:
            _walk(child)

    _walk(tree)
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tree_eval.py tests/test_metric_tree.py tests/test_comparator.py -q`
Expected: all pass. If a golden snapshot or report test now fails because `leaf_path` appears in `flatten_evaluation` output, that is expected and handled in Task 11 (goldens are re-blessed there); note it and continue.

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/metric_tree.py src/dstf/comparison/tree_eval.py tests/test_tree_eval.py
git commit -m "feat: the evaluator stamps every leaf with its path and prefixes its geometry"
```

---

### Task 8: `summarize` on the descriptor replaces `_leaf_score_display`

**Files:**
- Create: `src/dstf/comparison/summaries.py`
- Modify: `src/dstf/comparison/registry.py` (~line 40, `MetricDescriptor`; new `summarize`)
- Modify: `src/dstf/comparison/builtins.py` (~lines 110–182)
- Modify: `src/dstf/reporting/context_builder.py` (delete `_leaf_score_display` ~lines 189–286; use `summarize` in `_augment_tree_view` ~line 318)
- Modify: `src/dstf/cli/review.py` (`_print_detail` ~lines 309–333)
- Modify: `tests/test_reporters.py` (~lines 319, 346), `tests/test_scalar_metric.py` (~lines 803–835)
- Test: `tests/test_summaries.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_summaries.py`:

```python
"""One mode-aware summary per leaf, dispatched through the descriptor."""

from __future__ import annotations

import numpy as np

from dstf.comparison.algorithms import (
    _compare_event_timing,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.registry import metrics, summarize
from dstf.comparison.types import ScoreSummary, VariableComparison


def _vc(**kw) -> VariableComparison:
    base = dict(
        index=0, name="x", passed=True, nrmse=1e-3, rmse=1e-3, signal_range=1.0,
        max_abs_error=2e-3, max_abs_error_time=0.5, reference_final=1.0, actual_final=1.0,
        tolerance_used=1e-4,
    )
    base.update(kw)
    return VariableComparison(**base)


def test_every_registered_metric_summarizes_a_sentinel_without_raising():
    for desc in metrics():
        s = desc.summarize(_vc(mode=desc.name))
        assert isinstance(s, ScoreSummary) and s.display and s.criterion


def test_an_error_sentinel_is_mode_agnostic():
    s = summarize(_vc(mode="tube", diagnostics={"error": "empty window"}))
    assert s.display == "not scored"
    assert s.criterion == "tube leaf not scored — empty window"


def test_tube_summary_reads_the_inside_fraction():
    t = np.linspace(0.0, 1.0, 11)
    vc = _compare_tube(t, t, t, t + 0.5, {"tube_width_mode": "band", "tube_abs": 0.1})
    s = summarize(vc)
    assert s.display == "0.0% in tube"
    assert s.criterion.endswith("→ FAIL (requires 100%)")
    assert s.score == 0.0 and s.score_label == "in tube"


def test_range_summary_reads_the_max_violation():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(_compare_range(t, t * 2, min_value=None, max_value=1.0))
    assert s.display.startswith("max_viol 1.000e+00")
    assert "→ FAIL (requires 0)" in s.criterion


def test_event_timing_summary_counts_events():
    ref_t = np.array([0.0, 1.0, 1.0, 2.0])
    s = summarize(_compare_event_timing(ref_t, ref_t, time_tolerance=1e-3))
    assert s.display == "Δt 0.000e+00 (1/1 events)"


def test_nrmse_is_the_default_shape():
    t = np.linspace(0.0, 1.0, 11)
    s = summarize(_compare_trajectories(t, t, t, t + 0.001, tolerance=0.1))
    assert s.display.startswith("NRMSE ") and s.score_label == "NRMSE"
    assert "→ PASS" in s.criterion
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_summaries.py -v`
Expected: FAIL with `ImportError: cannot import name 'summarize'`

- [ ] **Step 3: Create `summaries.py`**

Create `src/dstf/comparison/summaries.py` — the per-mode branches of the old `_leaf_score_display`, one function each, same wording:

```python
"""Per-mode :class:`~.types.ScoreSummary` builders.

Each function turns one mode's ``VariableComparison`` into the pill text and
criterion sentence the report and the terminal review loop show. They are
attached to the mode's :class:`~.registry.MetricDescriptor` and dispatched
by :func:`~.registry.summarize`; a third-party metric supplies its own or
inherits :func:`summarize_default`. No mode name appears outside this file
and the registrations in :mod:`.builtins`.
"""

from __future__ import annotations

import math

from .types import ScoreSummary, VariableComparison


def _verdict(vc: VariableComparison) -> str:
    return "PASS" if vc.passed else "FAIL"


def summarize_default(vc: VariableComparison) -> ScoreSummary:
    """NRMSE-shaped: the score is an error against a tolerance."""
    return ScoreSummary(
        display=f"NRMSE {vc.nrmse:.3e}",
        criterion=(
            f"NRMSE {vc.nrmse:.3e} vs tolerance {vc.tolerance_used:.3e} → {_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="NRMSE",
    )


summarize_nrmse = summarize_default


def summarize_tube(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_points_inside is None:
        return summarize_default(vc)
    pct = vc.tube_points_inside * 100
    return ScoreSummary(
        display=f"{pct:.1f}% in tube",
        criterion=f"{pct:.1f}% inside tube → {_verdict(vc)} (requires 100%)",
        score=float(vc.tube_points_inside),
        score_label="in tube",
    )


def summarize_range(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_worst_violation is None:
        return summarize_default(vc)
    return ScoreSummary(
        display=f"max_viol {vc.tube_worst_violation:.3e}",
        criterion=(
            f"max violation {vc.tube_worst_violation:.3e} → {_verdict(vc)} (requires 0)"
        ),
        score=float(vc.tube_worst_violation),
        score_label="max violation",
    )


def summarize_points(vc: VariableComparison) -> ScoreSummary:
    return ScoreSummary(
        display=f"|err| {vc.max_abs_error:.3e}",
        criterion=(
            f"Final value error {vc.max_abs_error:.3e} vs tolerance "
            f"{vc.tolerance_used:.3e} → {_verdict(vc)}"
        ),
        score=float(vc.max_abs_error),
        score_label="|err|",
    )


def summarize_scalar(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    verdict = _verdict(vc)
    if not math.isfinite(vc.actual_final):
        # Hard-fail sentinel (unresolvable quantity, no samples): there is no
        # scalar to show, so surface the reason instead of "nan vs nan".
        reason = vc.name or "quantity could not be resolved"
        return ScoreSummary(display="not scored", criterion=f"Scalar leaf not scored — {reason}")
    lo, hi = diag.get("min_value"), diag.get("max_value")
    if lo is not None or hi is not None:
        lo_s = "-inf" if lo is None else f"{lo:g}"
        hi_s = "inf" if hi is None else f"{hi:g}"
        return ScoreSummary(
            display=f"value {vc.actual_final:.6g}",
            criterion=f"Scalar {vc.actual_final:.6g} within [{lo_s}, {hi_s}] → {verdict}",
            score=float(vc.actual_final),
            score_label="value",
        )
    fallback = bool(diag.get("abs_fallback"))
    rel = diag.get("tolerance_mode") == "rel" and not fallback
    criterion = "relative" if rel else "absolute"
    note = " (|ref|=0 → absolute)" if fallback else ""
    return ScoreSummary(
        display=(f"rel Δ {vc.nrmse:.3e}" if rel else f"|Δ| {vc.nrmse:.3e}"),
        criterion=(
            f"Scalar {vc.actual_final:.6g} vs baseline {vc.reference_final:.6g}: "
            f"{criterion} deviation {vc.nrmse:.3e} vs "
            f"tolerance {vc.tolerance_used:.3e}{note} → {verdict}"
        ),
        score=float(vc.nrmse),
        score_label="rel Δ" if rel else "|Δ|",
    )


def summarize_event_timing(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    ref_n = diag.get("ref_event_count", "?")
    act_n = diag.get("act_event_count", "?")
    tol = diag.get("time_tolerance", 0.0)
    return ScoreSummary(
        display=f"Δt {vc.nrmse:.3e} ({act_n}/{ref_n} events)",
        criterion=(
            f"Max event Δt {vc.nrmse:.3e} vs {tol:.3e} "
            f"({act_n} actual events / {ref_n} reference) → {_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="Δt",
    )


def summarize_dominant_frequency(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    paired = diag.get("paired_peaks") or []
    n_matched = sum(1 for p in paired if p.get("matched_hz") is not None)
    n_declared = len(paired)
    if n_declared == 0:
        return ScoreSummary(
            display="no peaks declared",
            criterion=(
                "No peaks declared — use 'Detect peaks from reference' "
                "to seed the table from the reference spectrum"
            ),
        )
    return ScoreSummary(
        display=f"{n_matched}/{n_declared} peaks matched",
        criterion=(
            f"{n_matched} / {n_declared} declared peaks found in actual "
            f"spectrum within tolerance → {_verdict(vc)}"
        ),
        score=float(n_matched),
        score_label="peaks matched",
    )
```

- [ ] **Step 4: Add the descriptor field and the dispatcher**

In `src/dstf/comparison/registry.py`, import `ScoreSummary`, `VariableComparison` from `.types` and `summarize_default` from `.summaries` (add to the existing imports). Add the field to `MetricDescriptor` after `accepts_quantity`:

```python
    #: One leaf's verdict in words. The default is NRMSE-shaped; every
    #: builtin supplies its own in :mod:`.builtins`.
    summarize: Callable[[VariableComparison], ScoreSummary] = summarize_default
```

Extend the class docstring with one sentence: ``` ``summarize`` turns the mode's ``VariableComparison`` into the pill text and criterion sentence; see :mod:`.summaries`. ```

Add after `metrics()`:

```python
def summarize(vc: VariableComparison) -> ScoreSummary:
    """The pill text and criterion for one leaf, by its mode's descriptor.

    Any sentinel carrying a reason — a leaf config that could not build, an
    empty-data failure — shows that reason instead of a numeric score there
    is none of. Mode-agnostic on purpose: the flat variable_overrides path
    emits the same shape. Uses ``vc.mode`` raw: the unknown-mode sentinel
    has no mode, and "nrmse leaf" would name the wrong metric.
    """
    error = (vc.diagnostics or {}).get("error")
    if error:
        label = f"{vc.mode} leaf" if vc.mode else "leaf"
        return ScoreSummary(display="not scored", criterion=f"{label} not scored — {error}")
    desc = metric(vc.mode or "nrmse")
    fn = desc.summarize if desc is not None else summarize_default
    return fn(vc)
```

In `src/dstf/comparison/builtins.py`, import the seven functions from `.summaries` and pass one per registration: `summarize=summarize_nrmse`, `summarize=summarize_tube`, `summarize=summarize_points`, `summarize=summarize_range`, `summarize=summarize_event_timing`, `summarize=summarize_dominant_frequency`, `summarize=summarize_scalar`.

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_summaries.py tests/test_metric_registry.py -v`
Expected: all pass

- [ ] **Step 6: Rewire the report and the review loop, delete `_leaf_score_display`**

In `src/dstf/reporting/context_builder.py`:
- Delete the whole `_leaf_score_display` function (from `def _leaf_score_display` to the line before `def _augment_tree_view`) and the now-unused `import math` if nothing else uses it (check with `uv run ruff check`).
- Add `from ..comparison.registry import summarize` to the imports.
- In `_augment_tree_view`, replace `score_display, criterion = _leaf_score_display(vc)` with:

```python
        summary = summarize(vc)
        score_display, criterion = summary.display, summary.criterion
```

In `src/dstf/cli/review.py`, replace `_print_detail`:

```python
def _print_detail(comp) -> None:
    """Print each leaf's mode-aware verdict for interactive review."""
    from ..comparison.registry import summarize

    if comp.error_message:
        print(f"  Error: {comp.error_message}")

    for var in comp.variables:
        name = var.name or f"x[{var.index}]"
        status = "\033[92mPASS\033[0m" if var.passed else "\033[91mFAIL\033[0m"
        summary = summarize(var)
        print(f"    {status}  {name}  [{var.mode}] {summary.display}")
        print(f"           {summary.criterion}")

    if comp.warnings:
        print("    Structural warnings:")
        for w in comp.warnings:
            print(f"      {w.field}: {w.reference_value} -> {w.current_value}")
    print()
```

In `tests/test_reporters.py` (~line 319 and ~346) and `tests/test_scalar_metric.py` (~lines 803–835), replace every `from dstf.reporting.context_builder import _leaf_score_display` with `from dstf.comparison.registry import summarize`, and every `score, criterion = _leaf_score_display(vc)` (whatever the local names) with:

```python
        summary = summarize(vc)
        score, criterion = summary.display, summary.criterion
```

Search for any other reference: `grep -rn "_leaf_score_display" src tests` must return nothing.

- [ ] **Step 7: Run the affected tests**

Run: `uv run pytest tests/test_summaries.py tests/test_reporters.py tests/test_scalar_metric.py tests/test_report_suite.py tests/test_cli_review.py -q` (skip any of those files that do not exist)
Expected: all pass

- [ ] **Step 8: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/comparison/summaries.py src/dstf/comparison/registry.py src/dstf/comparison/builtins.py src/dstf/reporting/context_builder.py src/dstf/cli/review.py tests/test_summaries.py tests/test_reporters.py tests/test_scalar_metric.py
git commit -m "refactor: one mode-aware summary per leaf, on the descriptor — the review loop stops printing NRMSE for every mode"
```

---

### Task 9: The `figures` package — view, decimation, Plotly builders

**Files:**
- Create: `src/dstf/figures/__init__.py`, `src/dstf/figures/view.py`, `src/dstf/figures/plotly.py`
- Move: `src/dstf/reporting/decimate.py` → `src/dstf/figures/decimate.py` (add `decimate_band`)
- Modify: `src/dstf/reporting/report_suite.py` (~line 355, the `from .decimate import decimate_pair`), `tests/test_decimate.py` (line 13)
- Test: `tests/test_figures.py`

- [ ] **Step 1: Move the decimation module**

```bash
git mv src/dstf/reporting/decimate.py src/dstf/figures/decimate.py
```

Create `src/dstf/figures/__init__.py`:

```python
"""One figure layer, two consumers.

A :class:`~.view.VariableView` is everything a plot of one variable needs —
trajectories, overlays, the leaves that target it with their geometry and
summaries, the error series — assembled the same way by the HTML report
and by the authoring tool. :func:`~.plotly.variable_figure` renders it to a
plain Plotly figure dict (``data`` / ``layout`` / ``config``), with no
``plotly.py`` dependency; the report embeds the dict, the tool hands it to
its plotly element. Nothing here knows a mode name: drawing is keyed on
primitive type (:mod:`dstf.comparison.geometry`).
"""
```

Update the moved module's docstring first line to: `"""Largest-Triangle-Three-Buckets (LTTB) decimation for figure rendering.` and its "Used only on the ``interactive.html`` embed path" sentence to: `Used on every figure embed — the report page and the authoring tool share the budget — while the full-resolution arrays stay on disk in ``comparison_data.json``.`

Append to `src/dstf/figures/decimate.py`:

```python
def decimate_band(
    time: NDArray[np.float64],
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
    n_out: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Decimate an envelope on ONE shared time axis.

    LTTB picks the kept samples from ``upper``; ``lower`` is then read at
    exactly those times. The kept times are grid nodes, so ``np.interp`` at
    them returns the node values — no approximation, and the two edges can
    never drift onto different x arrays.
    """
    t2, u2 = lttb(np.asarray(time, dtype=np.float64), np.asarray(upper, dtype=np.float64), n_out)
    l2 = np.interp(t2, np.asarray(time, dtype=np.float64), np.asarray(lower, dtype=np.float64))
    return t2, l2, u2
```

Update the import in `src/dstf/reporting/report_suite.py` (`_decimate_context_for_html`): `from ..figures.decimate import decimate_pair`. Update `tests/test_decimate.py` line 13: `from dstf.figures.decimate import decimate_pair, lttb`.

Run: `uv run pytest tests/test_decimate.py tests/test_report_suite.py -q` → all pass.

- [ ] **Step 2: Write the failing figure tests**

Create `tests/test_figures.py`:

```python
"""The figure layer: a view per variable, one Plotly dict, no mode names."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_range,
    _compare_tube,
)
from dstf.comparison.geometry import Band, Marker, Window
from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
from dstf.figures.decimate import decimate_band
from dstf.figures.plotly import variable_figure
from dstf.figures.view import build_view, decimate_view


def _stamped(vc, path="/metrics/children/0"):
    stamp_leaf_path(leaf_from_variable(vc), path)
    return path, vc


def _tube_view(n=11, **cfg):
    t = np.linspace(0.0, 1.0, n)
    ref = np.sin(t)
    act = ref + 0.01
    vc = _compare_tube(t, ref, t, act, {"tube_width_mode": "band", "tube_abs": 0.05, **cfg})
    vc.name = "x"
    return build_view("x", act_time=t, act_values=act, ref_time=t, ref_values=ref, leaves=[_stamped(vc)])


class TestView:
    def test_error_series_exist_only_when_both_curves_do(self):
        t = np.linspace(0.0, 1.0, 5)
        with_ref = build_view("x", act_time=t, act_values=t, ref_time=t, ref_values=t * 2)
        assert [s.id for s in with_ref.errors] == ["error/signed", "error/abs", "error/nrmse"]
        np.testing.assert_allclose(with_ref.errors[0].y, t - t * 2)
        assert build_view("x", act_time=t, act_values=t).errors == ()
        assert build_view("x").act_time is None and build_view("x").ref_time is None

    def test_leaf_views_carry_geometry_and_summary(self):
        view = _tube_view()
        (leaf,) = view.leaves
        assert leaf.path == "/metrics/children/0" and leaf.metric == "tube"
        assert leaf.summary.display.endswith("% in tube")
        assert any(isinstance(p, Band) for p in leaf.geometry)

    def test_decimate_view_keeps_band_edges_on_one_axis_and_stamps_counts(self):
        view = decimate_view(_tube_view(n=2001), 100)
        assert len(view.act_time) <= 100 and view.full_samples == {"act": 2001, "ref": 2001}
        band = next(p for leaf in view.leaves for p in leaf.geometry if isinstance(p, Band))
        assert len(band.time) <= 100 and len(band.lower) == len(band.upper) == len(band.time)
        assert all(len(s.x) <= 100 for s in view.errors)

    def test_decimate_band_reads_lower_at_the_kept_nodes(self):
        t = np.linspace(0.0, 1.0, 1001)
        t2, lo2, up2 = decimate_band(t, -t, t, 50)
        np.testing.assert_allclose(lo2, -t2)
        np.testing.assert_allclose(up2, t2)


class TestFigure:
    def test_figure_is_a_plain_dict_with_ids_on_every_trace_and_shape(self):
        fig = variable_figure(_tube_view())
        assert set(fig) == {"data", "layout", "config"}
        names = [tr["name"] for tr in fig["data"]]
        assert names[:2] == ["Actual", "Reference"]
        band = next(tr for tr in fig["data"] if tr["meta"].get("id") == "/metrics/children/0/tube/band")
        assert band["fill"] == "toself" and len(band["x"]) == 2 * 11
        assert all(isinstance(tr["x"], list) for tr in fig["data"])  # JSON-ready, no arrays
        assert "xaxis2" not in fig["layout"]

    def test_handles_group_into_one_trace_per_family_with_ids_in_customdata(self):
        fig = variable_figure(_tube_view(tube_points=[{"time": 0.2, "upper": 0.1, "lower": 0.1}, {"time": 0.8, "upper": 0.1, "lower": 0.1}]))
        upper = next(tr for tr in fig["data"] if tr["meta"].get("family") == "tube/upper")
        assert upper["customdata"] == ["/metrics/children/0/tube/upper/0", "/metrics/children/0/tube/upper/1"]
        assert upper["meta"]["kind"] == "handle" and upper["mode"] == "markers"

    def test_window_and_range_bounds_become_shapes_clipped_to_the_window(self):
        t = np.linspace(0.0, 1.0, 11)
        vc = _compare_range(t, t, min_value=None, max_value=0.5)
        vc.name = "x"
        vc.geometry = vc.geometry + (Window(id="window", start=0.25, end=None),)
        view = build_view("x", act_time=t, act_values=t, leaves=[_stamped(vc)])
        fig = variable_figure(view)
        shapes = {s["name"]: s for s in fig["layout"]["shapes"]}
        assert shapes["/metrics/children/0/window"]["xref"] == "x" and shapes["/metrics/children/0/window"]["x1"] == 1.0
        strong = shapes["/metrics/children/0/range/max"]
        assert (strong["x0"], strong["x1"], strong["y0"]) == (0.25, 1.0, 0.5)
        assert shapes["/metrics/children/0/range/max/outside"]["line"]["color"] != strong["line"]["color"]
        # An invisible anchor keeps the bound inside the autorange.
        assert any(tr.get("meta", {}).get("anchor_for") == "/metrics/children/0/range/max" for tr in fig["data"])

    def test_spectrum_primitives_open_a_second_panel(self):
        t = np.linspace(0.0, 10.0, 1001)
        sig = np.sin(2 * np.pi * 2.0 * t)
        vc = _compare_dominant_frequency(t, sig, t, sig, peaks=[{"freq": 2.0, "tolerance": 0.05}])
        vc.name = "x"
        view = build_view("x", act_time=t, act_values=sig, ref_time=t, ref_values=sig, leaves=[_stamped(vc)])
        fig = variable_figure(view)
        assert fig["layout"]["xaxis2"]["title"]["text"] == "Frequency [Hz]"
        spectrum = next(tr for tr in fig["data"] if tr["meta"].get("id") == "/metrics/children/0/spectrum/act")
        assert spectrum["xaxis"] == "x2" and spectrum["yaxis"] == "y2"
        window = next(s for s in fig["layout"]["shapes"] if s["name"] == "/metrics/children/0/peaks/window/0")
        assert window["xref"] == "x2" and window["yref"] == "paper"

    def test_error_series_ride_on_a_right_axis_hidden_by_default(self):
        fig = variable_figure(_tube_view())
        err = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
        assert [tr["meta"]["id"] for tr in err] == ["error/signed", "error/abs", "error/nrmse"]
        assert all(tr["visible"] == "legendonly" and tr["yaxis"] == "y3" for tr in err)
        assert fig["layout"]["yaxis3"]["overlaying"] == "y"

    def test_budget_decimates_inside_variable_figure(self):
        fig = variable_figure(_tube_view(n=5001), max_samples=200)
        actual = next(tr for tr in fig["data"] if tr["name"] == "Actual")
        assert len(actual["x"]) <= 200 and actual["meta"]["full_samples"] == 5001

    def test_a_view_with_no_trajectory_still_renders_its_lines(self):
        from dstf.comparison.algorithms import _compare_scalar

        one = np.array([0.0])
        vc = _compare_scalar(one, np.array([3.0]), one, np.array([3.1]), reduction="final", tolerance=0.5, tolerance_mode="abs", min_value=None, max_value=None)
        vc.name = "CPUtime.final"
        fig = variable_figure(build_view("CPUtime.final", leaves=[_stamped(vc)]))
        names = {s["name"] for s in fig["layout"]["shapes"]}
        assert {"/metrics/children/0/scalar/actual", "/metrics/children/0/scalar/reference"} <= names
        assert fig["data"] == [] or all("anchor_for" in tr.get("meta", {}) for tr in fig["data"])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_figures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dstf.figures.view'`

- [ ] **Step 4: Create `view.py`**

Create `src/dstf/figures/view.py`:

```python
"""The library-neutral picture of one variable.

Both tiers build a :class:`VariableView` the same way — from the report's
trajectory dicts and the evaluated leaves — and hand it to
:func:`~.plotly.variable_figure`. Decimation happens on the view
(:func:`decimate_view`), before any trace exists, so an envelope's two edges
stay on one time axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from ..comparison.geometry import Band, Primitive, Series
from ..comparison.registry import summarize
from ..comparison.types import ScoreSummary, VariableComparison
from .decimate import decimate_band, lttb


@dataclass(frozen=True)
class OverlayView:
    name: str
    role: str
    kind: str | None
    time: np.ndarray
    values: np.ndarray
    full_samples: int | None = None


@dataclass(frozen=True)
class LeafView:
    path: str
    metric: str
    passed: bool
    geometry: tuple[Primitive, ...]
    summary: ScoreSummary


@dataclass(frozen=True)
class VariableView:
    name: str
    act_time: np.ndarray | None = None
    act_values: np.ndarray | None = None
    ref_time: np.ndarray | None = None
    ref_values: np.ndarray | None = None
    overlays: tuple[OverlayView, ...] = ()
    leaves: tuple[LeafView, ...] = ()
    errors: tuple[Series, ...] = ()
    #: Pre-decimation sample counts, stamped by :func:`decimate_view`.
    full_samples: dict[str, int] = field(default_factory=dict)

    @property
    def has_spectrum(self) -> bool:
        return any(p.panel == "spectrum" for leaf in self.leaves for p in leaf.geometry)


def _array(values) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    return arr if arr.size else None


def error_series(
    ref_time: np.ndarray | None,
    ref_values: np.ndarray | None,
    act_time: np.ndarray | None,
    act_values: np.ndarray | None,
) -> tuple[Series, ...]:
    """Signed, absolute and range-normalised error of the actual on the
    reference grid — the same linear interpolation the comparator uses."""
    if ref_time is None or ref_values is None or act_time is None or act_values is None:
        return ()
    act_on_ref = np.interp(ref_time, act_time, act_values)
    signed = act_on_ref - ref_values
    absolute = np.abs(signed)
    span = float(np.max(ref_values) - np.min(ref_values))
    per_point = absolute / span if span > 1e-15 else absolute
    return (
        Series(id="error/signed", kind="error", x=ref_time, y=signed),
        Series(id="error/abs", kind="error", x=ref_time, y=absolute),
        Series(id="error/nrmse", kind="error", x=ref_time, y=per_point),
    )


def build_view(
    name: str,
    *,
    act_time=None,
    act_values=None,
    ref_time=None,
    ref_values=None,
    overlays: Sequence[dict] = (),
    leaves: Sequence[tuple[str, VariableComparison]] = (),
) -> VariableView:
    """Assemble a view from array-likes (lists or arrays), overlay dicts of
    the report's shape (``name`` / ``role`` / ``kind`` / ``time`` /
    ``values``), and ``(leaf_path, VariableComparison)`` pairs."""
    at, av, rt, rv = _array(act_time), _array(act_values), _array(ref_time), _array(ref_values)
    return VariableView(
        name=name,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(
            OverlayView(
                name=str(ov.get("name") or ""),
                role=str(ov.get("role") or "companion"),
                kind=ov.get("kind"),
                time=np.asarray(ov.get("time") or [], dtype=float),
                values=np.asarray(ov.get("values") or [], dtype=float),
            )
            for ov in overlays
        ),
        leaves=tuple(
            LeafView(
                path=path,
                metric=vc.mode or "nrmse",
                passed=bool(vc.passed),
                geometry=tuple(vc.geometry),
                summary=summarize(vc),
            )
            for path, vc in leaves
        ),
        errors=error_series(rt, rv, at, av),
    )


def _pair(time, values, n):
    if time is None or values is None:
        return time, values
    return lttb(time, values, n)


def decimate_view(view: VariableView, max_samples: int) -> VariableView:
    """A copy under the sample budget: trajectories, overlays, error series
    and bands are LTTB-decimated; ``full_samples`` records what they were."""
    if max_samples is None or max_samples <= 0:
        return view
    at, av = _pair(view.act_time, view.act_values, max_samples)
    rt, rv = _pair(view.ref_time, view.ref_values, max_samples)

    def _prim(p: Primitive) -> Primitive:
        if isinstance(p, Band):
            t2, l2, u2 = decimate_band(p.time, p.lower, p.upper, max_samples)
            return replace(p, time=t2, lower=l2, upper=u2)
        if isinstance(p, Series):
            x2, y2 = lttb(p.x, p.y, max_samples)
            return replace(p, x=x2, y=y2)
        return p

    return replace(
        view,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(
            OverlayView(
                name=ov.name,
                role=ov.role,
                kind=ov.kind,
                time=lttb(ov.time, ov.values, max_samples)[0],
                values=lttb(ov.time, ov.values, max_samples)[1],
                full_samples=len(ov.time),
            )
            for ov in view.overlays
        ),
        leaves=tuple(replace(leaf, geometry=tuple(_prim(p) for p in leaf.geometry)) for leaf in view.leaves),
        errors=tuple(_prim(s) for s in view.errors),  # type: ignore[misc]
        full_samples={
            "act": 0 if view.act_time is None else len(view.act_time),
            "ref": 0 if view.ref_time is None else len(view.ref_time),
        },
    )
```

- [ ] **Step 5: Create `plotly.py`**

Create `src/dstf/figures/plotly.py`:

```python
"""Render a :class:`~.view.VariableView` to a Plotly figure dict.

Plain dicts, JSON-ready, no ``plotly.py``. One builder per primitive type in
:data:`TRACE_BUILDERS`; assembly adds the actual / reference / overlay
traces, the error axis and the spectrum panel. No mode name appears here —
a new metric is drawn by emitting primitives, a new primitive by adding one
builder.

Every trace carries the primitive id in ``meta`` (grouped marker traces
carry the ids in ``customdata`` and the family in ``meta``); every shape
carries it as ``name``. Those are the handles on-plot editing attaches to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..comparison.geometry import Band, Box, HLine, Marker, Primitive, Series, VLine, Window
from .view import LeafView, VariableView, decimate_view

PLOT_CONFIG = {"responsive": True, "displaylogo": False, "scrollZoom": True}

ACTUAL = "#2196F3"
REFERENCE = "#FF9800"
PASS = "rgba(76,175,80,0.15)"
PASS_LINE = "rgba(76,175,80,0.6)"
FAIL = "rgba(244,67,54,0.15)"
FAIL_LINE = "rgba(244,67,54,0.8)"
MUTED = "rgba(117,117,117,0.35)"
WINDOW_FILL = "rgba(33,150,243,0.08)"
WINDOW_LINE = "rgba(33,150,243,0.4)"
ERROR = "#f44336"

#: Vertical extent of the two panels when a spectrum is present.
TIME_DOMAIN = (0.45, 1.0)
SPECTRUM_DOMAIN = (0.0, 0.32)

_LABELS = {
    "tube/band": "Tube",
    "tube/upper": "Tube upper points",
    "tube/lower": "Tube lower points",
    "points/target": "Checkpoints",
    "points/box": "Checkpoint boxes",
    "range/min": "Min bound",
    "range/max": "Max bound",
    "scalar/actual": "Actual (scalar)",
    "scalar/reference": "Reference (scalar)",
    "scalar/min": "Min bound",
    "scalar/max": "Max bound",
    "events/ref": "Reference events",
    "events/act": "Actual events",
    "events/window": "Event windows",
    "spectrum/ref": "Reference spectrum",
    "spectrum/act": "Actual spectrum",
    "peaks/declared": "Declared peaks",
    "peaks/matched": "Matched peaks",
    "peaks/detected": "Reference peaks",
    "peaks/window": "Peak windows",
    "error/signed": "Signed error",
    "error/abs": "Abs error",
    "error/nrmse": "NRMSE (per-point)",
}

_MARKER_SYMBOLS = {
    "tube/upper": "triangle-up",
    "tube/lower": "triangle-down",
    "points/target": "diamond",
    "peaks/declared": "diamond",
    "peaks/matched": "x",
    "peaks/detected": "circle-open",
}


@dataclass(frozen=True)
class _Ctx:
    x_min: float
    x_max: float
    has_spectrum: bool


def family(prim: Primitive, leaf: LeafView) -> str:
    """``tube/upper`` from ``/metrics/children/0/tube/upper/3``."""
    local = prim.id[len(leaf.path) + 1 :] if prim.id.startswith(leaf.path + "/") else prim.id
    return "/".join(local.split("/")[:2])


def _label(fam: str) -> str:
    return _LABELS.get(fam, fam)


def _axes(prim: Primitive) -> dict:
    return {"xaxis": "x2", "yaxis": "y2"} if prim.panel == "spectrum" else {}


def _xref(prim: Primitive) -> str:
    return "x2" if prim.panel == "spectrum" else "x"


def _paper_span(prim: Primitive, ctx: _Ctx) -> tuple[float, float]:
    if not ctx.has_spectrum:
        return (0.0, 1.0)
    return SPECTRUM_DOMAIN if prim.panel == "spectrum" else TIME_DOMAIN


def _window_span(window: Window | None, ctx: _Ctx) -> tuple[float, float]:
    if window is None:
        return ctx.x_min, ctx.x_max
    return (
        ctx.x_min if window.start is None else float(window.start),
        ctx.x_max if window.end is None else float(window.end),
    )


def _band(prim: Band, leaf: LeafView, window: Window | None, ctx: _Ctx):
    color = PASS if leaf.passed else FAIL
    return [
        {
            "type": "scatter",
            "x": [*prim.time.tolist(), *prim.time[::-1].tolist()],
            "y": [*prim.upper.tolist(), *prim.lower[::-1].tolist()],
            "fill": "toself",
            "fillcolor": color,
            "line": {"color": "rgba(0,0,0,0)", "width": 0},
            "name": _label(family(prim, leaf)),
            "hoverinfo": "skip",
            "showlegend": True,
            "meta": {"id": prim.id, "kind": prim.kind},
            **_axes(prim),
        }
    ], []


def _box(prim: Box, leaf: LeafView, window: Window | None, ctx: _Ctx):
    full_height = prim.y0 is None or prim.y1 is None
    y0, y1 = _paper_span(prim, ctx) if full_height else (prim.y0, prim.y1)
    ok = prim.passed is not False
    return [], [
        {
            "type": "rect",
            "xref": _xref(prim),
            "yref": "paper" if full_height else ("y2" if prim.panel == "spectrum" else "y"),
            "x0": prim.t0,
            "x1": prim.t1,
            "y0": y0,
            "y1": y1,
            "fillcolor": (PASS if ok else FAIL) if not full_height else "rgba(117,117,117,0.09)",
            "line": {"color": PASS_LINE if ok else FAIL_LINE, "width": 1, "dash": "dot"},
            "name": prim.id,
        }
    ]


def _hline(prim: HLine, leaf: LeafView, window: Window | None, ctx: _Ctx):
    ok = prim.passed is not False
    color = (PASS_LINE if ok else FAIL_LINE) if prim.kind in ("handle", "result", "act") else MUTED
    x0, x1 = _window_span(window, ctx)
    shapes = [
        {
            "type": "line",
            "xref": _xref(prim),
            "yref": "y2" if prim.panel == "spectrum" else "y",
            "x0": x0,
            "x1": x1,
            "y0": prim.value,
            "y1": prim.value,
            "line": {"color": color, "width": 1.5, "dash": "dash"},
            "name": prim.id,
        }
    ]
    if window is not None:
        shapes.append(
            {
                "type": "line",
                "xref": "paper",
                "yref": "y2" if prim.panel == "spectrum" else "y",
                "x0": 0,
                "x1": 1,
                "y0": prim.value,
                "y1": prim.value,
                "line": {"color": MUTED, "width": 1, "dash": "dot"},
                "name": f"{prim.id}/outside",
            }
        )
    anchor = {
        "type": "scatter",
        "x": [ctx.x_min],
        "y": [prim.value],
        "mode": "markers",
        "marker": {"opacity": 0},
        "showlegend": False,
        "hoverinfo": "skip",
        "meta": {"anchor_for": prim.id},
        **_axes(prim),
    }
    return [anchor], shapes


def _vline(prim: VLine, leaf: LeafView, window: Window | None, ctx: _Ctx):
    y0, y1 = _paper_span(prim, ctx)
    ref = prim.kind == "ref"
    return [], [
        {
            "type": "line",
            "xref": _xref(prim),
            "yref": "paper",
            "x0": prim.time,
            "x1": prim.time,
            "y0": y0,
            "y1": y1,
            "line": {"color": "#757575" if ref else "#1976D2", "width": 1 if ref else 1.5, "dash": "dash" if ref else "solid"},
            "name": prim.id,
        }
    ]


def _series(prim: Series, leaf: LeafView, window: Window | None, ctx: _Ctx):
    fam = family(prim, leaf)
    color = REFERENCE if prim.kind == "ref" else ACTUAL
    return [
        {
            "type": "scatter",
            "mode": "lines",
            "x": prim.x.tolist(),
            "y": prim.y.tolist(),
            "name": _label(fam),
            "line": {"color": color, "width": 1.2, "dash": "dash" if prim.kind == "ref" else "solid"},
            "meta": {"id": prim.id, "kind": prim.kind},
            **_axes(prim),
        }
    ], []


def _window(prim: Window, leaf: LeafView, window: Window | None, ctx: _Ctx):
    x0, x1 = _window_span(prim, ctx)
    y0, y1 = _paper_span(prim, ctx)
    return [], [
        {
            "type": "rect",
            "xref": "x",
            "yref": "paper",
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "fillcolor": WINDOW_FILL,
            "line": {"color": WINDOW_LINE, "width": 1, "dash": "dot"},
            "name": prim.id,
        }
    ]


Builder = Callable[[Primitive, LeafView, "Window | None", _Ctx], "tuple[list[dict], list[dict]]"]

#: One builder per primitive type. ``Marker`` is grouped per family in
#: :func:`variable_figure` rather than built one trace at a time.
TRACE_BUILDERS: dict[type, Builder] = {
    Band: _band,  # type: ignore[dict-item]
    Box: _box,  # type: ignore[dict-item]
    HLine: _hline,  # type: ignore[dict-item]
    VLine: _vline,  # type: ignore[dict-item]
    Series: _series,  # type: ignore[dict-item]
    Window: _window,  # type: ignore[dict-item]
}


def _marker_traces(markers: list[Marker], leaf: LeafView) -> list[dict]:
    by_family: dict[str, list[Marker]] = {}
    for m in markers:
        by_family.setdefault(family(m, leaf), []).append(m)
    traces = []
    for fam, group in by_family.items():
        failed = any(m.passed is False for m in group)
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "x": [m.x for m in group],
                "y": [m.y for m in group],
                "customdata": [m.id for m in group],
                "name": _label(fam),
                "marker": {
                    "symbol": _MARKER_SYMBOLS.get(fam, "circle"),
                    "size": 10,
                    "color": FAIL_LINE if failed else ("#2e7d32" if group[0].kind == "handle" else ACTUAL),
                    "line": {"color": "white", "width": 1},
                },
                "hoverinfo": "x+y",
                "showlegend": True,
                "meta": {"family": fam, "kind": group[0].kind},
                **_axes(group[0]),
            }
        )
    return traces


def _base_traces(view: VariableView) -> list[dict]:
    traces = []
    if view.act_time is not None and view.act_values is not None:
        traces.append(
            {
                "type": "scatter", "mode": "lines", "name": "Actual",
                "x": view.act_time.tolist(), "y": view.act_values.tolist(),
                "line": {"color": ACTUAL, "width": 1.5},
                "meta": {"id": "actual", "kind": "act", "full_samples": view.full_samples.get("act", len(view.act_time))},
            }
        )
    if view.ref_time is not None and view.ref_values is not None:
        traces.append(
            {
                "type": "scatter", "mode": "lines", "name": "Reference",
                "x": view.ref_time.tolist(), "y": view.ref_values.tolist(),
                "line": {"color": REFERENCE, "width": 1.5, "dash": "dash"},
                "meta": {"id": "reference", "kind": "ref", "full_samples": view.full_samples.get("ref", len(view.ref_time))},
            }
        )
    for ov in view.overlays:
        if ov.role == "soft_check":
            color, dash = "#7B1FA2", "dot"
        elif ov.kind == "sibling-backend":
            color, dash = "#1976D2", "dash"
        else:
            color, dash = "#388E3C", "dashdot"
        traces.append(
            {
                "type": "scatter", "mode": "lines",
                "name": f"{ov.role}: {ov.name}" if ov.name else ov.role,
                "x": ov.time.tolist(), "y": ov.values.tolist(),
                "line": {"color": color, "width": 1.2, "dash": dash},
                "opacity": 0.85, "visible": "legendonly",
                "meta": {"id": f"overlay/{ov.role}/{ov.name}", "kind": "overlay", "role": ov.role, "full_samples": ov.full_samples},
            }
        )
    return traces


def _error_trace(s: Series, leaf_path_free_id: str) -> dict:
    return {
        "type": "scatter", "mode": "lines",
        "x": s.x.tolist(), "y": s.y.tolist(),
        "name": _label(leaf_path_free_id),
        "line": {"color": ERROR, "width": 1}, "opacity": 0.6,
        "yaxis": "y3", "visible": "legendonly",
        "hovertemplate": f"{_label(leaf_path_free_id)}: %{{y:.4g}}<extra></extra>",
        "meta": {"id": s.id, "kind": "error"},
    }


def _time_span(view: VariableView) -> tuple[float, float]:
    xs = [a for a in (view.act_time, view.ref_time) if a is not None and len(a)]
    if not xs:
        return 0.0, 1.0
    return float(min(a[0] for a in xs)), float(max(a[-1] for a in xs))


def _layout(view: VariableView, shapes: list[dict], has_errors: bool) -> dict:
    layout: dict = {
        "xaxis": {"title": {"text": "Time"}, "hoverformat": ".6~g", "uirevision": "keep"},
        "yaxis": {"title": {"text": "Value", "standoff": 10}, "tickformat": ".4g", "automargin": True, "nticks": 6, "uirevision": "keep"},
        "margin": {"t": 25, "b": 45, "l": 60, "r": 20},
        "legend": {"x": 0, "y": 1, "bgcolor": "rgba(255,255,255,0.8)"},
        "hovermode": "x unified",
        "shapes": shapes,
        "uirevision": "keep",
    }
    if view.has_spectrum:
        layout["yaxis"]["domain"] = list(TIME_DOMAIN)
        layout["xaxis2"] = {"title": {"text": "Frequency [Hz]"}, "anchor": "y2"}
        layout["yaxis2"] = {"title": {"text": "Magnitude"}, "domain": list(SPECTRUM_DOMAIN), "anchor": "x2"}
    if has_errors:
        layout["yaxis3"] = {
            "title": {"text": "Error", "font": {"color": ERROR, "size": 10}},
            "tickformat": ".4g", "nticks": 4, "automargin": True,
            "overlaying": "y", "side": "right", "showgrid": False,
            "tickfont": {"color": ERROR, "size": 10},
        }
    return layout


def variable_figure(view: VariableView, max_samples: int | None = None) -> dict:
    """The one figure both tiers draw. ``max_samples`` decimates the view first."""
    if max_samples:
        view = decimate_view(view, max_samples)
    x_min, x_max = _time_span(view)
    ctx = _Ctx(x_min=x_min, x_max=x_max, has_spectrum=view.has_spectrum)
    data: list[dict] = _base_traces(view)
    shapes: list[dict] = []
    for leaf in view.leaves:
        window = next((p for p in leaf.geometry if isinstance(p, Window)), None)
        markers: list[Marker] = []
        for prim in leaf.geometry:
            if isinstance(prim, Marker):
                markers.append(prim)
                continue
            traces, new_shapes = TRACE_BUILDERS[type(prim)](prim, leaf, window, ctx)
            data.extend(traces)
            shapes.extend(new_shapes)
        data.extend(_marker_traces(markers, leaf))
    for s in view.errors:
        data.append(_error_trace(s, s.id))
    return {"data": data, "layout": _layout(view, shapes, bool(view.errors)), "config": dict(PLOT_CONFIG)}
```

- [ ] **Step 6: Run the tests until they pass; fix only what the tests name**

Run: `uv run pytest tests/test_figures.py tests/test_decimate.py -v`
Expected: all pass. Typical first-run correction: numpy scalars leaking into dicts — wrap with `float(...)`.

- [ ] **Step 7: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/figures tests/test_figures.py tests/test_decimate.py src/dstf/reporting/report_suite.py
git commit -m "feat: the figures package — one view per variable, one Plotly dict, no mode names"
```

---

### Task 10: The report embeds figure dicts; leaves are looked up by path

**Files:**
- Modify: `src/dstf/reporting/context_builder.py` (`_build_tree_view_and_variables`, ~lines 955–975)
- Modify: `src/dstf/reporting/report_suite.py` (`generate_comparison_plots` ~line 149; new `_build_figures`)
- Modify: `src/dstf/reporting/templates/interactive.html` (~line 424, `window.MT_REPORT`)
- Test: `tests/test_report_suite.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_suite.py` (reuse the file's existing fixtures for a rendered test directory if one exists; otherwise this self-contained one):

```python
class TestEmbeddedFigures:
    def _render(self, tmp_path):
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 3001)
        ref = {"test_id": "ref_0001", "time": t.tolist(), "variables": [{"index": 1, "name": "x", "values": np.sin(t).tolist()}]}
        result = TestResult(model_id="M", success=True, variables=[VariableResult(index=1, name="x", time=t, values=np.sin(t) + 0.001)])
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            variable_overrides={"x": {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.01}},
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots("M", ref, result, comp.variables, plot_dir, test_model=test, metric_tree=comp.metric_tree, max_embedded_samples=500)
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads((plot_dir / "comparison_data.json").read_text(encoding="utf-8"))
        return page, sidecar

    def test_page_embeds_a_decimated_figure_per_variable_and_the_sidecar_does_not(self, tmp_path):
        page, sidecar = self._render(tmp_path)
        assert "FIGURES:" in page and '"/metrics/children/0/tube/band"' in page
        assert "figures" not in sidecar and "diag_figures" not in sidecar
        assert len(sidecar["trajectories"][0]["act_time"]) == 3001
        start = page.index("FIGURES:")
        assert "full_samples" in page[start:] and "3001" in page[start:]
```

If `variable_overrides` is not a `TestModel` field name in this repo, use the name the flat-override path reads in `comparator.compare_test` (`test.variable_overrides`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_report_suite.py::TestEmbeddedFigures -v`
Expected: FAIL (`"FIGURES:" not in page`)

- [ ] **Step 3: Replace the positional zip with the path lookup**

In `src/dstf/reporting/context_builder.py`, `_build_tree_view_and_variables`, replace the block from `# Map path → VariableComparison so _augment_tree_view can enrich each` through the `logger.warning(...)` call (the whole `if len(leaf_paths_full) == len(comparisons): ... else: ...` construct, and the `leaf_paths_full = _collect_leaf_paths(spec)` line) with:

```python
    # Leaves are addressed by the path a patch op would use — stamped by the
    # evaluator on both evaluation routes — never by position in the flat
    # comparison list.
    from ..comparison.tree_eval import leaf_comparisons_by_path

    comparisons_by_path = (
        leaf_comparisons_by_path(metric_tree) if metric_tree is not None else {}
    )
```

Remove the now-unused `collect_leaf_paths as _collect_leaf_paths` import in that function. If `logger` has no other use in the module, remove it too (ruff will say).

- [ ] **Step 4: Build the figures after the sidecar write**

In `src/dstf/reporting/report_suite.py`, add after `_sanitize_filename`:

```python
def _build_figures(context: dict, metric_tree, max_samples: int | None) -> dict:
    """Figure dicts for the page: one per tracked variable, plus diagnostic
    and no-baseline plots. Built from the FULL-resolution context — the view
    decimates under ``max_samples`` — and after the sidecar write, so
    ``comparison_data.json`` keeps its trajectory shape and no figure."""
    from ..comparison.tree_eval import leaf_comparisons_by_path
    from ..figures.plotly import variable_figure
    from ..figures.view import build_view

    budget = max_samples if max_samples and max_samples > 0 else None
    by_path = leaf_comparisons_by_path(metric_tree) if metric_tree is not None else {}
    leaves_by_var: dict[str, list] = {}
    for path, vc in by_path.items():
        leaves_by_var.setdefault(vc.name or f"x[{vc.index}]", []).append((path, vc))

    figures = {}
    for name, var in (context.get("variables_by_name") or {}).items():
        traj = var.get("trajectory") or {}
        view = build_view(
            name,
            act_time=traj.get("act_time"),
            act_values=traj.get("act_values"),
            ref_time=traj.get("ref_time"),
            ref_values=traj.get("ref_values"),
            overlays=var.get("overlays") or [],
            leaves=leaves_by_var.get(name, []),
        )
        figures[name] = variable_figure(view, budget)
    diag = [
        variable_figure(
            build_view(t["name"], act_time=t.get("act_time"), act_values=t.get("act_values"), ref_time=t.get("ref_time"), ref_values=t.get("ref_values")),
            budget,
        )
        for t in context.get("diag_trajectories") or []
    ]
    nb = [
        variable_figure(
            build_view(t["name"], act_time=t.get("time"), act_values=t.get("values"), overlays=t.get("overlays") or []),
            budget,
        )
        for t in context.get("nobaseline_trajectories") or []
    ]
    return {"figures": figures, "diag_figures": diag, "nb_figures": nb}
```

In `generate_comparison_plots`, immediately after the sidecar `data_path.write_text(...)` call and before `_decimate_context_for_html(context, max_embedded_samples)`, add:

```python
    # Figures come from the full-resolution context and decimate themselves;
    # they are added AFTER the sidecar write so the sidecar's shape (the
    # handoff artifact dashboard_render reads) does not change.
    context.update(_build_figures(context, metric_tree, max_embedded_samples))
```

Also delete the stale tombstone comment in the same function (the four lines beginning `# Stage 5 — static matplotlib per-variable PNGs were retired along`).

- [ ] **Step 5: Marshal the figures into the page**

In `src/dstf/reporting/templates/interactive.html`, inside `window.MT_REPORT = { ... }`, add after the `NB_TRAJECTORIES` line:

```
  {# Figure dicts from dstf.figures — the first paint of every plot. #}
  FIGURES: {{ (figures or {}) | tojson }},
  DIAG_FIGURES: {{ (diag_figures or []) | tojson }},
  NB_FIGURES: {{ (nb_figures or []) | tojson }},
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_report_suite.py tests/test_reporters.py tests/test_report_size_budget.py -q`
Expected: all pass. If `test_report_size_budget.py` now exceeds its budget, report the measured size in the commit message and STOP: the budget is a spec-level decision (§4.3 says the test stays), not one to relax in this task.

- [ ] **Step 7: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/reporting/context_builder.py src/dstf/reporting/report_suite.py src/dstf/reporting/templates/interactive.html tests/test_report_suite.py
git commit -m "feat: the report embeds Python-built figure dicts and looks leaves up by path"
```

---

### Task 11: First paint from the figure dict

**Files:**
- Modify: `src/dstf/reporting/templates/scorers.js` (~line 14, the globals)
- Modify: `src/dstf/reporting/templates/plots.js` (`renderVariablePlot`, ~line 131)
- Modify: `src/dstf/reporting/templates/export.js` (`setErrorOverlay` ~line 59; `renderDiagPlots` ~line 111; `renderNBPlots` ~line 134)
- Modify: `tests/golden/interactive_*.hash` (re-blessed)
- Test: `tests/test_interactive_figures.py`

- [ ] **Step 1: Write the failing browser test**

Create `tests/test_interactive_figures.py`:

```python
"""The page's first paint comes from the embedded figure dict; an edit
falls back to the JavaScript path until sub-project 2 removes it."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from test_interactive_points import _render_with_context  # noqa: E402
from test_interactive_playwright import _fixture_context  # noqa: E402


def _context_with_figure():
    from dstf.comparison.algorithms import _compare_tube
    from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx = _fixture_context()
    var = next(iter(ctx["variables_by_name"]))
    traj = ctx["variables_by_name"][var]["trajectory"]
    t = np.asarray(traj["ref_time"], dtype=float)
    ref = np.asarray(traj["ref_values"], dtype=float)
    act_t = np.asarray(traj["act_time"], dtype=float)
    act = np.asarray(traj["act_values"], dtype=float)
    vc = _compare_tube(t, ref, act_t, act, {"tube_width_mode": "band", "tube_abs": 0.05})
    vc.name = var
    stamp_leaf_path(leaf_from_variable(vc), "/metrics/children/0")
    fig = variable_figure(build_view(var, act_time=act_t, act_values=act, ref_time=t, ref_values=ref, leaves=[("/metrics/children/0", vc)]))
    ctx["figures"] = {var: fig}
    ctx["diag_figures"] = []
    ctx["nb_figures"] = []
    return ctx, fig


def test_first_paint_draws_exactly_the_figure_dict(tmp_path, playwright_browser):
    ctx, fig = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    painted = page.evaluate("() => { const el = document.getElementById('plot-0'); return { n: el.data.length, python: !!el._mt_python, ids: el.data.map(t => (t.meta || {}).id || null) }; }")
    page.close()
    assert painted["python"] is True
    assert painted["n"] == len(fig["data"])
    assert "/metrics/children/0/tube/band" in painted["ids"]


def test_error_overlay_toggles_the_embedded_error_trace(tmp_path, playwright_browser):
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    visible = page.evaluate("""async () => {
        setErrorOverlay(0, 'abs');
        await new Promise(r => setTimeout(r, 100));   // Plotly.restyle settles on the next tick
        const el = document.getElementById('plot-0');
        return el.data.filter(t => (t.meta || {}).kind === 'error').map(t => [t.meta.id, t.visible]);
    }""")
    page.close()
    assert dict(visible) == {"error/signed": "legendonly", "error/abs": True, "error/nrmse": "legendonly"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_interactive_figures.py -v`
Expected: FAIL (`painted["python"] is False`) — or SKIP if Playwright is not installed; in that case the assertions run in Task 12's gate on a machine that has it, and the implementer notes it as unrun.

- [ ] **Step 3: Expose the figures to the script**

In `src/dstf/reporting/templates/scorers.js`, after `const NB_TRAJECTORIES = MT_REPORT.NB_TRAJECTORIES || [];` add:

```js
// Figure dicts built by dstf.figures (Python). The first paint of every
// plot comes from these; the JavaScript contributions below take over only
// once the user edits a leaf, and go away entirely in the two-tier split.
const FIGURES = MT_REPORT.FIGURES || {};
const DIAG_FIGURES = MT_REPORT.DIAG_FIGURES || [];
const NB_FIGURES = MT_REPORT.NB_FIGURES || [];
```

- [ ] **Step 4: Paint from the dict on first render**

In `src/dstf/reporting/templates/plots.js`, at the top of `renderVariablePlot` right after `if (!el) return;` (before `const actTime = ...`), insert:

```js
  // First paint: the Python figure layer's dict, verbatim. Every later
  // call comes from an edit and re-renders through the JS contributions
  // below (Plotly.react), which is unchanged behaviour until the two-tier
  // split deletes that path.
  const fig = FIGURES[varname];
  if (fig && !el._mt_plotted) {
    Plotly.newPlot(el, fig.data, fig.layout, PLOT_CFG);
    el._mt_plotted = true;
    el._mt_python = true;
    return;
  }
  el._mt_python = false;
```

- [ ] **Step 5: Diagnostic and no-baseline plots, and the error toggle**

In `src/dstf/reporting/templates/export.js`, at the top of `setErrorOverlay` after `if (!el || !el.data) return;`, insert:

```js
  // Python-painted plot: the error series are already traces on the right
  // axis, hidden. Toggle visibility instead of recomputing anything.
  if (el._mt_python) {
    const idx = [];
    const vis = [];
    el.data.forEach((tr, i) => {
      if (!tr.meta || tr.meta.kind !== 'error') return;
      idx.push(i);
      vis.push(mode !== 'none' && tr.meta.id === `error/${mode}` ? true : 'legendonly');
    });
    if (idx.length) Plotly.restyle(el, { visible: vis }, idx);
    return;
  }
```

In `renderDiagPlots`, right after `if (!el) return;` insert:

```js
    const fig = DIAG_FIGURES[idx];
    if (fig) { Plotly.newPlot(el, fig.data, fig.layout, PLOT_CFG); return; }
```

In `renderNBPlots`, right after `if (!el) return;` insert:

```js
    const fig = NB_FIGURES[idx];
    if (fig) { Plotly.newPlot(el, fig.data, fig.layout, PLOT_CFG); return; }
```

- [ ] **Step 6: Re-bless the golden snapshots and run every browser test**

```bash
UPDATE_GOLDEN=1 uv run pytest tests/test_interactive_html_snapshot.py -q
uv run pytest -m playwright -q
```

Expected: the snapshot test rewrites `tests/golden/interactive_*.hash`; every Playwright test passes, including the existing editor tests (an edit still re-renders through the JS path). If an existing editor test fails because it inspects `el.data` immediately after load and now sees the Python trace set, the fix is in the TEST's expectation, not in the JS: the first paint is now the figure dict by design.

- [ ] **Step 7: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dstf/reporting/templates tests/golden tests/test_interactive_figures.py
git commit -m "feat: the report's first paint is the Python figure dict; edits keep the JS path until the split"
```

---

### Task 12: Remove the dead matplotlib dependency

**Files:**
- Modify: `pyproject.toml` (line 8), `uv.lock`, `src/dstf/cli/review.py` (~line 292)

- [ ] **Step 1: Confirm nothing imports it**

Run: `grep -rn "matplotlib\|pyplot" src/ tests/ examples/ scripts/`
Expected: only `src/dstf/cli/review.py:292` (an error string). If anything else appears, stop and report it.

- [ ] **Step 2: Remove the dependency and the string**

Delete the line `    "matplotlib>=3.8",` from `[project] dependencies` in `pyproject.toml`. In `src/dstf/cli/review.py`, change `print("  Plot generation failed (matplotlib not installed?)")` to `print("  Plot generation failed")`.

```bash
uv lock
uv sync --extra dev --locked
```

- [ ] **Step 3: Run the suite**

Run: `uv run pytest -m "not playwright" -q`
Expected: all pass

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add pyproject.toml uv.lock src/dstf/cli/review.py
git commit -m "chore: drop matplotlib — nothing has imported it since the PNG plots were retired"
```

---

### Task 13: Sub-project gate

- [ ] **Step 1: Full gates**

```bash
uv run pytest -q
uv run ruff check && uv run ruff format --check && uv run mypy
uv run pytest --cov -q 2>&1 | tail -5     # the coverage floor CI enforces
```

Expected: all pass; coverage at or above the floor in `pyproject.toml`.

- [ ] **Step 2: Real-library gate (rescoring only, no simulator)**

Using the TRANSFORM setup the memory file `transform-verification-setup.md` describes:

```bash
uv run dstf --config <TRANSFORM-UnitTests>/ReferenceResults/testing.json compare --report
```

Expected: every test renders; the pass/fail counts equal the last recorded run (331 verdicts unchanged); the per-test pages open and show a tube band on tube tests. Record the counts in the handoff. If the work directory with last results is absent, name this gate as unrun.

- [ ] **Step 3: End-state predicates (the done-check for the whole sub-project)**

Each of these must hold; report any that does not:

1. Every registered metric emits geometry on its scored path: `uv run pytest tests/test_geometry.py -q` passes and covers tube, points, range, scalar, event-timing, dominant-frequency (nrmse emits none by design).
2. `grep -rn "_leaf_score_display" src tests` returns nothing; `grep -rn "comparisons_by_path = dict(zip" src` returns nothing.
3. `grep -rn "matplotlib" src pyproject.toml` returns nothing.
4. `src/dstf/figures/plotly.py` dispatches on primitive type only: `grep -n "\.metric\b\|\.mode\b\|mode ==\|metric ==" src/dstf/figures/plotly.py` returns nothing. (Family labels such as `tube/band` are id prefixes used for legend text, not dispatch.)
5. The report page's first paint is the figure dict: `tests/test_interactive_figures.py` passes where Playwright is installed.
6. `comparison_data.json` carries no figure: asserted by `TestEmbeddedFigures`.

- [ ] **Step 4: Plan-versus-spec check**

Read spec §4.2, §4.3 and §5 step 1 once more against the diff (`git diff a91275f..HEAD --stat`). Report: anything the spec asked for in step 1 that is missing; anything built that the spec did not ask for. The known, deliberate deviation is the one named under "Conventions" (`_extract_mode_values` and the spectrum gate survive until sub-project 2).

- [ ] **Step 5: Handoff note**

Write into the handoff what was run and what was not (the TRANSFORM gate if unrun; Playwright if not installed), the measured page size from the budget test, and the golden re-bless. Nothing is pushed.
