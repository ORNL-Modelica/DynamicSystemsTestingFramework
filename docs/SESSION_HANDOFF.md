# Session handoff — post-Phase-6-MVP (reporter-as-IDE + baseline-role split + patch round-trip shipped)

**Date**: 2026-04-20

The Phase 6 MVP committed in this session — all seven steps from the retired
`docs/PHASE_6_PLAN.md` are in. Seven landing units across four commits
(payload budget, baseline-role split, 6.1.1 + #46, 6.1.2/3/5 + 6.1.4, 6.4,
and a snapshot-goldens fixup). Test count **404 → 531**. Authoritative
as-built record: **D67** in `docs/decisions.md`. The reporter is now the
primary authoring surface for acceptance criteria; the CLI is the execution
surface; round-trip through `spec-update` preserves hand-authored
`description` / `info` / `metadata` byte-compatibly.

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

### A. Finish half-shipped features (A-tier — close real debt from the MVP)

Both small-to-medium; together they make the MVP honestly complete.

1. **Window UI on auto-derived panels** — idea **#46** backend (parse + slice + score) shipped in 6.1.1, but there's no browser field to author windows. Users hand-write `"window": {"start": t, "end": t}` JSON. Add two number inputs to `reporting/ui/mode_controls.py`'s auto-derived renderer (universal; not per-mode). Or, richer: a range-brush on the trajectory plot. ~½ day scalar, ~1 day brush.
2. **Companion + soft_check overlay rendering (idea #50; 6.3 first slice)** — baseline-role data model shipped (companions + soft_checks on disk, CLI, validator), but the reporter only plots the primary. `companion add` is invisible to users today. Load CSV/JSON companion files (graceful degradation on missing), overlay them as Plotly traces, add a view-only multi-select picker in the plot-section header. Defaults: companions + soft_checks OFF; user toggles on. 1–2 days.

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

Largest remaining gaps by impact (post Phase-6-MVP):
1. **A-tier — half-shipped MVP pieces**: #46 window UI surfacing + #50 companion/soft_check overlay rendering. Closing these delivers what the MVP promised but didn't visually render.
2. **B-tier — user-facing new features**: Phase 7 rule-based recommender (onboarding leverage); idea #45 python-driven tests + FMU-path semantic-gap closure (unlocks real FMU work).
3. **C-tier — performance polish**: #47 dedup + #48 lazy-fetch (cap 1000 → 2000 + full-fidelity drill-down).
4. **D-tier — external distribution**: tool rename, blocked on a name.
5. **E-tier — foundational**: Phase 9 dataset types; unlocks new leaf families (Fréchet / ISO-18571 / KS-distribution / pyfunnel).

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

> Resuming ModelicaTesting post Phase-6-MVP. **Read D67 in `docs/decisions.md` first** for the as-built Phase 6 state; D66 is the design-intent record it realized. Test baseline: **531 passing** at HEAD. Two pieces of the MVP are half-shipped — backend done but UX missing — and closing them is this session's A-tier default.
>
> **Default — A-tier, finish the half-shipped MVP pieces** (bundle both; they share the per-variable template + JS wiring):
> 1. **Window UI on auto-derived panels (idea #46 UI surfacing)**. Backend already parses `LeafSpec.window_start/end` and `tree_eval._slice_window` applies before `mode.compare`, but the browser has no authoring field. Simplest version: extend `reporting/ui/mode_controls.py`'s renderer to append two number inputs (`window_start`, `window_end`) as a universal cross-mode suffix (not per-mode — window lives on `LeafSpec`, not on any `ModeConfig`). Stretch: a range-brush on the trajectory plot. Wire into `buildPatchData` so window edits round-trip through RFC 6902.
> 2. **Companion + soft_check overlay rendering (idea #50; 6.3 first slice)**. Reporter currently stores companion pointers on disk (`companions/ref_NNNN/<name>.json`) but never loads or renders them. Build `reporting/overlay_loader.py` (graceful CSV + JSON load, tolerates missing files), attach overlays to the per-variable template context, render as Plotly traces with a view-only multi-select picker. Companions + soft_checks default OFF to avoid clutter; user toggles on. This delivers the D66 baseline-role visual distinction the MVP skipped.
>
> **Alternatives if A-tier feels too small or too big**: Phase 7 rule-based recommender scaffolding (B-tier, ~1–2 weeks, new `recommender/` package); idea #45 python-driven tests + FMU-path closure (B-tier, ~2 weeks, shared `SimulationResult` dataclass); #47 time-array dedup (C-tier, ~1 day, payload optimization); drag-to-edit range handles (C-tier, ~½ day); tool rename (D-tier, small-but-touches-everything, **needs a name first**).
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types, ML, drag-to-edit beyond range, any mid-stream refactor of the existing six modes. `docs/PHASE_6_PLAN.md` is retired — refer to D67 for what shipped.

**Context to hand the agent on day 1**: `CLAUDE.md`, `docs/vision.md`, `docs/decisions.md` (especially D66 + D67), `docs/architecture.md`, `docs/ideas.md` (especially the Recommended-order footer and the #46 / #50 entries). Reporter QA: `docs/qa/reporter_checklist.md`.
