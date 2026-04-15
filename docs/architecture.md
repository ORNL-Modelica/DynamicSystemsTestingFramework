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
    │               (NRMSE, tube, final-only, event-timing, spectral, Fréchet, KS, user-defined)
    ▼
  MetricTree      → composition: AND / OR / weighted / K-of-N → overall pass/fail + diagnostics
```

**Backend capabilities** are declared on the runner contract, not inferred from type:
- `supports_persistent_workers`, `supports_batch_fallback`, `supports_fmu_export`,
  `supports_experiment_ingest`, `produced_datasets`.
Features (e.g. cross-backend verification, which chains `Backend.export_fmu()` → `FMPy.simulate()`) are composed from capabilities, not hardcoded modes.

### Layer ↔ Code mapping (Current)

| Layer | Current implementation | Status |
|---|---|---|
| Source | Modelica `package.mo` directory (via `config.package_path`); `config.source_type = "modelica"` declared | single concrete type; `source_type` field reserved for Phase 2+ backends |
| Discovery | `discovery/mo_parser.py` + `discovery/spec_parser.py` | two modes, both Modelica-flavored |
| Backend | `simulators/` registry; only `DymolaRunner` implemented; declares `capabilities` + `produced_datasets` | registry + capabilities contract exercised by one backend; second backend lands in Phase 2 |
| Dataset | Implicit `TimeSeries` returned by `read_result()`; `DatasetType` enum declared on backends | one concrete type materialized; additional types reserved for Phase 3 metrics |
| Metric | `comparison/modes.py`: `NrmseMode`, `TubeMode`, `FinalOnlyMode`; `VariableComparison` carries `diagnostics: dict` for structured extras | strategy pattern in place; diagnostics bag ready for future metrics |
| MetricTree | `comparison/metric_tree.py`: `MetricResult` + `AndCombinator` / `OrCombinator` / `KOfNCombinator` / `WarnCombinator`; `implicit_and_tree()` matches current flat-AND semantics | abstraction exists + unit-tested; pipeline still uses flat AND directly (wiring is a future change) |
| Reference | Hybrid-schema storage; `Baseline` view presents named baselines uniformly | primary baseline flat-at-top-level; additional baselines under optional `baselines` key |

Forward work is to make layers increasingly *user-driven* — most contracts exist; what's left is wiring the MetricTree and letting user-authored `test_spec.json` drive composition. See [extensibility.md](extensibility.md) for per-layer plug-in contracts.

---

## Project Layout

```
ModelicaTesting/
├── examples/
│   └── modelica/
│       └── ModelicaTestingLib/      # Modelica demo library (test fixture + reference implementation)
│           ├── Components/UnitTests.mo   # Reusable UnitTests component
│           ├── Examples/                 # SimpleTest, EventTest, ConstantTest, IntervalTest, NoUnitTest
│           └── Resources/ReferenceResults/  # testing.json + baselines for this library
├── tests/                           # pytest suite
│   ├── fixtures/results/Dymola/     # Real Dymola artifacts (.mat, dslog.txt, etc.)
│   └── test_*.py                    # Unit tests for all modules
```

## Package Layout

```
src/modelica_testing/
├── cli.py                    # argparse CLI: discover, run, compare, export, manifest (cleanup/dump), add
├── config.py                 # Config dataclass, path resolution, testing.json loading
├── discovery/
│   ├── mo_parser.py          # Scans .mo files for UnitTests components
│   ├── spec_parser.py        # Parses test_spec.json (external test definitions)
│   └── test_registry.py      # TestModel dataclass, discover_tests() merges both sources
├── simulators/
│   ├── __init__.py           # Simulator registry: @register decorator, get_runner() factory
│   ├── base.py               # SimulatorRunner ABC, VariableResult, TestResult, BatchManifest
│   ├── progress.py           # Backend-agnostic ProgressReporter (status.json + auto-refresh dashboard.html)
│   └── dymola/
│       ├── runner.py          # DymolaRunner (@register("Dymola")), DymolaConfig, batch .mos generation, queue-dispatched batches
│       ├── mat_reader.py      # Custom MAT4 binary parser with numpy.memmap for selective reads
│       └── log_parser.py      # Parses dslog.txt + translation_log.txt for statistics
├── comparison/
│   ├── comparator.py         # compare_test/compare_all orchestration, piecewise NRMSE, tube, final-value
│   └── modes.py              # ComparisonMode ABC, NrmseMode/TubeMode/FinalOnlyMode, typed configs, resolve_mode()
├── storage/
│   └── reference_store.py    # RefIndex + ReferenceStore (per-test JSON files)
└── reporting/
    ├── console_report.py     # Terminal output with pass/fail, NRMSE, structural warnings
    ├── junit_report.py       # JUnit XML for CI
    ├── html_report.py        # Builds context dict, renders Jinja2 template, writes comparison_data.json sidecar
    ├── templates/
    │   ├── comparison.html   # Jinja2 template: static matplotlib-based report with progressive disclosure
    │   └── interactive.html  # Jinja2 template: interactive Plotly.js report (zoom, pan, hover, live tolerance editing)
    └── plot_comparison.py    # Per-variable PNG plots + HTML viewer with stats tables
```

## Data Flow

```
discover_tests(config)
    → mo_parser scans .mo files for UnitTests
    → spec_parser reads test_spec.json
    → test_registry merges both into list[TestModel]

runner.run_tests(tests)
    → creates ProgressReporter, registers each test (with report_dir from runner.ref_id_map)
    → generates startup.mos (loads libs, enables OutputCPUtime + TranslationInCommandLog)
    → generates per-test .mos (clearlog, simulateModel, savelog)
    → generates shutdown.mos, per-batch .mos scripts
    → splits tests into batches: config.batch_size if set, else ceil(total/parallel)
    → submits all batches to ThreadPoolExecutor (queue-dispatched; worker_id from thread slot)
    → emits progress.on_start when batch begins, progress.on_finish per test as results parsed
    → parses dslog.txt (runtime stats) + translation_log.txt (structural stats) per test
    → progress.finalize() strips auto-refresh from dashboard.html
    → returns list[BatchManifest] with TestRunResult per test

runner.read_results(manifests, tests)
    → lists variable names from .mat (fast header scan, no data loaded)
    → resolves variable patterns against available names
    → reads only needed variables from .mat via numpy.memmap (selective row access)
    → auto-captures diagnostic variables (configurable, default: CPUtime, EventCounter)
    → returns dict[model_id → TestResult]

compare_all(tests, results, store, default_tolerance, final_only)
    → loads reference JSON per test from ReferenceStore
    → per-variable: resolve_mode(override, tolerance, final_only) → ComparisonMode
    → mode.compare(ref_time, ref_values, act_time, act_values) → VariableComparison
    → NrmseMode: piecewise NRMSE with event boundary handling
    → TubeMode: envelope check with three width modes (rel, band, absolute)
    → FinalOnlyMode: compare only final values
    → returns list[TestComparison]

reporters render TestComparison → console / JUnit / HTML / plots
    → HTML reporter builds a context dict and renders via Jinja2 templates
    → generates comparison.html (static matplotlib) and interactive.html (Plotly.js)
    → per-test report directories named ref_NNNN (has reference) or test_NNNN (no baseline)
    → writes comparison_data.json sidecar (includes full trajectory data) for downstream tooling
```

## Key Types

| Type | Location | Purpose |
|------|----------|---------|
| `Config` | `config.py` | All resolved paths, simulator settings, tolerances |
| `TestModel` | `discovery/test_registry.py` | One test: model ID, sim params, tracked variables, source |
| `TestResult` | `simulators/base.py` | Simulation output: success flag, variables, diagnostics, statistics |
| `VariableResult` | `simulators/base.py` | One variable's time series (time + values arrays) |
| `TestComparison` | `comparison/comparator.py` | Comparison result: pass/fail, per-variable NRMSE, warnings |
| `ReferenceStore` | `storage/reference_store.py` | CRUD for reference JSON files via RefIndex |
| `RefIndex` | `storage/reference_store.py` | In-memory index mapping model IDs ↔ numeric IDs (built by scanning ref files) |
| `BatchManifest` | `simulators/base.py` | Maps test keys to `{"model_id", "ref_id"}` within a batch run |

## Reference File Structure

`<reference_root>/<Simulator>/<os>/ref_0001.json` — per simulator+OS, self-contained:
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

### Multiple named baselines (hybrid schema, Phase 1.7)

The reference file supports multiple named baselines via a **hybrid schema**:

* The ``"primary"`` baseline is stored as flat top-level fields exactly as the
  original format — no change to existing files, no migration utility, no
  reader churn.
* *Additional* named baselines (``experiment``, ``analytical``, user-defined)
  live under an optional top-level ``baselines`` map. Primary is never
  duplicated inside this map.

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
  "variables": [...],

  // --- optional: additional named baselines ---
  "baselines": {
    "experiment": {
      "provenance": {
        "origin": "rig-run-2024-03-15",
        "captured_at": "2024-03-15T09:20:00Z",
        "notes": "Benchmark 3B, lab 2 test stand.",
        "citation": "Internal report XYZ-2024-042, §4.3"
      },
      "time": [...], "variables": [...]
    },
    "analytical": {
      "provenance": {"origin": "closed-form", "citation": "Nomura et al. 2019, eq. 12"},
      "time": [...], "variables": [...]
    }
  }
}
```

Readers use the ``Baseline`` view (``ReferenceStore.get_baseline(model_id, name="primary")``)
which adapts both cases uniformly: for a file with no ``baselines`` key, only
``primary`` is present (synthesized from flat fields, provenance filled in from
``date_added`` / ``last_updated``). For a hybrid file, ``primary`` plus any
additional entries are returned.

Writer preservation: accepting fresh primary results carries any non-primary
entries under ``baselines`` forward on rewrite; they are never clobbered.

Subsumes three distinct workflows in one schema:

* **Regression** — one primary baseline (current default).
* **Validation against experiment** — primary + experiment baseline; a metric
  referencing ``"against": "experiment"`` gates or warns on that comparison.
* **Cross-simulator / cross-backend verification** — multiple simulator
  baselines under different names; a metric compares them.

See ``src/modelica_testing/storage/reference_store.py`` for the ``Baseline``
dataclass and adapter logic. Comparator / reporter support for consuming
non-primary baselines is deferred to a later phase and driven by real use cases.

Two manifest files are written to the work directory before simulation starts:
- `batch_manifest.json` — maps `test_key -> {"model_id": "...", "ref_id": "ref_NNNN"}` for all tests in the batch
- `reference_manifest.json` — maps ref IDs to model names; also available via `manifest dump` CLI command

## Simulator Abstraction

### Current

```
SimulatorRunner (ABC)
  ├── run_tests(tests) → list[BatchManifest]      # override for batch execution
  ├── run_single_test(test, ...) → TestRunResult   # override for per-process execution
  ├── read_result(test, ...) → TestResult          # abstract — must implement
  └── read_results(manifests, tests) → dict        # shared implementation
        │
        └── DymolaRunner
              ├── run_tests() — batch .mos scripts, parallel workers
              └── read_result() — mat_reader + variable pattern resolution
```

### Forward

Rename to `Backend` (a `Runner` is one execution strategy *within* a Backend). Each Backend declares capabilities; the framework enables/disables workflows based on what the Backend supports rather than on its class.

```
Backend (ABC)
  ├── capabilities: frozenset[Capability]           # supports_persistent_workers, supports_batch_fallback,
  │                                                  # supports_fmu_export, supports_experiment_ingest, ...
  ├── produced_datasets: frozenset[DatasetType]    # {TimeSeries, Events}, or {TimeSeries} for data-file ingest
  ├── run_tests(tests) → list[BatchManifest]       # orchestration (persistent workers / batch / ingest)
  ├── read_result(test) → Dataset                  # typed result (not only TimeSeries)
  └── export_fmu(test) → Path                      # optional; present iff supports_fmu_export
```

Concrete targets (roadmap): `DymolaBackend` (current, refactored), `FmpyBackend` (Phase 2), `OmcBackend`, `JuliaBackend`, `MatlabBackend`, `DataFileBackend` (experiments).
