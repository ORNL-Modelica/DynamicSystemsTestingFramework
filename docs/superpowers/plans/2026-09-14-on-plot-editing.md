# On-Plot Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the figure hook's first client. In `dstf author`, a user drags a tube control point, a range or scalar bound, a checkpoint or a declared peak on the centre plot, and the edit is staged as a patch op, rescored, and redrawn with the zoom kept. The handle-bearing lists (`tube_points`, `points`, `events`, `peaks`), shown read-only as JSON today, get editable tables with add and remove rows in the same capability's panel.

**Architecture:** Sub-project 6 of `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.2 "Handles are geometry too" / "Ids are addresses", §4.3, §4.6 "Panel" and all three amendment notes, §4.7 "Edit criteria", §5 step 6, §6 "every handle's id resolves to the spec path it edits", §7.2). Sub-project 1 made every mode emit its handles as `kind="handle"` primitives with ids the evaluator prefixes with the leaf's RFC 6902 path. Sub-project 4 shipped the shell and a `FigureHook` no capability used. This plan adds: an editable variant of the figure (`variable_figure(..., editable=True)`, which the Session's figure uses); the inverse of each mode's handles as `MetricDescriptor.edit_handle`, beside `summarize`, in `comparison/handles.py`; `columns` metadata on the Config list fields; the hook reading the payloads Plotly actually sends; the shell updating the centre figure in place and rebuilding the other panels after an edit; and one builtin capability, **Edit on plot** (`builtin/edit_on_plot.py`, VARIABLE scope).

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (the `[author]` extra; its plotly element bundles plotly.js **3.8.2**, while the report vendors 2.35.0 — the editable figure is the tool's only, so that drift does not bite here), pytest-asyncio for `user_simulation`, Playwright for the one browser test, uv. No new dependency.

**Owner rulings (2026-09-14, asked after the spike):**
1. **Any handle on the plot drags** — the capability is VARIABLE scope, not the LEAF the §4.6 table says. As soon as a test's plot is up, any leaf's handle can be dragged; the op targets the leaf the handle's id names.
2. **The shell updates the figure in place.** A staged edit today rebuilds the plot element, which resets the zoom after every drag. The centre keeps its element while the test, variable and project are unchanged and calls `update_figure`. That is a shell change beyond the hook: §7.2 is amended to say so, and the amendment records it as what the first client showed.
3. **A checkpoint that follows the baseline moves in time only.** A point with no `value` key keeps none after a drag; a point with an explicit `value` moves in both axes. A drag never silently makes a checkpoint baseline-free.

**Spike results this plan rests on** (run 2026-09-14: a NiceGUI 3.16 page in headless Chromium serving a figure shaped like `figures/plotly.py` output, every forwarded event logged server-side; scripts were in the session scratchpad):
- **Clicks.** NiceGUI forwards `points[]` with `fullData`, `xaxis`, `yaxis` stripped — `data` (so `data.meta`), `customdata`, `curveNumber`, `pointNumber`, `x`, `y`, `bbox` arrive. Under the figure's own `hovermode: "x unified"` a click lists **every trace at that x**: clicking a handle marker gave `points[0]` = the Actual trace (`meta.id "actual"`) and `points[1]` = the handle (`customdata` its id, `meta.kind "handle"`). Under `"closest"` only the handle. **The shipped `FigureHook._dispatch` reads `points[0]`, so it names `actual` for a handle click.**
- **Trace points cannot be dragged; shapes can.** With `config.edits = {"shapePosition": True}` a drag emits ONE `plotly_relayout` on release, in data coordinates: a line `{"shapes[0].x0", "shapes[0].x1", "shapes[0].y0", "shapes[0].y1"}` (whole-line drag: `y0 == y1`); dragging one END of a line sends only that end's keys (`{"shapes[0].x0": 0, "shapes[0].y0": 2.02}`); a pixel-sized circle (`xsizemode`/`ysizemode: "pixel"`, `xanchor`/`yanchor` in data units) sends `{"shapes[2].xanchor": 4.0, "shapes[2].yanchor": 2.99}`. **The payload carries the shape's index, never its `name`.** A box zoom sends `{"xaxis.range[0]", ..., "yaxis.range[1]"}`.
- **Per-shape `editable: true` is unusable in 3.8.2:** without `config.edits` a plain drag does nothing; click-to-activate then drag works but writes PIXEL values into a data-referenced shape (`x0: 170.7` for a line spanning data 0..10) and fires a relayout on every mouse move. `config.edits.shapePosition` is the only viable path, and it makes EVERY shape draggable (windows, event lines, checkpoint boxes).
- **In-place update.** `plot.update_figure(dict)` with an unchanged `config` goes through `Plotly.react`: the zoom survives, events keep flowing on the same element, no relayout echoes back. Replacing the element (what the shell does on a `spec` event today) resets the zoom to the figure's own range.
- **`editrevision`.** With `layout.uirevision` constant (the figure sets `"keep"`), `Plotly.react` KEEPS a user's shape drag whenever the new figure repeats the value the app sent last — a dragged circle resent at its old anchor stayed where the user dropped it; `editrevision: null` did not help. Setting `layout.editrevision` to a value that changes on every update makes every shape land where the server says, and the axis zoom (governed by `uirevision`) still survives.

**Probe results over the fixture** (run 2026-09-14 against `tests/test_session._project`; reference and D's result are `x = t` on `t = 0, 0.25, 0.5, 0.75, 1.0`). Staging `PatchOp(D, "add", "/metrics", TREE)` with
```python
TREE = {"combinator": "and", "children": [
    {"metric": "tube", "variable": "x", "tube_width_mode": "band",
     "tube_points": [{"time": 0.5, "upper": 0.1, "lower": 0.2}, {"time": 0.9, "upper": 0.1, "lower": 0.1}]},
    {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 2.0},
    {"metric": "points", "variable": "x",
     "points": [{"time": 0.25, "tolerance": 0.05}, {"time": 0.75, "value": 0.75, "tolerance": 0.05}]},
    {"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}, "min_value": 0.0, "max_value": 2.0},
    {"metric": "tube", "variable": "x", "tube_width_mode": "rel", "tube_points": [{"time": 0.5, "upper": 0.1, "lower": 0.1}]},
    {"metric": "tube", "variable": "x", "tube_width_mode": "abs", "tube_points": [{"time": 0.0, "upper": 1.5, "lower": -0.5}]},
]}
```
validates and stages; D stays PASS; `s.tree(D)` before staging is `{"combinator": "and", "children": [{"metric": "nrmse", "variable": "x"}]}`. Handles in `s.view(D, "x")`: `/metrics/children/0/tube/upper/0` Marker (0.5, 0.6), `.../tube/lower/0` (0.5, 0.3), `.../tube/upper/1` (0.9, 1.0), `.../tube/lower/1` (0.9, 0.8); `/metrics/children/1/range/min` HLine −0.5, `.../range/max` 2.0; `/metrics/children/2/points/target/0` Marker (0.25, 0.25), `.../points/box/0` Box (0.25, 0.25, 0.2, 0.3), `.../points/target/1` (0.75, 0.75), `.../points/box/1`; `/metrics/children/3/scalar/min` HLine 0.0, `.../scalar/max` 2.0; `/metrics/children/4/tube/upper/0` (0.5, 0.55), `.../tube/lower/0` (0.5, 0.45); `/metrics/children/5/tube/upper/0` (0.0, 1.5), `.../tube/lower/0` (0.0, −0.5). The non-editable figure's `layout.shapes`, in order: `range/min`, `range/max`, `points/box/0`, `points/box/1`, `scalar/actual`, `scalar/min`, `scalar/max` (all names full ids). A **single-leaf root** tree (`add /metrics {"metric": "range", ...}` on A) has leaf path `/metrics` and ids `/metrics/range/max`. A `dominant-frequency` root leaf on A emits `/metrics/peaks/declared/0` (`kind "handle"`, `panel "spectrum"`) and `/metrics/peaks/window/0` (handle, spectrum); its marker traces carry `xaxis "x2"`. TRANSFORM's `test_spec.json` has no `tube`, `range`, `points`, `scalar`, `dominant-frequency`, `event-timing` or `metrics` key at all: every TRANSFORM leaf is nrmse, so the real-library gate stages a tube in memory (Task 10).

---

## Conventions for every task

- Run commands from the repo root with `uv run --no-sync ...` (agents share the venv; nothing in this plan changes a dependency). Gates before every commit: `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright"`, and the tests each task names.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`.** New files need `git add <file>` first. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf` against `testing_output/` or any real work directory** from a task or a review; Task 10's real-library gate is the orchestrator's. Tests use `tmp_path`.
- Done-checks are predicates over the end state, never counts.
- `docs/ideas.md` and `.soul/` are NOT touched by this plan. `docs/decisions.md` gains no entry.
- **Every file under `src/dstf/authoring/` other than `app.py`, `forms.py` and `builtin/*` imports no UI framework**; `builtin/*` import `nicegui` only inside functions. **`src/dstf/comparison/` and `src/dstf/figures/` import nothing from `dstf.authoring`.** `figures/plotly.py` still names no mode.
- In a `user_simulation` test, after any action that refreshes a region, wait on something the refresh RENDERS before asserting, and assert the Session's state too, never only the page's text. `should_see` retries 0.3 s by default: pass `retries=20` wherever the 0.1 s tick must act first. The `_settled(predicate)` helper in `tests/test_shell.py` polls a model predicate.
- **Every "never" / "only" / "exactly" / "always" written into a docstring ships with a test that tries the input it rules out** (a malformed payload, a missing key, a non-finite number).
- A test that pins a guard (a refusal, a stale-state check, an identity check) is mutation-checked in the commit that adds it: the implementer deletes or inverts the guard, confirms the test fails, restores it, and names the mutation in the commit message.
- Calls made here, not in the spec (Task 9 records them in a §4.6 "Amended in sub-project 6" note and amends §7.2 and the §4.6 table row):
  1. **Edit on plot is VARIABLE scope** (owner ruling 1).
  2. **`variable_figure(view, max_samples=None, *, editable=False)`.** `editable` adds `config.edits = {"shapePosition": True}` and one pixel-sized ring SHAPE per `kind="handle"` `Marker`, named with the marker's id, anchored at its `x`/`y` on its panel's axes; the marker traces stay. `Session.figure` passes `editable=True`; the report never does. Plotly drags shapes, not trace points (spike).
  3. **`layout.editrevision`** is stamped by the shell with a counter that changes on every in-place update, so a refused or no-op drag snaps back to where the Session says the handle is (spike).
  4. **The hook's payload contract.** A click is dispatched with the id of the first point whose trace `meta.kind` is `"handle"`, else of the first point (customdata → `meta.id` → `curveNumber`, as today). A relayout that moves shapes is dispatched ONCE PER MOVED SHAPE, with the id resolved from the rendered figure's `layout.shapes[N].name` and a payload of that shape's keys with the `shapes[N].` prefix stripped (`{"y0": 6.0, "y1": 6.0}`, `{"xanchor": 4.0, "yanchor": 2.99}`); any other relayout is dispatched once with id `""` and the raw payload. After a shape relayout the shell re-sends the figure whether or not anything was staged.
  5. **The centre figure updates in place** (owner ruling 2) while `(project, model, variable, the variable picker's names)` is unchanged; the `spec` and `results` events mark it dirty and the 0.1 s tick redraws once. A selection change, an open, a close or a changed name list rebuilds the centre as today.
  6. **After a capability acts, the shell rebuilds every OTHER available capability's panel in place, and after an act that came from the figure hook, the actor's too; the active tab is kept.** The tabs region is still not rebuilt on `spec` / `results`, which is what protects a field being typed in; but a panel that captured the leaf when it was built (Edit criteria) would otherwise show — and on its next keystroke re-stage — values another capability has since changed. A panel whose build raises shows the exception in its slot instead of breaking the page.
  7. **`MetricDescriptor.edit_handle: Callable[[HandleEdit], dict] | None`** — the inverse of the mode's handles, beside `summarize`: the raw leaf, the handle's local id and the dragged position in, the new raw leaf out, `HandleEditError` for a refusal. A third-party metric supplies its own. Builtins: `tube`, `range`, `scalar`, `points`, `dominant-frequency`; `nrmse` and `event-timing` have none.
  8. **`columns` field metadata** on the Config list-of-dict fields (`tube_points`, `points`, `events`, `peaks`) names each row's keys and their types. Edit on plot reads it from `dataclasses.fields(config_cls)` — NOT through `derive_schema`, so `FieldSpec` and the `export-schema` output keep their shape. Edit criteria's read-only JSON for these fields stays (`forms.py` already says their controls are on-plot editing's).
  9. **Every staged edit is one `replace` of the whole leaf**, the op Edit criteria stages, so consecutive drags on one leaf collapse into one pending op (`Session.stage`'s Convention 8), and a drag and a form edit of the same leaf read as "leaf → new leaf" in the drawer.

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/figures/plotly.py` | `variable_figure(..., editable=False)`; the ring shapes; `config.edits`. |
| `src/dstf/authoring/session.py` | `Session.figure` asks for the editable figure. |
| `src/dstf/comparison/handles.py` (new) | `HandleEdit`, `HandleEditError`, `edit_tube_handle`, `edit_bound_handle`, `edit_points_handle`, `edit_peaks_handle`. |
| `src/dstf/comparison/registry.py`, `comparison/builtins.py` | `MetricDescriptor.edit_handle`; the five builtins wired. |
| `src/dstf/comparison/modes.py` | `columns` metadata on four fields; help texts that name deleted editors corrected. |
| `src/dstf/authoring/app.py` | `FigureHook` (payload contract, owners, the shape-moved callback); `ShellPanel.figure` an owned view of it; the centre's in-place figure; per-capability panel slots rebuilt after acts. |
| `src/dstf/authoring/capabilities.py` | The `FigureHook` protocol docstring states the payload contract. |
| `src/dstf/authoring/builtin/edit_criteria.py` | A selection path that names a combinator reads "this leaf is not in the scored tree". |
| `src/dstf/authoring/builtin/edit_on_plot.py` (new), `builtin/__init__.py` | The capability, registered after Rerun. |
| `tests/test_figures.py`, `tests/test_handles.py` (new), `tests/test_shell.py`, `tests/test_builtin_capabilities.py`, `tests/test_capability_conformance.py`, `tests/test_session.py`, `tests/test_author_smoke_playwright.py` | New assertions per task. |
| `docs/extensibility.md`, `docs/usage.md`, `docs/architecture.md`, `docs/qa/reporter_checklist.md`, `CLAUDE.md`, the spec | Updated (Task 9). |

---

### Task 1: The editable figure

**Files:**
- Modify: `src/dstf/figures/plotly.py` (`variable_figure`, `_marker_traces` or a sibling helper), `src/dstf/authoring/session.py:307-313`
- Test: `tests/test_figures.py`, `tests/test_session.py`

- [ ] **Step 1: The failing tests** — in `tests/test_figures.py`, over a view built the way that file already builds them (`build_view` with `(leaf_path, VariableComparison)` pairs whose geometry holds two `kind="handle"` Markers on the time panel, one `kind="handle"` Marker on the spectrum panel, one `kind="result"` Marker, an `HLine` handle and a `Box` handle):
  1. `variable_figure(view, editable=True)["config"]["edits"] == {"shapePosition": True}`.
  2. Its `layout.shapes` holds exactly one `type "circle"` shape per `kind="handle"` Marker — `name` equal to the marker's id, `xanchor`/`yanchor` equal to its `x`/`y`, `xsizemode == ysizemode == "pixel"`, `xref`/`yref` `"x"`/`"y"` for the time panel and `"x2"`/`"y2"` for the spectrum one — and no circle for the `kind="result"` Marker. Every other shape and every trace of the non-editable figure is present, in the same order, unchanged (compare the non-circle shapes and `data` to `variable_figure(view)`'s).
  3. `variable_figure(view)` has no `edits` key and no circle shape, and equals `variable_figure(view, editable=False)`.
  4. The goldens (`tests/test_figure_goldens.py`) pass with no re-blessing — the report's figure did not change.
  In `tests/test_session.py`: after staging the header's `TREE` on D, `session.figure(D, "x")` has `config.edits` and circle shapes whose names are exactly the eight Marker handles the probe lists (four tube handles on children 0, two point targets on child 2, two tube handles each on children 4 and 5 — the set, not a count: `{"/metrics/children/0/tube/upper/0", ...}`), and no circle named for a `points/box`.

- [ ] **Step 2: The builder** — `variable_figure(view, max_samples=None, *, editable=False)`. When `editable`, after the per-leaf builders run, append for each `kind="handle"` `Marker` of each leaf: `{"type": "circle", "xref": _xref(m), "yref": "y2" if m.panel == "spectrum" else "y", "xsizemode": "pixel", "ysizemode": "pixel", "xanchor": m.x, "yanchor": m.y, "x0": -7, "x1": 7, "y0": -7, "y1": 7, "line": {"color": "#2e7d32", "width": 2}, "fillcolor": "rgba(46,125,50,0.25)", "name": m.id}` (a visible fill: a shape drags by its painted area, and a transparent fill is not guaranteed to receive the pointer), and set `config["edits"] = {"shapePosition": True}`. The module docstring gains one sentence: in the editable figure every shape is draggable (a plotly.js limitation) and the rings are what an editor attaches to. `Session.figure` passes `editable=True`; its docstring says the tool's figure is the editable one and the report's is not. No other caller changes (`grep -rn "variable_figure(" src/` — the report's callers keep the default).

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_figures.py tests/test_figure_goldens.py tests/test_session.py tests/test_report_size_budget.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/figures/plotly.py src/dstf/authoring/session.py tests/test_figures.py tests/test_session.py`). Message: `feat: the editable figure — handle rings a user can drag, for the tool only`

---

### Task 2: Handle editors on the descriptor

**Files:**
- Create: `src/dstf/comparison/handles.py`
- Modify: `src/dstf/comparison/registry.py` (`MetricDescriptor`), `src/dstf/comparison/builtins.py` (the five registrations)
- Test: `tests/test_handles.py` (new)

- [ ] **Step 1: The failing tests** — `tests/test_handles.py`, pure Python (no Session for the rule tests). `REF = (np.array([0, 0.25, 0.5, 0.75, 1.0]), np.array([0, 0.25, 0.5, 0.75, 1.0]))`. Each case calls `metric(<name>).edit_handle(HandleEdit(leaf=<raw leaf from the header's TREE>, local=..., x=..., y=..., reference=REF))`:
  1. **tube, band** (`TREE` child 0): `tube/upper/0` to `(0.5, 0.8)` → `tube_points[0] == {"time": 0.5, "upper": pytest.approx(0.3), "lower": 0.2}`; `tube/lower/1` to `(0.9, 0.6)` → `lower == approx(0.3)`, `upper` 0.1 kept; `tube/upper/0` to `(0.6, 0.8)` → `time == 0.6`, `upper == approx(0.2)` (width against `ref(0.6) = 0.6`); `x=None` leaves `time` untouched. The input leaf is unchanged afterwards (`copy.deepcopy` it before the call and compare).
  2. **tube, rel** (child 4): `tube/upper/0` to `(0.5, 0.6)` → `upper == approx(0.2)`; to `(0.0, 0.1)` → `HandleEditError` whose message says the reference is zero there.
  3. **tube, abs** (child 5): `tube/lower/0` to `(0.0, -1.0)` → `lower == -1.0`; `tube/upper/0` to `(0.0, -0.6)` → `HandleEditError` (the upper edge would cross the lower one at −0.5).
  4. **tube refusals**: band `tube/upper/0` to `(0.5, 0.4)` (negative width) → error; `x = 1.5` (outside the reference span `[0, 1]`) → error; `reference=None` on a band leaf → error; a band leaf with `"against": "other"` → error naming `other`; an abs leaf with `reference=None` still edits (abs needs no reference); `tube/upper/7` (no such point) → error.
  5. **range / scalar**: `range/max` with `y=1.5` → `max_value == 1.5`, `min_value` −0.5 kept, `x` ignored; `range/min` with `y=3.0` → error (min above max 2.0); `scalar/min` `y=-1.0` → `min_value == -1.0`; `scalar/actual` → error; a range leaf with only `max_value` accepts any `range/max`.
  6. **points** (child 2): `points/target/0` to `(0.3, 0.9)` → `points[0] == {"time": 0.3, "tolerance": 0.05}` — no `value` key (owner ruling 3); `points/target/1` to `(0.8, 0.7)` → `{"time": 0.8, "value": 0.7, "tolerance": 0.05}`; `points/target/0` to `(0.25, 0.9)` returns a leaf EQUAL to the input; `points/target/final` → error saying there is no declared point; `points/box/0` → error.
  7. **dominant-frequency** (`{"metric": "dominant-frequency", "variable": "x", "peaks": [{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}]}`): `peaks/declared/0` to `(2.0, 99.0)` → `freq == 2.0`, `y` ignored; `x = 0.0` → error; `peaks/window/0` → error.
  8. **Non-finite input**: `y = float("nan")` or `float("inf")` for any family above → `HandleEditError`, never a leaf with a NaN in it.
  9. **Registry**: `metric("nrmse").edit_handle is None` and `metric("event-timing").edit_handle is None`; the other five are not None.
  10. **§6, "every handle's id resolves to the spec path it edits"** — through the Session over `_project`: stage `TREE` on D; for every `kind="handle"` primitive of every leaf in `session.view(D, "x")`, with `local` = the id minus its leaf's path and a nudged position (a Marker: `(m.x + 0.01, m.y + 0.01)`; an HLine: `(None, value + 0.01)`; a Box: `(None, y0)`), call the leaf's `edit_handle` with the raw leaf from `session.tree(D)`; assert EITHER it raises `HandleEditError`, OR every JSON Pointer at which old and new leaf differ lies under the handle's address from this table, written in the test as the claim under test: `tube/upper/i` and `tube/lower/i` → `/tube_points/i`; `range/min`, `scalar/min` → `/min_value`; `range/max`, `scalar/max` → `/max_value`; `points/target/i` → `/points/i`; `peaks/declared/i` → `/peaks/i`. (A small recursive differ in the test yields the differing pointers.) Also assert every family in that table was edited at least once — a handle kind that only ever raised would make the test vacuous.
  11. **Round trip** — staging the edit puts the handle where it was dragged: after case 1's `tube/upper/0` edit is staged on D as `PatchOp(D, "replace", "/metrics/children/0", new_leaf)`, `session.view(D, "x")`'s `/metrics/children/0/tube/upper/0` Marker has `y == approx(0.8)`; same for the rel case (`0.6`) and `range/max` (`1.5`).

- [ ] **Step 2: The module** — `comparison/handles.py`:

```python
@dataclass(frozen=True, kw_only=True)
class HandleEdit:
    leaf: dict                    # the raw leaf, as `metrics` holds it; never mutated
    local: str                    # the handle's id without its leaf path: "tube/upper/3"
    x: float | None               # the dragged position; None where that axis did not move
    y: float | None
    reference: tuple[np.ndarray, np.ndarray] | None   # the primary baseline, full resolution

class HandleEditError(ValueError): ...
```

Each editor deep-copies `leaf`, applies the rule, returns the copy; refuses with `HandleEditError` naming the handle and the reason. Rules (the tests above are their executable form): **tube** — the index addresses `tube_points` in authored order, as `algorithms._tube_geometry` enumerates it; `x` sets `time` (refused outside `[ref_time[0], ref_time[-1]]`, where `_tube_geometry` emits no handle); `y` sets the side's width at the point's (new) time: `abs` → the value itself (refused if upper would fall below the point's lower, or lower above its upper, a missing side reading as `0.0` as `_interpolate_tube_widths` reads it); `band` → `y − ref(t)` for upper, `ref(t) − y` for lower; `rel` → the same divided by `|ref(t)|`, refused below `types._EPS`; `band`/`rel` refuse a negative width, a missing reference, and a leaf whose `against` is not `"primary"` (the view's reference is the primary one). `ref(t)` is `np.interp(t, ref_time, ref_values)`. The docstring says a width under `tube_min_width` is written as dragged and the band then draws at the minimum — the handle lands on the band's real edge. **bounds** (range and scalar share it) — `min`/`max` set `min_value`/`max_value` from `y`; refused when the result has `min_value > max_value`. **points** — `target/i` sets `time` from `x`, and `value` from `y` only when the point already has `value`; `target/final` refused. **peaks** — `declared/i` sets `freq` from `x`, refused `<= 0`; `y` ignored. Every editor refuses a non-finite `x`/`y` and any local it does not name ("<local> cannot be moved on the plot"). `MetricDescriptor` gains `edit_handle: Callable[[HandleEdit], dict] | None = None` with a docstring sentence beside `summarize`'s; `builtins.py` wires `tube → edit_tube_handle`, `range` and `scalar → edit_bound_handle`, `points → edit_points_handle`, `dominant-frequency → edit_peaks_handle`. `handles.py` imports nothing from `dstf.authoring` or `dstf.figures`.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_handles.py tests/test_geometry.py tests/test_plugins.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/comparison/handles.py src/dstf/comparison/registry.py src/dstf/comparison/builtins.py tests/test_handles.py`). Message: `feat: edit_handle — each mode's handles have an inverse, and every handle's id resolves to the spec path it edits`

---

### Task 3: The hook reads the payloads Plotly sends

**Files:**
- Modify: `src/dstf/authoring/app.py` (`FigureHook`, `ShellPanel`), `src/dstf/authoring/capabilities.py` (`FigureHook` protocol docstring)
- Test: `tests/test_shell.py` (`test_the_figure_hook_names_the_primitive_from_meta_id` and new tests beside it)

- [ ] **Step 1: The failing tests** — pure (a `FigureHook` and a stand-in plot object with a `figure` dict attribute and an `on` method recording registrations; no page). The payloads are the spike's, verbatim in the test:
  1. **x-unified click**: `{"event": {}, "points": [{"curveNumber": 0, "pointNumber": 50, "x": 5, "y": 5, "data": {"meta": {"id": "actual", "kind": "act"}}}, {"curveNumber": 1, "pointNumber": 1, "x": 5, "y": 7, "customdata": "/metrics/children/0/tube/upper/1", "data": {"meta": {"family": "tube/upper", "kind": "handle"}}}]}` → the click handler receives `"/metrics/children/0/tube/upper/1"`. The existing four cases of `test_the_figure_hook_names_the_primitive_from_meta_id` keep their expectations.
  2. **shape relayouts**, with the stand-in plot's `figure["layout"]["shapes"] = [{"name": "/metrics/children/1/range/max"}, {"name": "/metrics/children/2/points/box/0"}, {"name": "/metrics/children/0/tube/lower/0"}]` attached: `{"shapes[0].x0": 0, "shapes[0].x1": 10, "shapes[0].y0": 6.0, "shapes[0].y1": 6.0}` → one relayout call `("/metrics/children/1/range/max", {"x0": 0, "x1": 10, "y0": 6.0, "y1": 6.0})`; `{"shapes[2].xanchor": 4.0, "shapes[2].yanchor": 2.99}` → `("/metrics/children/0/tube/lower/0", {"xanchor": 4.0, "yanchor": 2.99})`; `{"shapes[0].x0": 0, "shapes[0].y0": 2.0}` → `(".../range/max", {"x0": 0, "y0": 2.0})`; a payload moving shapes 0 and 2 → two calls, index order.
  3. **zoom**: `{"xaxis.range[0]": 0.49, "xaxis.range[1]": 1.49}` → one call `("", <that payload>)`, and the shape-moved callback is NOT called; for every case in 2 it IS called, once per relayout.
  4. **nothing in `_dispatch` raises** — each of these dispatches without an exception, and a shape key whose index cannot be resolved yields id `""` for that shape: `{"shapes[9].y0": 1.0}` (index past the list), `{"shapes[x].y0": 1.0}`, `{"shapes[0]": 1.0}` (no key after the index), `{"shapes[0].y0": 1.0}` with no plot attached, a shape dict without `name`, a figure whose `layout` has no `shapes`, payload `None`, payload `[]`, `points` holding a non-dict, `data` holding a non-dict `meta`.
  5. **owners**: `hook.owned("a").on("plotly_click", h1)`, `hook.owned("b").on("plotly_click", h2)`; `hook.clear("a")` leaves only `h2`; `hook.clear()` leaves none.

- [ ] **Step 2: The hook** — `FigureHook.__init__(self, on_shape_moved: Callable[[], None] | None = None)`. Handlers are stored with their owner: `on(kind, handler, owner="")`; `owned(owner)` returns a small object whose `on(kind, handler)` registers under that owner (it is what `ShellPanel.figure` holds, so it satisfies the `FigureHook` protocol unchanged); `clear(owner=None)` removes one owner's handlers or all. `attach(plot)` keeps `self._plot = plot` beside today's `plot.on(...)` registrations. `dispatching: bool` is true while handlers run (Task 5 reads it). `_dispatch(kind, payload)`: for `plotly_click` / `plotly_selected`, choose the first point whose `data.meta.kind == "handle"`, else the first point, and resolve the id as today; for `plotly_relayout`, collect the keys matching `^shapes\[(\d+)\]\.(.+)$` grouped by index; with none, dispatch `("", payload)`; otherwise for each index ascending, the id is `self._plot.figure["layout"]["shapes"][index]["name"]` when every step of that lookup succeeds, else `""`, dispatched with that shape's stripped keys; then call `on_shape_moved` once. The docstrings of both `FigureHook`s (the class and the protocol in `capabilities.py`) state the contract in Conventions call 4, including that a shape's position arrives in data coordinates and that the payload never names the shape (the hook resolves it). `Shell.__init__` builds `FigureHook()` for now; Task 4 passes the callback.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_shell.py tests/test_capability_conformance.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/app.py src/dstf/authoring/capabilities.py tests/test_shell.py`). Message: `fix: the figure hook names the handle a click or a drag moved — per shape, from the rendered figure, under x-unified hover`

---

### Task 4: The centre figure updates in place

**Files:**
- Modify: `src/dstf/authoring/app.py` (`Shell.__init__` event wiring, `_centre`, `_tick`, a new `_tree` refreshable, a `_centre_key` helper)
- Test: `tests/test_shell.py`

- [ ] **Step 1: The failing tests** — `user_simulation` over the `shell` fixture:
  1. Select A (`user.find(marker=A).click()`, `await user.should_see("/metrics/children/0")`); `(plot,) = user.find(ui.plotly).elements`; record `plot.id` and `rev0 = plot.figure["layout"].get("editrevision")`. `shell.session.stage([LOOSEN])`; `await user.should_see("2 pending")`; `await _settled(lambda: plot.figure["layout"].get("editrevision") != rev0)`. Then: the only `ui.plotly` element on the page has `plot.id`; the tree row for `/metrics/children/0` shows the rescored verdict (`await user.should_see("PASS")` inside the centre — check what LOOSEN's 0.5 tolerance gives A's nrmse 0.153 by reading `shell.session.comparisons[A].passed` first and asserting the row's text agrees with it); `shell.session.selection.tests == {A}`.
  2. Selecting D afterwards puts a plot with a DIFFERENT element id on the page (the old one is gone).
  3. A `results`-only event (`shell.session.rescore(A)`) leaves the same element id and changes `editrevision`.
  4. Two events emitted in one call (`stage` emits `spec` then `results`) redraw the figure at most twice and at least once: monkeypatch the plot element's `update_figure` to count calls after the element exists; after `stage` and `_settled(...)`, `1 <= calls <= 2`.
  5. The hook's shape-moved callback marks the figure dirty: `shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})` with the plot attached → `editrevision` changes and the element id stays, with nothing staged (`shell.session.spec.pending == []`).
  6. `close` then `open` of the same config (the project object changes) rebuilds the centre (new element id) — the key includes the project's identity.
  Mutation check (named in the commit message): with `spec`/`results` refreshing `centre` as before, test 1's identity assertion fails.

- [ ] **Step 2: The shell** — `_centre_key()` returns `(id(project), model_id, variable, tuple(names))` for a single selected test with a variable (the same `variable` resolution `_centre` does today), else `None`. `_centre` builds the label, the variable select, the plot and then `self._tree(model_id)` (a new `@ui.refreshable_method` holding today's tree rows and the "no criteria scored" label); when it builds a plot it stores `self._plot`, `self._plot_key = self._centre_key()`, stamps `figure["layout"]["editrevision"] = self._figure_rev` and attaches the hook. Event wiring: `spec` refreshes `header`, `tests`, `drawer`, `jobs` as today and calls `self._follow_centre()`; `results` refreshes `tests`, `leaf_error` and calls `_follow_centre()`. `_follow_centre()`: when `_centre_key()` is not `None`, equals `_plot_key`, and the plot element still exists (`not self._plot.is_deleted`), set `self._figure_dirty = True` and refresh `_tree`; otherwise refresh `centre`. `_tick()`, after draining the queue: if `_figure_dirty` and the key still matches, clear the flag, `self._figure_rev += 1`, build `fig = self.session.figure(model_id, variable)`, stamp `editrevision`, `self._plot.update_figure(fig)`. `FigureHook(on_shape_moved=self._mark_figure_dirty)`. A selection event refreshes `centre` as today. A comment at `_follow_centre` says why: a rebuilt plot element loses the user's zoom (spike) and `editrevision` must change for a dragged shape to land where the Session puts it.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_shell.py tests/test_capability_conformance.py tests/test_builtin_capabilities.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/app.py tests/test_shell.py`). Message: `feat: the centre figure updates in place — a staged edit keeps the plot element, so the zoom survives`

---

### Task 5: Panels follow edits made elsewhere

**Files:**
- Modify: `src/dstf/authoring/app.py` (`_tabs`, `act`, `ShellPanel`), `src/dstf/authoring/builtin/edit_criteria.py` (`panel`)
- Test: `tests/test_shell.py`, `tests/test_builtin_capabilities.py`

- [ ] **Step 1: The failing tests** — `user_simulation`; a dummy capability `_Setter` (scope VARIABLE, `available` True, `panel` renders a `ui.input` marked `setter-input` and registers one hook handler, `act(s, **inputs)` returns `Patch(ops=[PatchOp(D, "replace", "/metrics/children/1", inputs["leaf"])])`) registered after the builtins; the header's `TREE` staged on D first:
  1. Select D's leaf `/metrics/children/1` (`shell.session.select(tests={D}, variable="x", leaf_path="/metrics/children/1")`, wait on `Edit criteria`). Edit criteria's form shows the upper bound 2.0 (find its `ui.number` whose label is `Upper bound (optional)` — the `RangeConfig` label — and read `.value`). Call `_Setter`'s panel `submit(leaf={... "max_value": 3.0 ...})` through the `ShellPanel` the shell built (reach it by the setter's registered handler's closure, or have `_Setter.panel` stash `ui_panel` on the instance). After `await _settled(...)` on the model (`session.tree(D)["children"][1]["max_value"] == 3.0`), the Edit criteria number reads 3.0.
  2. The actor's own slot is not rebuilt by its own panel-originated act: the `setter-input` element's id is the same before and after.
  3. A figure-originated act rebuilds the actor's slot too: a `_Setter` hook handler that calls `ui_panel.submit(...)`, fired through `shell.figure_hook._dispatch("plotly_relayout", {...})` with a plot attached whose shape 0 is named — after it, the `setter-input` element id has changed.
  4. After three acts, every capability's registered hook handlers number exactly what one panel build registers (`_Setter` owns one) — no accumulation across slot rebuilds.
  5. The active tab is kept: set the tab panels' value to `_Setter`'s label, act, and the value is still that label afterwards (read the `ui.tab_panels` element's `value`). And when an act changes which capabilities are available — `_Setter`'s act makes a second dummy `_Late` (available only once D's child 1 has `max_value == 3.0`) available — the tabs rebuild, `_Late`'s tab is enabled, and the active tab is still `_Setter`'s.
  6. An act that raises shows the error in the actor's slot and the tab stays active; nothing else changes.
  7. A panel whose build raises: a dummy whose `panel` raises `RuntimeError("panel boom")` shows `RuntimeError: panel boom` in its slot, and the other tabs still render.
  8. (`tests/test_builtin_capabilities.py`) With `TREE` staged on D and D's leaf `/metrics/children/1` (range) selected, Edit tree wraps that leaf in `warn` through the act inputs its existing tests use (wrapping A's ONLY leaf would be refused — the validator wants a primary leaf outside `warn`; D's tube leaves stay outside); Edit criteria's slot then reads `this leaf is not in the scored tree`, and nothing raises — the selection's path now names the combinator.
  Mutation checks (named in the commit message): remove the rebuild-others call → test 1 fails; rebuild the actor too on a panel-originated act → test 2 fails.

- [ ] **Step 2: The shell** — `_tabs` builds, inside each available capability's `ui.tab_panel`, a container stored as `self._slots[cap.name] = (container, cap)` and fills it through `_fill_slot(cap)`: `container.clear()`; `figure_hook.clear(cap.name)`; within the container, the error label, the LEAF-scope `_leaf_error` as today, then `cap.panel(self.view, ShellPanel(self, cap, figure_hook.owned(cap.name)))` inside `try` / `except Exception as e` → `ui.label(f"{type(e).__name__}: {e}").classes("text-negative")`. `_tabs` still calls `figure_hook.clear()` first (a full rebuild drops every owner). `act(cap, **inputs)`: on success, compare the names of the applicable-and-available capabilities now (`applicable()`) with the slots' names; when they differ (Edit criteria switched a leaf to `tube` and Edit on plot became available, or a lock greyed one), a full `_refresh("tabs")` that keeps the active tab when its label is still available; otherwise `_fill_slot` for every slot whose capability is not `cap`, and `cap`'s own too when `self.figure_hook.dispatching`. On an exception: record the error and `_fill_slot(cap)` (not a full tabs refresh). The tab panels element is kept on `self._tab_panels`; a slot rebuild does not touch its `value`. Edit criteria's `panel`: `leaf = _leaf_node(...)`; `if leaf is None or leaf.get("kind") != "leaf":` the existing label. A comment at `act` gives Conventions call 6's reason in two sentences.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_shell.py tests/test_builtin_capabilities.py tests/test_capability_conformance.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/app.py src/dstf/authoring/builtin/edit_criteria.py tests/test_shell.py tests/test_builtin_capabilities.py`). Message: `fix: a capability's panel follows an edit another one staged — slots rebuild in place, the active tab and the typed-in field kept`

---

### Task 6: The Config declares its list rows

**Files:**
- Modify: `src/dstf/comparison/modes.py` (`TubeConfig.tube_points`, `PointsConfig.points`, `EventTimingConfig.events`, `DominantFrequencyConfig.peaks`)
- Test: `tests/test_handles.py` (or `tests/test_mode_schema.py` if that is where Config metadata is tested — check first)

- [ ] **Step 1: Before anything, record** `uv run --no-sync dstf export-schema --output <scratch>/before.json` (a tmp path; the command needs no config — verify, and if it does, run it in `tmp_path` via a test instead).

- [ ] **Step 2: The failing test** — for each of the four fields, `dataclasses.fields(<Config>)` metadata has `columns`, a tuple of `(key, type)` where `type` is `"float"` or a tuple of choices. The expected columns, each key checked against the reader in `algorithms.py` before it is written here: `tube_points` → `time`, `upper`, `lower` (all float); `points` → `time`, `value`, `tolerance` (float), `tolerance_mode` (`("abs", "rel")`), `time_tolerance` (float); `events` → `time`, `tolerance` (float); `peaks` → `freq`, `tolerance` (float), `tolerance_mode` (the choices `_compare_dominant_frequency` accepts, read from its code). A second assertion: every `passthrough` list-of-dict field on a builtin Config has `columns` — the predicate that a new list field cannot ship without its table.

- [ ] **Step 3: The metadata** — add `columns` to the four `field(metadata=...)` dicts. Correct the help texts that name editors which no longer exist (`"Edit via the rich tube editor below the plot"`, `"Authored via the table editor in the interactive HTML reporter"`, `"Authored via the table editor; use 'Detect peaks from reference'..."`) to name the tool's Edit on plot tab (drag a handle, or edit the table). Then `dstf export-schema` again into `after.json` and diff: the ONLY differences are those help strings. If `columns` appears in the export, stop — `derive_schema` must not carry it (Conventions call 8); fix by not reading it there.

- [ ] **Step 4: Run**, gates. **Commit** (paths: `src/dstf/comparison/modes.py` and the test file). Message: `feat: the Config's list fields declare their row columns; help texts name the editor that exists` — the body lists the help strings that changed in the export.

---

### Task 7: Edit on plot

**Files:**
- Create: `src/dstf/authoring/builtin/edit_on_plot.py`
- Modify: `src/dstf/authoring/builtin/__init__.py` (register after Rerun)
- Test: `tests/test_builtin_capabilities.py`, `tests/test_capability_conformance.py` (the builtin order), `tests/test_shell.py`

- [ ] **Step 1: The failing tests** — over the `_project` fixture with the header's `TREE` staged on D (`shell.session.stage([PatchOp(D, "add", "/metrics", TREE)])`, then `shell.session.select(tests={D}, variable="x", leaf_path=None)`):
  **`act`, pure** (`EditOnPlot().act(shell.view, ...)`):
  1. `act(kind="handle", model_id=D, variable="x", handle="/metrics/children/1/range/max", x=None, y=1.5)` → `Patch` with one `PatchOp(D, "replace", "/metrics/children/1", {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 1.5})`.
  2. `handle="/metrics/children/0/tube/upper/0", x=0.5, y=0.8` → the replace of `/metrics/children/0` whose `tube_points[0]["upper"] == approx(0.3)`; applied through `shell.session.apply`, `shell.session.spec.pending[-1]` is that op and a second drag of the same handle to `y=0.9` leaves ONE pending op for that path (Convention 8's collapse).
  3. `handle="/metrics/children/2/points/target/0", x=0.25, y=0.9` → `None` (nothing moved that the rule lets move).
  4. `handle="/metrics/children/2/points/box/0", ...` → raises `HandleEditError`; `handle="/metrics/children/9/tube/upper/0"` (no such leaf) → raises `ValueError` naming the id; `handle=""` → `None`.
  5. On a single-leaf root tree (A with `add /metrics {"metric": "range", "variable": "x", "max_value": 5.0}` staged): `handle="/metrics/range/max", y=4.0` → `PatchOp(A, "replace", "/metrics", {... "max_value": 4.0})` — the leaf path is resolved by the longest leaf path the id starts with followed by `/`, so `/metrics` is not mistaken for a prefix of a child's path and `/metrics/children/1` not for `/metrics/children/10`.
  6. `act(kind="rows", model_id=D, path="/metrics/children/0", field="tube_points", rows=[{"time": 0.5, "upper": 0.1, "lower": 0.2}, {"time": 0.9, "upper": 0.1, "lower": None}])` → the replace whose `tube_points[1] == {"time": 0.9, "upper": 0.1}` (a `None` cell omits its key).
  7. A metric whose descriptor has no `edit_handle` (an nrmse leaf — D's tree before `TREE`) → `HandleEditError`-style refusal saying the metric has no on-plot handles.
  **`available`**: no test selected → `"select one test"`; two tests → the same; D selected with `TREE` staged → `True`; A as loaded (one nrmse leaf) → `"no criterion on x has on-plot handles"`; a gated `Job` on D running → the lock reason `"<label> is running on FakeLib.D"`.
  **Panel** (`user_simulation`): select D; the `Edit on plot` tab is enabled; inside it, a table per list field of each leaf on `x` (tube children 0, 4, 5; points child 2) with one row per entry and a number input per float column (a `ui.select` per choice column). Editing child 0's first row `upper` to `0.25` stages the replace (`await _settled(lambda: <pending last op value's tube_points[0]["upper"] == 0.25>)`). `Add row` on child 0 appends a row copying the last row's values with `time` set to the reference span's end (then the user edits it) and stages; `Remove row` on the new row stages the original list back. `Add row` on an empty list (A with `add /metrics {"metric": "points", "variable": "x", "points": []}` staged — an empty `points` is the final-value check, a valid leaf) appends `{"time": 0.5, "value": 0.0, "tolerance": 0.0, "tolerance_mode": "abs", "time_tolerance": 0.0}`: every float column `0.0`, a column named `time` at the reference span's midpoint (`0.0` with no reference), a choice column its first choice. **The drag, end to end through the hook:** with the centre plot attached, `shell.figure_hook._dispatch("plotly_relayout", {f"shapes[{i}].xanchor": 0.5, f"shapes[{i}].yanchor": 0.8})` where `i` is the index of the ring named `/metrics/children/0/tube/upper/0` in the plot element's `figure["layout"]["shapes"]` → the pending last op is the replace with `upper == approx(0.3)`, the plot element id is unchanged, and after `_settled` the plot's `editrevision` has changed. A drag of the ring named `.../points/box/0`? It has none — use the `points/box/0` RECT's index with `{"shapes[k].y0": 0.3, "shapes[k].y1": 0.4}` → the tab shows the `cannot be moved on the plot` error text, nothing is staged, and the figure is re-sent (`editrevision` changed).
  **Conformance**: `tests/test_capability_conformance.py`'s builtin-order assertion now lists `edit-criteria, edit-tree, accept, rerun, edit-on-plot`.

- [ ] **Step 2: The capability** — `builtin/edit_on_plot.py`, `name = "edit-on-plot"`, `label = "Edit on plot"`, `scope = Scope.VARIABLE`. No module-level `nicegui` import.
  - `available(s)`: `"select one test"` unless exactly one test and a variable are selected; the lock reason when that model is locked; `"no criterion on {variable} has on-plot handles"` when none of the variable's leaves (from `leaves_by_variable(s.comparisons[model_id].metric_tree)`, cheap — no figure is built) has a descriptor with `edit_handle` or a Config field with `columns`; else `True`.
  - `panel(s, ui_panel)`: `ui_panel.figure.on("plotly_relayout", on_move)` where `on_move(handle, payload)`: return on an empty `handle`; position from the payload — `xanchor`/`yanchor` when present (either may be missing → `None`); else `y0`/`y1` → `x = None`, `y = y0` when both are present, the one present otherwise; else return (a horizontal-only line drag moves nothing a rule reads; the shell re-sends the figure regardless); then `ui_panel.submit(kind="handle", model_id=..., variable=..., handle=handle, x=x, y=y)`. Below it, a label `drag a handle on the plot, or edit a table`, then per leaf on the variable (project order of paths) and per Config field with `columns` that the raw leaf (`s.tree(model_id)` at the leaf path, walked with `edit_tree._node_at`) holds or could hold: a caption `<path> · <metric> · <field>`, one row per entry with a control per column, `Remove row` per row, `Add row`; every change submits `kind="rows"` with the whole list as the table now holds it. The table keeps its rows in panel-local state and re-renders its own container after `Add row` / `Remove row` — the shell does not rebuild the acting capability's slot after a panel-originated act (Conventions call 6) — and when a submit is refused the shell rebuilds the slot with the error, which re-reads the rows from the Session. `Add row` on an empty list uses the rule Step 1 states.
  - `act(s, **inputs)`: `kind == "handle"` → `""` handle → `None`; resolve the leaf path (Step 1 test 5's rule over the leaf paths of `leaves_by_variable`) or raise `ValueError`; `desc = metric(<leaf metric>)`; `desc.edit_handle is None` → raise `HandleEditError(f"{metric} has no on-plot handles")`; reference = `(view.ref_time, view.ref_values)` from `s.view(model_id, variable)` (full resolution — `view`, not `figure`) or `None`; `new = desc.edit_handle(HandleEdit(leaf=raw, local=handle[len(path) + 1:], x=x, y=y, reference=reference))`; `new == raw` → `None`; else `Patch(ops=[PatchOp(model_id, "replace", path, new)])`. `kind == "rows"` → the raw leaf with `field` set to the rows with `None` cells dropped, the same replace (or `None` when equal).
  - `builtin/__init__.py` registers it after Rerun.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_builtin_capabilities.py tests/test_shell.py tests/test_capability_conformance.py tests/test_handles.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/builtin/edit_on_plot.py src/dstf/authoring/builtin/__init__.py tests/test_builtin_capabilities.py tests/test_shell.py tests/test_capability_conformance.py`). Message: `feat: Edit on plot — drag a handle or edit its table; one replace of the leaf per edit`

---

### Task 8: The browser test drags a handle

**Files:**
- Modify: `tests/test_author_smoke_playwright.py` (a second test beside the existing one)

- [ ] **Step 1: The test** — the same launch helper as the existing test (`_project`, a free port, `PYTEST_CURRENT_TEST` stripped, `dstf author --no-browser`), but before launch the header's `TREE` is written into D's entry of the fixture's `test_spec.json` on disk as its `metrics` (so the page opens with handles and nothing pending); record the file's bytes. In Chromium: click D's row (the locator pattern the existing test documents); wait for `.js-plotly-plot .main-svg`; box-zoom on an empty region (the spike's mouse sequence) and read `gd._fullLayout.xaxis.range` / `yaxis.range`; open the `Edit on plot` tab; drag the ring of `/metrics/children/0/tube/upper/0` from its anchor to `y = 0.8` (pixel positions from `gd._fullLayout.xaxis.d2p` / `yaxis.d2p` plus `_offset` and the element's bounding rect — find the ring's index by `name` in `gd.layout.shapes`; the drag steps through 10 mouse moves with short sleeps, as the spike did); `expect` the header's pending button to read `1 pending`; poll `gd.layout.editrevision` until it changes; then the ring's `yanchor` in `gd.layout.shapes` is within 0.02 of 0.8 (the rescored handle), the axis ranges equal the recorded zoom, and `document.querySelectorAll('.js-plotly-plot').length` is 1. Drag the `/metrics/children/1/range/max` line to `y = 1.5`; `expect` `2 pending`. Open the drawer (the pending button) and `expect` its text to contain `/metrics/children/1` and `"max_value": 1.5` to two decimals (read the text, parse the number, compare with tolerance 0.02). Terminate; the spec file's bytes are unchanged.

- [ ] **Step 2: Run** — `uv run --no-sync pytest tests/test_author_smoke_playwright.py -q` three times in a row; all pass (the drag is timing-sensitive — if a run flakes, lengthen the waits on what the page renders, never add a bare sleep before an assertion). Gates.

- [ ] **Step 3: Commit** (path: the test file). Message: `test: the real page drags a tube handle and a range bound — staged, rescored, zoom kept, nothing saved`

---

### Task 9: Documents

**Files:** `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md`, `docs/extensibility.md`, `docs/usage.md`, `docs/architecture.md`, `docs/qa/reporter_checklist.md`, `CLAUDE.md`

- [ ] The spec: §4.6 gains an **"Amended in sub-project 6."** paragraph after sub-project 5's, recording Conventions calls 1–9 in prose with their reasons (the spike's facts for 2–4, the owner's rulings for 1, 5 and the checkpoint rule, the stale-panel reason for 6, the export-schema constraint for 8); the §4.6 table's On-plot editing row reads `VARIABLE` scope, `handlers on the handle rings via the figure hook; a table per list field`, status `v1 (sub-project 6)`. §7.2 is amended in place, not rewritten: after "with no change to the shell beyond the hook that existed since step 4" add a sentence saying that sub-project 6 changed the shell in two places the hook could not reach — the centre updates its figure in place (owner ruling 2026-09-14: a rebuilt element loses the user's zoom on every drag) and a capability's panel is rebuilt after another capability's edit (a panel that captured its leaf would re-stage stale values) — and that both are general to any capability, not specific to on-plot editing. §4.7 "Edit criteria": the `tube_points` sentence says the lists are edited in the Edit on plot tab.
- [ ] `docs/extensibility.md`: the metric plug-in section documents `edit_handle` (the `HandleEdit` fields, the refusal, the "one replace of the leaf" consequence) and `columns`; the capability section documents the hook's payload contract (Conventions call 4) and that a panel may be rebuilt after another capability's edit, so a panel must build from the view, not from state it kept.
- [ ] `docs/usage.md`: the `dstf author` section gains the Edit on plot tab (drag, tables, what snaps back and why).
- [ ] `docs/architecture.md`: `comparison/handles.py` and the editable figure in the layer mapping.
- [ ] `docs/qa/reporter_checklist.md`: the tool section gains: drag a tube handle and a range bound; zoom survives; a box drag snaps back with its reason; a table edit and add/remove row stage; Save writes them.
- [ ] `CLAUDE.md`: the `authoring/` line names Edit on plot among the builtins; `comparison/` names `handles.py`.
- [ ] Every `tube_points`, `peaks`, `points` or `events` sentence in `docs/` that describes an editor that no longer exists either names the tool's Edit on plot tab or is removed: `grep -rn "table editor\|tube editor\|interactive HTML reporter" docs/usage.md docs/extensibility.md docs/patterns.md` and read each hit for its referent (a hit in a dated plan or the decision log is history and stays).
- [ ] Gates; commit (paths listed). Message: `docs: on-plot editing — the hook's payloads, edit_handle, the in-place figure, §7.2 amended`

---

### Task 10: Sub-project gate (orchestrator)

- [ ] **Step 1: Full gates** — `uv run --no-sync pytest -q` (browser tests included), `uv run --no-sync ruff check && uv run --no-sync ruff format --check && uv run --no-sync mypy`, `uv run --no-sync pytest --cov -q | tail -3` (the floor is 66%).
- [ ] **Step 2: End-state predicates** — every one checked by a command, reported with its output:
  1. `src/dstf/comparison/` and `src/dstf/figures/` import nothing from `dstf.authoring`; `figures/plotly.py` names no mode (`grep -n '"tube"\|"range"\|"points"\|"scalar"\|dominant' src/dstf/figures/plotly.py` — every hit is a `_LABELS` / `_MARKER_SYMBOLS` family key, which predates this plan; list them).
  2. Module-level `nicegui` imports exist only in `authoring/app.py` and `authoring/forms.py`.
  3. The builtins register in the order `edit-criteria, edit-tree, accept, rerun, edit-on-plot`.
  4. Every builtin metric whose mode emits a `kind="handle"` primitive has `edit_handle`, or its handles are all refused by name in `tests/test_handles.py` — `event-timing`'s `events/window` is the one known case (no editor; its table edits `events`).
  5. The report's figures are unchanged: `tests/test_figure_goldens.py` passes with the goldens' `git log` showing no commit in this sub-project.
  6. `dstf export-schema` differs from the pre-plan output only in the help strings Task 6 listed.
  7. A no-extra venv (`uv run --no-sync --no-project`-style check the earlier sub-projects used, or `python -c` in a venv without nicegui) imports `dstf.authoring`, `dstf.comparison.handles` and `dstf.authoring.builtin.edit_on_plot`.
- [ ] **Step 3: Real-library gate** (orchestrator only): (a) `uv run --no-sync dstf --config /mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json compare --work-dir testing_output/TRANSFORM/Dymola/linux --report` — 330 PASS + 1 SIM_FAIL unchanged. (b) `dstf author --no-browser` on the same config and work dir, driven by a Chromium scratch script (scratchpad; not named after a stdlib module; `PYTEST_CURRENT_TEST` stripped): select one PASS test; the `Edit on plot` tab is greyed with `no criterion on <var> has on-plot handles`; select its leaf, switch Edit criteria's mode to `tube`, set width mode `band` and a `tube_abs` (the form stages); the Edit on plot tab is now enabled; `Add row` in its `tube_points` table (stages; a ring appears in the centre figure — count the circles in `gd.layout.shapes` before and after); box-zoom; drag that ring; the pending count rises, the zoom is kept, the plot element is the same DOM node (`isSameNode` on a handle kept from before the drag); then `Discard`; terminate. Report what the feature DID on the real data: rings before/after, the drag's staged value, the zoom ranges before/after, the open time. The spec file's hash, the reference dir listing and the work dir listing are unchanged.
- [ ] **Step 4: Plan-versus-spec check** — a fresh-context agent reads §4.2 (handles, ids), §4.6 (all four amendment notes and the table), §4.7 "Edit criteria", §5 step 6, §6 (the handle-id sentence), §7.2 as amended, and this plan's owner rulings, against `git diff <first commit of this plan>^..HEAD`: missing, unasked, contradicted; told which text is a correction and which dirs are shared state it must not run against.
- [ ] **Step 5: Handoff** — what ran and what did not; **NOT run: a rerun through Dymola or OpenModelica from the tool, and Windows — owner-run, once (§6)**; carry-forwards (resizing tolerance boxes and windows on the plot; a drag moves both axes, so a vertical drag of a tube handle nudges its time by the hand's jitter; the report vendors plotly.js 2.35.0 and NiceGUI bundles 3.8.2; a structural Edit tree action leaves the selection's leaf path naming what is now a combinator). Nothing is pushed.
