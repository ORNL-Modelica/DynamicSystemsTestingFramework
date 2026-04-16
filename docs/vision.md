# Vision

## What this tool aspires to be

A **regression and unit-testing framework for any time-dependent system behavior** — whether that behavior comes from a simulator, an FMU, a native solver binding, a hardware-in-the-loop rig, or a physical experiment. The framework provides the scaffolding (discovery, execution, baseline management, comparison, reporting, interactive review) and lets the user plug in:

- **Where the behavior comes from** (a Modelica library, an FMU, a Julia script, a Simulink model, a recorded CSV from a test bench).
- **How it is produced** (Dymola Python interface, OMPython, FMPy, JuliaCall, MATLAB engine, a subprocess, nothing at all for data-file ingest).
- **What counts as "correct"** (NRMSE, tube envelope, event timing, spectral coherence, statistical distribution, domain-specific metrics the user writes).

The current codebase is a strong, careful Modelica/Dymola implementation of this idea. The framework already has extension points (`@register` runners, `ComparisonMode` strategies); they have not been exercised. This document captures where we are going, so Phase 1+ refactors and new backends hit a consistent target.

---

## Target user base

1. **Modelica library authors** using Dymola, OpenModelica, SystemModeler, or any combination — the current primary audience.
2. **FMU consumers** — anyone who receives FMUs from vendors and needs regression/acceptance testing against reference traces.
3. **Julia modellers** using ModelingToolkit / Dyad — parallel ecosystem, same regression needs.
4. **Simulink users** — batch-mode MATLAB integration, or Simulink → FMU export.
5. **Experimentalists** — people with recorded time-series data from physical test rigs who want the same tolerance/tube/event-timing infrastructure applied to empirical data or to calibration runs.

These audiences share one problem: *"here is a time-dependent signal, does it match the expected behavior within my tolerances?"* — across ecosystems, they each solve it themselves today.

---

## Six-layer abstraction

The framework is structured around six layers. Each is a plug-in point; each has a typed contract.

```
  Source          — what holds the behavior to test (library, FMU, script, data file)
    │
    ▼
  Discovery       — how tests are found from a Source (.mo scan, spec file, pytest-style, experiment registry)
    │
    ▼
  Backend/Runner  — how a test is executed (Dymola Python, FMPy, OMPython, JuliaCall, MATLAB engine, nothing-for-data-file)
    │
    ▼
  Dataset         — typed result (TimeSeries, Scalars, Events, Spectrum, Distribution; Field in future)
    │
    ▼
  Metric          — a scoring function on a Dataset vs. baseline (NRMSE, tube, final-only, event-timing, spectral, Fréchet, KS)
    │
    ▼
  MetricTree      — composition: AND / OR / weighted / K-of-N combinators over Metrics → overall pass/fail + diagnostics
```

Most layers are explicit: Source selects via `source_type` (Modelica or FMU today), Discovery scans `.mo` and/or reads `test_spec.json`, Backend registry carries Dymola + FMPy, Dataset is still time-series only, Metric offers four built-ins (NRMSE, tube, final-only, range), MetricTree accepts user-authored trees from `test_spec.json` with AND / OR / k-of-n / warn combinators. What remains implicit: datasets beyond time-series, additional discovery strategies, more leaf types, weighted combinator.

---

## Pluggable in-source test annotations

Pre-PTA, Modelica discovery hardcoded one recognizer: a class containing the `UnitTests` component (with parameters `n`, `x={...}`, `error_expected`) plus the standard `experiment(...)` annotation. That was a usable default but a hard adoption barrier — a library that already has its own test-tagging convention had to either rewrite every model to instantiate `UnitTests`, or fork the framework.

**Now (Phase 5 / PTA, complete)**: in-source test annotation is a **registered recognizer**, not a hardcoded pattern. The bundled `ModelicaTestingLib.Components.UnitTests` is one recognizer (the recommended default); users who can't adopt it provide a small JSON map from their convention to the framework's concepts — no Python required.

A recognizer declares:

- **What it looks for** (Modelica class name / annotation pattern / FMU vendor extension / Julia macro / …)
- **How fields map** to the framework's `TestModel` (which parameter holds the variables-to-track, which holds the timeout, …)
- **What it can extract** as a capability profile — `{variables, reference_signals, timeout, tolerance, requested_fmu_export, simulate_only, …}`. Recognizers don't need to extract everything; a "minimal" recognizer can declare only "this is a test, simulate it" and pass on success.

Discovery composes results from every registered recognizer, merging by model_id. A library can ship its own recognizer alongside the bundled default — both run; results combine.

The contract is **richer than today's purely-declarative `UnitTests`**: an annotation can request *runtime behavior* (cross-backend verification, FMU export, increased timeout, …) so users keep test orchestration concerns in the model where the test logic lives, rather than splitting them across `.mo` and `test_spec.json`.

The same registry shape applies cross-source: an FMU recognizer could read vendor extensions from `modelDescription.xml`; a Julia recognizer could parse a `@unittest` macro. Each registers as a recognizer with its own capability profile; Discovery doesn't care which Source produced them.

This is the natural completion of the "Modelica is the first consumer, not the reference model" principle (§Forward-looking principles): the in-source test convention itself becomes pluggable, not just the simulation backend.

---

## Runner capabilities (first-class)

Runners are not all equivalent. Rather than assume every backend supports every workflow, runners declare **capabilities** on their contract:

- `supports_persistent_workers` — can hold a loaded model in memory and simulate many tests per worker (Dymola Python: yes, batch `.mos`: no, FMPy: trivially yes, CSV ingest: N/A).
- `supports_batch_fallback` — exposes a script-driven non-interactive fallback (Dymola: yes via `.mos`, OMC: yes via `.mos`, FMPy: no, MATLAB: yes).
- `supports_fmu_export` — can export a test artefact as an FMU (Dymola: yes, OMC: yes, FMPy: N/A). Enables cross-backend verification (see below).
- `supports_experiment_ingest` — reads recorded data rather than simulating (data-file "runner").
- `produced_datasets` — set of `Dataset` types this runner can yield (most produce `TimeSeries` and `Events`; a data-file runner may produce only `TimeSeries`).

Capabilities let the framework enable/disable features per backend without special-casing, and let users query at config time ("does this backend support what my test needs?").

---

## Cross-backend verification (forward bet)

A deliberate forward architectural bet: if backend A declares `supports_fmu_export` and backend B can consume FMUs, the framework can run the same test through both and compare results. This is Gap E from the evaluation report ("cross-simulator validation") rendered as a composable feature on top of capabilities, not a hardcoded mode.

Not implemented in initial broadening phases — but the capability hook is in the interface from day one so we don't have to re-break the contract later.

---

## Metric composition

Today, a test passes iff every variable's single metric passes. This is too narrow for the target audience:

- Users want to stack metrics: *"pass if NRMSE < 0.01 **AND** dominant-frequency shift < 1%"*.
- Users want disjunctive fallbacks: *"pass if tube OK **OR** NRMSE loose-bound OK"* — captures "one of these two views is fine."
- Users want roll-up logic: per-variable metrics aggregate into per-test; per-test can have global metrics (overall simulation time, event count, final energy balance).
- Users want custom metrics: a domain-specific score (e.g. power-system stability margin) should slot in via the same interface as NRMSE.

**MetricTree** is the abstraction: a tree whose leaves are `Metric` evaluations and whose internal nodes are combinators (AND, OR, weighted-sum-with-threshold, K-of-N, `warn`). Evaluates to a boolean + numeric score + diagnostics, not just a boolean. The diagnostic shape carries through to reports — the interactive HTML report will visualize *which* branch of the tree failed.

The `test_spec.json` schema extends to express MetricTrees. Simple cases remain terse (a bare variable list with a scalar tolerance stays valid and is interpreted as a flat AND of NRMSE-per-variable).

### Multiple named baselines

A test can carry **multiple named baselines**, not a single reference. Example: a Dymola simulation compared against (a) its own prior regression trace, (b) data from a physical experiment, (c) a closed-form analytical solution, (d) another simulator's output. Each baseline is named and carries provenance metadata (origin, capture date, citation, notes).

MetricTree leaves reference baselines by name (`{"metric": "nrmse", "against": "primary", ...}`). The user controls each baseline's pass/fail role by where they place it in the tree:

- **Hard fail** — the baseline comparison is inside an `and` branch.
- **Warning only** — wrapped in a `warn` combinator that always passes but surfaces child diagnostics as warnings in the report.
- **Display only** — baseline is stored but no MetricTree leaf references it; reporter auto-overlays it on plots for context without contributing to pass/fail.

The same abstraction covers three distinct use cases with no special modes:

| Use case | How expressed |
|---|---|
| Regression against prior self | One baseline (`primary`), AND branch |
| Validation against experiment | Two baselines (`primary` + `experiment`), second may be AND (hard) or `warn` (soft) |
| Cross-simulator / cross-backend verification | Two baselines (`dymola` + `fmu`), a metric compares them |

This subsumes Gap E (cross-simulator validation) as a composable feature rather than a hardcoded mode.

---

## Scope: what is in, what is out

**In scope**:
- Simulation regression (Modelica, FMU, Julia, Simulink, etc.).
- Unit testing of simulated behavior (single-run pass/fail with tolerances).
- Experiment / calibration regression (recorded data vs. expected trajectory).
- Cross-backend verification as a composable feature on top of capabilities.
- Deterministic and stochastic (via `Distribution` dataset + KS-style metrics) — stochastic is a later phase, but the abstraction accommodates it.

**Out of scope (explicit anti-goals)**:
- Being a simulator. We orchestrate existing simulators; we do not re-implement solvers or re-invent FMI.
- FMU generation *as a framework feature*. If a backend supports FMU export as a capability, we expose it — but producing FMUs is the backend's job, not the framework's.
- Locking to one IDE or one ecosystem. Every feature must be expressible without assuming the user uses Dymola (or any single tool).
- Full replacement of `pytest`/`unittest`. The framework can be driven from them, and a minimal pytest-style plug-in is plausible, but the framework's scope is "compare signals against baselines," not "run arbitrary Python test functions."
- Building data-acquisition / instrumentation pipelines. We ingest recorded data; we do not record it.

---

## Forward-looking principles

1. **The framework is the primitive; backends and metrics are plug-ins.** If we find ourselves writing backend-specific code in the framework core, we re-draw the line.
2. **Capabilities over `isinstance`.** Feature availability is a runner-declared capability, not a type check.
3. **The baseline is the contract.** Tolerances, metric choices, and per-variable overrides live *with* the baseline so they travel with it — no separate config drift. (Already a strength of the current tool; preserve it.)
4. **Clean breaks during development.** No backwards-compatibility shims while we are pre-1.0. Rename, restructure, remove as needed.
5. **Modelica is the first consumer, not the reference model.** Design decisions must hold up when the source is an FMU or a CSV, not only when it's a `.mo` file.
