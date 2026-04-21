# Session handoff — interactive reporter complete

**Date**: 2026-04-21

The interactive reporter is now feature-complete for the Phase 6 MVP
authoring-surface story. Over the last run of sessions the reporter
was restructured around a single recursive `SpecNodeView` component
(Stage 1–5 refactor), then gained three interactive plot editors (tube,
range drag, window brush) on a unified `MODE_PLOT_EDITORS` contract,
then got Playwright browser-driven tests, then went through a
lifecycle-sync-legend-error-overlay polish pass, then a final
tube-editor v2 reimplementation with proper zoom preservation + smooth
drag + inline remove-confirm.

**Current test count: 637 passing** (pytest suite ~90 s with Playwright,
~20 s without). Baseline at session start was 562.

---

## Current architecture snapshot

### Data model

- `SpecNode` (tree_spec.py) — the authored tree. Leaves carry
  `{metric, variable, params, against, window}`; combinators carry
  `{combinator, children, k?, weights?, threshold?, direction?}`.
- `MetricResult` (metric_tree.py) — the evaluated tree; result of
  `evaluate_spec`. Carries pass/fail + score per node.
- `leafState[path]` (JS runtime) — path-keyed `{params, window, visible,
  original_*}`. Single source of truth for user edits; every handler
  mutates here, every render reads here.
- `WORKING_TREE` (JS runtime) — deep-cloned `SpecNode` tree mutated by
  structural edits (+ / −). `structureDirty` flag gates wholesale
  `/metrics` patch emission.

### Serializers

- `tree_spec.spec_to_view(spec, *, evaluation_by_path)` → JSON-safe
  nested dict with JSON-Pointer paths at every node, merged with
  evaluation results.
- `tree_eval.flatten_evaluation(result)` → `{path: {passed, score,
  label, ...}}`, same pointer space as spec_to_view.
- `mode_controls.emit_mode_schemas()` → per-mode schema dicts for JS
  `renderModeControlsHtmlJs` to walk at runtime.

### UI components (JS, in `interactive.js`)

- **`SpecNodeView`** — recursive `renderNode` / `renderLeaf` /
  `renderCombinator`. Mounts at top-of-report (full tree) and below
  each per-variable plot (filtered by variable). Click a leaf header →
  `activateLeaf`; ESC or click another leaf → deactivates.
- **`activateLeaf` / `deactivateLeaf`** — unified lifecycle:
  1. clear `.node-editor` slots,
  2. inject universal `🔲 Set window from plot` button,
  3. invoke the metric's `MODE_PLOT_EDITORS[metric].activate` if registered.
  Editors only own event-handler cleanup; DOM clearing is core's job.
- **`MODE_PLOT_EDITORS`** — registry keyed by metric name. Three
  implementations:
  - `tube` — control-point table + Shift+click add, Shift+drag move,
    Shift+right-click remove; width-mode (rel/band/absolute), sync /
    unsync with per-point per-side modes, interpolation (linear/step),
    min-width floor, live pass/fail.
  - `range` — min/max dashed lines draggable via Plotly's
    `edits.shapePosition`; drop → `plotly_relayout` → state.
  - Window brush (universal, not per-metric) — `🔲 Set window from plot`
    button arms `dragmode: 'select', selectdirection: 'h'`.
- **`MODE_PLOT_CONTRIBUTIONS`** — static plot artifacts per mode
  (polygon, shapes, markers). For tube, delegates to the editor's
  `_resolveAllBoundsOnGrid` so the polygon visual matches the editor's
  computed bounds exactly.
- **`MODE_SCORERS`** — live pass/fail recompute for nrmse, final-only,
  range, tube. Event-timing + dominant-frequency stay
  CLI-authoritative (FFT / event detection not reimplemented JS-side).
- **Error overlay dropdown** — per-plot select (none / signed / abs /
  NRMSE). Adds / removes a single right-axis error trace. State is the
  DOM select's `.value` — no parallel JS state to desync.
- **Add-leaf modal** — inline modal with metric dropdown + variable
  `<input list>` + `<datalist>` of tracked variables. Browser
  auto-filters as user types; glob chars (`*`, `?`) accepted; unknown
  variable names emit a non-blocking warning. Preset variable (from
  per-variable mount's `+`) makes the input `readonly`.
- **Remove-confirm popup** — inline amber popup with "Confirm" / "Cancel"
  buttons (not `window.confirm`). Click-away / ESC cancels.

### Zoom preservation

- `Plotly.react` replaces `Plotly.newPlot` after first paint
  (`el._mt_plotted` marker).
- `uirevision: 'keep'` on xaxis, yaxis, and top-level layout —
  preserves user zoom/pan across re-renders (without it, Plotly
  reapplies default `autorange: true` on every data change).
- `edits.shapePosition` always-on in `PLOT_CFG` so range drag doesn't
  require a config swap.

### Dev-env prereqs (one-time per machine)

```bash
uv pip install -e ".[dev,fmpy]"
uv pip install pytest-playwright
uv run playwright install chromium
```

Both reachable through the aliyun pypi mirror (`--index-url
https://mirrors.aliyun.com/pypi/simple/`) when the user's network has
the Fastly block active. Chromium downloads from `cdn.playwright.dev`
(Azure, not Fastly).

---

## What's tested

- **Python unit tests** — 561 (tree_spec, tree_eval, comparator,
  overlay_loader, mode_controls, patch_apply, validator,
  reference_store, discovery recognizers, etc.).
- **HTML structural hash** — 6 golden hashes per mode + substring
  presence checks on `interactive.js` + `interactive.html`.
- **Playwright** — 35 browser-driven tests covering: per-variable
  dedup, full-tree mount, per-variable filtering, leaf activation /
  deactivation (ESC + click-away), cross-mount input sync, `+` modal
  (select + datalist + cancel + glob), inline remove-confirm (Confirm
  + Cancel + ESC), wholesale `/metrics` replace on structural edit,
  live pass/fail recompute (AND combinator bubble-up, warn immunity),
  tube editor (auto-seed, add-point, width-mode reprojection, sync /
  unsync columns, Shift+click, Shift+right-click remove, cross-mount
  refresh), range drag, window brush injection, error overlay add /
  remove, tube markers not in legend, no brush-button stacking on
  re-activation.

Run with:

```bash
uv run pytest -q                       # full suite, ~90s
uv run pytest --deselect tests/test_interactive_playwright.py -q   # fast, ~20s
uv run pytest tests/test_interactive_playwright.py -q              # just browser, ~75s
```

---

## Known limitations (deferred by design; do not file as bugs)

1. **Event-timing + dominant-frequency are CLI-authoritative.** Live
   recompute not implemented for these two (no JS-side FFT / event
   detection). Badge shows on those leaves so users know.
2. **No wrap-in-combinator** from the `+` button. Click `+` on a
   combinator only adds a leaf child; to wrap an existing leaf in
   a new combinator (e.g., `warn`), edit the JSON directly.
3. **No combinator-kind editing** from the UI. Change and↔or↔warn in
   the JSON spec.
4. **Window brush is one-shot per activation.** Click button, drag
   once, brush exits. No "keep selecting" mode.
5. **Range drag on extreme values** — if min/max is set outside the
   plot's autoscale range the dashed line renders off-screen. Plotly
   shape autoscale doesn't include layout shapes.
6. **Visual regression** not automated. Playwright covers behavior;
   color / layout regressions need an eyeball pass.

---

## Reporter QA checklist

`docs/qa/reporter_checklist.md` covers the ~20% that requires human
judgment. Most of the old manual checklist is now Playwright-automated;
what remains is visual quality, drag feel on real data, color
consistency, and the structural-editing UX items noted above.

---

## Pre-session sanity

```bash
uv run pytest -q                                                    # expect 637 passed
uv run modelica-testing --config examples/fmu/testing.json run --report  # BouncingBall smoke
git status                                                          # confirm clean tree
```

If pytest-playwright or fmpy is missing, reinstall via aliyun mirror
as noted above.

---

## Candidate next moves

### B-tier (user-facing features)

- **Phase 7 — rule-based recommender.** New `recommender/` package.
  Input: signal + optional baseline. Output: MetricTree proposals
  bounded by D66's complexity budget (≥ 1 primary leaf, ≤ 3 leaves,
  ≤ 1 combinator layer). Each `ComparisonMode` declares
  `requires_baseline` + shape requirements so candidate filtering is
  automatic. No ML (Phase 8 removed). Lowers onboarding barrier.
  ~1–2 weeks for full, ~2–3 days for a credible skeleton.
- **Idea #45 python-driven tests + FMU-path semantic-gap closure.**
  Pair them — shared `SimulationResult` dataclass refactor.
  `FmpyRunner` gains input-schedule support, `fmi_type` selection
  (CS vs ME), `start_values` override, python-driver test shape.
  `CustomPythonRunner` slots into the same return contract. ~2 weeks.
- **Wrap-in-combinator + combinator-kind editing** (finishes the
  structural-editing UX).
- **TRANSFORM end-to-end sweep** — exercise the post-refactor pipeline
  against a real production library. Migration: `migrate-baselines
  --apply` once, then `run --report`.

### C-tier (performance / polish, nobody's blocked)

- `#47` time-array dedup (cap 1000 → 2000 at same budget). ~1 day.
- `#48` lazy-fetch full-res on zoom. ~½ day.
- `#49` per-test `max_embedded_samples` override. ~30 min.
- Visual-regression Playwright screenshots.

### D-tier (external distribution)

- Tool rename to a simulator-neutral name. Technical scope small;
  blocked on picking a name.

### E-tier (foundational)

- Phase 9 dataset types (`Events`, `Spectrum`, `Distribution`,
  `Scalars`, `Field`) unlocking Fréchet / spectral coherence /
  pyfunnel x-tolerance / ISO 18571 leaf metrics.

---

## Starter prompt for the next session

> Resuming ModelicaTesting. Interactive reporter is feature-complete:
> recursive `SpecNodeView`, three interactive plot editors (tube +
> range drag + window brush) via `MODE_PLOT_EDITORS`, Playwright
> coverage, inline remove-confirm, error overlay dropdown, uirevision
> zoom preservation. **Test baseline: 637 passing at HEAD** (~90 s with
> Playwright, ~20 s without).
>
> **Read first**: `docs/SESSION_HANDOFF.md` (this file; current
> architecture snapshot), D67 in `docs/decisions.md` (Phase 6 MVP
> as-built), `docs/qa/reporter_checklist.md` (what's manual vs
> automated).
>
> **Default next move — pick one**:
>
> 1. **Phase 7 rule-based recommender** (B-tier, ~1–2 weeks, or ~2–3
>    days for a skeleton). New `recommender/` package. Lowers
>    onboarding barrier for new users.
>
> 2. **Idea #45 python-driven tests + FMU-path semantic-gap closure**
>    (B-tier, ~2 weeks). Shared `SimulationResult` dataclass refactor
>    unlocks pyomo / scipy / custom-solver tests.
>
> 3. **Wrap-in-combinator + combinator-kind editing** (finishes the
>    structural-editing UX — UI equivalent of manually wrapping a leaf
>    in a `warn` combinator or changing an `and` to an `or`). Small to
>    medium, probably 1 day.
>
> 4. **TRANSFORM end-to-end sweep** against
>    `D:\Modelica\TRANSFORM-UnitTests\ReferenceResults`. First real
>    post-refactor sanity run against a production Modelica library.
>    Migration: `migrate-baselines --apply` once. Low-risk; mostly an
>    integration test.
>
> **Smaller alternatives**: `#47` time-array dedup (C-tier, ~1 day);
> `#48` lazy-fetch on zoom (C-tier, ~½ day); drag-to-edit range handles
> (C-tier, ~½ day); visual-regression Playwright screenshots (C-tier).
>
> **Dev-env prereqs** (if venv was recreated): `uv pip install -e
> ".[dev,fmpy]"` + `uv pip install pytest-playwright --index-url
> https://mirrors.aliyun.com/pypi/simple/` + `uv run playwright install
> chromium` (one-time).
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types,
> ML, any mid-stream refactor of the existing six modes. `docs/
> PHASE_6_PLAN.md` is retired — refer to D67 for what shipped.
