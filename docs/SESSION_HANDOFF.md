# Session handoff — Phase 2 → Phase 3

**Date**: 2026-04-16

This handoff replaces the prior 2.3 → 2.4 doc. Phase 2 is essentially closed
(2.4 done, 2.5 deferred to a plan doc); Phase 3 is the next major arc —
wiring `comparison/metric_tree.py` into the live comparison pipeline.

---

## Where we are

**Phase 1** (foundation abstractions) — complete. Decisions D44–D47.

**Phase 2** (FMPy backend) — 2.1–2.4 complete. Decisions D48–D50.

- 2.1: Reference-FMUs fetch script.
- 2.2: `"FMPy"` registered in `SIMULATOR_BACKENDS`.
- 2.3: `FmpyRunner` simulates Reference-FMUs end-to-end.
- 2.4: CLI works against `examples/fmu/testing.json` with no Modelica
  source. `Config.__post_init__` and `discover_tests` both gate on
  `source_type`; `library_name` falls back to the config dir name when
  there is no `package.mo`. Linux baselines for BouncingBall, Dahlquist,
  VanDerPol committed under
  `examples/fmu/ReferenceResults/FMPy/linux/`.
- 2.5: deferred. The full GitHub-Actions YAML, decisions, and validation
  steps live in [`docs/PHASE_2_5_CI_PLAN.md`](PHASE_2_5_CI_PLAN.md).
  Intended pickup: when the repo flips public (or when there's a reason
  to spend private-repo Action minutes pre-launch).

## What Phase 3 is

The MetricTree abstraction was landed in Phase 1 (D44) but is **not wired
into the main comparison pipeline yet**. `comparator.compare_test()` still
implements an implicit flat-AND over per-variable `VariableComparison`s.
The vision (`docs/vision.md` "Metric composition") wants users to be able
to author metric trees in `test_spec.json`:

- *"pass if NRMSE < 0.01 **AND** dominant-frequency shift < 1%"*
- *"pass if tube OK **OR** loose-NRMSE OK"*
- *"this baseline is informational — wrap in `warn`"*

Phase 3 is the work to get there. Suggested decomposition (subject to
revision when we plan in detail):

### 3.1 — Wire `implicit_and_tree()` into `compare_test()`
Behavior-preserving. `compare_test` builds a MetricTree from the existing
per-variable comparisons via `implicit_and_tree()`, derives `passed` from
the tree root, and stashes the `MetricResult` on `TestComparison` for
downstream consumers. No reporter or CLI change. Goal: prove the tree
is the source of truth for pass/fail, with zero observable change.

### 3.2 — Test_spec MetricTree schema (parser only, no semantics yet)
Define the JSON shape for explicit trees. Likely:
```json
"metrics": {
  "combinator": "and",
  "children": [
    {"metric": "nrmse", "variable": "h", "tolerance": 0.01},
    {"metric": "tube",  "variable": "v", "tube_rel": 0.05}
  ]
}
```
Plus a top-level `"warn": [...]` shorthand for informational baselines.
Parse into a `MetricTree` object; do not yet replace the implicit tree.

### 3.3 — Replace the implicit tree when the spec provides one
When `test.metrics` is set, build the user-authored tree and use it in
place of the implicit AND. When unset, fall back to the implicit tree
(3.1's behavior). Existing flat-AND tests stay green.

### 3.4 — Reporter renders the tree
Per-test HTML report shows the tree shape, which branch failed, and why.
Index page surfaces tree-level pass/fail in the same column as today.
Likely needs a small Jinja template addition in
`reporting/templates/comparison.html`.

### 3.5 — One non-NRMSE leaf type to prove the abstraction
Pick one — leading candidates: final-value-only (already exists as a
`compare_final_values` helper, just not exposed as a leaf), event-count
delta, or a simple range-bound leaf. Goal is the same as Phase 2.3:
exercise the leaf contract end-to-end with a non-trivial second case so
the contract isn't shaped only around NRMSE.

### Out of scope for Phase 3 (push to Phase 4+)

- Multiple named baselines as MetricTree leaves (the hybrid schema is
  there per D47, but reading non-primary baselines into the tree is its
  own beat).
- Cross-backend verification (Gap E from the eval report) — requires
  FMU export from `DymolaRunner` and the chain logic.
- Spectral / Fréchet / KS metrics — interesting and aligned with vision,
  but the leaf contract has to settle first.
- Stochastic / Distribution dataset type.

## Key files, fast reference

- `src/modelica_testing/comparison/metric_tree.py` — combinators, `MetricResult`,
  `implicit_and_tree()`. Has 18 unit tests, no integration usage.
- `src/modelica_testing/comparison/comparator.py` — `compare_test()` is the
  integration point for 3.1.
- `src/modelica_testing/comparison/modes.py` — current per-variable strategy
  pattern. Leaves in 3.x will likely build on this.
- `src/modelica_testing/discovery/spec_parser.py` — adds the MetricTree
  parsing branch in 3.2.
- `src/modelica_testing/reporting/templates/comparison.html` — tree
  rendering target for 3.4.

## How to start the next session

Read this file. If continuing the proposed plan, start with 3.1 — the
behavior-preserving wire-in. Confirm scope with the user before committing
to the full 3.x sequence; the decomposition above is a suggestion, not a
commitment.

## Pre-session sanity checklist

```bash
# All tests pass (includes Phase 1 metric_tree tests)
uv run pytest -q                   # expect 253 passed

# Phase 2.4 FMU end-to-end still works
uv run modelica-testing --config examples/fmu/testing.json run

# Repo is clean / expected
git status
```
