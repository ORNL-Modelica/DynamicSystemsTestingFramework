# Usage Guide

## Setup

### Configuration file (`testing.json`)

Create a `testing.json` in your reference results directory. This is the single entry point for the tool — it tells it where the library is, which simulator to use, and how to run tests.

```json
{
  "source_path": "../../MyLibrary/MyLib",
  "simulator": "Dymola",
  "simulators": {
    "Dymola": [
      "C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe",
      "C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"
    ],
    "Dymola 2025": [
      "C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"
    ],
    "Dymola 2026": [
      "C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"
    ]
  },
  "dependencies": [],
  "test_spec": "test_spec.json"
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `source_path` | No | Path to library source location (Modelica package.mo directory, FMU directory, etc.). Relative to `testing.json` location. Can be provided via `--source-path` instead. |
| `simulator` | No | Simulator name (default: `"Dymola"`). Use named entries like `"Dymola 2025"` to target specific versions. |
| `simulators` | No | Map of simulator names to candidate executable paths (first existing path wins). Falls back to system PATH. |
| `dependencies` | No | Paths to dependency library roots loaded before simulation. Relative to `testing.json` location. |
| `simulator_setup` | No | Modelica commands run after library loading. For user-specific settings only — `OutputCPUtime` and translation log capture are handled automatically by the Dymola runner. |
| `test_spec` | No | Path to external test definitions file. Relative to `testing.json` location. |
| `diagnostic_variables` | No | Variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`). |
| `reference_root` | No | Override where references are stored. Defaults to the directory containing `testing.json` if it's named `ReferenceResults`, otherwise `<repo>/Resources/ReferenceResults`. |

All relative paths resolve from where `testing.json` is located.

### Test definitions

Tests are discovered from three sources (can be combined; merged by model_id, last writer wins per field):

**1. Bundled in-model recognizer** — finds any class with the `ModelicaTestingLib.Components.UnitTests` component plus the standard `experiment(...)` annotation. Scans `.mo` files automatically, no setup needed.

**2. User-provided recognizers in `testing.json`** (Phase 5 / PTA) — declarative JSON; finds tests via your library's own convention without forcing adoption of `UnitTests`. Example:

```json
{
  "source_path": "../../",
  "simulator": "Dymola",
  "recognizers": [
    {
      "name": "mylib:icons-example-as-simulate-only",
      "applies_to": ["modelica"],
      "match": {
        "type": "extends",
        "class_pattern": "*Icons.Example"
      },
      "fields": {
        "simulate_only": {"from": "constant", "value": true},
        "stop_time": {"from": "experiment-annotation", "name": "StopTime"}
      }
    }
  ],
  "disable_bundled": []
}
```

Modelica match types: `component-instantiation` (`component_name`), `extends` (`class_pattern` glob). Field sources: `parameter` (from matched component), `constant` (literal value), `experiment-annotation` (from standard `experiment(...)` block). Per-field merge with last-writer-wins lets a user recognizer override individual fields without disabling bundled. To turn the bundled recognizer off entirely (e.g. when a dependency lib's example tests would otherwise be discovered), add its name to `disable_bundled`: `["modelica:bundled-unit-tests"]`. See `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json` for a working demo.

**3. External `test_spec.json`** — for per-test overrides, MetricTrees, or variable specifications independent of any in-model annotation:

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.SimpleTest",
      "variables": ["pipe.T[1]", "tank.level"],
      "simulation": {
        "stop_time": 100,
        "tolerance": 1e-4,
        "timeout": 60
      },
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"tolerance": 0.1}
        }
      }
    },
    {
      "model": "MyLib.Examples.AnotherTest",
      "variables": ["*"],
      "simulation": {"stop_time": 500}
    }
  ]
}
```

Variable patterns support `*` and `?` wildcards. `["*"]` tracks all non-parameter variables. `[]` (empty list) simulates without tracking variables.

**Per-test fields inside `simulation` (all optional):**

| Field | Type | Description |
|---|---|---|
| `stop_time` | float | Simulation end time. Overrides the model's `experiment` annotation. |
| `tolerance` | float | Solver tolerance. |
| `method` | string | Solver method name (e.g. `"Dassl"`, `"Esdirk45a"`). |
| `number_of_intervals` | int | Output sample count. |
| `output_interval` | float | Output interval (alternative to `number_of_intervals`). |
| `timeout` | int | Per-test timeout in seconds. Overrides the global `--timeout` flag. Honored by both backends — Dymola via the persistent-runner watchdog, FMPy via `concurrent.futures` (worker thread is left to finish in background since FMPy runs in-process). |

**FMU backend only**: add an `"fmu": "path/to/Model.fmu"` field (path relative to the spec file) to point at a prebuilt FMU.

**MetricTree**: add a top-level `"metrics"` block to author an explicit pass/fail tree — see `docs/extensibility.md` §6 for the schema.

### Multiple named baselines

A reference file can carry more than one baseline: the `primary` baseline (what the framework writes on `--accept`, stored at the flat top level) plus any number of named baselines under a `baselines` map — `experiment`, `analytical`, `dymola`, etc. MetricTree leaves can pick which baseline to score against via `"against": "<name>"` (default is `"primary"`):

```json
{
  "model": "MyLib.HeatExchanger",
  "variables": ["T"],
  "metrics": {
    "combinator": "and",
    "children": [
      {"metric": "nrmse", "variable": "T", "tolerance": 0.01},
      {"combinator": "warn", "children": [
        {"metric": "nrmse", "variable": "T", "against": "experiment", "tolerance": 0.1}
      ]}
    ]
  }
}
```

Non-primary baselines can be added programmatically:

```python
from modelica_testing.config import Config
from modelica_testing.storage.reference_store import ReferenceStore

config = Config(config_file="path/to/testing.json")
store = ReferenceStore(config)
store.add_named_baseline(
    model_id="MyLib.HeatExchanger",
    name="experiment",
    time=[0.0, 1.0, 2.0],
    variables=[{"index": 1, "name": "T", "values": [300.0, 310.5, 315.2]}],
    provenance={"origin": "rig-run-2026-04-17", "citation": "Report Q2"},
)
```

The model must already have a primary baseline (run with `--accept` once). Primary stays untouched by subsequent accepts — see D47 in `docs/decisions.md` for schema details.

---

## Commands

All commands accept these global options:

```
--config PATH          Path to testing.json (default: auto-search)
--source-path PATH     Path to library source location (Modelica package dir, FMU dir, ...)
--reference-root PATH  Path to reference results root
--test-spec PATH       Path to test_spec.json
```

If `testing.json` includes `source_path`, you only need `--config` or `--reference-root`.

### `discover` — Find tests

Lists all discoverable tests without running anything.

```bash
# All tests
uv run modelica-testing --config testing.json discover

# Filter by package
uv run modelica-testing --config testing.json discover --package MyLib.Fluid

# Filter by glob pattern
uv run modelica-testing --config testing.json discover --filter "*Pipe*"
```

Output shows model ID, variable count, stop time, solver method, and source (unit_tests, spec, or both).

### `run` — Simulate and compare

Runs simulations and compares against stored reference baselines.

```bash
# Run all tests, report pass/fail
uv run modelica-testing --config testing.json run

# Run a subset
uv run modelica-testing --config testing.json run --package MyLib.Fluid

# Run with 4 parallel Dymola instances
uv run modelica-testing --config testing.json run --parallel 4

# Accept all results as new baselines (first run, or after intentional changes)
uv run modelica-testing --config testing.json run --accept

# Interactive review — decide per test
uv run modelica-testing --config testing.json run -i
```

**Options:**

| Flag | Description |
|------|-------------|
| `--accept` | Store all results as new reference baselines |
| `-i`, `--interactive` | Review each test interactively |
| `--parallel N` | Number of parallel Dymola instances |
| `--simulator NAME` | Override simulator (e.g., `"Dymola 2025"`) |
| `--simulator-path PATH` | Override simulator executable path |
| `--show-ide` | Show Dymola GUI instead of headless |
| `--tolerance FLOAT` | Override NRMSE comparison tolerance |
| `--final-only` | Compare only final values (not full trajectories) |
| `--timeout SECS` | Per-test timeout in seconds (default: 600) |
| `--work-dir PATH` | Override output directory |
| `--report-format` | `console` (default), `junit`, or `html` |

**Interactive mode (`-i`) actions:**

| Key | Action |
|-----|--------|
| `a` | Accept this test's results as new baseline |
| `s` | Skip (don't update baseline) |
| `d` | Show detailed per-variable comparison |
| `p` | Generate plots and open in browser |
| `v` | Add variable patterns to track (updates test_spec.json) |
| `q` | Quit interactive review |

### `compare` — Re-compare without simulating

Uses the last simulation results (from `testing_output/`) and compares against current references. Useful after changing tolerances or updating references externally.

```bash
uv run modelica-testing --config testing.json compare

# With different tolerance
uv run modelica-testing --config testing.json compare --tolerance 0.001

# Only check final values
uv run modelica-testing --config testing.json compare --final-only

# Output as JUnit XML (for CI)
uv run modelica-testing --config testing.json compare --report-format junit
```

### `export` — Dump reference data

Exports stored reference data to JSON or CSV for external inspection.

```bash
# Export all references as JSON
uv run modelica-testing --config testing.json export

# Export as CSV
uv run modelica-testing --config testing.json export --format csv

# Export to specific file
uv run modelica-testing --config testing.json export --output results.csv --format csv

# Export subset
uv run modelica-testing --config testing.json export --package MyLib.Fluid
```

### `manifest` — Manage test IDs

Each test gets a stable numeric ID (e.g., `ref_0001.json`). The manifest tracks the mapping between IDs and model paths.

```bash
# Show all active tests and their IDs
uv run modelica-testing --config testing.json manifest show

# Also show obsolete (retired) tests
uv run modelica-testing --config testing.json manifest show --show-obsolete

# Delete reference files for obsolete tests
uv run modelica-testing --config testing.json manifest cleanup

# Rebuild manifest from currently discovered tests
uv run modelica-testing --config testing.json manifest rebuild
```

### `add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file.

```bash
# Add a test with specific variables
uv run modelica-testing --config testing.json add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run modelica-testing --config testing.json add MyLib.Examples.NewTest
```

---

## Typical workflows

### First-time setup for a new library

```bash
# 1. Create reference directory and testing.json
mkdir -p /path/to/MyLib-Tests/ReferenceResults
# Edit testing.json with source_path, simulator, etc.

# 2. Verify discovery
uv run modelica-testing --config /path/to/testing.json discover

# 3. Run all tests and accept as initial baselines
uv run modelica-testing --config /path/to/testing.json run --accept
```

### Routine regression testing

```bash
# Run tests after code changes
uv run modelica-testing --config testing.json run

# If tests fail, review interactively
uv run modelica-testing --config testing.json run -i
```

### CI integration

```bash
# JUnit output for CI systems
uv run modelica-testing --config testing.json run --report-format junit

# HTML report
uv run modelica-testing --config testing.json run --report-format html
```

---

## Reference file structure

```
ReferenceResults/
├── testing.json              # Configuration
├── test_spec.json            # External test definitions (optional)
└── Dymola/
    └── windows/
        ├── ref_0001.json     # One file per test (self-contained)
        ├── ref_0002.json
        └── ...
```

References are partitioned by simulator backend and OS because different solvers and platforms produce numerically different results. No manifest file is needed — each ref file contains its own `model_id`, `test_id`, `status`, `date_added`, and `last_updated`. The index is rebuilt in memory by scanning ref files at startup.

Each ref file also contains simulation params, statistics, diagnostic trajectories (`CPUtime`, `EventCounter` when `OutputCPUtime := true;`), and the time/variable data.

**Status field:** Each ref file has a `status` field:
- `active` — normal, included in test runs
- `skip` — temporarily excluded from comparison
- `obsolete` — pending deletion (remove with `manifest cleanup`)

---

## Development

### Setup

```bash
# Install the package in editable mode (links to source, changes take effect immediately)
uv pip install -e .

# Install with dev dependencies (includes pytest)
uv pip install -e ".[dev]"
```

### Running the test suite

```bash
# Run all tests
uv run pytest

# Verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_comparator.py

# Run a specific test class or method
uv run pytest tests/test_comparator.py::TestCompareTrajectories::test_identical_with_single_event
```

Tests cover: NRMSE comparison with event handling, config path resolution, `.mo` parsing, test spec parsing, manifest management, reference store round-trips, dslog parsing, `.mat` file reading, and variable pattern matching.

Tests that require Dymola are marked with `@pytest.mark.dymola` and can be skipped with:

```bash
uv run pytest -m "not dymola"
```

### Testing ModelicaTestingLib itself

The project includes `examples/modelica/ModelicaTestingLib/`, a small Modelica library used both as a test fixture and as a reference implementation. To regression test it (requires Dymola):

```bash
# Run and compare against stored references
uv run modelica-testing --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run

# Accept new baselines after intentional changes
uv run modelica-testing --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run --accept
```
