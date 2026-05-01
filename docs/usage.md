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
        "method": "Dassl",
        "number_of_intervals": 500,
        "output_interval": null,
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
      "model": "MyLib.Examples.MinimalTest",
      "variables": ["*"]
    }
  ]
}
```

The first entry shows every field `simulation` accepts; the second shows the minimum (everything else falls through to the annotation, then to backend defaults). All `simulation.*` fields are optional.

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

#### Two-layer contract: annotation vs `test_spec.json`

Modelica simulation parameters resolve from two layers, in order:

1. **`experiment(...)` annotation** on the model — the source-of-truth defaults the model author encoded. Recognized fields: `StopTime`, `Tolerance`, `__Dymola_Algorithm` (→ `method`), `NumberOfIntervals` (or `__Dymola_NumberOfIntervals`), `Interval` (→ `output_interval`).
2. **`simulation.*` block in `test_spec.json`** — per-test override. Any field set here wins over the annotation.

Resolution rule: **user omits the field → annotation if present, else simulator default. User provides the field → it is used.** No third layer; no per-field "force annotation" toggle (delete the override to defer to the annotation).

Use case for the two layers: a model author can write `experiment(StopTime=100)` so the model demos cleanly in Dymola/OMEdit, then set `simulation.stop_time = 10` in `test_spec.json` so CI runs fast — no need to edit the source.

**Known gap**: `StartTime` from the annotation is not honored — every backend assumes `t=0`. Authoring `StartTime=5.0` in the annotation will be silently ignored. If you need a non-zero start, file an issue with the use case.

**Components.UnitTests** (the in-source `n=N, x={...}` block) is a **discovery marker** carrying the variable list only. It deliberately does not carry simulation parameters — those live in the annotation (model-author-facing, editor-honored) or in `test_spec.json` (test-author-facing, override-only). Keeping the third layer out is what makes the contract teachable.

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
from dstf.config import Config
from dstf.storage.reference_store import ReferenceStore

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
uv run dstf --config testing.json discover

# Filter by package
uv run dstf --config testing.json discover --package MyLib.Fluid

# Filter by glob pattern
uv run dstf --config testing.json discover --filter "*Pipe*"
```

Output shows model ID, variable count, stop time, solver method, and source (unit_tests, spec, or both).

### `run` — Simulate and compare

Runs simulations and compares against stored reference baselines.

```bash
# Run all tests, report pass/fail
uv run dstf --config testing.json run

# Run a subset
uv run dstf --config testing.json run --package MyLib.Fluid

# Run with 4 parallel Dymola instances
uv run dstf --config testing.json run --parallel 4

# Accept all results as new baselines (first run, or after intentional changes)
uv run dstf --config testing.json run --accept

# Interactive review — decide per test
uv run dstf --config testing.json run -i
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
| `--default-points` | Use PointsMode by default (empty list ⇒ final-value-only check) |
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

### Live progress + final report

DSTF writes a single `dashboard.html` to your `work_dir` that serves as both the live progress page during a run and the final report after comparison.

- **During a run**: the page polls `status.json` every 2s and updates rows in place (no full reload, scroll position survives). A "Refresh now" button forces an immediate fetch.
- **After comparison**: the same page is re-rendered in final mode — the JS poll loop is dropped, and post-comparison columns are populated (Worst NRMSE, Variables, Warnings, Translate / Sim / Total wall times). Add `--report` for per-test interactive deep dives at `reports/test_NNNN/interactive.html` accessible from the Model column links.

The dashboard supports status filter buttons, per-column text filtering, and a 3-state sort cycle (none → sorted → reverse → none) on every column. Numeric columns sort descending first (largest first when triaging failures); text columns sort ascending first.

The **Resolution** column shows where each test's simulation parameters came from: `annotation` (from the `experiment(...)` annotation in the .mo file), `test_spec` (overridden in test_spec.json), or `mixed` (some fields from each — hover to see the per-field breakdown). This is the resolution-explainer for the two-layer contract documented above.

### `compare` — Re-compare without simulating

Uses the last simulation results (from `testing_output/`) and compares against current references. Useful after changing tolerances or updating references externally.

```bash
uv run dstf --config testing.json compare

# With different tolerance
uv run dstf --config testing.json compare --tolerance 0.001

# Only check final values (default mode = PointsMode with empty list)
uv run dstf --config testing.json compare --default-points

# Output as JUnit XML (for CI)
uv run dstf --config testing.json compare --report-format junit
```

### `export` — Dump reference data

Exports stored reference data to JSON or CSV for external inspection.

```bash
# Export all references as JSON
uv run dstf --config testing.json export

# Export as CSV
uv run dstf --config testing.json export --format csv

# Export to specific file
uv run dstf --config testing.json export --output results.csv --format csv

# Export subset
uv run dstf --config testing.json export --package MyLib.Fluid
```

### `manifest` — Manage test IDs

Each test gets a stable numeric ID (e.g., `ref_0001.json`). The manifest tracks the mapping between IDs and model paths.

```bash
# Show all active tests and their IDs
uv run dstf --config testing.json manifest show

# Also show obsolete (retired) tests
uv run dstf --config testing.json manifest show --show-obsolete

# Delete reference files for obsolete tests
uv run dstf --config testing.json manifest cleanup

# Write the ref ID → model ID mapping to the work directory
uv run dstf --config testing.json manifest dump
```

(The reference index is rebuilt automatically on every command — there's
no separate "rebuild" subcommand because there's nothing to rebuild.)

### `add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file.

```bash
# Add a test with specific variables
uv run dstf --config testing.json add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run dstf --config testing.json add MyLib.Examples.NewTest
```

---

## Typical workflows

### First-time setup for a new library

```bash
# 1. Create reference directory and testing.json
mkdir -p /path/to/MyLib-Tests/ReferenceResults
# Edit testing.json with source_path, simulator, etc.

# 2. Verify discovery
uv run dstf --config /path/to/testing.json discover

# 3. Run all tests and accept as initial baselines
uv run dstf --config /path/to/testing.json run --accept
```

### Routine regression testing

```bash
# Run tests after code changes
uv run dstf --config testing.json run

# If tests fail, review interactively
uv run dstf --config testing.json run -i
```

### CI integration

```bash
# JUnit output for CI systems
uv run dstf --config testing.json run --report-format junit

# HTML report
uv run dstf --config testing.json run --report-format html
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
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run

# Accept new baselines after intentional changes
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run --accept
```
