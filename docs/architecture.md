# Architecture

> See [vision.md](vision.md) for where the framework is going. This document describes both the **forward conceptual model** (the six-layer abstraction every phase is converging on) and the **current state** (what is implemented today). Sections labelled *Current* describe today's code; sections labelled *Forward* describe abstractions we are aligning the code toward.

## Conceptual Model (Forward)

The framework is a pipeline of six typed, plug-in layers:

```
  Source          → what holds the behavior to test
    │               (Modelica library, FMU, Julia script, Simulink model, CSV / HDF5 data file)
    ▼
  Discovery       → how tests are produced from a Source
    │               (.mo UnitTests scan, test_spec.json, FMU directory scan, experiment registry)
    ▼
  Backend         → how a test is executed, with declared capabilities
    │               (Dymola Python / FMPy / OMPython / JuliaCall / MATLAB engine / data-file ingest)
    ▼
  Dataset         → typed result
    │               (TimeSeries, Scalars, Events, Spectrum, Distribution; Field reserved)
    ▼
  Metric          → scoring function on Dataset vs. baseline
    │               (NRMSE, tube, points, event-timing, spectral, Fréchet, KS, user-defined)
    ▼
  MetricTree      → composition: AND / OR / weighted / K-of-N → overall pass/fail + diagnostics
```

**Backend capabilities** are declared on the backend contract, not inferred from type:
- `capabilities: frozenset[Capability]` (`PERSISTENT_WORKERS`, `BATCH_FALLBACK`,
  `FMU_EXPORT`) and `produced_datasets: frozenset[DatasetType]`.
Features (e.g. cross-backend verification, which chains `Backend.export_fmu()` → `FMPy.simulate()`) are composed from capabilities, not hardcoded modes.

### Layer ↔ Code mapping (Current)

| Layer | Current implementation | Status |
|---|---|---|
| Source | Four concrete `Config.source_type` values in the shipped demos: `"modelica"` (default — `config.source_path` → `package.mo` dir), `"fmu"`, `"julia"` and `"python"` (`config.source_path` → the FMU / project / script dir; per-test `"source"` field in spec). Only `"modelica"` is branched on in code (`config.py` package.mo lookup, `test_registry.discover_tests`); the rest select recognizers via `Recognizer.applies_to` and are otherwise opaque strings. | Simulink / data-file remain names only |
| Discovery | Pluggable `Recognizer` registry (`discovery/recognizer.py`). Bundled: `BundledModelicaUnitTestsRecognizer` (UnitTests + experiment) in `mo_parser.py`. User-provided: `JsonRecognizer` (`discovery/json_recognizer.py`) — declarative JSON in `testing.json`'s `"recognizers"` list, no Python. Plus `spec_parser.py` for `test_spec.json` (universal fallback). Merged by `model_id`. | Phase 5 / PTA: registry + bundled + JSON-driven all live; cross-source recognizers (FMU vendor-ext, Julia macros) unimplemented |
| Backend | `backends/` registry with five concrete backends: `DymolaBackend` / `OpenModelicaBackend` (native Modelica), `FmpyBackend` (FMU via FMPy), `JuliaBackend` (ModelingToolkit), and `PythonBackend` (arbitrary `simulate()`). Each declares `capabilities` + `produced_datasets`; Dymola/OpenModelica/Julia ship persistent-worker variants. `PersistentDymolaBackend` implements `export_fmu` via `translateModelFMU` (it needs a live `DymolaInterface`; the batch `DymolaBackend` has no export path). | Cross-backend chain (`backends/cross_backend.py`) wires Dymola export → FMPy simulate → named baseline. Validation caveat: export step requires Windows + Dymola FMI license (D63). |
| Dataset | Implicit `TimeSeries` returned by `read_result()`; `DatasetType` enum declared on backends | one concrete type materialized; additional types reserved for Phase 3 metrics |
| Metric | `comparison/modes.py`: `NrmseMode`, `TubeMode`, `PointsMode`, `RangeMode`, `EventTimingMode`, `DominantFrequencyMode`, `ScalarMode`; `VariableComparison.diagnostics` carries structured extras | 7 concrete leaf types spanning trajectory-comparison, signal-only bounds, event-timing, spectral shapes, and single-number (reduction / stored-statistic) checks |
| MetricTree | `comparison/metric_tree.py` (`And`/`Or`/`KOfN`/`Warn`/`Weighted` + `MetricResult`), `comparison/tree_spec.py`, `comparison/tree_eval.py`. `compare_test` derives `passed` from the tree root; users author trees via `test_spec.json` `"metrics"` block | 5 combinators (weighted is direction-aware); `simulate_only` leaf short-circuits comparison entirely |
| Reference | Role-partitioned storage; `Baseline` view presents named baselines uniformly | primary baseline flat-at-top-level; soft_checks and companions in role subdirs beside it |

Forward work: cross-backend verification beyond the Dymola→FMPy chain (each new primary backend needs its own `export_fmu`), and richer metrics (event-timing, spectral, Fréchet, KS). See [extensibility.md](extensibility.md) for per-layer plug-in contracts.

---

## Project Layout

```
DynamicSystemsTestingFramework/          # repo root (project name: DSTF)
├── examples/                            # Demo libraries — one per backend, used by README first-5-min walkthrough
│   ├── modelica/ModelicaTestingLib/       # Components/UnitTests.mo + Examples/ + Resources/ReferenceResults/
│   ├── fmu/                                # Reference-FMUs demo (BouncingBall / Dahlquist / VanDerPol)
│   ├── julia/JuliaMtkTestingLib/           # ModelingToolkit demo, mirrors the Modelica layout
│   └── python/PythonTestingLib/            # SimpleRamp (scipy) + ConstantCsv (pure data loader)
├── tests/                               # pytest suite (Playwright subset gated by importorskip)
│   ├── fixtures/results/Dymola/         # Real Dymola artifacts (.mat, dslog.txt, etc.)
│   └── test_*.py                        # Unit tests for all modules
```

## Package Layout

```
src/dstf/
├── cli/                      # argparse CLI package: discover, run, compare, export, manifest, add, spec-update,
│                             #   companion, soft-check, import-baseline, export-schema, check-dymola
├── config.py                 # Config dataclass, path resolution, testing.json loading, backend auto-detect
├── discovery/
│   ├── recognizer.py         # Recognizer registry (bundled + user-declared, merged by model_id)
│   ├── mo_parser.py          # Scans .mo files for UnitTests components
│   ├── json_recognizer.py    # Declarative recognizers from testing.json
│   ├── spec_parser.py        # Parses test_spec.json (external test definitions)
│   ├── patch_apply.py        # RFC 6902 JSON-Patch round-trip (powers `dstf spec-update`)
│   ├── patch_envelope.py     # ModelPatch + parse/build for the patch envelope (both forms)
│   ├── spec_update.py        # Read-modify-write of the spec: entry lookup, implicit-tree
│   │                         #   materialization, tree validation, apply_validated_patches
│   └── test_registry.py      # TestModel dataclass, discover_tests() merges all sources
├── backends/
│   ├── __init__.py           # Backend registry: @register decorator, get_backend() factory
│   ├── base.py               # Backend ABC + RunSetup/_prepare_run (types/persistent/batch_manifest split out, D116)
│   ├── progress.py           # Backend-agnostic ProgressReporter (status.json + auto-refresh dashboard.html)
│   ├── common/               # Shared: mat_reader, process tree-kill, result variables, JSON-driver base
│   ├── cross_backend.py      # EXPERIMENTAL Dymola → FMU → FMPy baseline chain
│   ├── dymola/               # DymolaBackend (.mos batch) + PersistentDymolaBackend (Python interface)
│   ├── fmpy/                 # FmpyBackend (in-process; per-test thread)
│   ├── openmodelica/         # OpenModelicaBackend (omc -s) + PersistentOpenModelicaBackend (OMPython ZMQ)
│   ├── julia/                # JuliaBackend (subprocess) + PersistentJuliaBackend (stdin-JSON pipe)
│   └── python/               # PythonBackend — subprocess-per-test, any `simulate(stop_time, tol) -> dict`
├── comparison/
│   ├── comparator.py         # compare_test / compare_all orchestration; per-mode scoring functions
│   ├── modes.py              # ComparisonMode ABC + 7 modes (nrmse, tube, points, range, event-timing,
│   │                         #   dominant-frequency, scalar); typed configs; resolve_mode()
│   ├── mode_schema.py        # derive_schema — per-mode schema from typed comparison configs
│   ├── tree_spec.py          # MetricTree LeafSpec / CombinatorSpec + implicit_and_tree() builder
│   ├── tree_eval.py          # Tree evaluator threading primary + named baselines
│   └── validator.py          # Baseline-role rules (≥ 1 primary leaf outside warn; soft_checks warn-wrapped)
├── storage/
│   ├── baseline.py           # Baseline value type + PRIMARY_BASELINE
│   ├── ref_index.py          # RefIndex + atomic text writes
│   ├── roles.py              # soft_check/companion CRUD mixin
│   ├── accept.py             # accept_run_results — the one accept path, with the aborted-run refusal
│   └── reference_store.py    # ReferenceStore: primary CRUD + accept path
├── authoring/
│   ├── session.py            # Session: open / rescore / view / stage / save / discard / export / apply
│   ├── capabilities.py       # The capability contract: Capability, Scope, the five outcomes
│   ├── spec_document.py      # SpecDocument — the spec as loaded, its pending ops and its on-disk hash
│   ├── app.py                # The NiceGUI shell: fixed layout, event→refresh bridge, capability routing
│   ├── forms.py              # NiceGUI controls from a mode's derived schema
│   └── builtin/              # The capabilities that ship: edit_criteria.py, edit_tree.py
└── reporting/
    ├── console_report.py     # Terminal output with pass/fail, NRMSE, structural warnings
    ├── junit_report.py       # JUnit XML for CI
    ├── status.py             # Shared per-test status classifier (SIM_FAIL > FAIL > NO_REF > PASS)
    ├── dashboard_render.py   # The one suite-wide HTML page (live + final + --report-format html)
    ├── context_builder.py    # Per-test results → template context (the pure half)
    ├── report_suite.py       # Per-test interactive.html render + comparison_data.json sidecar
    ├── overlay_loader.py     # Companion CSV/JSON ingest + soft-check baseline projection
    ├── schema_export.py      # JSON-Schema emitter for test_spec.json (powers `dstf export-schema`)
    └── templates/            # interactive.html (server-rendered tree, summary, banner) + report.js
                              #   (copied out as interactive.js via report_suite.INTERACTIVE_JS_PARTS)
                              #   + dashboard.html / dashboard.js + vendor/plotly.min.js
```

The figure layer sits beside them, and both tiers draw from it:

```
src/dstf/figures/
├── view.py                   # VariableView — the library-neutral picture of one variable
├── plotly.py                 # VariableView → a plotly figure dict (traces + shapes)
├── assemble.py               # Trajectories + evaluated leaves → a VariableView
└── decimate.py               # LTTB decimation on the view, before any trace exists
```

`authoring/` is the authoring tier — the tool's state machine and the shell
`dstf author` serves. Only `app.py`, `forms.py` and the built-in panels import a
UI framework; the Session, the capability contract and the spec document stay
pure, so the rest of `dstf` runs without the `[author]` extra. It shares the
CLI's paths rather than duplicating them: `discovery/spec_update.py` is the
read-modify-write both `dstf spec-update` and `Session.save` call, and
`storage/accept.py` is the one accept path `run --accept` and `compare --accept`
call, and the one an authoring Accept capability will call.

## Data Flow

```
discover_tests(config)
    → mo_parser scans .mo files for UnitTests
    → spec_parser reads test_spec.json
    → test_registry merges both into list[TestModel]

backend.run_tests(tests)
    → creates ProgressReporter, registers each test (with report_dir from backend.ref_id_map)
    → generates startup.mos (loads libs, enables OutputCPUtime + TranslationInCommandLog)
    → generates per-test .mos (clearlog, simulateModel, savelog)
    → generates shutdown.mos, per-batch .mos scripts
    → splits tests into batches: config.batch_size if set, else ceil(total/parallel)
    → submits all batches to ThreadPoolExecutor (queue-dispatched; worker_id from thread slot)
    → emits progress.on_start when batch begins, progress.on_finish per test as results parsed
    → parses dslog.txt (runtime stats) + translation_log.txt (structural stats) per test
    → progress.finalize() strips auto-refresh from dashboard.html
    → returns list[BatchManifest] with TestRunResult per test

backend.read_results(manifests, tests)
    → lists variable names from .mat (fast header scan, no data loaded)
    → resolves variable patterns against available names
    → reads only needed variables from .mat via numpy.memmap (selective row access)
    → auto-captures diagnostic variables (configurable, default: CPUtime, EventCounter)
    → returns dict[model_id → TestResult]

compare_all(tests, results, store, default_tolerance, default_points)
    → loads reference JSON per test from ReferenceStore
    → per-variable: resolve_mode(override, tolerance, default_points) → ComparisonMode
    → mode.compare(ref_time, ref_values, act_time, act_values) → VariableComparison
    → NrmseMode: piecewise NRMSE with event boundary handling
    → TubeMode: envelope check with three width modes (rel, band, absolute)
    → PointsMode: declared-checkpoint comparison (empty list ⇒ final value only;
                  non-empty ⇒ multi-point with abs/rel y-tolerance + x-tolerance box)
    → returns list[TestComparison]

reporters render TestComparison → console / JUnit / HTML / plots
    → context_builder turns results + comparisons into a context dict
    → report_suite writes the full-resolution comparison_data.json sidecar FIRST
    → figures/view builds one VariableView per variable from the trajectories and
      the evaluated leaves; figures/decimate thins it (LTTB) before any trace exists;
      figures/plotly turns it into a figure dict (traces + shapes for the criterion
      geometry each metric emitted: band, bounds, boxes, event windows, spectrum)
    → the trajectory arrays are dropped from the page context — interactive.html
      carries figure dicts and server-rendered markup only (tree, summary, pills,
      decimation banner); report.js paints the figures and toggles trace visibility
    → per-test report directories named ref_NNNN (has reference) or test_NNNN (no baseline)
    → the sidecar keeps its shape (full trajectory data) for downstream tooling
```

## Key Types

| Type | Location | Purpose |
|------|----------|---------|
| `Config` | `config.py` | All resolved paths, backend settings, tolerances |
| `TestModel` | `discovery/test_registry.py` | One test: model ID, sim params, tracked variables, source |
| `TestResult` | `backends/types.py` | Simulation output: success flag, variables, diagnostics, statistics |
| `VariableResult` | `backends/types.py` | One variable's time series (time + values arrays) |
| `TestComparison` | `comparison/comparator.py` | Comparison result: pass/fail, per-variable NRMSE, warnings |
| `ReferenceStore` | `storage/reference_store.py` | CRUD for reference JSON files via RefIndex |
| `RefIndex` | `storage/ref_index.py` | In-memory index mapping model IDs ↔ numeric IDs (built by scanning ref files) |
| `BatchManifest` | `storage/batch_manifest.py` | Maps test keys to `{"model_id", "ref_id", "last_run_status"}` within a batch run |

## Reference File Structure

`<reference_root>/<Backend>/<os>/ref_0001.json` — per backend+OS, self-contained:
```json
{
  "model_id": "...", "test_id": "0001",
  "status": "active",
  "date_added": "2026-01-15T...", "last_updated": "2026-04-08T...",
  "simulation": {"stop_time": 100, "tolerance": 1e-4, "method": "Dassl", "number_of_intervals": 500, "output_interval": null},
  "comparison": {"tolerance": 0.01, "variable_overrides": {"pipe.T[1]": {"tolerance": 0.1}}},
  "statistics": {
    "translation": {
      "continuous_time_states": 4,
      "nonlinear": [3, 1], "nonlinear_count": 2, "nonlinear_total": 4, "nonlinear_max": 3,
      "init_nonlinear": [5], "init_nonlinear_count": 1, ...
    },
    "simulation": {"cpu_time": 0.5, ...},
    "CPUtime": 12.3, "EventCounter": 42
  },
  "diagnostics": [
    {"name": "CPUtime", "values": [...]},
    {"name": "EventCounter", "values": [...]}
  ],
  "n_vars": 3,
  "time": [0.0, 0.1, ...],
  "variables": [{"index": 1, "name": "pipe.T[1]", "values": [...]}]
}
```

The `comparison` section is optional. When present, it stores the comparison tolerances that were active when the baseline was accepted, so tolerances travel with the reference data. `variable_overrides` maps variable names to per-variable settings including `tolerance` and optional tube comparison parameters (`mode`, `tube_width_mode`, `tube_abs`/`tube_rel`, `tube_points`, `tube_interpolation`).

No persistent manifest file for the index. The in-memory `RefIndex` is built by scanning ref files at startup.
Valid statuses: `active` (normal), `skip` (temporarily excluded), `obsolete` (pending deletion).

### Multiple named baselines (D66)

A reference file carries exactly one baseline — the ``"primary"``, stored as
flat top-level fields. Additional named baselines are separate files in the
role subdirs beside it: ``soft_checks/ref_NNNN/<name>.json`` (scorable,
warn-wrapped) and ``companions/ref_NNNN/<name>.{json,csv}`` (plot-only).

```json
{
  "model_id": "...", "test_id": "0001", "status": "active",
  "date_added": "2026-01-15T12:34:56Z",
  "last_updated": "2026-02-20T10:00:00Z",

  // --- primary baseline (flat top-level fields; authoritative) ---
  "simulation": {"stop_time": 100, "tolerance": 1e-4, ...},
  "comparison": {"tolerance": 0.01, "variable_overrides": {...}},
  "statistics": {...},
  "diagnostics": [...],
  "n_vars": 3,
  "time": [...],
  "variables": [...]
}
```

A soft_check file holds the same per-baseline fields plus ``provenance``:

```json
{
  "provenance": {
    "origin": "rig-run-2024-03-15",
    "captured_at": "2024-03-15T09:20:00Z",
    "notes": "Benchmark 3B, lab 2 test stand.",
    "citation": "Internal report XYZ-2024-042, §4.3"
  },
  "time": [...], "variables": [...]
}
```

Readers use the ``Baseline`` view: ``ReferenceStore.get_baselines(model_id)``
returns ``primary`` (synthesized from the flat fields, provenance filled in
from ``date_added`` / ``last_updated``) merged with every soft_check.
Companions are excluded — they cannot be scored against.

Separate files mean accepting fresh primary results can never clobber a named
baseline. A top-level ``baselines`` key left in an old ref file is ignored with
a warning and dropped on the next accept.

The three baseline roles serve three workflows:

* **Regression** — the primary baseline alone (the default).
* **Validation against experiment** — primary + an `experiment` soft_check; a
  leaf referencing ``"against": "experiment"`` gates or warns on that
  comparison.
* **Cross-backend verification** — baselines from several backends
  registered as soft_checks under different names, each targetable
  by its own leaf.

See ``src/dstf/storage/baseline.py`` for the ``Baseline`` dataclass and
the role CRUD. The comparator threads soft_checks into the tree evaluator and
the reporter renders per-leaf `against=` provenance — both shipped in Phase 4 /
D66.

Two manifest files are written to the work directory before simulation starts:
- `batch_manifest.json` — maps `test_key -> {"model_id": "...", "ref_id": "ref_NNNN", "last_run_status": "..."}` for all tests in the batch
- `reference_manifest.json` — maps ref IDs to model names; also available via `manifest dump` CLI command

## Backend Abstraction

### Current (as of D80)

```
Backend (ABC)
  ├── capabilities: frozenset[Capability]           # PERSISTENT_WORKERS, BATCH_FALLBACK, FMU_EXPORT
  ├── artifact_files: tuple[tuple[str, str], ...]  # (filename, label) pairs for the report
  ├── run_tests(tests) → list[BatchManifest]       # override for batch execution
  ├── run_single_test(test, ...) → TestRunResult   # override for per-process execution
  ├── read_result(test, ...) → TestResult          # abstract — must implement
  └── read_results(manifests, tests) → dict        # shared implementation
        │
        ├── DymolaBackend (backends/dymola/)
        │     ├── Python interface (default) — persistent workers, live phase labels,
        │     │     per-test timeout watchdog with disk-check rescue, worker restart
        │     ├── Batch fallback (--batch) — .mos scripts, parallel workers, psutil kill
        │     └── read_result() — mat_reader + variable pattern resolution
        │
        ├── FmpyBackend (backends/fmpy/)
        │     ├── fmpy.simulate_fmu on the prebuilt FMU, writes npz result
        │     ├── concurrent.futures timeout per test
        │     └── phase labels: loading / simulating
        │
        ├── OpenModelicaBackend (backends/openmodelica/)
        │     ├── Persistent workers (the default; absent OMPython aborts)
        │     │     — OMCSessionZMQ per worker, library-load amortized across tests
        │     │     — psutil kill on timeout, disk-fallback, 3× worker restart
        │     ├── Batch fallback (--batch) — .mos + omc subprocess per test
        │     └── read_result() — mat_reader (DSresult-compatible)
        │
        ├── JuliaBackend (backends/julia/)         ← D77/D78/D79
        │     ├── Persistent workers (default)        ← D78
        │     │     — long-lived julia --project=... subprocess reading JSON-per-line
        │     │       requests from stdin, writes JSON responses to stdout
        │     │     — `using MTK, OrdinaryDiffEq, JSON3` paid ONCE per worker
        │     │     — ready-pulse handshake front-loads warmup cost
        │     │     — Base.invokelatest(build_mtk_system) after include for fresh gen
        │     │     — psutil kill on timeout, 3× worker restart
        │     ├── Batch fallback (--batch) — `julia run_test.jl ...` per test
        │     └── read_result() — JSON parser; materializes unknowns + observables
        │
        └── PythonBackend (backends/python/)       ← D80
              ├── Subprocess per test only (no persistent worker yet) — shipped
              │     driver imports the user's .py and calls
              │     simulate(stop_time, tolerance) -> dict
              ├── Shares backends/common/json_driver.py with JuliaBackend
              └── read_result() — same JSON parser
```

Each backend declares its capabilities via `frozenset[Capability]`:

- `PERSISTENT_WORKERS` — long-lived worker process; library loads once, amortized across N tests.
- `BATCH_FALLBACK` — per-test subprocess; robust but slower per-test startup cost.
- `FMU_EXPORT` — can export an FMU from the source (`PersistentDymolaBackend` only today — the batch `DymolaBackend` has no `.mos` export path and does not declare it; OM deferred per D69).

CLI's `_get_backend(persistent=True)` swaps to the persistent variant only when the backend **declares** `PERSISTENT_WORKERS`; a backend without the flag takes the batch path. A persistent preflight that raises `RuntimeError` aborts with a message pointing at `--batch` (no silent fallback). The cross-backend chain gates the same way on `FMU_EXPORT`.

### Forward

Each Backend declares capabilities; the framework enables/disables workflows based on what the Backend supports rather than on its class.

```
Backend (ABC)
  ├── capabilities: frozenset[Capability]           # PERSISTENT_WORKERS, BATCH_FALLBACK,
  │                                                  # FMU_EXPORT, ...
  ├── produced_datasets: frozenset[DatasetType]    # {TimeSeries, Events}, or {TimeSeries} for data-file ingest
  ├── run_tests(tests) → list[BatchManifest]       # orchestration (persistent workers / batch / ingest)
  ├── read_result(test) → Dataset                  # typed result (not only TimeSeries)
  └── export_fmu(test) → Path                      # optional; present iff FMU_EXPORT is declared
```

Concrete targets (roadmap): `DymolaBackend` (implemented), `FmpyBackend` (Phase 2 — implemented), `OpenModelicaBackend` (D69–D70 — implemented), `JuliaBackend` (D77–D79 — implemented; Dyad untested but should work via same path), `PythonBackend` (D80 — implemented), `MatlabBackend` (future), `DataFileBackend` (experiments — future).
