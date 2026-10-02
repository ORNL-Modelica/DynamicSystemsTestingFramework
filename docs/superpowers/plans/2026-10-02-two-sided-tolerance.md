# Two-Sided Tolerance (step E1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tolerance a criterion carries can be a number (both sides equal, as today) or a pair `{"lower": a, "upper": b}`: the scorers apply the two sides separately, the plot draws them, the report says them, the editor shows two fields with a link toggle, and each side has a handle on the plot.

**Architecture:** One value type, `Sides(lower, upper)`, parsed in ONE place (`comparison/sides.py`) from the number-or-pair a spec holds; every scorer takes `Sides` and a number is `Sides(v, v)`, so an existing spec scores bit-for-bit as before. The shape in the file IS the link state (a number is linked, a pair is unlinked) — the tool keeps no hidden flag. The form layer learns one new field kind, `sides`, which the leaf form, the row tables and the JSON-Schema export all render from; the handles are new geometry of a new kind, `grip`, drawn only for the open criterion, so the report's figures do not change except where a tolerance was never drawn (scalar).

**Tech Stack:** Python 3.10+, numpy, NiceGUI 3.16 (`[author]`), plotly.js 3.1.1 (bundled), pytest + pytest-asyncio, Playwright + Chromium, uv.

**This is step E's first half.** E2 (live drag, arm a field then click, Ctrl+click rows that settle on the data, snapping — rulings Q3 A, Q4 A, Q5 A and round 1's Q4 C) is planned against the tree this plan leaves. Nothing of E2 is built here: a handle still commits on release, and the values a drag writes keep today's 12 significant digits.

**Owner rulings (board https://claude.ai/artifact/L4NcKEpLtShKCEs5DSuDz6, 2026-10-02; round 1's board https://claude.ai/artifact/88WhEgGHtSjT3pkiJTJ4Sm):**
- **Round 1, Q5 A** — two fields with a link toggle; linked, typing or dragging one moves both; unlinked, each side is set on its own; the plot draws an upper and a lower handle. nrmse is one ceiling and keeps one tolerance.
- **Q1 B** — `tolerance` is a number or a pair `{"lower": …, "upper": …}`: one key, no second pair of keys. Chosen over the controller's recommendation (two extra keys): "now is the time to do it right", and a pair object can grow.
- **Q2 B** — every tolerance gets two sides, a point's time tolerance included. The minimum tube width (a floor) and nrmse stay one number. Said with less certainty than the others ("I think B"); reversible until Task 3 starts.
- **Q6 A** — a scalar scored against its baseline draws what it allows, with a handle per side.

**Controller's calls (said to the owner; reversible):**
- (C1) **Which fields take a pair:** a leaf's `tolerance` on `points` and `scalar`; a `points` row's `tolerance` and `time_tolerance`; `event-timing`'s `time_tolerance` and an `events` row's `tolerance`; a `peaks` row's `tolerance`; `tube_abs` and `tube_rel`. The same fields in `comparison.variable_overrides`. NOT the base tolerances — `testing.json`'s `tolerance`, `--tolerance`, `comparison.tolerance` in a spec entry and in a reference JSON stay one number (the board said so under Q1). A pair on `nrmse` is that leaf's config error: "nrmse has one ceiling: tolerance must be a number".
- (C2) **What the sides mean.** `d = actual − target`; the check passes iff `−lower ≤ d ≤ upper`. In a `rel` mode each side is a fraction of `|target|`. In time, `lower` is how much EARLIER and `upper` how much LATER: a point's time box is `[t − lower, t + upper]`, an event's window likewise. A peak's window is `[f·(1 − lower), f·(1 + upper)]` (rel) or `[f − lower, f + upper]` (abs). A constant tube's edges are `ref + upper` and `ref − lower` (`band`), or those fractions of `|ref|` (`rel`).
- (C3) **Inclusive or strict stays as it is per check** (declared points, scalar, events, peaks inclusive; the implicit final-value check strict), applied to both sides.
- (C4) **No sign check on a side in the scorer** — a negative number is not refused today and is not refused in a pair. The editor's handles refuse to write one (a side dragged across its target).
- (C5) **Re-linking two different sides takes the LARGER for both**, so a re-link never turns a passing criterion into a failing one. Unlinking writes a pair holding the number twice. A pair with equal sides stays a pair: only the toggle changes the shape.
- (C6) **`VariableComparison.tolerance_used` becomes `Sides`**; wherever it leaves Python as data (the tree view's `tolerance`, a leaf's diagnostics) it is written as a number when the sides are equal and as the pair otherwise. In sentences: `1.000e-02` when equal, `−1.000e-02 / +2.000e-02` otherwise.
- (C7) **A new geometry kind, `grip`:** the resolved position of one SIDE of a tolerance. A grip is drawn only as a draggable ring or line for the open criterion in the tool — never as a marker trace, never in the report. Without it, four extra dots per checkpoint would appear on every report page.
- (C8) **The scalar's drawing (Q6 A) is two dashed lines**, `baseline + upper` and `baseline − lower`, drawn and dragged as the bounds form's lines are — no fill (the scorer of a statistics leaf has no time span to fill). They are `kind="handle"`, so the report draws them too: the one deliberate change to a report figure.
- (C9) **Side labels:** value and frequency tolerances read `−` and `+`; a point's time tolerance reads `before` and `after`; an event's reads `early` and `late`.
- (C10) **Out of E1:** an event's own time has no drag handle (it has none today); `count_must_match` unchanged; the seeds keep writing a number.

**Evidence (read at e816762; a read-only code map and my own reads):**
- Scorers, all symmetric today (`comparison/algorithms.py`): points final `delta < tolerance` (:361) with its box `ref_final ± tolerance` (:384-391); declared points `y_limit = per_tol * abs(target) if mode == "rel" else per_tol` (:443-446), time box `t ± x_tol` (:447-456), `point_passed = delta <= y_limit` (:476), box (:486-496), `_min_delta_in_box` (:251-315); tube constant `raw_lower = raw_upper.copy()` (:596-600); scalar `err = abs(act − ref)`, `score <= tolerance` (:1078-1093), geometry `_scalar_geometry` (:964-986) draws no tolerance; events auto `abs(ref − act) <= time_tolerance` (:1187-1193, :1219), declared window `(target − tol, target + tol)` (:1245-1251), `_event_geometry` (:1116-1145); peaks window (:1663-1669) and AGAIN in `_frequency_geometry` (:1469-1474).
- Builders (`comparison/builtins.py:64-122`): nrmse, points, scalar take the RESOLVED tolerance argument; event-timing reads `override.get("time_tolerance", 1e-3)`; tube lifts `_TUBE_KEYS`.
- Resolution: `_leaf_tolerance` (`tree_eval.py:618-626`) is `float(leaf.params.get("tolerance", base))`, a non-number being the leaf's config error (D107), `vc.tolerance_used = tolerance` (:331); flat path `var_override.get("tolerance", base_tolerance)` (`comparator.py:533`), `tolerance_used` set at :587, :610, :629, :643; **`float(leaf.params.get("tolerance", …))` at `comparator.py:706` and `float(override.get("tolerance", …))` at :742 sit OUTSIDE their `try` — a pair there raises `TypeError` and takes the run down.** `implicit_and_tree` stamps `float(base_tolerance)` (`tree_spec.py:432-433`). `_parse_leaf` keeps unknown params (`tree_spec.py:626-631`); `validator.py` reads no param.
- Readers of the number: `summaries.py:49, :137, :178, :193`; `metric_tree.py:290` (`"tolerance": vc.tolerance_used`) → `tree_eval.to_view` (:260-261); `authoring/shell/stats_card.py:125-128` (`summary`); `authoring/builtin/edit_tree.py:630` (`_requirement`, reads a SEED's tolerance — a number); `reporting/context_builder.py:82-104` (`_in_effect`, the BASE tolerance — a number).
- Schema: `FIELD_TYPES` (`mode_schema.py:32`), `_classify_type` (:107-138), `FieldSpec` (:35-48); row `columns` metadata is read straight off the dataclass by `edit_on_plot._list_fields` (:57-68) with kinds `"float"`, `"float?"`, a tuple of choices; `schema_export._field_to_schema` (:29-56), list fields export as `{}`.
- Forms: `forms.render_form` / `_control` (`authoring/forms.py:73-213`), one `ui.number` per float, the in-effect hint shown while the field is empty (:117-135, :151-161), `Form.values()` omits `None`. Tables: `edit_on_plot._table` (:408-516), `_act_rows` (:365-405), `_new_row` (:169-195).
- Handles: `_act_handle` drops everything but `Marker` / `HLine` of `kind="handle"` (`edit_on_plot.py:315`); `_position` (:124-142); editors in `comparison/handles.py` (tube :226, bounds :370, points :404, peaks :448), every written number through `_written` (:75-80); `event-timing` has no `edit_handle` (`builtins.py:181-189`).
- Figures (`figures/plotly.py`): `_box` (:278-303) and `_band` (:252-275) already take independent edges; `_hline` (:306-352) and `_vline` (:355-374) are line shapes; `_marker_traces` (:438-470) draws EVERY marker of a leaf as a trace; `_handle_rings` (:477-502) rings those of `kind == "handle"`, only when `editable` and the leaf is in `rings_for` (:813-817); a hidden leaf's shapes stay in the list as `visible: false` because a drag names a shape by INDEX (:823-828). Goldens: `tests/golden/figure_*.json`, one per mode.
- A browser spike on the tool's own figure (NiceGUI 3.16, plotly.js 3.1.1, 2026-10-02): a dragged LINE shape's release is one `plotly_relayout` with `shapes[i].x0, x1, y0, y1`; a ring's is `xanchor`, `yanchor`. A vertical line with `yref: paper` dragged sideways was NOT spiked — Task 6 opens with it.

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file in your own scratchpad subdir> -- <explicit paths>`; before each commit run its tests and CONFIRM they were collected and passed, then `ruff format --check`; never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>`, never switch branches. Never push. No `.soul/` record id in a tracked file.
- FOREGROUND commands with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (`tests/test_test_page.py` whole is ~520 s — run it in halves; Playwright by test id); `uv run --no-sync`; kill only PIDs you started, never `pkill -f`; never port 8765; screenshots under the scratchpad; never `dstf run` / `compare` / `author` against a real work dir.
- Test-first; named mutants applied after the commit and restored (cp + cmp; `PYTHONDONTWRITEBYTECODE=1`).
- A UI change: a real browser, thumbnails READ (`~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb`); measure layout claims (boundingBox / computed style). A design choice not written here: stop and report.
- Gates before each commit: its tests, ruff check, ruff format --check; before a task's last commit also bare mypy, the whole not-playwright suite in chunks, the Playwright files it touched, `tests/test_capability_conformance.py`.
- **The identity rule of this plan:** for every mode and every tolerance field, a spec holding the number `v` and one holding `{"lower": v, "upper": v}` produce the same verdict, the same score fields and the same geometry coordinates; and a spec holding only numbers produces what the commit before this plan produced. Each scoring task carries both tests.

## File map

| File | Change |
|---|---|
| `src/dstf/comparison/sides.py` (new) | T1: `Sides`, `parse_sides`, `as_written`, `sides_text`. The ONE place a number-or-pair is read or written. |
| `src/dstf/comparison/types.py`, `tree_eval.py`, `comparator.py`, `tree_spec.py`, `builtins.py`, `modes.py`, `metric_tree.py` | T2: `Sides` from the spec to the mode (a refactor: every scorer still symmetric). |
| `src/dstf/comparison/algorithms.py`, `summaries.py` | T3 points; T4 scalar and the constant tube; T5 events and peaks. |
| `src/dstf/comparison/geometry.py`, `algorithms.py`, `src/dstf/figures/plotly.py`, `tests/golden/figure_scalar.json` | T6: the `grip` kind, the grips each mode emits, the scalar's two lines. |
| `src/dstf/comparison/mode_schema.py`, `modes.py`, `src/dstf/reporting/schema_export.py` | T7: the `sides` field kind and column kind; the exported schema. |
| `src/dstf/authoring/forms.py`, `shell/style.py`, `builtin/edit_criteria.py` | T8: the two fields and the link toggle on a leaf's form. |
| `src/dstf/authoring/builtin/edit_on_plot.py` | T9: the same control in a table row. T10: grips reach their editors. |
| `src/dstf/comparison/handles.py`, `builtins.py` | T10: a dragged grip back to the leaf. |
| `src/dstf/authoring/shell/stats_card.py`, `src/dstf/reporting/context_builder.py` | T8: the Statistics row's summary and the in-effect entry read a pair. |
| `docs/usage.md`, `docs/decisions.md` (D147), `CLAUDE.md`, the roadmap | T11. |

---

## Part A — scoring (no UI change)

### Task 1: One value, read and written in one place

**Files:** create `src/dstf/comparison/sides.py`; test `tests/test_sides.py` (new).

```python
@dataclass(frozen=True)
class Sides:
    lower: float
    upper: float

    @property
    def equal(self) -> bool: ...          # lower == upper

def parse_sides(value: object, what: str) -> Sides: ...
def as_written(sides: Sides) -> float | dict: ...
def sides_text(sides: Sides, fmt: str = ".3e") -> str: ...
```

- `parse_sides`: an `int` / `float` that is not a `bool` and is finite → `Sides(v, v)`; a `dict` whose keys are EXACTLY `lower` and `upper`, both such numbers → `Sides(lower, upper)`; a `Sides` → itself. Anything else raises `ValueError(f"{what} must be a number or {{\"lower\": …, \"upper\": …}}, got {value!r}")` — a missing side, an extra key, a string, `None`, a bool, `nan` / `inf` each named by that one sentence. No sign check (C4).
- `as_written`: the number when `equal`, else `{"lower": …, "upper": …}` — for data leaving Python (C6). It is NOT how the tool writes a spec: there the shape is the user's (C5).
- `sides_text`: `format(lower, fmt)` when equal; else `f"−{lower:{fmt}} / +{upper:{fmt}}"` (U+2212 minus, as the report's other signs).

- [ ] Tests first (each shape accepted; each refusal's message names `what` and the value; `as_written` round-trips through `parse_sides`; `sides_text` both forms). Run red, implement, run green. Mutants: accept a dict with an extra key; treat `True` as 1; `as_written` returns the pair when equal. Commit "comparison: Sides — a tolerance is a number or a lower/upper pair".

### Task 2: `Sides` from the spec to the mode (refactor; nothing scores differently)

**Files:** `comparison/types.py`, `tree_eval.py`, `comparator.py`, `tree_spec.py`, `builtins.py`, `modes.py`, `metric_tree.py`, `summaries.py`; tests `tests/test_tree_eval.py`, `tests/test_comparator.py`, `tests/test_summaries.py`, `tests/test_metric_registry.py`.

A refactor in its own commit (the stage block): after it every tolerance travels as `Sides`, every scorer still reads only `.upper` where it read the number, and a pair with UNEQUAL sides is refused as the leaf's config error "two-sided tolerance is not scored yet" by ONE guard in `resolve_mode` that Tasks 3-5 narrow mode by mode and Task 5 deletes. So no commit of this plan ever scores a pair wrongly.

- `MetricDescriptor.build` becomes `Callable[[dict, Sides], ComparisonMode]`; `resolve_mode(var_override, tolerance: Sides, …)`.
- `_leaf_tolerance(leaf, base) -> Sides`: `parse_sides(leaf.params.get("tolerance", base), "tolerance")`; its `ValueError` is the leaf's config error as today, and the fallback reported is `Sides(base, base)`.
- Flat path (`comparator.py:533`): `parse_sides(var_override.get("tolerance", base_tolerance), "tolerance")` INSIDE the `try` that already turns `resolve_mode`'s `ValueError` into the variable's config error.
- `comparator.py:706` and `:742`: `parse_sides(...)` inside their `try` (a malformed tolerance means "not baseline-free", as an unresolvable mode does) — today a non-number there is an uncaught `ValueError`/`TypeError`.
- `tree_spec.py:432-433` keeps stamping the base as a `float` (a base is a number, C1).
- `NrmseConfig.tolerance` stays `float`: `_build_nrmse` refuses unequal sides with C1's sentence and passes `.upper`. `PointsConfig.tolerance` and `ScalarConfig.tolerance` become `Sides` (default `Sides(1e-4, 1e-4)`); `EventTimingConfig.time_tolerance` becomes `Sides` (default `Sides(1e-3, 1e-3)`) parsed in `_build_event_timing`; `TubeConfig.tube_abs` / `tube_rel` become `Sides` (default `Sides(0.0, 0.0)`), parsed in `_build_tube`, and `to_dict` keeps omitting a zero one.
- `VariableComparison.tolerance_used: Sides = Sides(0.0, 0.0)`; `metric_tree.py:290` writes `as_written(vc.tolerance_used)`; `summaries.py:49, :137, :178` use `sides_text(vc.tolerance_used)` — identical text for equal sides.
- `derive_schema` must keep working over the changed dataclasses until Task 7: in THIS task `_classify_type` returns `"float"` for a `Sides` hint and `_field_default` returns `.upper` for a `Sides` default, each with a comment naming Task 7 — the forms and the exported schema are unchanged by this commit, and a test pins that (`derive_schema(PointsConfig)` and `export-schema`'s output equal to the pre-task ones).

- [ ] Tests: a leaf tolerance as a number, as an equal pair (same `VariableComparison` fields), as each malformed shape (the leaf's config error, the run goes on, the rest of the tree is scored); an unequal pair → the "not scored yet" config error on every mode but nrmse, C1's sentence on nrmse; the flat path the same through `variable_overrides`; `_test_is_baseline_free`'s two sites given a pair and a string return without raising; the tree view's `tolerance` for an equal pair is the number. The whole existing suite green unchanged is this task's main check. Mutants: `_leaf_tolerance` reads `.lower` of the base; the `:706` parse left outside its `try`; `as_written` dropped at `metric_tree.py:290`. Commit "comparison: a tolerance travels as Sides (refactor; scoring unchanged)".

### Task 3: Points

**Files:** `comparison/algorithms.py` (`_compare_points`, a new `_violation_in_box`), `comparison/summaries.py`, `comparison/modes.py`, `builtins.py`; tests `tests/test_comparator.py`, `tests/test_summaries.py`, `tests/test_geometry.py`.

- Final-value check: `d = act_final − ref_final`; `passed = −tol.lower < d < tol.upper` (strict, C3); the box is `y0 = ref_final − tol.lower`, `y1 = ref_final + tol.upper`. `delta = abs(d)` and every reported field as today.
- Declared points: the row's `tolerance` is `parse_sides(row["tolerance"], f"points[{i}].tolerance")` when present and not `None`, else the leaf's; `time_tolerance` likewise (`f"points[{i}].time_tolerance"`), absent or `None` being `Sides(0, 0)`. A malformed one raises `ValueError` out of `_compare_points` — CHECK how a `ValueError` from `mode.compare` reaches the leaf today (`tree_eval._evaluate_leaf`); if it is not already the leaf's config error, parse the rows in `PointsMode.__init__` instead, where `resolve_mode`'s callers already catch it. Say which in the commit message.
- `lo, hi = target − lower_limit, target + upper_limit`, each limit the side times `abs(target)` in `rel`. Time box `[t − time.lower, t + time.upper]`, clipped to the actual span as today.
- `point_passed = _violation_in_box(act_time, act_values, t_lo, t_hi, lo, hi) == 0.0`, where the new function returns how far the piecewise-linear actual stays outside `[lo, hi]` over the box — `0.0` when any candidate (the interpolated ends and the samples inside, NaN dropped, as `_min_delta_in_box` builds them) lies in `[lo, hi]` or two adjacent candidates lie on opposite sides of it; else the smallest distance to the band; `inf` for an empty box. `_min_delta_in_box` stays and still supplies `delta`, `worst_delta`, `worst_time` — so with equal sides every number is today's (`violation == 0 ⟺ delta <= y_limit`; a test proves it over a randomised sweep of 2,000 cases with a fixed seed).
- The failure reason: equal sides keep `delta … exceeds limit …`; unequal read `closest value {v:.6g} is outside [{lo:.6g}, {hi:.6g}]`.
- The `Box`: `t0 = t − time.lower`, `t1 = t + time.upper`, `y0 = lo`, `y1 = hi`.
- `summarize_points`' criterion uses `sides_text`. Narrow Task 2's guard: points now scores a pair.

- [ ] Tests: the identity rule (number vs equal pair; numbers-only vs the pre-plan output, from a fixture recorded at Task 2's commit); a point 0.03 above its target passes with `{"lower": 0.01, "upper": 0.05}` and fails with the sides swapped; the same in `rel` with a negative target (the side scales by `|target|`); a curve that is above the band at one sample and below it at the next passes (it crossed); a box asymmetric in time catches a late crossing and not an early one; the final-value check strict on each side; the box's four coordinates; a row pair with a missing side is the leaf's config error naming `points[1].tolerance`. Mutants: sides swapped; `rel` scaling applied to one side only; the straddle check dropped from `_violation_in_box`; the time box built from `.upper` on both sides; `<=` for the final check. Commit "points: lower and upper tolerance, in value and in time".

### Task 4: Scalar and the constant tube

**Files:** `comparison/algorithms.py` (`_compare_scalar`, `_tube_bounds`), `summaries.py`, `authoring/shell/stats_card.py` is NOT touched here (Task 8); tests `tests/test_scalar_metric.py`, `tests/test_comparator.py`, `tests/test_tube_union_grid.py`.

- Scalar, baseline form: `d = act − ref`; `rel` (and `|ref| != 0`) scores `d / |ref|`; `passed = −tol.lower <= signed <= tol.upper`. `nrmse` stays `abs(signed)`, `rmse` and `max_abs_error` stay `abs(d)` — the score a `weighted` combinator reads does not change meaning. The bounds form is untouched.
- `summarize_scalar`: `sides_text`.
- Tube: `_tube_bounds` reads `tube_abs` / `tube_rel` through `parse_sides` (the dict `TubeConfig.to_dict` hands it holds `Sides`): `raw_upper = full(.upper)`, `raw_lower = full(.lower)`. `tube_min_width` floors both, as today. Control points untouched.
- Narrow the guard for scalar and tube.

- [ ] Tests: the identity rule for both; a scalar 4 % under its baseline passes `{"lower": 0.05, "upper": 0.01}` rel and fails swapped; the `|ref| == 0` fallback with a pair; a constant band `{"lower": 0.1, "upper": 0.5}` holds a curve 0.3 above and fails one 0.3 below, on the union grid AND the reference grid (the two-grid rule of `_compare_tube`); `rel` likewise; the `Band`'s `upper` / `lower` arrays. Mutants: sides swapped (each mode); the signed score replaced by its magnitude in the pass test; `raw_lower` copied from `raw_upper`. Commit "scalar, tube: lower and upper tolerance".

### Task 5: Events and peaks; the guard goes

**Files:** `comparison/algorithms.py` (`_compare_event_timing`, `_event_geometry`, `_compare_dominant_frequency`, `_frequency_geometry`), `summaries.py`, `modes.py`; tests `tests/test_event_and_freq_modes.py`, `tests/test_summaries.py`.

- Events, auto-detected: `d = act_events[i] − ref_events[i]`; a pair passes iff `−tol.lower <= d <= tol.upper`; `max_delta` stays the largest `abs(d)`. Declared: the row's `tolerance` parsed as `f"events[{k}].tolerance"`, else the leaf's `time_tolerance`; the window `(target − lower, target + upper)`; the interval matching (sorted by right endpoint) unchanged. `_event_geometry` takes a `Sides` per event. The diagnostics' `time_tolerance` is `as_written(...)`; `summarize_event_timing` prints it with `sides_text`.
- Peaks: ONE helper `_peak_window(declared) -> (lo, hi, Sides, mode)` used by the scorer AND `_frequency_geometry` (the window is computed twice today); `rel`: `f·(1 − lower)`, `f·(1 + upper)`; `abs`: `f − lower`, `f + upper`; a missing `tolerance` is `Sides(0.01, 0.01)` as today. `paired[...]["tolerance"]` is `as_written(...)`.
- Delete Task 2's guard and its sentence; a test that greps for the sentence finding nothing is NOT the check — the check is that an unequal pair on each of the six modes scores.

- [ ] Tests: the identity rule for both modes, both event paths; an event 2 ms late passes `{"lower": 0, "upper": 0.005}` and fails swapped, on both paths; the window box's `t0` / `t1`; two declared events with overlapping uneven windows still each claim their own actual event; a peak 3 % high passes rel `{"lower": 0.01, "upper": 0.05}` and fails swapped; abs likewise; the scorer's window and the drawn `peaks/window/i` box are equal for uneven sides (one helper). Mutants: sides swapped (each path); the geometry helper left on the old symmetric arithmetic; early and late exchanged in the auto path. Commit "event-timing, dominant-frequency: lower and upper tolerance".

### Task 6: Grips, and the scalar's two lines

**Files:** `comparison/geometry.py`, `comparison/algorithms.py`, `figures/plotly.py`, `tests/golden/figure_scalar.json`; tests `tests/test_geometry.py`, `tests/test_figures.py`, `tests/test_plot_figure.py`, `tests/test_figure_goldens.py`.

Open with a SPIKE, in the scratchpad, on a figure `variable_figure(..., editable=True)` really emits: a vertical line shape with `yref: "paper"`, dragged sideways with a real mouse in Chromium — the `plotly_relayout` keys and values (expected `shapes[i].x0` / `x1`), and whether a diagonal drag changes its paper `y0` / `y1`. If the release does not carry the x, STOP and report: the window grips below then need another shape.

- `geometry.py`: `"grip"` joins the `kind` vocabulary in the module docstring — "the resolved position of one side of a tolerance; drawn only for the open criterion".
- Emitted, all `kind="grip"`:
  - points — `Marker` `points/upper/{i}` at `(t, hi)`, `points/lower/{i}` at `(t, lo)`; `points/before/{i}` at `(t − time.lower, target)` and `points/after/{i}` at `(t + time.upper, target)` ONLY when the row holds a `time_tolerance` (else two rings would sit on the target's); the final check: `points/upper/final`, `points/lower/final`.
  - tube, constant width only — `Marker` `tube/upper/const` and `tube/lower/const` on the band's edges at the eval grid's middle sample.
  - events — `VLine` `events/early/{i}` at `t − lower`, `events/late/{i}` at `t + upper`.
  - peaks — `VLine` (spectrum panel) `peaks/lower/{i}` at `lo`, `peaks/upper/{i}` at `hi`.
- Scalar, baseline form — `HLine` `scalar/upper` at `ref + upper_limit` and `scalar/lower` at `ref − lower_limit`, `kind="handle"` (C8), the limit being the side times `|ref|` in `rel` (the plain side under the `|ref| == 0` fallback).
- `figures/plotly.py`: a grip never becomes a trace (`_marker_traces` is not given them) and a grip `VLine` never goes through `TRACE_BUILDERS`; both are collected with the leaf's rings, so they exist only when `editable` and the leaf is in `rings_for`, and take `visible: false` with a hidden leaf exactly as rings do. A grip ring is `_handle_rings`' shape; a grip line is `_vline`'s shape in the ring colour (`HANDLE_RING`), `name` its id.
- The report's figures: byte-identical goldens for nrmse, tube, points, range, event-timing, dominant-frequency; `figure_scalar.json` regenerated, its diff being exactly the two lines and their two anchor traces.

- [ ] Tests: each mode's grips at the coordinates above, for equal and unequal sides; none emitted where the rule says none (a point without `time_tolerance`; a tube with control points); a non-editable figure holds no shape and no trace named by a grip id; an editable one holds them only for the leaf in `rings_for`; six goldens unchanged, the scalar's diff as stated. Mutants: grips given `kind="handle"` (the report then draws them — the golden test must fail); the `rings_for` filter skipped for grip lines; upper and lower grips exchanged. Commit "figures: a grip per tolerance side for the open criterion; a scalar draws what it allows".

---

## Part B — the tool

### Task 7: The `sides` field kind and the exported schema

**Files:** `comparison/mode_schema.py`, `comparison/modes.py`, `reporting/schema_export.py`; tests `tests/test_mode_schema.py`, `tests/test_export_schema.py`.

- `FIELD_TYPES` gains `"sides"`; `_classify_type(Sides)` → `("sides", False, None)` and Task 2's stand-in goes; `FieldSpec.default` for a `Sides` default is `as_written(default)`; `FieldSpec` gains `side_labels: tuple[str, str] | None` from the field's metadata `"side_labels"` (default `("−", "+")`; `EventTimingConfig.time_tolerance`: `("early", "late")`).
- Row `columns`: the kinds `"sides"` and `"sides?"` join `"float"`, `"float?"` and the choices tuple; an entry may carry a third element, its side labels. `points`: `("tolerance", "sides?")`, `("time_tolerance", "sides?", ("before", "after"))`; `events`: `("tolerance", "sides?", ("early", "late"))`; `peaks`: `("tolerance", "sides?")`. Every reader of `columns` unpacks by index 0 and 1 (`edit_on_plot.py` unpacks `for key, kind in columns` in five places — fix them all in THIS task so the tables keep working, rendering a `sides` column as today's single number cell until Task 9).
- `schema_export._field_to_schema`: `"sides"` → `{"oneOf": [{"type": "number"}, {"type": "object", "properties": {"lower": {"type": "number"}, "upper": {"type": "number"}}, "required": ["lower", "upper"], "additionalProperties": false}]}` with the title, description and default as other kinds.

- [ ] Tests: the classification; the default; the labels; the exported `$defs` for points, scalar, event-timing, tube validate a number and a pair and refuse a pair with one side (run `jsonschema` if the dev extra has it — check `uv.lock`; otherwise assert the dict); nrmse's `tolerance` still `{"type": "number"}`; the row tables of every list field still render and submit (the existing `tests/test_edit_on_plot.py` green). Mutants: `additionalProperties` true; `required` holding one side; nrmse's field classified `sides`. Commit "schema: a sides field — a number or a lower/upper pair".

### Task 8: Two fields and a link toggle on a criterion's form

**Files:** `authoring/forms.py`, `authoring/shell/style.py`, `authoring/builtin/edit_criteria.py` (only if its row layout needs it), `authoring/shell/stats_card.py`, `reporting/context_builder.py`; tests `tests/test_forms.py`, `tests/test_in_effect.py`, `tests/test_builtin_capabilities.py`, `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

One function, `forms.sides_control(label, value, on_change, *, side_labels, hint=None, mark=None) -> ui.element`, used by the leaf form here and by the tables in Task 9:

- Three elements in one no-wrap group: a number field labelled `{side_labels[0]} {label}` (e.g. `− Tolerance`), an icon button, a number field `{side_labels[1]} {label}`. The button shows Material `link` when linked and `link_off` when not, `aria-pressed`, and a tooltip "Linked: both sides change together" / "Unlinked: each side is set on its own".
- `value` is what the spec holds: `None` (unset: both fields empty, linked, the in-effect `hint` under the first field while both are empty), a number (linked: both fields show it), a pair (unlinked).
- Linked, typing in either field sets both and calls `on_change(number)`; clearing either clears both and calls `on_change(None)`. Unlinked, typing calls `on_change({"lower": a, "upper": b})`; while one side is blank NOTHING is submitted and the blank field is marked "required" (the tables' blank-cell rule); both blank calls `on_change(None)` and the control is linked again.
- The toggle: linked → unlinked writes the pair holding the number twice (unset stays unset, and unlinked); unlinked → linked writes the LARGER side (C5).
- Marks: `{mark}:lower`, `{mark}:upper`, `{mark}:link`.
- `render_form`: a `sides` spec renders through it, `form.controls[name]` being the group; `BOTTOM` on both number fields so the row's input lines share one y (K4); the group's min-width follows the hint as today's single field's does.
- `stats_card.summary`: an unequal pair reads `−0.5 / +1` (abs), `−5% / +10%` (rel); equal sides and a number read as today (`±0.5`, `±10%`, `exact`).
- `context_builder._in_effect` is unchanged in content (the base is a number); its test gains a leaf holding a pair (no hint, as a leaf holding a number).

- [ ] Tests (user simulation): each transition above stages exactly the value stated and one pending edit; an unset field shows the hint and writes nothing; a leaf holding a pair opens unlinked with both values; the nrmse form still shows ONE tolerance field. Playwright: type into `−` while linked and READ the `+` field's value; unlink, type, and read the staged leaf in the preview drawer (`"tolerance": {"lower": …, "upper": …}`); the three elements' boxes sit on one line with the row's other fields' input lines at the same y (boundingBox) at 1280 and 900 px; keyboard: Tab reaches the toggle, Space flips it. LOOK at a points form and a scalar form, linked and unlinked. Mutants: re-link takes the smaller side; unlinked with one blank submits a half pair; the toggle flips the icon without writing; the hint shown while a side holds a value. Commit "editor: lower and upper tolerance with a link toggle".

### Task 9: The same control in a table row

**Files:** `authoring/builtin/edit_on_plot.py` (`_table`, `_act_rows`, `_new_row`); tests `tests/test_edit_on_plot.py`, `tests/test_author_test_page_playwright.py`.

- A `sides` / `sides?` column renders `forms.sides_control` with its column's side labels, the cell's label the column key; a `sides?` cell left empty is omitted from the row (it follows the criterion's), a `sides` one is required.
- `_act_rows`: a `sides` cell is checked with `parse_sides(value, f"row {i}: {key}")` — its `ValueError` is the refusal; the finite check covers both sides.
- `_new_row` copies the last row as today (a pair is deep-copied).
- The row must stay usable: a points row now holds time, value, three tolerance elements, the mode, three time-tolerance elements and Remove. It WRAPS inside the row rather than scroll sideways; measure at 1280 and 900 px that no element is clipped and the page's `scrollWidth == clientWidth`.

- [ ] Tests: a pair typed in a points row, an events row and a peaks row each stage one `replace` whose row holds the pair; a half pair stages nothing and marks the blank side; a refused row names it; Add row after a row holding a pair. Playwright: the layout measurements; LOOK at each of the three tables with one linked and one unlinked row. Mutants: the pair written under the column key of the neighbouring column; `_act_rows` accepting `{"lower": 1}`; the cell's side labels taken from the default for the time column. Commit "edit on plot: a table row's tolerance has two sides".

### Task 10: A dragged grip back to its side

**Files:** `comparison/handles.py`, `comparison/builtins.py`, `authoring/builtin/edit_on_plot.py` (`_position`, `_act_handle`); tests `tests/test_handles.py`, `tests/test_edit_on_plot.py`, `tests/test_author_test_page_playwright.py`.

- `_act_handle` accepts `Marker`, `HLine`, `VLine` of `kind in ("handle", "grip")`; `_position` reads a `VLine` by `x0` / `x1` (both when the line was moved, the one present otherwise; neither is no move) and returns `(x, None)`.
- One rule for every grip, in `handles.py` as `_set_side(edit, holder, key, side, value)`: `value < 0` is refused ("the {side} side would cross its target"); when `holder[key]` is absent or a number the result is the NUMBER `value` (linked: both sides move); when it is a pair only `side` changes. Written through `_written`.
- Which holder: a points row's own `tolerance` / `time_tolerance` when the ROW has that key, else the leaf's `tolerance` (the value grips; a time grip exists only on a row that has the key). An event's row `tolerance` when present, else the leaf's `time_tolerance`. A peak's row `tolerance` (written on first drag when absent). The scalar's and the final check's: the leaf's `tolerance`. The tube's: `tube_abs` (`band`) or `tube_rel` (`rel`).
- The value from the position: points `upper`: `y − target`, `lower`: `target − y`, divided by `|target|` in `rel` (refused where the target is 0), the target being the row's `value` or `ref(t)` — a leaf scored `against` another baseline is refused as `_refuse_other_baseline` does; `before`: `t − x`, `after`: `x − t`. Events: `early`: `t − x`, `late`: `x − t`, `t` the declared time or — on the auto-detected path — the `i`-th event boundary of the reference time (the same detection the scorer uses; import it, do not re-implement it). Peaks: `lower`: `1 − x/f` (rel) or `f − x` (abs), `upper`: `x/f − 1` or `x − f`. Scalar: as points with the baseline's scalar as target — the editor needs that number: read it from the leaf view's `scalar/reference` primitive, which `_act_handle` already has (`leaf.geometry`), and pass it on `HandleEdit` as a new optional field `target: float | None`. Tube: `y − ref(t)` / `ref(t) − y` at the grip's own `x`, over `|ref(t)|` in `rel`.
- `event-timing` gains an `edit_handle` (`edit_events_handle`); points, peaks, scalar and tube editors gain their grip locals. A local an editor does not name keeps raising `_unnamed`.

- [ ] Tests (`test_handles.py`, each editor): a linked number moved by one grip stays a number; a pair changes one side only; the first drag on a row without the key (peaks) and on a row that defers to the leaf (points, events); `rel` arithmetic with a negative target; each refusal (crossing, zero target in rel, another baseline, a non-finite position). `test_edit_on_plot.py`: a `VLine` payload with `x0` and `x1`; a grip of a leaf that is not open is refused as today's lines are. Playwright, real mouse, over FakeLib: drag a point's upper ring up 30 px — the `+` field of that row reads the new value, the `−` field is unchanged for a pair and equal for a number, and the box's top edge is at the ring; drag an event window's late line; drag a scalar's lower line; a drag across the target snaps back with the reason above the editor. LOOK at a points criterion with two rows open for editing: the target ring and its grips are distinguishable. Mutants: a linked number turned into a pair by a drag; upper's arithmetic used for lower; `rel` division dropped; the VLine read by `y0`; the crossing check `<=` (a zero side is allowed). Commit "edit on plot: drag a tolerance side".

### Task 11: Docs

**Files:** `docs/usage.md`, `docs/decisions.md`, `CLAUDE.md`, `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md`.

- `docs/usage.md`: the tolerance shape (a number or `{"lower", "upper"}`), C1's list of fields that take a pair and those that do not, C2's meanings with one example per metric, the editor's two fields and toggle (C5), the grips.
- `docs/decisions.md` D147 "A tolerance is a number or a lower/upper pair": the rulings, C1-C10, what was measured at the gates, what E2 still owes.
- `CLAUDE.md`: the comparison-modes list and the `test_spec.json` section say the pair in one sentence each; the authoring-tool comment says the two fields.
- The roadmap's E row: split into E1 (BUILT, this plan, D147, the commit range) and E2 (to plan).

- [ ] Read every sentence you touch for its referent (Mind: a rename is dangerous in prose). Commit "docs: D147 — two-sided tolerance".

---

## Gates (the controller's, after the last task's review)

1. **TRANSFORM over a COPY** (`/tmp/claude-1000/gate-U3` refreshed from the owner's files, read-only on the originals; the code from a `git archive`, absolute `PYTHONPATH`, `dstf.__file__` asserted): `compare --report` at e816762 and at HEAD — the same verdict for each of the 331 tests AND the same per-leaf score, tolerance text and geometry ids in every report page's embedded data (TRANSFORM holds only numbers, so this is the identity rule on real data). The owner's spec, references and both repos hash-identical before and after.
2. **What the feature did, in Chromium over that copy:** on one test, stage in memory a points criterion with an uneven pair through the editor, drag one grip, read the staged entry in the preview, see the verdict follow, Discard — pending 0. Screenshots READ.
3. Suite: not-playwright in chunks, author Playwright, conformance, ruff, format, bare mypy.

## Self-review notes

- Every ruling has a task: Q5 A round 1 → T8, T9, T10; Q1 B → T1, T7; Q2 B → T3 (value and time), T4, T5; Q6 A → T6, T10. nrmse's single ceiling → T2.
- Names used across tasks: `Sides`, `parse_sides`, `as_written`, `sides_text` (T1); `_violation_in_box` (T3); `_peak_window` (T5); kind `"grip"` (T6); field kind `"sides"`, column kinds `"sides"` / `"sides?"`, `side_labels` (T7); `forms.sides_control` (T8, T9); `_set_side`, `edit_events_handle`, `HandleEdit.target` (T10).
- Known open point for the executing review to settle by running it: how a `ValueError` raised inside `mode.compare` reaches a leaf (T3's CHECK), and the vertical-line drag payload (T6's spike).

---

## Corrections 1 (2026-10-02, from two executing reviews and a grip spike; these OVERRIDE the text above)

Both reviews built the plan in archives of e816762 and ran every named mutant; a third run spiked the grips with a real mouse. Where a correction and a task disagree, the correction wins. The owner's Q7 pick is K26; Tasks 1-5 and 7-9 do not depend on it.

### Part A

- **K1 (T2, the guard).** "ONE guard in `resolve_mode`" sees only the `tolerance` argument: built as worded, an unequal pair in `time_tolerance`, `tube_abs`, `tube_rel` was scored with `.upper` and a pair in any ROW raised `TypeError` out of `mode.compare`. The guard is a table in `modes.py`, `_NOT_SCORED_YET: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]]` — per mode, its leaf keys (`tolerance`, `time_tolerance`, `tube_abs`, `tube_rel`) and its `(list field, row key)` pairs (`("points", "tolerance")`, `("points", "time_tolerance")`, `("events", "tolerance")`, `("peaks", "tolerance")`). `resolve_mode` refuses an unequal pair under a listed leaf key and ANY dict under a listed row key, with the "not scored yet" sentence; Tasks 3, 4, 5 each delete their mode's entry and Task 5 deletes the table. T2's tests cover all nine fields on both paths.
- **K2 (T3, the pass test).** `lo <= v <= hi` with `lo = target − limit` is NOT today's `|v − target| <= limit`: target 1.0, tolerance 0.1, actual 1.1 fails today (`delta = 0.10000000000000009`) and passes on band edges; 1,450 of 20,000 edge-placed cases flipped. `_violation_in_box(act_time, act_values, t_lo, t_hi, target, lower, upper) -> tuple[float, float]` judges `d = v − target` against `−lower <= d <= upper`, does the straddle test on `d`, skips the straddle test when `−lower > upper` (an inverted band from a negative side must keep failing, as a negative tolerance does today), and returns `(violation, closest value)` — the reason for unequal sides prints that value. The identity sweep places samples ON the edge (20,000 cases, fixed seed), not only at random.
- **K3 (T3, T5: where rows are parsed).** A `ValueError` from `mode.compare` is the leaf's config error on the tree path only; on the flat path nothing catches it, and a `TypeError` is caught on neither. Row pairs are parsed in `PointsMode.__init__`, `EventTimingMode.__init__` and `DominantFrequencyMode.__init__` (where `resolve_mode`'s callers catch on both paths) AND at use in the scorer (tests call scorers directly). A flat-path test with a malformed row pins it for each of the three.
- **K4 (T2, callers the plan missed).** Built as worded, 101 existing tests failed in 13 files. Coerce with `parse_sides` at every entry: `resolve_mode`, each mode's `__init__`, each scorer's entry, and `VariableComparison.__post_init__` (a float `tolerance_used` becomes `Sides`). Then name and edit in T2: `reporting/stats_rows.py:75` (builds `ScalarMode(ScalarConfig(**params))` from a seed's float — must keep working, with a test), `comparison/registry.py` (the `build` signature — add to T2's files), and the 21 tests that compare `tolerance_used` or a config field to a number or format `ScalarConfig.tolerance` (13 in `test_comparator`, 2 in `test_tree_eval`, 2 in `test_scalar_metric`, 1 in `test_event_and_freq_modes`, 1 in `test_metric_registry`, `test_builtin_capabilities.py:2820` and `:2853`). `Sides` does NOT compare equal to a number. "The whole existing suite green unchanged" is withdrawn: the check is that those 21 change and nothing else does.
- **K5 (T2, the Statistics card).** `stats_card.summary` reads `ScalarConfig.tolerance` as a class attribute and calls `float()`: from T2 a scalar criterion with no tolerance of its own raises. Its change (Task 8's bullet) moves into T2, with a test for `summary({"params": {}})` and one for a pair.
- **K6 (T2, mutants).** "`_leaf_tolerance` reads `.lower` of the base" is not a mutant (the base is a number; unequal pairs are refused). Replace with "the fallback reports `Sides(0, 0)` instead of the base". No `.upper`→`.lower` mutant in T2.
- **K7 (T4).** `TubeConfig.to_dict` must test `!= Sides(0.0, 0.0)` — a dataclass is always truthy. Mutant: the truthiness test.
- **K8 (T3, T5).** The final check's `diagnostics["tolerance"]` goes through `as_written`. `_peak_window(declared, i)` takes the row index to name `peaks[i].tolerance`.
- **K9 (C1, enforced).** A base tolerance is a number: `comparator.py`'s base resolution refuses a pair in `comparison.tolerance` (spec or reference JSON) with "the base tolerance must be a number; a lower/upper pair belongs on a criterion" as the test's comparison error. Test both sources.
- **K10 (T1).** `parse_sides` is stricter than today's `float(...)` — the string `"0.01"` and `True` scored before and are config errors now. Deliberate; say it in D147. (A scan of the owner's spec, `testing.json` and 898 baselines found 29 tolerance values, none a non-number.)
- **K11 (T6, tests it breaks).** `test_session.py::TestView::test_the_tools_figure_rings_every_marker_handle` and four `test_geometry` tests that pin exact id sets change at T6 — add both files. Add an explicit "a non-editable figure holds no grip" test PER MODE (the tube golden has control points, so the golden alone lets the tube's grips through as `handle`).
- **K12 (T6 and T10 land together).** T6 as written changes the tool: grips drag and snap back until T10, and today's editors refuse the scalar's new lines. T6 is split: **T6a** (Part A) emits only the scalar's two lines and regenerates its golden, and `edit_scalar_handle` learns `scalar/upper` / `scalar/lower` in the same commit; **T6b** (the `grip` kind, every mode's grips, the figure layer) moves to Part B, directly before T10, and the two are reviewed as one part.

### Part B

- **K13 (T7 is not green alone).** A `sides` field fell through `forms._control` to a text input that stages a string. T7 and T8's form change are ONE task (schema kind + the control on the leaf form); the tables' stand-in stays as written until T9.
- **K14 (T8, the linked fields).** Setting the other field's value from the server fires ITS `on_change`: written literally the control calls `on_change` twice per keystroke. `sides_control` sets the other field under a guard flag; the test asserts the CALL COUNT (`seen == [0.05]`) — "one pending edit" passes with the double call because the Session folds it.
- **K15 (T8, T9: the blank mark).** The tables' "required" mark does not render in a browser today: NiceGUI resets an `error` prop it did not set, and the existing test passes on the key's presence. Mark through the field's own `validation=` callable and assert `cell.error == "required"`; fix the existing required table cell the same way, in T9, as its own commit ("fix the cause").
- **K16 (T9, the half pair).** With one side blank, editing another cell staged the STALE pair. `sides_control` returns a group exposing `half() -> bool`; the table's `blanks()` holds the whole list while any cell is half.
- **K17 (T8, labels).** Seven tests find the tolerance field by its label `"Tolerance"` (six in `test_test_page.py`, one in `test_builtin_capabilities.py`) and the Playwright file does too: list and update them in T8. Marks on the leaf form's control: `{field}:lower`, `{field}:upper`, `{field}:link`.
- **K18 (T9, width).** At 900 px with a drawer open the editor overflowed (`scrollWidth` 454 > 439) and one points row took 296 px of a 336 px scroll box. The group WRAPS below 420 px of available width and the hint's min-width applies only while the group is on one line; the measurement is taken at 1280 and 900 px, each with and without the Variables drawer open.
- **K19 (C5, the wording).** "A re-link never turns a passing criterion into a failing one" is false for dominant-frequency (a wider window can swallow a neighbouring peak). The rule stays — re-link takes the larger side — and the sentence becomes "never narrows what the criterion allows".
- **K20 (T10, windowed leaves).** `HandleEdit.reference` is unwindowed; the scorer detects events and takes the final value on the WINDOWED reference. The event grips' index and the final check's target are computed after slicing by the leaf's window (`_time_span`'s rule). A test with `window.start` pins each.
- **K21 (T10, the ids).** `tube/upper/const` reaches `edit_tube_handle`, which parses its third segment as an index: handle `const` before `_index`. A grip of a closed leaf cannot exist on the Test page (`rings_for` is always a tuple); the "not open" refusal for grips is tested through `act` directly, and through the page for the scalar's lines.
- **K22 (Q6 A's reach).** A statistics scalar has no plot: its two sides are fields only. Say so in the docs.
- **K23 (T7, T11 counts and files).** `columns` is unpacked in SIX places in `edit_on_plot.py` (:179, :186, :380, :394, :434, :481), in `tests/test_mode_schema.py` and two helpers of `tests/test_edit_on_plot.py`. `docs/extensibility.md` (:481 the column kinds, :538 the `build` signature) joins T11. `jsonschema` is importable only through the `fmpy` extra, so CI would skip a test that needs it: assert the dict.
- **K24 (existing defects the spike found; fixed in T6b).** The target ring moves only when pressed in its middle third — 3 px off centre RESIZES it and stages nothing (a pixel shape larger than 10 px in both dimensions has resize zones); and the tolerance box rect can itself be dragged by its edges. Rings become pixel `path` circles (a `path` shape never resizes), and a Playwright test presses 5 px off centre and reads a time move.
- **K25 [Q7] (T6b, T10: the grip's shape).** Rings on the edges bury the target whenever a box is under ~16 px: with a tolerance of 0.001 a sideways drag on the checkpoint rewrote the tolerance and left the time. Pending the owner's pick; the spiked option is a pixel-sized `path` triangle anchored at the edge's data value and offset 10 px outward (`M 0,10 L 6,17 L -6,17 Z` for upper; positive pixel y is UP), which never touches the ring at any box size, never resizes, and stays above the `rect` shapes `_layout` sorts first. Its handler reads ONLY the grip's own-axis anchor (the other is re-snapped by Plotly and drifts by the full amount beyond 8 px), accepts that a drag under 8 px does nothing, and the released anchor is the new edge to within 0.005 px. Whatever is picked: a Playwright test with a 1e-3 tolerance drags the checkpoint sideways and reads a time move and an unchanged tolerance.

- **K26 (Q7 ruled: A, with a hover rule).** The owner picked the arrows outside each edge, and asked that "these grab points show when hovering on/near the point only so they don't clutter the screen", if possible. So K25's triangle is the grip, and a grip is HIDDEN at rest and shown while the pointer is near its checkpoint, event or peak, and for the whole of a drag. The mechanism is the page's, not the server's: the shape list and the figure never change with the hover (a drag names a shape by index, and a mid-drag figure change loses it). It is settled by a spike on the tool's own figure before T6b is written in detail; if hiding cannot be made reliable across a `Plotly.react`, the fallback — said to the owner — is arrows always shown for the open criterion. A keyboard or touch user has no hover: the two fields remain the way in.

### Carried, not fixed here

- A row that defers to the criterion's tolerance: dragging its side rewrites the criterion's, so every deferring row's box moves (the plan's rule; shown to the owner at the gate).
- Typing `1e-3` passes through a blank (`1e`), which for one keystroke stages "unset" — today's single field does the same.
- A refusal's sentence appears in the editor, which can be off screen while the eye is on the plot (existing placement).
- A declared-points leaf whose rows hold their own tolerance still reads "vs tolerance <the leaf's>" in its criterion sentence (not introduced here).
