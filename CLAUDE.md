# CLAUDE.md

## Project Overview

**ModelicaTesting** is a standalone Python tool for regression testing Modelica libraries. It is library-agnostic — it works with any Modelica library that uses the `UnitTests` pattern for tracking simulation variables, or with external test specifications (`test_spec.json`).

The tool discovers tests by scanning `.mo` files and/or reading `test_spec.json`, runs simulations via Dymola (batch mode), compares results against stored references using NRMSE or tube-based envelope comparison, and reports pass/fail.

## Project Structure

```
ModelicaTesting/
├── src/
│   └── modelica_testing/        # Python package (src layout)
│       ├── cli.py               # CLI: discover, run, compare, export, manifest, add, spec-update
│       ├── config.py            # Config dataclass, path resolution, testing.json loading
│       ├── discovery/           # Test discovery: scan .mo for UnitTests, parse test_spec.json
│       ├── simulators/          # Abstract runner + Dymola backend (batch .mos, .mat reader, dslog parser)
│       ├── comparison/          # NRMSE and tube comparison with piecewise event handling
│       ├── storage/             # JSON reference storage with in-memory index
│       └── reporting/           # Console, JUnit XML, HTML reporters, plot generation
│           └── templates/       # Jinja2 templates (comparison.html, interactive.html) + comparison_data.json sidecar
├── ModelicaTestingLib/          # Modelica library: UnitTests component + example models
│   ├── Components/UnitTests.mo  # Reusable UnitTests component for tracking variables
│   ├── Examples/                # SimpleTest, EventTest, ConstantTest, IntervalTest, NoUnitTest
│   └── Resources/ReferenceResults/  # testing.json + reference baselines for this library
├── tests/                       # pytest test suite (174 tests)
│   ├── fixtures/                # Test data: dslog.txt, .mat file, test_spec.json
│   └── test_*.py                # Comparator, config, discovery, storage, simulators
├── docs/                        # Design decisions, patterns, architecture, constraints, usage
├── pyproject.toml               # uv/hatchling project config
└── CLAUDE.md
```

## Running the Tool

```bash
# With testing.json containing package_path — single entry point
uv run python -m modelica_testing --config path/to/testing.json run

# Or with explicit flags
uv run python -m modelica_testing --package-path /path/to/MyLib --reference-root /path/to/refs run

# Interactive review (accept/skip/plot per test)
uv run python -m modelica_testing --config testing.json run -i

# Interactive review filtered to specific categories
uv run python -m modelica_testing --config testing.json run -i failed
uv run python -m modelica_testing --config testing.json run -i no-baseline
# Categories: failed, no-baseline, warnings, sim-failed, passed, all

# Accept all results as new baselines
uv run python -m modelica_testing --config testing.json run --accept

# Generate HTML report with per-test plots (static + interactive Plotly)
uv run python -m modelica_testing --config testing.json run --report ./reports

# Parallel run with small-batch queue dispatch (better load balancing + crash isolation)
uv run python -m modelica_testing --config testing.json run --parallel 4 --batch-size 3
# Live progress: open work_dir/dashboard.html (auto-refreshes every 2s; URL printed on start)

# Filter accepts: glob, comma-separated list, or @file (one pattern per line, # comments)
uv run python -m modelica_testing --config testing.json run --filter "Foo.A,Foo.B"
uv run python -m modelica_testing --config testing.json run --filter @rerun.txt

# Incremental rerun + full merged report (rerun a subset, report covers everything)
uv run python -m modelica_testing --config testing.json run --filter @failed.txt --merge --report

# Auto-rerun previously failed tests (implies --merge)
uv run python -m modelica_testing --config testing.json run --rerun --report
# Or pick categories: failed, no-baseline, warnings, sim-failed, passed
uv run python -m modelica_testing --config testing.json run --rerun failed,sim-failed --report

# Compare without re-running simulations (uses last results)
uv run python -m modelica_testing --config testing.json compare

# Apply tolerance config exported from interactive report to test_spec.json
uv run python -m modelica_testing --config testing.json spec-update tolerance_config.json

# Dump reference manifest (ref ID to model name mapping) without running tests
uv run python -m modelica_testing --config testing.json manifest dump

# Prune orphan manifest entries (models no longer in discovery — dry-run by default)
uv run python -m modelica_testing --config testing.json manifest cleanup --orphans
uv run python -m modelica_testing --config testing.json manifest cleanup --orphans --apply
```

## Running Tests

```bash
uv pip install -e ".[dev]"    # One-time: install package + pytest
uv run pytest                  # Run the test suite
```

## Configuration

The tool looks for `testing.json` near the library root or reference root. Key fields:

- `package_path` — path to library's package.mo directory (relative to testing.json)
- `simulator` — named entry like `"Dymola"` or `"Dymola 2025"`
- `simulators` — map of simulator names to candidate executable paths
- `simulator_setup` — list of Modelica commands run after library loading (user-specific settings)
- `dependencies` — paths to dependency library roots loaded before simulation
- `reference_root` — where reference results live (default: `<repo>/Resources/ReferenceResults`)
- `test_spec` — path to external test definitions file
- `tolerance` — global NRMSE comparison tolerance (default: `1e-4`)
- `diagnostic_variables` — variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`)

Note: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are hardcoded in the Dymola runner — no need to add them to `simulator_setup`.

Reference results are partitioned by `<reference_root>/<SimulatorBackend>/<os>/`.

### test_spec.json format

Simulation parameters live under a `simulation` key, comparison settings under a `comparison` key. Both are optional — minimal entries need only `model` and `variables`:

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.SimpleTest",
      "variables": ["pipe.T[1]", "pipe.m_flow"],
      "simulation": {"stop_time": 100, "tolerance": 1e-6},
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"tolerance": 0.1},
          "pipe.p[1]": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02}
        }
      }
    }
  ]
}
```

### Comparison modes

**NRMSE** (default): `NRMSE = RMSE / signal_range`. Pass if below tolerance.

**Tube**: envelope around the reference trajectory. Configured per-variable via `variable_overrides` with `"mode": "tube"`. Three width modes controlled by `tube_width_mode`:
- `"rel"` (default in interactive UI): width = fraction of |reference| (e.g., `"tube_rel": 0.02` = 2%)
- `"band"` (or legacy `"abs"`): width = offset in signal units (e.g., `"tube_abs": 500`)
- `"absolute"`: upper/lower are literal y-axis values (not offsets from reference)

Legacy format (no `tube_width_mode`): width = `max(tube_abs, tube_rel * |reference|)`. Pass if every point stays inside the tube. Supports time-varying tubes via `tube_points` with linear or stepwise interpolation.

### Tolerance resolution order

Per-variable override (spec) > per-variable override (reference JSON) > per-test comparison tolerance > reference JSON comparison tolerance > config.tolerance > default (1e-4). When accepting results, comparison settings are saved in the reference JSON's `comparison` section so tolerances travel with the baseline.


## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults; passed to runners and reporters but not to comparison functions
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test with model ID, simulation params, tracked variables, source
- **`SimulatorRunner`** (`simulators/base.py`) — abstract interface; backends self-register via `@register` decorator; `get_runner(config)` factory in `simulators/__init__.py`
- **`DymolaRunner`** (`simulators/dymola/runner.py`) — batch execution backend; `DymolaConfig` dataclass extracts Dymola-specific settings from Config
- **`ReferenceStore`** (`storage/reference_store.py`) — CRUD for per-test JSON reference files; `RefIndex` built in-memory from scanning ref files
- **`ComparisonMode`** (`comparison/modes.py`) — strategy pattern for variable comparison: `NrmseMode`, `TubeMode`, `FinalOnlyMode` with typed config dataclasses; `resolve_mode()` factory builds mode from per-variable override dict
- **`comparator`** (`comparison/comparator.py`) — orchestrates per-test comparison; `compare_test()` takes `default_tolerance` and `final_only` (not Config); delegates per-variable comparison to `ComparisonMode` strategies

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo`, all paths configurable
2. **Simulator-agnostic**: Dymola-specific code isolated in `simulators/dymola/`; abstract `SimulatorRunner` interface with registry pattern
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with model ID inside each file; IDs never reused; in-memory index built by scanning ref files (no persistent manifest)
4. **Reference partitioning**: results split by simulator backend and OS since solvers produce platform-specific results
5. **Batch execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes
7. **Strategy over conditionals**: comparison modes and simulator backends use strategy/registry patterns instead of if/elif dispatch
