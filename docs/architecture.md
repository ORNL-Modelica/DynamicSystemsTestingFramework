# Architecture

## Project Layout

```
ModelicaTesting/
├── ModelicaTestingLib/               # Modelica library (test fixture + reference implementation)
│   ├── Components/UnitTests.mo      # Reusable UnitTests component
│   ├── Examples/                    # SimpleTest, EventTest, ConstantTest, IntervalTest, NoUnitTest
│   └── Resources/ReferenceResults/  # testing.json + baselines for this library
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
│   ├── base.py               # SimulatorRunner ABC, VariableResult, TestResult, BatchManifest
│   └── dymola/
│       ├── runner.py          # DymolaRunner: batch .mos generation and execution
│       ├── mat_reader.py      # Custom MAT4 binary parser with numpy.memmap for selective reads
│       └── log_parser.py      # Parses dslog.txt + translation_log.txt for statistics
├── comparison/
│   └── comparator.py         # NRMSE comparison with piecewise event handling
├── storage/
│   └── reference_store.py    # RefIndex + ReferenceStore (per-test JSON files)
└── reporting/
    ├── console_report.py     # Terminal output with pass/fail, NRMSE, structural warnings
    ├── junit_report.py       # JUnit XML for CI
    ├── html_report.py        # HTML summary report
    └── plot_comparison.py    # Per-variable PNG plots + HTML viewer with stats tables
```

## Data Flow

```
discover_tests(config)
    → mo_parser scans .mo files for UnitTests
    → spec_parser reads test_spec.json
    → test_registry merges both into list[TestModel]

runner.run_tests(tests)
    → generates startup.mos (loads libs, enables OutputCPUtime + TranslationInCommandLog)
    → generates per-test .mos (clearlog, simulateModel, savelog)
    → generates shutdown.mos, batch .mos
    → launches Dymola subprocess(es)
    → parses dslog.txt (runtime stats) + translation_log.txt (structural stats) per test
    → returns list[BatchManifest] with TestRunResult per test

runner.read_results(manifests, tests)
    → lists variable names from .mat (fast header scan, no data loaded)
    → resolves variable patterns against available names
    → reads only needed variables from .mat via numpy.memmap (selective row access)
    → auto-captures diagnostic variables (configurable, default: CPUtime, EventCounter)
    → returns dict[model_id → TestResult]

compare_all(tests, results, store, config)
    → loads reference JSON per test from ReferenceStore
    → piecewise NRMSE comparison per variable
    → returns list[TestComparison]

reporters render TestComparison → console / JUnit / HTML / plots
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

No persistent manifest file for the index. The in-memory `RefIndex` is built by scanning ref files at startup.
Valid statuses: `active` (normal), `skip` (temporarily excluded), `obsolete` (pending deletion).

Two manifest files are written to the work directory before simulation starts:
- `batch_manifest.json` — maps `test_key -> {"model_id": "...", "ref_id": "ref_NNNN"}` for all tests in the batch
- `reference_manifest.json` — maps ref IDs to model names; also available via `manifest dump` CLI command

## Simulator Abstraction

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
