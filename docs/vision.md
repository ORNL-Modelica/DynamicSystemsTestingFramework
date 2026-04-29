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
  Metric          — a scoring function on a Dataset vs. baseline (NRMSE, tube, points, event-timing, spectral, Fréchet, KS)
    │
    ▼
  MetricTree      — composition: AND / OR / weighted / K-of-N combinators over Metrics → overall pass/fail + diagnostics
```

Most layers are explicit: Source selects via `source_type` (Modelica or FMU today), Discovery scans `.mo` and/or reads `test_spec.json`, Backend registry carries Dymola + FMPy, Dataset is still time-series only, Metric offers four built-ins (NRMSE, tube, points, range), MetricTree accepts user-authored trees from `test_spec.json` with AND / OR / k-of-n / warn combinators. What remains implicit: datasets beyond time-series, additional discovery strategies, more leaf types, weighted combinator.

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

## Reporter as authoring IDE (D66)

The interactive HTML report is the **primary authoring surface** for acceptance criteria — not just a review UI. Users compose tube widths, range bounds, combinator structures, and multi-criteria scoring interactively against a freshly-simulated signal, then download a patch and apply it via the CLI. Editing `test_spec.json` by hand stays supported; it is not the intended default.

**Workflow boundary (hard)**: the reporter is the authoring surface; the CLI is the execution surface. Reporter state lives in the browser until an explicit download. No local server; no live-apply; no auto-rerun. The download payload is an **RFC 6902 JSON-Patch** against the test spec. The `spec-update` CLI applies the patch via read-modify-write, preserving unknown keys and the `description` / `info` / `metadata` conventions for human notes.

**Per-leaf modularity**: `ComparisonMode` stays pure compute (no UI coupling). UI controls are **auto-derived from each mode's typed Config dataclass** (NrmseConfig, TubeConfig, …) with override slots where richer UI makes sense (tube conditional fields, range visual handles). Tightening Config types to use `Literal[...]` for enum-like fields buys JSON-Schema export as a first-class handoff artifact.

**Live preview policy**: modes with tractable math ship an in-browser JS recompute function so users see pass/fail update as they drag. Today this covers `nrmse`, `tube`, `range`, `points`, and `dominant-frequency` (the FFT path was added in D75-D76 once both sides agreed on a power-of-2 resampling that gives bit-identical bin frequencies). `event-timing` is the only mode that stays CLI-authoritative — its event-pairing algorithm has too many tie-breakers to mirror cleanly.

**Testing asymmetry, with a parity floor**: the Python data contract (patch schema, spec-update round-trip, JSON-Schema export, validator rules) is exhaustively tested. The JS layer's structural shape is covered by golden-file HTML snapshots; behavioral correctness of the live-preview scorers is covered by `tests/test_scorer_parity.py`, which renders a synthetic fixture suite, runs the Python `_compare_*` functions for the authoritative verdict, and asserts every JS `MODE_SCORERS[mode](leaf)` agrees. Drift between the two implementations is the only failure mode this catches mechanically — interaction bugs (drag handlers, plot wiring) still rely on the human QA checklist (`docs/qa/reporter_checklist.md`).

**Performance budget**: interactive.html for a 50-variable test stays under ~5 MB embedded payload. Trajectories decimated when needed; full-resolution data lives in a sidecar the reporter lazy-loads on demand.

---

## Rule-based recommender (Phase 7)

A lightweight heuristic layer that looks at a signal (and optional baseline) and **proposes a starter metric tree** for the user to inspect and tune. Never replaces authoring; seeds it.

**Hard containment rules (D66)**:

1. **Input contract**: signal (time + values) + optional baseline. Nothing else — no source file, no model ID, no simulation metadata. The recommender never "understands the model."
2. **Output contract**: one or more proposed metric subtrees in the same JSON shape users author. Nothing else — no model change suggestions, no parameter hints, no simulator recommendations.
3. **Feature vocabulary is bounded and declared**: each signal feature (mean, std, range, monotonicity, event count, FFT peak ratio, step-like score, SNR, …) lives as a named function in `recommender/features.py`. New features require a decision entry.
4. **Rule-based only, not model-based**: proposals come from an auditable `(feature-predicate → leaf-spec)` table. Each `ComparisonMode` declares its **baseline compatibility** (`requires_baseline` flag + shape requirements) so the recommender filters candidates automatically — e.g., `event-timing` only proposed when events are detectable; `range` proposed for no-baseline signals.
5. **Complexity budget per proposal**: at minimum one leaf targeting primary (when a baseline exists). At most three leaves total. At most one combinator layer. Prefer simpler leaves (`range`/`points`/`tube` before `event-timing`/`dominant-frequency`). A recommender that proposes complex trees is a recommender users can't trust.
6. **Not runtime-load-bearing**: if `recommender/` disappears, tests still run. The recommender is a tooling convenience, not a load-bearing part of the comparator.

**No ML in repo**. ML-backed ranking, clustering, or prediction belong in a separate tool that consumes our handoff artifacts. The framework emits artifacts cleanly shaped for such tools; it does not host them.

---

## Metric composition

Today, a test passes iff every variable's single metric passes. This is too narrow for the target audience:

- Users want to stack metrics: *"pass if NRMSE < 0.01 **AND** dominant-frequency shift < 1%"*.
- Users want disjunctive fallbacks: *"pass if tube OK **OR** NRMSE loose-bound OK"* — captures "one of these two views is fine."
- Users want roll-up logic: per-variable metrics aggregate into per-test; per-test can have global metrics (overall simulation time, event count, final energy balance).
- Users want custom metrics: a domain-specific score (e.g. power-system stability margin) should slot in via the same interface as NRMSE.

**MetricTree** is the abstraction: a tree whose leaves are `Metric` evaluations and whose internal nodes are combinators (AND, OR, weighted-sum-with-threshold, K-of-N, `warn`). Evaluates to a boolean + numeric score + diagnostics, not just a boolean. The diagnostic shape carries through to reports — the interactive HTML report will visualize *which* branch of the tree failed.

The `test_spec.json` schema extends to express MetricTrees. Simple cases remain terse (a bare variable list with a scalar tolerance stays valid and is interpreted as a flat AND of NRMSE-per-variable).

### Baseline model: three distinct roles (D66)

The tool is a **regression testing** framework. "Does this signal match the stored reference within tolerance?" is the single question it answers. Reference data takes three distinct roles — not a single flat "named baseline" concept:

**Primary baseline** — the regression anchor.
- Always the stored simulation result for this test.
- Created and overwritten by `--accept`.
- Required when the test carries a MetricTree.
- At least one tree leaf must target primary outside a `warn` combinator (validator rule). This keeps every regression test honestly regression-checked.

**Companion references** — visual overlays, never scored.
- External time-series data (experimental CSV, analytical solution output, vendor reference, digitized plot data).
- Stored as file paths in the test spec; two storage options — `external` (path points outside the repo; tool loads best-effort on each run) or `frozen` (file copied into `ReferenceResults/.../companions/`; immutable).
- Load failures emit a warning on the test but do not affect pass/fail — graceful degradation.
- MetricTree leaves cannot target companions (schema error). Companions are plot-only.
- Commands: `companion add <test> <name> <path>` / `companion freeze <test> <name>`.

**Soft checks** — warn-wrapped scoring only.
- Another regression system's primary imported here (e.g., the `dymola-via-fmpy` chain's output; another ecosystem's regression baseline).
- MetricTree leaves can target soft_checks via `"against": "<name>"`, but the validator enforces that they be inside a `warn` combinator — soft_checks can never hard-fail a test.
- Commands: produced by cross-backend chains, or `import-baseline <test> <name> <path-to-another-systems-ref>`.

**Composable regression systems**. To regression-test experimental data itself, run the tool *on* the experimental data as its own tests. That produces its own primary baselines. Then import those baselines into sim tests as soft_checks for cross-checks. One tool, multiple instances, clean composition. This replaces "experiment-as-primary in a sim test" — which would blur the regression identity — with "experiment-has-its-own-regression-tests; sim tests soft-check against them."

The picker in the interactive reporter multi-selects across primary + companions + soft_checks for visual overlays only, with zero scoring effect. Each baseline-kind renders in a distinct visual style.

This three-role split subsumes Gap E (cross-simulator validation) as a composable feature — a cross-backend chain produces a soft_check named by convention (`dymola-via-fmpy`), and a warn-wrapped leaf scores against it. Primary stays the regression anchor.

---

## Scope: what is in, what is out (D66)

The single question this tool answers: *"does this time-dependent signal match the stored reference within tolerance?"* Everything in scope serves that question; everything out of scope belongs to a different tool in an **economy of tools** that composes around ours.

**In scope**:
- Simulation regression (Modelica, FMU, Julia, Simulink, etc.).
- Unit testing of simulated behavior (single-run pass/fail with tolerances).
- Experiment / calibration regression via composable instances (experiment data gets its own regression test suite; sim tests soft_check against those baselines).
- Cross-backend verification as a composable feature on top of capabilities.
- Acceptance-criteria authoring via the interactive reporter (Phase 6 — reporter-as-IDE).
- Rule-based recommender for starter criteria on new signals (Phase 7).
- Deterministic and stochastic (via `Distribution` dataset + KS-style metrics) — stochastic is a later phase, but the abstraction accommodates it.

**Out of scope (explicit anti-goals — hard lines)**:
- **Being a simulator**. We orchestrate existing simulators; we do not re-implement solvers or re-invent FMI.
- **FMU generation as a framework feature**. If a backend supports FMU export as a capability we expose it; producing FMUs is the backend's job.
- **Parameter estimation / model calibration**. We check whether a model matches data; we do not tune parameters to fit data. A downstream calibration tool can consume our comparison artifacts.
- **Root-cause analysis of failures at the physics layer**. We emit pass/fail, scores, and diagnostics (observations about the comparison — max deviation, where, when). We do not emit hypotheses about causes.
- **Design-of-experiments / test selection**. We do not propose which sweeps to run or which tests to author.
- **Property-based / fuzz testing**. We do not generate inputs to find edge cases.
- **Static analysis / linting of model code**.
- **Load / performance testing** (throughput, latency SLAs).
- **General-purpose scientific visualization**. We render what comparison needs; we do not compete with matplotlib/Plotly-as-frameworks.
- **Model repository / VCS**. We consume tests from a Source; we do not host or version the models themselves.
- **ML in the repo**. The recommender (Phase 7) is rule-based. Any ML-backed recommender belongs in a separate tool that consumes our handoff artifacts. Learning from user behavior is out.
- **Full replacement of `pytest`/`unittest`**. The framework can be driven from them; a pytest-style plug-in is plausible; but our scope is "compare signals against baselines," not "run arbitrary Python test functions."
- **Locking to one IDE or one ecosystem**. Every feature must be expressible without assuming the user uses any specific tool.

**Rejection criteria for new features**:
- Does it answer "does this signal match expected?" → in
- Does it require generating inputs or selecting which tests to run? → out
- Does it require understanding *why* a test failed at the physics layer? → out
- Does it compete with a general-purpose tool the user already has? → out

**Economy of tools principle**. Our artifacts (reports, JSON outputs, JSON-Schema exports, diagnostics, comparison records) are designed as handoff-ready for downstream tools — calibration, RCA, parameter estimation. We do one thing well; we emit well-shaped outputs so other tools can build on us. We do not grow into those other tools.

---

## Forward-looking principles

1. **The framework is the primitive; backends and metrics are plug-ins.** If we find ourselves writing backend-specific code in the framework core, we re-draw the line.
2. **Capabilities over `isinstance`.** Feature availability is a runner-declared capability, not a type check.
3. **The baseline is the contract.** Tolerances, metric choices, and per-variable overrides live *with* the baseline so they travel with it — no separate config drift. (Already a strength of the current tool; preserve it.)
4. **Clean breaks during development.** No backwards-compatibility shims while we are pre-1.0. Rename, restructure, remove as needed.
5. **Modelica is the first consumer, not the reference model.** Design decisions must hold up when the source is an FMU or a CSV, not only when it's a `.mo` file.
6. **Regression is the identity (D66).** The tool is a regression testing framework. Primary = stored regression anchor. Companions are visual-only. Soft_checks are warn-only. V&V against experiment is a composed use of the tool on experimental data, not a separate test shape.
7. **Economy of tools (D66).** We do one thing well and emit well-shaped handoff artifacts. Calibration, RCA, parameter estimation, ML-backed ranking — those belong to downstream tools that consume our outputs. We do not grow into them.
8. **Reporter is the authoring surface; CLI is the execution surface (D66).** No local servers, no live-apply. Download-patch + CLI-apply is the roundtrip.
9. **Compute layers are UI-free (D66).** `ComparisonMode` and `SimulatorRunner` know nothing about presentation. Reporting is a parallel layer that derives UI from typed Config dataclasses + explicit overrides.
