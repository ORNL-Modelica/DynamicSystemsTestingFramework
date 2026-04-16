# Session handoff — post-Phase 3

**Date**: 2026-04-16

Phase 3 (MetricTree wiring) is complete. This doc captures the landing
state and candidate next moves. Replaces the prior Phase 2 → 3 handoff.

---

## Where we are

**Phase 1** (foundation abstractions) — complete. Decisions D44–D47.

**Phase 2** (FMPy backend) — 2.1–2.4 complete; 2.5 deferred.

- 2.1–2.3: Reference-FMUs fetch, backend registration, `FmpyRunner`.
- 2.4: CLI works against `examples/fmu/testing.json` with no Modelica.
- 2.5: GitHub Actions CI — deferred to [`docs/PHASE_2_5_CI_PLAN.md`](PHASE_2_5_CI_PLAN.md).
  Pick up when the repo goes public (or when there's a reason to spend
  private-repo Action minutes pre-launch).

**Phase 3** (MetricTree wiring) — complete. Decisions D51–D53.

- 3.1: `compare_test` builds a `MetricResult` tree and derives `passed`
  from its root. Default tree is the implicit flat-AND (behavior-preserving).
- 3.2: `"metrics"` block in `test_spec.json` parsed into `LeafSpec` /
  `CombinatorSpec` by `comparison/tree_spec.py`. Path-bearing validation.
- 3.3: `comparison/tree_eval.py` walks a parsed spec against sim +
  reference data to produce an evaluated tree. When present, the spec
  tree replaces the implicit AND and the legacy
  `comparison.variable_overrides` is ignored on that path.
- 3.4: Per-test HTML report renders user-authored trees via recursive
  Jinja. Implicit trees stay suppressed (per-variable table conveys them).
- 3.5: `RangeMode` — first signal-only leaf type (bounds from spec, not
  baseline). Validates the leaf contract across two shapes
  (reference-consuming + signal-only).

Test count: **298 passed** (was 253 at the start of Phase 3 — +45).
BouncingBall in `examples/fmu/test_spec.json` exercises an `and[3]` tree
with two NRMSE leaves and one range leaf.

---

## Candidate next moves

None of these are committed — pick based on what's most valuable next.

### Phase 4.A — Multi-baseline MetricTree leaves
Hybrid schema (D47) already stores multiple named baselines (`primary`,
`experiment`, etc.); no tree leaf type reads non-primary baselines yet.
Add an `"against": "<name>"` field on leaves so users can write
`{"metric": "nrmse", "variable": "h", "against": "experiment", "tolerance": 0.05}`
and wrap in `warn` for informational overlays. Enables the
"validation against experiment" use case from `vision.md`.

### Phase 4.B — Cross-backend verification (Gap E)
Add `supports_fmu_export` to `DymolaRunner` as a real capability
(Dymola can export FMUs via its Python interface), chain
`DymolaRunner.export_fmu()` → `FmpyRunner.simulate()` → compare as a
second baseline. Requires multi-baseline leaves (4.A) or a dedicated
cross-backend metric.

### Phase 4.C — More leaf types
Event-timing, dominant-frequency shift, Fréchet distance, KS for
stochastic outputs. Each is an additive `ComparisonMode` + a
`VALID_METRICS` entry + a test. The `range` landing (D53) is the
template.

### Phase 4.D — Source / Modelica-neutral rename sweep
Deferred from Phase 2: rename `TestModel.mo_file` → `source_file`,
`TestModel.package_path` → `source_package`, `Config.package_path` →
`Config.source_path`. Touches many sites but is a mechanical find-and-
replace. Worth doing before user-facing docs land.

### Phase 4.E — `weighted` combinator
`vision.md` lists it; not built. Useful for "overall score = 0.7 * NRMSE(h)
+ 0.3 * NRMSE(v), pass if > 0.95" patterns.

### Out of scope through Phase 4
- Spectral / Distribution dataset types and metrics.
- Third-party plug-in entry points (`[project.entry-points]`).
- IDE / GUI / web viewer.

---

## Key files, fast reference

- `src/modelica_testing/comparison/comparator.py` — `compare_test`
  (dispatches implicit vs. spec path), `_compare_range`, `_compare_tube`,
  `_compare_trajectories`.
- `src/modelica_testing/comparison/modes.py` — `NrmseMode`, `TubeMode`,
  `FinalOnlyMode`, `RangeMode`; `resolve_mode` factory.
- `src/modelica_testing/comparison/metric_tree.py` — combinators +
  `MetricResult`.
- `src/modelica_testing/comparison/tree_spec.py` — JSON parse.
- `src/modelica_testing/comparison/tree_eval.py` — JSON + data → tree.
- `src/modelica_testing/reporting/templates/comparison.html` — tree
  render macro.
- `examples/fmu/test_spec.json` — demo of a user-authored tree.

---

## Pre-session sanity checklist

```bash
# All tests pass
uv run pytest -q                   # expect 298 passed

# FMU end-to-end still works (BouncingBall uses the spec tree)
uv run modelica-testing --config examples/fmu/testing.json run

# Repo is clean / expected
git status
```

## How to start the next session

Pick a Phase 4 candidate above, confirm scope with the user, then plan
sub-phases the way Phase 3 was decomposed (wire, parse, evaluate, render,
exercise). Each sub-phase should be either behavior-preserving or
additive so regressions stay isolated.
