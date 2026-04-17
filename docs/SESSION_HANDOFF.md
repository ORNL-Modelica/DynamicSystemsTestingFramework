# Session handoff — post-D66 (Phase 6-9 design grill landed; reporter-as-IDE committed)

**Date**: 2026-04-17

A bundled session executed five originally-separate moves: PTA follow-ups
(folder filter, match composition, class-name-glob, annotation source), 4.E
(weighted combinator), 4.C (event-timing + dominant-frequency leaf metrics),
4.B (cross-backend Dymola → FMPy chain), and interactive-HTML genericization
for non-NRMSE leaves. Tool rename remains deferred.

Follow-on (D65 — 2026-04-17): grilled the D63 deferred-validation caveat and
scoped the FMU pathway honestly. Cross-backend chain flagged **experimental**
with a runtime warning; FMPy primary gained a **Limitations** docstring (not
a status reversal — it remains validated for autonomous reference FMUs); new
`scripts/smoke_test_dymola_export.py` exists for the user to run on Windows
to lock `translateModelFMU` signature + FMI license + cwd. Real end-to-end
Dymola validation AND chain generalization (input drivers, CS/ME choice,
start-value overrides, python-driver tests) both deferred to a future
"FMU-path semantic gap closure" phase.

---

## Where we are

**Phase 1** (foundation abstractions) — complete. D44–D47.
**Phase 2** (FMPy backend) — 2.1–2.4 complete, 2.5 deferred ([PHASE_2_5_CI_PLAN.md](PHASE_2_5_CI_PLAN.md)). D48–D50.
**Phase 3** (MetricTree wiring) — complete. D51–D53.
**Cleanup pass** — complete. D54–D55.
**Phase 4.A** (multi-baseline leaves) — complete. D56.
**Phase 4.D** (rename sweep + cleanup follow-ups) — complete. D57–D58.
**Phase 5 / PTA** (pluggable test annotations) — complete. D59.
**Bundled session** (PTA follow-ups + 4.E + 4.C + 4.B + interactive HTML) — complete. D60–D64.
**FMU-pathway scope + cross-backend experimental labeling** — complete. D65. Smoke test passed on Dymola 2026x (2026-04-17).
**Phase 6-9 design grill (docs-only)** — complete. D66. Identity locked, baseline roles split into primary/companion/soft_check, reporter-as-IDE committed, Phase 8 removed, recommender contained.

### Current snapshot

- **Test count**: 404 passing (358 → 404; +46 from this bundled session).
- **Working backends**: Dymola (Python interface + batch `.mos`), FMPy (prebuilt FMUs).
- **MetricTree**: end-to-end. Users author trees in `test_spec.json` `"metrics"` block.
- **Leaf metrics**: `nrmse`, `tube`, `final-only`, `range`, `event-timing` (D62), `dominant-frequency` (D62).
- **Combinators**: `and`, `or`, `k-of-n`, `warn`, `weighted` (D61, direction-aware).
- **Multi-baseline**: `"against": "<name>"` on leaves; chains can produce baselines (D63).
- **Cross-backend chain**: `dymola-via-fmpy` — primary backend exports FMU → FMPy simulates → named baseline written. **EXPERIMENTAL** (D65): scoped to autonomous FMU-exportable tests; end-to-end validation still needs Windows+Dymola. Runtime warning emitted when chain fires.
- **Pluggable test discovery (PTA)**: `Recognizer` registry. Bundled `BundledModelicaUnitTestsRecognizer`; user-provided via `testing.json` `"recognizers"`. Match types: `component-instantiation`, `extends`, `class-name-glob`, `all-of`, `any-of`. Field sources: `parameter`, `constant`, `experiment-annotation`, `annotation`. Folder filter (`paths_include`/`paths_exclude`) per-recognizer.
- **Richer-contract TestModel fields**: `simulate_only` (wired in comparator — pass iff sim succeeds), `requested_fmu_export`, `requested_baselines` (drives cross-backend chains).
- **Interactive HTML**: mode-aware Score column; tolerance slider applies to NRMSE-mode variables only (others show `n/a (mode=...)`).

### External-consumer migration notes

- **PTA**: schema additions only — no `testing.json` break.
- **PTA follow-ups**: schema additions only.
- **4.B cross-backend**: opt-in via `requested_baselines` recognizer field; not active by default. TRANSFORM unchanged.

### Validated on real libraries

- **ModelicaTestingLib**: PTA demo + `SimulateOnlyTest.mo`.
- **TRANSFORM** (`D:\Modelica\TRANSFORM-UnitTests\ReferenceResults`): not yet exercised post-bundled-session; expected to keep working since all changes are additive.

### What needs Windows + Dymola to validate (per D63 / D65)

- ~~`DymolaWorker.export_fmu` signature/license/cwd~~ **DONE 2026-04-17**:
  `scripts/smoke_test_dymola_export.py` passed on Dymola 2026x against
  `Modelica.Blocks.Examples.PID_Controller`. Signature matches verbatim,
  FMI export license present, cwd-on-Windows works, FMU produced.
  Notable: Dymola sanitizes basenames with a `_0` disambiguation
  (`PID_Controller` → `PID_0Controller.fmu`) — `export_fmu` is already
  immune (uses Dymola's returned name + glob fallback).
- `produce_dymola_via_fmpy_baseline` full chain on real Dymola output
  (export a real FMU, feed it to FMPy, verify the baseline lands and is
  numerically sensible against the Dymola primary result) — still pending.
- CLI `_run_cross_backend_chains` integration end-to-end — still pending.

### Deferred to a dedicated phase (D65)

**"FMU-path semantic gap closure"** — bundle of:
- `FmpyRunner.run_single_test` input-schedule support (`input=` param to `fmpy.simulate_fmu`).
- Test-spec field for `fmi_type` selection (CS vs ME).
- Test-spec field for `start_values` override.
- Python-driver test shape: test declares a python entry point rather than
  a single model ID; the entry point receives the FMU handle and drives it.
- Cross-backend chain generalization to honor all of the above (inputs flow
  from the test spec into both primary FMPy and the chain's FMPy half).
- Real end-to-end Dymola validation pass + demo model in ModelicaTestingLib.

---

## Candidate next moves (D66 roadmap)

### Phase 6 — Reporter-as-IDE (post-paper, this is the primary next move)

MVP = **6.0 + 6.1 + 6.4 (~3–4 weeks)**. Implementation plan lives at `docs/PHASE_6_PLAN.md` with ordering, exit criteria per step, and review checkpoints. Ships the first closed authoring loop for all six existing modes.

- **6.0** Performance budget: interactive.html for a 50-var test stays under ~5 MB. Decimate trajectories; sidecar JSON for full-resolution.
- **6.1** Per-leaf detail panels. Config-dataclass-derived UI auto-rendered for each mode; custom overrides for tube (conditional fields) and range (visual handles). Replaces the `n/a (mode=...)` cells. Each simple mode ships an in-browser JS pass/fail recompute (nrmse, tube, range, final-only); event-timing and dominant-frequency skip live preview (CLI-authoritative).
- **6.4** Full-fidelity `spec-update`. RFC 6902 JSON-Patch download format; read-modify-write preserves unknown keys and `description`/`info`/`metadata` conventions. Tests in Python exhaustively cover the patch schema and round-trip faithfulness.

Post-MVP, in rough order of independent shippability:
- **6.3** Multi-baseline picker (view-only multi-select overlay; primary + companions + soft_checks).
- **6.2** Tree-level controls (combinator thresholds, weighted-combinator weights, add/remove/swap leaves for authoring).
- **6.5** Edit/view mode toggle.
- **6.6** Draft-tree preview against already-simulated data (browser-side, no server).

Parallel track: golden-file HTML snapshot tests, markdown QA checklist at `docs/qa/reporter_checklist.md`, JSON-Schema export command.

### Baseline-role implementation (wired alongside Phase 6)

D66 commits to three distinct roles. Implementation items:
- Split `ReferenceStore` named-baseline storage into two sections: companions (file-path or frozen under `ReferenceResults/.../companions/`) and soft_checks (under `ReferenceResults/.../soft_checks/`). Existing named-baseline code becomes soft_checks with a one-off migration.
- `companion add <test> <name> <path>` and `companion freeze <test> <name>` CLI commands.
- `import-baseline <test> <name> <path>` CLI command for importing another regression system's primary as a soft_check.
- Validator rules: primary-required-outside-warn; soft_check-must-be-in-warn; companions-never-targeted. Schema errors with clear messages.
- Cross-backend chain (D65) output now clearly a soft_check (update cross_backend.py terminology).

### Phase 7 — Rule-based recommender (post-Phase-6 MVP)

Contained to signal → metric tree proposals. Each `ComparisonMode` declares `baseline_compatibility` (`requires_baseline`, shape requirements) so candidates are filtered automatically. Bounded feature vocabulary in `recommender/features.py`. Complexity budget per proposal. Not runtime-load-bearing. No ML.

### Phase 9 — Dataset types beyond TIME_SERIES (post-Phase-6 full)

`Events`, `Spectrum`, `Distribution`, `Scalars`, `Field`. Reordered after the reporter rewrite because Phase 6 gives us a shape-aware render contract to plug each new dataset type into.

### Additional leaf types (additive, can parallel Phase 6)

- **Fréchet / iso-18571** — shape-sensitive metric.
- **KS-distribution** — stochastic regression (needs Distribution dataset from Phase 9).
- **x-tolerance / pyfunnel** — funnel comparison.

### Deferred (post-feature-complete)

- **Tool rename** — "ModelicaTesting" → neutral name. Touches package, CLI prog, HTML titles, `pyproject.toml`, all imports. Naming decision pending.
- **FMU-path semantic gap closure** (D65 follow-on) — input schedules, CS/ME choice, start-value overrides, python-driver tests, real Dymola validation.
- **Deferred PTA features** — `not-of` match composition; more cross-source recognizers (FMU vendor extensions, Julia macros).
- **Phase 8 (removed)** — ML-backed recommender is out-of-scope-in-repo; belongs in a downstream tool consuming our handoff artifacts.

---

## Architecture status vs. vision

| Layer | Status |
|---|---|
| Source | 🟢 modelica + fmu concrete. |
| Discovery | 🟢 Pluggable recognizer registry; bundled + JSON-driven; folder filter; match composition. |
| Backend | 🟢 Dymola + FMPy; FMU_EXPORT capability now real on Dymola; cross-backend chain orchestrated (experimental per D65). |
| Dataset | 🟡 Only `TIME_SERIES` produced. Phase 9. |
| Metric | 🟢 6 concrete (nrmse/tube/final-only/range/event-timing/dominant-frequency). Per-mode UI override slots pending (Phase 6). |
| MetricTree | 🟢 5 combinators (and/or/k-of-n/warn/weighted); user-authored. Validator refinement pending for D66 baseline-role rules. |
| Reference | 🟡 Multi-baseline live via flat named-baseline storage. D66 splits into three roles: primary (exists), companion references (new), soft_checks (reframe of current "named baselines"). Implementation pending alongside Phase 6. |
| Reporter | 🟡 Static + interactive HTML exist; NRMSE slider + mode-aware Score column. Phase 6 expands to per-leaf controls, tree-level controls, multi-baseline picker, full-fidelity round-trip. |
| Recommender | ⚪ Not started. Phase 7, rule-based only. |

Largest remaining gap by impact: **reporter-as-IDE (Phase 6)** — authoring surface has to mature before more leaves compound the `n/a (mode=...)` debt. Next structural piece: **baseline-role split (D66)** — wired alongside Phase 6. After that: **dataset types (Phase 9)**. Cross-backend chain shipped with a validation caveat (D65); `scripts/smoke_test_dymola_export.py` passed on Dymola 2026x so signature/license/cwd are locked.

---

## Key files, fast reference

- `src/modelica_testing/discovery/recognizer.py` — `Recognizer` ABC + registry; `applies_to_path` for filters.
- `src/modelica_testing/discovery/json_recognizer.py` — JSON recognizer; match types `component-instantiation`/`extends`/`class-name-glob`/`all-of`/`any-of`; field sources `parameter`/`constant`/`experiment-annotation`/`annotation`; `paths_include`/`paths_exclude`.
- `src/modelica_testing/discovery/test_registry.py` — discovery + per-field merge.
- `src/modelica_testing/comparison/modes.py` — `NrmseMode`/`TubeMode`/`FinalOnlyMode`/`RangeMode`/`EventTimingMode`/`DominantFrequencyMode`.
- `src/modelica_testing/comparison/metric_tree.py` — combinators incl. `WeightedCombinator`.
- `src/modelica_testing/comparison/comparator.py` — `_compare_event_timing`, `_compare_dominant_frequency`; `simulate_only` short-circuit; mode-aware `score_display` formatters in `plot_comparison.py`.
- `src/modelica_testing/simulators/base.py` — `SimulatorRunner.export_fmu` ABC method (default raises NotImplementedError).
- `src/modelica_testing/simulators/dymola/persistent_runner.py` — `DymolaWorker.export_fmu` + `PersistentDymolaRunner.export_fmu` (one-shot worker).
- `src/modelica_testing/simulators/cross_backend.py` — `produce_dymola_via_fmpy_baseline`.
- `src/modelica_testing/cli.py` — `_run_cross_backend_chains` invoked after `runner.run_tests`.
- `src/modelica_testing/reporting/templates/interactive.html` — Score column, mode-aware tolerance UI.

---

## Pre-session sanity checklist

```bash
# Full test suite
uv run pytest -q                          # expect 404 passed

# FMU end-to-end (uses the multi-baseline tree for BouncingBall)
uv run modelica-testing --config examples/fmu/testing.json run

# ModelicaTestingLib sanity (needs Dymola on Windows) — should also exercise
# the demo recognizer and the cross-backend chain if a model declares it.
# uv run modelica-testing --config examples/modelica/ModelicaTestingLib/... run

# Repo status
git status
```

If fmpy is missing: `uv pip install -e ".[dev,fmpy]"`.

---

## Starter prompt for the next session — Phase 6 MVP kickoff

> Resuming ModelicaTesting after the AMC 2026 paper drop. **Read `docs/PHASE_6_PLAN.md` first** — it is the implementation-oriented decomposition of this session's work. Also skim D66 in `docs/decisions.md` for the commitments behind the plan (identity, baseline-role split, reporter-as-IDE, Phase 8 removed). The concrete goal for this session is **Phase 6 MVP = 6.0 + 6.1 + 6.4** (~3–4 focused weeks):
>
> - **6.0** — performance budget. Interactive.html stays under ~5 MB for a 50-variable test; decimate trajectories + sidecar for full-resolution. Precondition to the rest.
> - **6.1** — per-leaf detail panels replacing today's `n/a (mode=…)` cells. Six modes × (auto-derived UI + JS recompute) with two custom overrides (tube, range). Live preview for nrmse/tube/range/final-only; CLI-authoritative-only for event-timing + dominant-frequency.
> - **6.4** — full-fidelity `spec-update` round-trip. Reporter emits RFC 6902 JSON-Patch; `cmd_spec_update` applies via read-modify-write preserving unknown keys (including `metadata`). New `export-schema` CLI derives JSON-Schema from the Config dataclasses.
>
> Baseline-role split lands alongside 6.0–6.4: `ReferenceStore` partitions primary / companions / soft_checks; new `companion add` / `companion freeze` / `import-baseline` CLIs; validator rules enforce D66 (primary-required-outside-warn, soft_checks-must-be-in-warn, companions-never-targeted).
>
> Follow the ordering + checkpoints in `docs/PHASE_6_PLAN.md`. Retire that file into D67 once the MVP merges. Out of scope for this MVP (do not absorb silently): 6.2/6.3/6.5/6.6, recommender (Phase 7), dataset types (Phase 9), tool rename, FMU-path semantic gap closure, any ML. Pre-session sanity check: `uv run pytest -q` expects 404 passed at commit `3a43487`.

**Context to hand the agent on day 1**: `CLAUDE.md`, `docs/vision.md`, `docs/decisions.md` (especially D66), `docs/architecture.md`, `docs/PHASE_6_PLAN.md`. Parallel paper artifacts live at `/mnt/d/Papers/amc2026_testing/` — the paper's SNAPSHOT.md and experimental_log.md pin to the same commit; do not break claims in flight.
