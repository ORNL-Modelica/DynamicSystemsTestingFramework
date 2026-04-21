# Session handoff — reporter-as-recursive-tree refactor (Stages 1–5 shipped)

**Date**: 2026-04-21

This session: **rebuilt the interactive reporter around a single recursive
`SpecNodeView` component**. One plot per unique variable, recursive tree
view below each plot, path-keyed JS state, +/− structural editing,
RFC 6902 wholesale `/metrics` replacement on structure change. Test
count **562 → 595** (+33). Static `comparison.html` + matplotlib PNG
generation retired along the way. **Follow-up required**: the rich
interactive tube editor (Shift+click/drag/right-click) was stripped and
needs to come back — user-flagged as critical. See memory:
`project_tube_editor_critical.md`.

## This session's changes — five-stage refactor

**Stage 1 — data helpers + registry shape** (pure Python, no UI yet):
- `tree_spec.collect_variables(spec)` + `leaves_for_variable(spec, var)`.
- `tree_spec.synthesize_implicit_tree(variables, *, variable_overrides)` — render-only synthesizer so flat-override tests feed the same recursive UI as tree-authored ones. Always wraps in AND to match `implicit_and_tree` path structure.
- `tree_spec.spec_to_view(spec, *, evaluation_by_path)` + `tree_eval.flatten_evaluation(result)` — serializer pair producing JSON-safe path-keyed dicts.
- `mode_controls.PlotContribution` dataclass + `plot_contribution` slot on `ModeUI`.
- `mode_controls.emit_mode_schemas()` bulk export for JS consumption.

**Stage 2 — recursive `SpecNodeView` + per-variable mounts**:
- One plot per unique variable (not per leaf). Trajectories deduped.
- Single recursive `renderNode(node, container, opts)` in JS; `renderLeaf` / `renderCombinator` dispatch. Per-variable views pass `variableFilter: varname`; combinator nodes whose descendants all filter out are skipped.
- Path-keyed `leafState = {path: {params, window, visible, original_*}}` replaces the per-leaf-index JS arrays.
- `MODE_PLOT_CONTRIBUTIONS` JS registry parallel to `MODE_SCORERS` of the old design — each mode returns `{traces, shapes, annotations, secondary_panel?}` consumed by the plot renderer. Current coverage: `range` (dashed lines), `tube` (polygon), `final-only` (vertical marker), window (x-band highlight). Stubs: `event-timing`, `dominant-frequency`, `nrmse`.
- Tube's `custom_renderer` retired; auto-derived schema-driven inputs now edit tube config.
- **Stripped** (per user's "reduce features" directive): rich Shift+click/drag tube editor, error-overlay dropdown (signed/abs/nrmse on plot), global tolerance slider, per-variable tolerance slider, mode-select dropdown (nrmse↔tube), per-plot error panels.

**Stage 3 — full-tree mount**:
- Top-of-report `<details>` collapsible mounts `SpecNodeView` with no variable filter — same component, different mount point.
- Cross-mount input sync: edits in the full-tree view propagate to the per-variable view via `syncSiblingInputs` (queries `[data-path=...][data-field=...]`, skips the active input to not stomp on focus).
- Retired `metric_tree_view` Jinja rendering.

**Stage 4 — structural patch ops**:
- `+` button on combinator nodes (opens a minimal prompt for metric + variable; adds a leaf child). `−` button on any non-root node. `WORKING_TREE` deep-cloned from `TREE_VIEW` at load; structural mutations set `structureDirty`.
- `nodeToSpec(node)` strips render artifacts to produce a clean spec dict.
- When `structureDirty`, `buildPatchData` emits a single `{op: "add", path: "/metrics", value: <new tree>}` rather than granular scalar ops. Unknown test-entry sibling keys (`description`, `metadata`) survive.
- Move (drag / up-down) not implemented — stretch polish.

**Stage 5 — cleanup**:
- Deleted static `comparison.html` template + its renderer (nothing linked to it from the index).
- Deleted matplotlib per-variable PNG generation loops and `_plot_variable` (~90 lines gone). Plotly inside interactive.html shows a superset of what the PNGs showed.
- Deleted the flat `variables` list construction in `_build_template_context` (~90 lines gone; tree_view carries all per-leaf data now).
- Deleted `diagnostic_plots` / `compared_plots` / `nobaseline_plots` context fields (PNG-path wrappers).
- Deleted `html as html_mod` import (unused after tube-cell custom-renderer removal).

Test count: **562 → 595** passing at HEAD.

---

The Phase 6 MVP committed in earlier sessions — all seven steps from
the retired `docs/PHASE_6_PLAN.md` are in. As of this session, the two
A-tier half-shipped follow-ons (window UI + overlay rendering) are
now complete too. Authoritative as-built record for the MVP itself:
**D67** in `docs/decisions.md` — add a short note for this session's
close-out when time permits. The reporter is now the primary authoring
surface for acceptance criteria and baseline-role context; the CLI is
the execution surface; round-trip through `spec-update` preserves
hand-authored `description` / `info` / `metadata` byte-compatibly.

---

## Where we are

**Phases 1–5, bundled PTA+4.E+4.C+4.B, D65, D66** — complete. D44–D66.
**Phase 6 MVP** — complete (this session). D67.
  - 6.0 payload budget (LTTB decimation in `interactive.html`; sidecar full-res).
  - Baseline-role split: primary / companion / soft_check as three distinct roles on disk, in CLI, in validator.
  - 6.1.1 auto-derive UI machinery + idea #46 time-windowed leaves (bundled per checkpoint criterion).
  - 6.1.2 / 6.1.3 / 6.1.5 JS scorer registry + per-mode panel wiring in the variable table.
  - 6.1.4 tube cell-link (custom_renderer) + range reference-line overlay.
  - 6.4 RFC 6902 JSON-Patch round-trip + validator in `spec-update` + `export-schema` CLI + QA markdown checklist.

### Current snapshot

- **Test count**: 531 passing (404 → 531 this session; +127 across the MVP).
- **Working backends**: Dymola (Python interface + batch `.mos`), FMPy (prebuilt FMUs).
- **MetricTree**: end-to-end; leaves scoped via optional `window: {start, end}` (idea #46).
- **Leaf metrics**: `nrmse`, `tube`, `final-only`, `range`, `event-timing`, `dominant-frequency`. Each mode has a typed Config dataclass with `Literal[...]` choices where applicable; the 6.1.1 auto-derive emits UI + JSON-Schema off the same shapes.
- **Combinators**: `and`, `or`, `k-of-n`, `warn`, `weighted` (direction-aware).
- **Baseline roles (D66/D67)**: three distinct on-disk partitions —
  - `ref_NNNN.json` — **primary** (unchanged; regression anchor).
  - `soft_checks/ref_NNNN/<name>.json` — **soft_check** (warn-wrapped scoring only; includes `dymola-via-fmpy` cross-backend output).
  - `companions/ref_NNNN/<name>.{json,csv}` — **companion** (plot-only overlay; never scored against).
- **Validator (D66)**: `comparison/validator.py` enforces — (a) ≥ 1 primary leaf outside warn; (b) soft_check leaves require warn ancestor; (c) companion-targeted leaves rejected; (d) unknown names rejected with hint. Run at parse time and inside `cmd_spec_update` before commit.
- **CLIs added this session**: `companion add/list/freeze/remove`, `soft-check list/remove`, `import-baseline`, `migrate-baselines`, `export-schema`, plus the RFC 6902 patch path in `spec-update`.
- **Reporter**: per-mode control panels auto-generated from Config dataclasses; live JS recompute for nrmse/tube/range/final-only (`MODE_SCORERS`); event-timing / dominant-frequency show a CLI-authoritative badge. Tube has a dedicated rich editor; range has horizontal reference-line overlay on the trajectory plot.
- **Export**: interactive HTML downloads `spec_patch.json` (RFC 6902 envelope). `cmd_spec_update` applies via read-modify-write preserving unknown keys; validator rejects invalid patches before writing.

### Payload budget

- **Current**: default `Config.max_embedded_samples = 1000`. A 50-var × 5000-sample fixture stays under the 5 MB ceiling. Full-resolution arrays live in `comparison_data.json` next to the HTML (Tier-2 artifact).
- **Follow-up knobs**: idea #47 (time-array dedup) will unlock the 2000 default at the same budget; idea #48 (lazy-fetch on zoom) restores in-place full fidelity; idea #49 adds per-test overrides.

### What the reporter does NOT do yet

- No drag-to-edit on range reference lines (v2 stretch — Plotly's `editable: shapePosition` config covers most of it).
- No window UI on auto-derived panels — `LeafSpec.window_*` parse + slice at eval time but there's no browser field yet. Candidate for 6.0.1 inclusion.
- No JS unit test framework / Playwright E2E (D66 Q8 — deferred unless reporter becomes a regression source).
- Legacy flat-dict `spec-update` format still accepted for one transition cycle.

---

## Candidate next moves

Reorganized 2026-04-20 by **what's half-shipped vs. new features vs. optimization vs. distribution blocker**, rather than by tier-of-effort. Two pieces of the MVP are partially delivered (backend shipped, UI or rendering missing) — those come first.

### A. Finish half-shipped features (A-tier — ✅ closed this session)

Both shipped; kept the historical entries below for reference. What's
still deferred from each:

1. **Window UI on auto-derived panels** — ✅ shipped as the
   standalone `render_window_controls_html` fragment + JSON-Pointer
   round-trip via `collect_leaf_paths` + `perVarWindows[]` + `add` /
   `remove` on `<leaf_path>/window`. Still deferred (not blocking):
   range-brush on the trajectory plot (visual window editing via Plotly
   selection — was noted as the stretch option).
2. **Companion + soft_check overlay rendering** — ✅ shipped as
   `reporting/overlay_loader.py` + per-plot picker + test-level
   summary. CSV + JSON overlay formats; graceful missing/invalid
   degradation; LTTB decimation integrated. Still deferred (not
   blocking): bulk toggle ("show all companions across all plots");
   overlay-vs-primary error overlay panel (an overlay analogue of the
   existing reference-error panels).

### B. User-facing new features (B-tier — each its own session)

3. **Phase 7 — rule-based recommender**. Input: signal + optional baseline. Output: MetricTree proposals. Bounded feature vocabulary in `recommender/features.py`; complexity budget per D66 (≥ 1 primary leaf, ≤ 3 leaves, ≤ 1 combinator layer). Each `ComparisonMode` declares `requires_baseline` + shape requirements so candidate modes filter automatically. Not runtime-load-bearing. No ML (Phase 8 removed). Lowers onboarding barrier for new users; less leverage for heavy existing users. ~1–2 weeks.
4. **Idea #45 python-driven tests (user-code backend) + FMU-path semantic-gap closure (D65 follow-on)**. Pair them because they share a `SimulationResult` dataclass refactor. `FmpyRunner` gains input-schedule support (`input=`), `fmi_type` selection (CS vs ME), `start_values` override, python-driver test shape; `CustomPythonRunner` slots into the same `SimulationResult` return contract, unlocking pyomo / scipy / custom-solver tests. High importance for real FMU work; low for pure Modelica-via-Dymola workflows. ~2 weeks.

### C. Performance / visual-fidelity follow-ups (C-tier — nobody's blocked)

5. **#47 time-array dedup (6.0.1)** — hoist shared `act_time` / `ref_time` out of per-variable trajectories into a `SHARED` object referenced by index; halves the HTML payload, lifts the default cap 1000 → 2000. Touches template JS at ~6 call sites. ~1 day.
6. **#48 lazy-fetch on zoom (6.0.2)** — hook `plotly_relayout` → fetch `comparison_data.json` slice for visible x-window → rerender at native fidelity. Works from `file://` URLs. Pure JS addition (~50 lines). Compounds with A1 window UI (same plot-interaction layer). ~½ day.
7. **#49 per-test `max_embedded_samples` override (6.0.3)** — escape hatch. Additive, ~30 lines across 3 files. Ship when someone asks.
8. **Drag-to-edit range reference lines** — Plotly's `editable: {shapePosition: true}`; sync shape coords back into the panel inputs on `plotly_relayout`. ~½ day.

### D. External-distribution blocker (D-tier — standalone; blocked on a name)

9. **Tool rename** — `"ModelicaTesting"` → neutral name. Touches package, CLI prog, HTML titles, `pyproject.toml`, all imports. Technical scope small; decision blocker is the name. Vision.md's own condition ("once the multi-backend abstraction stabilizes") is met — Dymola + FMPy both real, `SimulatorRunner` ABC locked. Any time before external distribution.

### E. Foundational / new leaves (E-tier — additive)

10. **Phase 9 — dataset types beyond `TIME_SERIES`**. `Events`, `Spectrum`, `Distribution`, `Scalars`, `Field`. Unlocks #23 Fréchet, #24 spectral coherence, #25 x-tolerance / pyfunnel, #26 ISO 18571 as leaf metrics. Reordered after Phase 6 gave us a shape-aware render contract.
11. **Additional leaf metrics** once their dataset type exists — each ~1–2 days.
12. **Smaller HTML polish** — see `docs/ideas.md` entries #7, #9, #14, #17, #18, #19, #21.
13. **Larger data-mining** — #11 test-discovery helper, #12 model-health analysis.

---

## External-consumer migration notes

- **Phase 6 MVP** changes are additive for `testing.json` consumers. `test_spec.json` gained the optional `"window"` field on leaves and `"patch"` envelope support in `spec-update`; both are opt-in.
- **Baseline-role split** has one hard break for anyone with legacy flat-named-baselines in their ref files: run `modelica-testing migrate-baselines --apply` once. Applied in this session to `examples/fmu/ReferenceResults/FMPy/linux/ref_0001.json` (BouncingBall `experiment`).
- **`add_named_baseline` removed**. Anyone calling it directly from Python tooling now calls `add_soft_check` with the same args.
- **TRANSFORM** (`D:\Modelica\TRANSFORM-UnitTests\ReferenceResults`) — not yet exercised post-MVP; changes are additive except for the migration requirement.

---

## Architecture status vs. vision

| Layer | Status |
|---|---|
| Source | 🟢 modelica + fmu concrete. |
| Discovery | 🟢 Pluggable Recognizer registry (PTA) + all match types / field sources. |
| Backend | 🟢 Dymola + FMPy; FMU_EXPORT capability real on Dymola; cross-backend chain stores output as soft_check (experimental per D65). |
| Dataset | 🟡 Only `TIME_SERIES` produced. Phase 9. |
| Metric | 🟢 6 concrete modes; per-mode Config tightened with `Literal[...]` choices; 6.1.1 auto-derive emits UI + JSON-Schema off the same shapes. |
| MetricTree | 🟢 5 combinators + `window` on every leaf (idea #46); D66 validator enforces baseline-role rules at parse + patch-apply time. |
| Reference | 🟢 Three-role storage split: primary + soft_checks + companions. CRUD CLIs. `migrate-baselines` one-off. |
| Reporter | 🟢 Interactive HTML has per-mode control panels, live pass/fail recompute for four simple modes, CLI-authoritative badge for two numerical modes, range reference-line overlay, tube rich editor, RFC 6902 patch download. 6.3 multi-baseline picker + companion-overlay rendering still pending. |
| Recommender | ⚪ Not started. Phase 7, rule-based only. |

Largest remaining gaps by impact (post A-tier close-out):
1. **B-tier — user-facing new features**: Phase 7 rule-based recommender (onboarding leverage); idea #45 python-driven tests + FMU-path semantic-gap closure (unlocks real FMU work).
2. **C-tier — performance polish**: #47 dedup + #48 lazy-fetch (cap 1000 → 2000 + full-fidelity drill-down).
3. **D-tier — external distribution**: tool rename, blocked on a name.
4. **E-tier — foundational**: Phase 9 dataset types; unlocks new leaf families (Fréchet / ISO-18571 / KS-distribution / pyfunnel).

---

## Key files, fast reference

### Phase 6 MVP landmarks (this session)

- `src/modelica_testing/reporting/decimate.py` — LTTB (6.0).
- `src/modelica_testing/reporting/ui/mode_controls.py` — auto-derive machinery, registry, tube custom_renderer (6.1.1 / 6.1.4).
- `src/modelica_testing/reporting/schema_export.py` — JSON-Schema emission (6.4.5).
- `src/modelica_testing/reporting/templates/interactive.html` — `MODE_SCORERS`, `wireModeControls`, `applyRangeOverlay`, `buildPatchData` (6.1.2/3/5 + 6.1.4 + 6.4.2).
- `src/modelica_testing/reporting/plot_comparison.py` — `_extract_mode_values`, `_render_mode_controls` (6.1.5).
- `src/modelica_testing/storage/reference_store.py` — `Companion` + soft_check/companion CRUD (baseline-role split).
- `src/modelica_testing/comparison/validator.py` — D66 role rules.
- `src/modelica_testing/comparison/tree_spec.py` + `tree_eval.py` — `window_start/end` + `_slice_window` (idea #46).
- `src/modelica_testing/discovery/patch_apply.py` — RFC 6902 applier with whitelist (6.4.1).
- `src/modelica_testing/cli.py` — `cmd_spec_update` dispatch, `cmd_export_schema`, `cmd_companion`, `cmd_soft_check`, `cmd_import_baseline`, `cmd_migrate_baselines`.
- `tests/test_mode_controls.py`, `tests/test_window.py`, `tests/test_baseline_roles.py`, `tests/test_validator.py`, `tests/test_migration.py`, `tests/test_patch_apply.py`, `tests/test_spec_update_cli.py`, `tests/test_export_schema.py`, `tests/test_interactive_html_snapshot.py` + `tests/golden/`.
- `docs/qa/reporter_checklist.md` — manual pre-release QA.
- `docs/decisions.md` — **D67** as-built record.

### Pre-existing (unchanged in spirit, still authoritative)

- `src/modelica_testing/comparison/modes.py` — six modes + typed Configs (Literal tightening applied).
- `src/modelica_testing/comparison/metric_tree.py` — combinators incl. `WeightedCombinator`.
- `src/modelica_testing/comparison/comparator.py` — `compare_test` (store-threaded for soft_checks); `_compare_range` stashes bounds in diagnostics.
- `src/modelica_testing/simulators/cross_backend.py` — writes soft_checks now.
- `src/modelica_testing/simulators/base.py` — `SimulatorRunner` ABC; `SimulationResult`-shape refactor still pending for idea #45.

---

## Pre-session sanity checklist

```bash
# Full test suite — expect 531 passed at HEAD (post Phase 6 MVP).
uv run pytest -q

# FMU end-to-end — BouncingBall exercises range + warn-wrapped soft_check + nrmse.
uv run modelica-testing --config examples/fmu/testing.json run

# Reporter smoke (generates HTML; opens a browser if one's available).
uv run modelica-testing --config examples/fmu/testing.json run --report

# JSON-Schema export (new this session).
uv run modelica-testing export-schema | head -30

# Baseline-role CLIs.
uv run modelica-testing --config examples/fmu/testing.json soft-check list BouncingBall
uv run modelica-testing --config examples/fmu/testing.json companion list

# Repo status
git status
```

If fmpy is missing: `uv pip install -e ".[dev,fmpy]"`.

---

## Starter prompt for the next session

> Resuming ModelicaTesting post A-tier MVP close-out. **Read D67 in `docs/decisions.md` first** for the as-built Phase 6 state; D66 is the design-intent record it realized. The A-tier half-shipped pieces (window UI on auto-derived panels; companion + soft_check overlay rendering) both landed in the previous session — 6.1.1 idea #46 and idea #50 / 6.3-first-slice are now visually complete. Test baseline: **562 passing** at HEAD.
>
> **Default — B-tier, pick one user-facing feature for a dedicated session**:
> 1. **Phase 7 — rule-based recommender** (~1–2 weeks). New `recommender/` package. Input: signal + optional baseline. Output: MetricTree proposals bounded by D66's complexity budget (≥1 primary leaf, ≤3 leaves, ≤1 combinator layer). Each `ComparisonMode` declares `requires_baseline` + shape requirements so candidate filtering is automatic. No ML (Phase 8 removed). Lowers onboarding barrier for new users.
> 2. **Idea #45 python-driven tests + FMU-path semantic-gap closure** (~2 weeks). Pair them — they share a `SimulationResult` dataclass refactor. `FmpyRunner` gains input-schedule support (`input=`), `fmi_type` selection (CS vs ME), `start_values` override, python-driver test shape. `CustomPythonRunner` slots into the same return contract, unlocking pyomo / scipy / custom-solver tests. High leverage for real FMU work.
>
> **Alternatives if a full B-tier session is too much**: #47 time-array dedup (C-tier, ~1 day, unlocks default cap 1000→2000); #48 lazy-fetch on zoom (C-tier, ~½ day, pure JS, compounds with existing Plotly handlers); drag-to-edit range handles (C-tier, ~½ day); tool rename (D-tier, small-but-touches-everything, **still needs a name first**).
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types, ML, any mid-stream refactor of the existing six modes. `docs/PHASE_6_PLAN.md` is retired — refer to D67 for what shipped.

**Context to hand the agent on day 1**: `CLAUDE.md`, `docs/vision.md`, `docs/decisions.md` (especially D66 + D67), `docs/architecture.md`, `docs/ideas.md` (especially the Recommended-order footer and the #45 / #47 / #48 entries). Reporter QA: `docs/qa/reporter_checklist.md`.
