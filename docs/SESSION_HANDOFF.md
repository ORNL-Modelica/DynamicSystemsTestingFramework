# Session handoff — OpenModelica backend + sibling-backend overlays

**Date**: 2026-04-22

This session shipped the first Modelica-source backend beyond Dymola
and wired a cross-backend visual-check path into the reporter. The
interactive reporter architecture described below is unchanged from
the previous session's "reporter feature-complete" shipment
(2026-04-21) — this handoff is primarily about the new backend and
the production-library validation against TRANSFORM.

**Current test count: 686 passing** (637 baseline → 679 after OM
backend → 686 after sibling-backend overlays). Pytest ~90 s with
Playwright, ~30 s without.

---

## What shipped this session

1. **OpenModelica backend** (D69, commits `101d0a8` … `9d03752`).
   Third `SimulatorRunner` alongside Dymola + FMPy, using `omc` as a
   subprocess driven by generated `.mos` scripts (analogous to
   Dymola's batch fallback; persistent-worker + FMU export deferred).
   `mat_reader` hoisted from `simulators/dymola/` to
   `simulators/common/` (OM's `.mat` is DSresult-compatible by
   design). Phase timings parsed from the REPL-echoed
   `record SimulationResult … end SimulationResult;` block — the
   `.mos` emits a bare `simulate(...)` and lets the record echo ride.
   `variableFilter` regex anchored `^(...)$`, escapes per-name,
   expands globs, includes `unitTests.x[i]` + diagnostics. MSL is
   auto-injected into dependencies so an empty-deps `testing.json`
   works across both backends. Latent `mat_reader` transpose bug
   fixed (wrong heuristic only surfaced for MATs with ≤ 4 vars; all
   Dymola fixtures exceed that, OM's tight filter exposed it).

2. **Unified `testing.json` with auto-detect**. New
   `_auto_detect_simulator` + `_looks_like_path` in `config.py`
   pick the first simulator whose binary resolves on the current
   machine. Pinned OS-specific absolute paths (`C:\Program Files\…`
   detected via regex — PosixPath.is_absolute() misses drive
   letters on Linux) block the PATH-fallback to prevent a stray
   `dymola` symlink on Linux being treated as equivalent to a
   Windows install. `BACKEND_BINARY_NAMES` map replaces the old
   `shutil.which(backend.lower())` fallback (OM's binary is `omc`,
   not `openmodelica`; FMPy has no binary). CLI `--simulator` and
   explicit `"simulator"` key still override.

3. **Sibling-backend auto-companion overlays** (commit `d3d6cfb`).
   When a new simulator has simulated but not been baselined,
   `load_overlays(store, model_id, config=config)` auto-scans
   `<reference_root>/<other_backend>/<os>/ref_*.json` for the same
   `model_id` and surfaces each match as a
   `kind="sibling-backend"` companion overlay (blue dashed line in
   the plot, togglable via the per-plot overlay picker). Zero
   persistence — discovery happens at report time. Lets the user
   eyeball "does OM agree with Dymola for this test?" before
   accepting baselines. `_sibling_backend_index` is `lru_cache`-d
   so a 326-test report doesn't rescan per test.

4. **TRANSFORM validation sweep**. First real production-library
   exercise of the post-refactor pipeline: 326 discovered tests →
   **235 pass / 89 fail / 2 timeout** (72% pass rate) via
   OpenModelica on Linux in ~2.3h wall-time at parallel=4. Failure
   taxonomy (sorted by frequency):

   | n | category | notes |
   |---|---|---|
   | 46 | missing `each` on array-param modifications | OM strict; Dymola permissive. Library-side portability fix, one-line each. |
   | 15 | ASUB non-scalar codegen (interpolation tables) | OM codegen bug. |
   |  7 | `SDF.NDTable not found` | needs `installPackage(SDF)`. |
   |  3 | MSL-Fluid equation imbalance | OM's MSL-Fluid gaps. |
   |  3 | equation-state count mismatch | OM index reduction on Fluid topologies. |
   |  3 | `spatialDistribution` variability inference | OM stricter than Dymola. |
   |  2 | C-compiler (clang) error | likely OM bug. |
   |  2 | Pantelides structural singularity | OM bug or real model issue. |
   | ~8 | single-case issues | composite-name lookup, NFScalarize, etc. |

   All 91 failures are OM-vs-Dymola portability / OM limitations —
   **zero framework bugs**. The 235 OM baselines + unified
   `testing.json` were committed to the TRANSFORM repo by the user
   after visual review of the sibling-backend overlays.

### Commits (chronological)

| commit | subject |
|---|---|
| `101d0a8` | docs: OpenModelica backend design spec |
| `8d31e37` | docs: OpenModelica backend implementation plan |
| `6895d43` | refactor: hoist mat_reader from dymola/ to simulators/common/ |
| `296f0c3` | feat(openmodelica): .mos script generator + unit tests |
| `5f99d91` | feat(openmodelica): stdout parser + captured fixture |
| `e312e45` | feat(openmodelica): OpenModelicaRunner + OpenModelicaConfig |
| `d7875c3` | test(openmodelica): real-omc integration tests + MAT fixture |
| `863c1e1` | feat(examples): ModelicaTestingLib baselines for OpenModelica/linux |
| `9d03752` | docs: record OpenModelica backend (D69) |
| `d3d6cfb` | feat(reporter): auto-discover sibling-backend companions |

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

- **Persistent-worker OpenModelica via OMPython / `OMCSessionZMQ`.**
  Natural D69 follow-up. TRANSFORM sweep showed per-test compile
  dominates at ~50–100s/test; a long-lived omc session per worker
  would load MSL + TRANSFORM once, give fine-grained `translating`
  /`simulating` phase labels, and cut wall-time 5–10×. Mirrors
  Dymola's `persistent_runner` → batch-fallback split — the shape
  is already proven in this repo. ~1–2 days.

- **TRANSFORM upstream portability fixes.** The 2026-04-22 sweep
  surfaced 46 `each`-modifier misuses that are legitimate TRANSFORM
  portability bugs (Dymola silently accepts, OM rejects). Land them
  as a PR to TRANSFORM-Library; would likely bump OM pass rate from
  72% to ~85%. The taxonomy at the top of this handoff lists the
  classification. Separate: `installPackage(SDF)` on the machine +
  adding `SDF` to TRANSFORM's deps would recover another 7 tests.
  Outside this repo's scope but low-effort + high-signal.

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

### C-tier (performance / polish, nobody's blocked)

- `#47` time-array dedup (cap 1000 → 2000 at same budget). ~1 day.
- `#48` lazy-fetch full-res on zoom. ~½ day.
- `#49` per-test `max_embedded_samples` override. ~30 min.
- Visual-regression Playwright screenshots.
- **OM FMU export via `buildModelFMU`** — D69 deferred. Would wire
  OM into the `Capability.FMU_EXPORT` cross-backend chain (currently
  Dymola-only + experimental per D65). ~1–2 days.
- **`check-openmodelica` CLI subcommand** (peer of `check-dymola`).
  Trivial. Verifies omc + MSL + SDF(?) availability and prints
  versions. ~½ day.

### D-tier (external distribution)

- Tool rename to a simulator-neutral name. Technical scope small;
  blocked on picking a name. Now more justified — three backends,
  no Modelica coupling in the core pipeline.

### E-tier (foundational)

- Phase 9 dataset types (`Events`, `Spectrum`, `Distribution`,
  `Scalars`, `Field`) unlocking Fréchet / spectral coherence /
  pyfunnel x-tolerance / ISO 18571 leaf metrics.

---

## Starter prompt for the next session

> Resuming ModelicaTesting. OpenModelica backend shipped
> (D69, commits `101d0a8` … `9d03752`) — third `SimulatorRunner`
> alongside Dymola + FMPy, omc subprocess + `.mos` analogous to
> Dymola's batch fallback, unified `testing.json` with auto-detect
> by current OS + binary availability (Dymola on Windows,
> OpenModelica on Linux, same config file). Sibling-backend
> auto-companion overlays (`d3d6cfb`) — at report time the reporter
> auto-discovers peer-backend references and renders them as
> visual-only blue-dashed overlays for pre-accept cross-check.
> TRANSFORM validation sweep: 235/326 pass (72%), failures fully
> classified (see `docs/SESSION_HANDOFF.md`), baselines committed
> to TRANSFORM repo. **Test baseline: 686 passing at HEAD** (~90 s
> with Playwright, ~30 s without).
>
> **Read first**: `docs/SESSION_HANDOFF.md` (this file; top-of-file
> "What shipped this session" is the current snapshot), D69 in
> `docs/decisions.md` (OM backend scope + rationale + deferred).
>
> **Default next move — pick one**:
>
> 1. **Persistent-worker OM via OMPython** (B-tier, ~1–2 days).
>    Follow-up explicitly deferred in D69. TRANSFORM sweep showed
>    compile dominates at ~50–100s/test; persistent omc sessions
>    cut wall-time 5–10× and enable fine-grained phase labels.
>    Same pattern as Dymola's `persistent_runner` → batch split.
>
> 2. **TRANSFORM upstream portability PR** (B-tier, ~1 day).
>    Land the 46 `each`-modifier fixes against TRANSFORM-Library
>    to raise OM pass rate from 72% → ~85%. Category list in this
>    handoff's failure taxonomy.
>
> 3. **Phase 7 rule-based recommender** (B-tier, ~1–2 weeks full,
>    ~2–3 days skeleton). New `recommender/` package. Lowers
>    onboarding barrier for new users. MetricTree proposals bounded
>    by D66's complexity budget.
>
> 4. **Wrap-in-combinator + combinator-kind editing** (~1 day).
>    Finishes the structural-editing UX in the reporter.
>
> **Smaller alternatives**: `#47` time-array dedup (C-tier, ~1 day);
> `check-openmodelica` CLI subcommand (C-tier, ~½ day); OM FMU
> export via `buildModelFMU` (C-tier, ~1–2 days).
>
> **Dev-env prereqs** (if venv was recreated): `uv pip install -e
> ".[dev,fmpy]"` + `uv pip install pytest-playwright --index-url
> https://mirrors.aliyun.com/pypi/simple/` + `uv run playwright
> install chromium` (one-time). OpenModelica: `apt install
> openmodelica` + `omc -e 'updatePackageIndex(); installPackage
> (Modelica); getErrorString();'` once per machine.
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types,
> ML, any mid-stream refactor of the existing six modes.
> `docs/PHASE_6_PLAN.md` retired — refer to D67 for what shipped;
> `docs/PHASE_2_5_CI_PLAN.md` still valid when the repo goes public.
