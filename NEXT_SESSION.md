# Next Session: Testing Framework Cleanup & Modularization

## Current State

The testing framework lives at `/testing/` inside the TRANSFORM-Library repo. It will be moved to its own repository. The framework is functional — it discovers tests, migrates old references, runs simulations in Dymola, compares results, and reports. But it was built incrementally and has Dymola-specific logic scattered throughout instead of isolated behind clean abstractions.

### What Works Today

- **Test discovery** (`discovery/`): Scans `.mo` files for `UnitTests` block instantiations, parses `experiment()` annotations, merges with `.mos` file parameters. Produces `TestModel` list.
- **Simulation** (`simulation/dymola_runner.py`): Generates per-test `.mos` scripts, runs Dymola with per-test timeout, parallel execution via ThreadPoolExecutor, captures dslog.txt statistics.
- **Result reading** (`simulation/result_reader.py`): Reads Dymola `.mat` files (MATLAB format via scipy), extracts `unitTests.x[1..n]` trajectories.
- **Reference storage** (`storage/reference_store.py`): JSON files with shared time array, per-variable values, simulation statistics. Index file for fast lookup. Compact JSON formatting (number arrays on single lines).
- **Migration** (`storage/migrate.py`): Converts old buildingspy `.txt` references to JSON format. Handles variable-length arrays via interpolation to shared time grid. Preserves statistics. Built-in verification (compares old vs new, generates summary + optional plots).
- **Comparison** (`comparison/comparator.py`): Mirrors `AbsRelRMS.mo` logic — absolute/relative error with machine-epsilon filtering, RMS aggregation. Supports full trajectory and final-value-only modes.
- **Reporting** (`reporting/`): Console (colored pass/fail), JUnit XML (for CI), HTML.
- **CLI** (`cli.py`): Subcommands: `discover`, `run`, `compare`, `export`, `migrate`.
- **Verification tool** (`tools/verify_migration.py`): Compares .txt source against migrated .json, produces CSV/text summary + optional per-variable plots.

### Reference JSON Format

```json
{
  "model_id": "TRANSFORM.Blocks.Examples.EasingRamp_Test",
  "last_updated": "2026-04-07T13:50:00.844000+00:00",
  "simulation": {
    "stop_time": 1.0, "tolerance": 0.0001, "method": "Dassl",
    "number_of_intervals": null
  },
  "n_vars": 2,
  "time": [ 0.0, 0.01, ..., 1.0 ],
  "variables": [
    { "index": 1, "expression": "easingRamp.y", "values": [ 1.0, ..., 2.0 ] },
    { "index": 2, "expression": "easingRamp1.y", "values": [ 1.0, ..., 2.0 ] }
  ],
  "statistics": {
    "initialization": { "numerical_jacobians": 0, "nonlinear": "2, 2" },
    "simulation": { "numerical_jacobians": 0 }
  }
}
```

### Configuration (`testing.json`)

```json
{
  "library_path": ".",
  "simulator": "Dymola",
  "mos_file": "runAll_Dymola.mos",
  "reference_root": "/path/to/references",
  "dependencies": ["/path/to/SomeDependency"],
  "path_abbreviations": { "HeatAndMassTransfer.ClosureRelations.": "HAMT_CR_" }
}
```

### Key Design Decisions Already Made

1. **Shared time array**: All variables in a reference share one time vector (matches Modelica spec — all tools write single time axis).
2. **Path abbreviations**: Configurable dict to shorten deeply nested package paths in filenames, solving Windows MAX_PATH issues.
3. **Per-test .mos scripts**: Each test gets its own script with numeric result file names (`test_0001.mat`), enabling per-test timeout and parallel execution.
4. **Reference partitioning**: `<reference_root>/<Simulator>/<os>/` (e.g., `Dymola/windows/`).
5. **Library-agnostic**: Auto-detects library name from `package.mo`, all paths configurable.
6. **Statistics capture**: Parses `dslog.txt` after each Dymola simulation for Jacobians, linear/nonlinear system sizes, continuous states, CPU time, events.
7. **UnitTests pattern**: Tests are discovered by scanning for `TRANSFORM.Utilities.ErrorAnalysis.UnitTests` instantiations. The `UnitTests` Modelica component will eventually move to its own library repo — the testing framework should not hardcode assumptions about where `UnitTests` lives. Discovery should be configurable (e.g., what component name to search for, or a pattern).

## Goals for This Session

### 1. Isolate Simulator-Specific Code

Currently Dymola-specific logic is in:
- `simulation/dymola_runner.py` — .mos script generation, Dymola CLI invocation, dslog.txt parsing
- `simulation/result_reader.py` — Dymola .mat file format (MATLAB via scipy)
- `simulation/dslog_parser.py` — Dymola-specific log format

**Target structure:**
```
simulation/
├── __init__.py
├── base.py              # Abstract SimulatorRunner interface
├── dymola/
│   ├── __init__.py
│   ├── runner.py         # Dymola-specific runner (generates .mos, invokes dymola CLI)
│   ├── result_reader.py  # Dymola .mat reader
│   ├── log_parser.py     # dslog.txt parser
│   └── mos_generator.py  # .mos script generation (per-test + runAll)
└── openmodelica/
    ├── __init__.py
    ├── runner.py         # OMEdit/omc runner (future)
    ├── result_reader.py  # OM result format reader (future)
    └── log_parser.py     # OM log parser (future)
```

Key interface:
```python
class SimulatorRunner(ABC):
    def generate_script(self, test: TestModel, work_dir: Path) -> Path: ...
    def run_test(self, test: TestModel, work_dir: Path, timeout: int) -> TestRunResult: ...
    def read_results(self, test: TestModel, work_dir: Path) -> TestResult: ...
    def parse_log(self, work_dir: Path) -> dict | None: ...
```

### 2. Eliminate Duplicate Code

Known duplication:
- `.mos` file parsing exists in both `discovery/mos_parser.py` (reading existing runAll) and `dymola_runner.py` (generating new .mos). The generation logic should also be able to produce a combined `runAll_*.mos`.
- Float array parsing is duplicated between `migrate.py` and `verify_migration.py`.
- `_filter_tests()` pattern (glob + package filter) could be shared utility.
- Config building from CLI args has repetitive `hasattr` checks.

### 3. Make UnitTests Pattern Configurable

Currently hardcodes searching for `UnitTests` and reading `unitTests.x[N]`. Make this configurable:
- What component to look for (e.g., `UnitTests`, `MyLibrary.Testing.Tracker`)
- What variable pattern to read from results (e.g., `unitTests.x[{i}]`, `tracker.output[{i}]`)
- Where the component lives (currently assumes `TRANSFORM.Utilities.ErrorAnalysis.UnitTests`)

### 4. Clean Up File Structure

Current:
```
testing/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── discovery/
│   ├── mo_parser.py
│   ├── mos_parser.py
│   └── test_registry.py
├── simulation/
│   ├── dymola_runner.py
│   ├── dslog_parser.py
│   └── result_reader.py
├── comparison/
│   └── comparator.py
├── storage/
│   ├── reference_store.py
│   └── migrate.py
├── reporting/
│   ├── console_report.py
│   ├── junit_report.py
│   └── html_report.py
└── tools/
    └── verify_migration.py
```

Target (proposed — adjust as needed):
```
modelica_testing/           # Renamed for standalone repo
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── discovery/
│   ├── mo_parser.py        # Parse .mo for test component instantiations
│   ├── mos_parser.py        # Parse existing .mos files for sim params
│   └── registry.py          # Orchestrate discovery, merge params
├── simulators/
│   ├── base.py              # Abstract interface
│   ├── dymola/
│   │   ├── runner.py
│   │   ├── reader.py
│   │   ├── log_parser.py
│   │   └── mos_writer.py    # Generate .mos (per-test + combined runAll)
│   └── openmodelica/
│       └── ...
├── comparison/
│   └── comparator.py
├── storage/
│   ├── store.py             # JSON reference CRUD
│   └── migrate.py           # Old format conversion
├── reporting/
│   ├── console.py
│   ├── junit.py
│   └── html.py
└── tools/
    └── verify_migration.py
```

### 5. Runnable Outputs

The framework should produce:
- Per-test `.mos` scripts (already done)
- A combined `runAll_<Simulator>.mos` that runs everything sequentially (already done via `discover --regenerate-mos`, but should be cleaner)
- Support running tests via Python (parallel, timeouts, progress) or via the generated combined .mos (simple batch mode for users who just want to run in Dymola)

## Files to Read First

Start by reading these to understand the current codebase:
1. `testing/config.py` — configuration and path resolution
2. `testing/cli.py` — CLI entry points and command routing
3. `testing/discovery/test_registry.py` — test discovery orchestration
4. `testing/simulation/dymola_runner.py` — the largest file, .mos generation + execution
5. `testing/simulation/result_reader.py` — .mat reading
6. `testing/simulation/dslog_parser.py` — log parsing
7. `testing/storage/reference_store.py` — JSON reference CRUD + compact formatting
8. `testing/comparison/comparator.py` — error calculation

## Context

- The `UnitTests` Modelica component (`TRANSFORM.Utilities.ErrorAnalysis.UnitTests`) currently lives in the TRANSFORM library. It will eventually be its own standalone Modelica library. The testing framework should not assume where it lives — just what variable pattern to look for in results.
- TRANSFORM has ~330 tests with 1-24 tracked variables each.
- The old buildingspy approach required patching buildingspy source in two places and had path length failures on Windows.
- Dependencies (other Modelica libraries) are loaded via `openModel()` in .mos scripts before the main library.
- Reference results can live in a separate repo from the library being tested.
- OS detection matters because Dymola can produce slightly different results on different platforms.
