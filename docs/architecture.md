# Architecture

## Package Layout

```
src/modelica_testing/
├── cli.py                    # argparse CLI: discover, run, compare, export, migrate, manifest, convert, add
├── config.py                 # Config dataclass, path resolution, testing.json loading
├── discovery/
│   ├── mo_parser.py          # Scans .mo files for UnitTests components
│   ├── spec_parser.py        # Parses test_spec.json (external test definitions)
│   └── test_registry.py      # TestModel dataclass, discover_tests() merges both sources
├── simulators/
│   ├── base.py               # SimulatorRunner ABC, VariableResult, TestResult, BatchManifest
│   └── dymola/
│       ├── runner.py          # DymolaRunner: batch .mos generation and execution
│       ├── mat_reader.py      # Parses Dymola .mat (MAT4) result files via scipy
│       └── log_parser.py      # Parses dslog.txt for statistics (nonlinear systems, states, etc.)
├── comparison/
│   └── comparator.py         # NRMSE comparison with piecewise event handling
├── storage/
│   ├── reference_store.py    # TestManifest + ReferenceStore (per-test JSON files)
│   ├── convert.py            # Bidirectional format conversion
│   └── migrate.py            # Migration from buildingspy format
├── reporting/
│   ├── console_report.py     # Terminal output with pass/fail, NRMSE, structural warnings
│   ├── junit_report.py       # JUnit XML for CI
│   ├── html_report.py        # HTML summary report
│   └── plot_comparison.py    # Per-variable PNG plots + HTML viewer with stats tables
└── tools/
    └── verify_migration.py   # Post-migration verification
```

## Data Flow

```
discover_tests(config)
    → mo_parser scans .mo files for UnitTests
    → spec_parser reads test_spec.json
    → test_registry merges both into list[TestModel]

runner.run_tests(tests)
    → generates startup.mos, per-test .mos, shutdown.mos, batch .mos
    → launches Dymola subprocess(es)
    → returns list[BatchManifest] with TestRunResult per test

runner.read_results(manifests, tests)
    → reads .mat files via mat_reader
    → resolves variable patterns against available names
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
| `TestResult` | `simulators/base.py` | Simulation output: success flag, list of VariableResult |
| `VariableResult` | `simulators/base.py` | One variable's time series (time + values arrays) |
| `TestComparison` | `comparison/comparator.py` | Comparison result: pass/fail, per-variable NRMSE, warnings |
| `ReferenceStore` | `storage/reference_store.py` | CRUD for reference JSON files via TestManifest |
| `TestManifest` | `storage/reference_store.py` | Maps stable numeric IDs ↔ model IDs |
| `BatchManifest` | `simulators/base.py` | Maps test keys to model IDs within a batch run |

## Reference File Structure

`<reference_root>/test_manifest.json` — shared across all simulators/OS:
```json
{"version": 1, "tests": {"0001": {"model_id": "Lib.Examples.Test1", "status": "active"}}}
```

`<reference_root>/<Simulator>/<os>/ref_0001.json` — per simulator+OS:
```json
{
  "model_id": "...", "test_id": "0001", "last_updated": "...",
  "simulation": {"stop_time": 100, "tolerance": 1e-4, "method": "Dassl"},
  "statistics": {"initialization": {...}, "simulation": {...}},
  "n_vars": 3,
  "time": [0.0, 0.1, ...],
  "variables": [{"index": 1, "name": "pipe.T[1]", "values": [...]}]
}
```

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
