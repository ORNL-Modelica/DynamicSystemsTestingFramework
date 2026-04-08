# Usage Guide

## Setup

### Configuration file (`testing.json`)

Create a `testing.json` in your reference results directory. This is the single entry point for the tool — it tells it where the library is, which simulator to use, and how to run tests.

```json
{
  "package_path": "../../MyLibrary/MyLib",
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
  "simulator_setup": [
    "OutputCPUtime := true;"
  ],
  "test_spec": "test_spec.json"
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `package_path` | No | Path to library's `package.mo` directory. Relative to `testing.json` location. Can be provided via `--package-path` instead. |
| `simulator` | No | Simulator name (default: `"Dymola"`). Use named entries like `"Dymola 2025"` to target specific versions. |
| `simulators` | No | Map of simulator names to candidate executable paths (first existing path wins). Falls back to system PATH. |
| `dependencies` | No | Paths to dependency library roots loaded before simulation. Relative to `testing.json` location. |
| `simulator_setup` | No | Modelica commands run after library loading (e.g., `"OutputCPUtime := true;"`). |
| `test_spec` | No | Path to external test definitions file. Relative to `testing.json` location. |
| `reference_root` | No | Override where references are stored. Defaults to the directory containing `testing.json` if it's named `ReferenceResults`, otherwise `<repo>/Resources/ReferenceResults`. |

All relative paths resolve from where `testing.json` is located.

### Test definitions

Tests are discovered from two sources (can be combined):

**1. In-model `UnitTests` components** — discovered automatically by scanning `.mo` files. No setup needed.

**2. External `test_spec.json`** — for models without `UnitTests`, or to override variable tracking:

```json
{
  "tests": {
    "MyLib.Examples.SimpleTest": {
      "variables": ["pipe.T[1]", "tank.level"],
      "stop_time": 100,
      "tolerance": 1e-4
    },
    "MyLib.Examples.AnotherTest": {
      "variables": ["*"],
      "stop_time": 500
    }
  }
}
```

Variable patterns support `*` and `?` wildcards. `["*"]` tracks all non-parameter variables. `[]` (empty list) simulates without tracking variables.

---

## Commands

All commands accept these global options:

```
--config PATH          Path to testing.json (default: auto-search)
--package-path PATH    Path to library's package.mo directory
--reference-root PATH  Path to reference results root
--test-spec PATH       Path to test_spec.json
```

If `testing.json` includes `package_path`, you only need `--config` or `--reference-root`.

### `discover` — Find tests

Lists all discoverable tests without running anything.

```bash
# All tests
uv run python -m modelica_testing --config testing.json discover

# Filter by package
uv run python -m modelica_testing --config testing.json discover --package MyLib.Fluid

# Filter by glob pattern
uv run python -m modelica_testing --config testing.json discover --filter "*Pipe*"
```

Output shows model ID, variable count, stop time, solver method, and source (unit_tests, spec, or both).

### `run` — Simulate and compare

Runs simulations and compares against stored reference baselines.

```bash
# Run all tests, report pass/fail
uv run python -m modelica_testing --config testing.json run

# Run a subset
uv run python -m modelica_testing --config testing.json run --package MyLib.Fluid

# Run with 4 parallel Dymola instances
uv run python -m modelica_testing --config testing.json run --parallel 4

# Accept all results as new baselines (first run, or after intentional changes)
uv run python -m modelica_testing --config testing.json run --accept

# Interactive review — decide per test
uv run python -m modelica_testing --config testing.json run -i
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
uv run python -m modelica_testing --config testing.json compare

# With different tolerance
uv run python -m modelica_testing --config testing.json compare --tolerance 0.001

# Only check final values
uv run python -m modelica_testing --config testing.json compare --final-only

# Output as JUnit XML (for CI)
uv run python -m modelica_testing --config testing.json compare --report-format junit
```

### `export` — Dump reference data

Exports stored reference data to JSON or CSV for external inspection.

```bash
# Export all references as JSON
uv run python -m modelica_testing --config testing.json export

# Export as CSV
uv run python -m modelica_testing --config testing.json export --format csv

# Export to specific file
uv run python -m modelica_testing --config testing.json export --output results.csv --format csv

# Export subset
uv run python -m modelica_testing --config testing.json export --package MyLib.Fluid
```

### `manifest` — Manage test IDs

Each test gets a stable numeric ID (e.g., `ref_0001.json`). The manifest tracks the mapping between IDs and model paths.

```bash
# Show all active tests and their IDs
uv run python -m modelica_testing --config testing.json manifest show

# Also show obsolete (retired) tests
uv run python -m modelica_testing --config testing.json manifest show --show-obsolete

# Delete reference files for obsolete tests
uv run python -m modelica_testing --config testing.json manifest cleanup

# Rebuild manifest from currently discovered tests
uv run python -m modelica_testing --config testing.json manifest rebuild
```

### `add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file.

```bash
# Add a test with specific variables
uv run python -m modelica_testing --config testing.json add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run python -m modelica_testing --config testing.json add MyLib.Examples.NewTest
```

---

## Typical workflows

### First-time setup for a new library

```bash
# 1. Create reference directory and testing.json
mkdir -p /path/to/MyLib-Tests/ReferenceResults
# Edit testing.json with package_path, simulator, etc.

# 2. Verify discovery
uv run python -m modelica_testing --config /path/to/testing.json discover

# 3. Run all tests and accept as initial baselines
uv run python -m modelica_testing --config /path/to/testing.json run --accept
```

### Routine regression testing

```bash
# Run tests after code changes
uv run python -m modelica_testing --config testing.json run

# If tests fail, review interactively
uv run python -m modelica_testing --config testing.json run -i
```

### CI integration

```bash
# JUnit output for CI systems
uv run python -m modelica_testing --config testing.json run --report-format junit

# HTML report
uv run python -m modelica_testing --config testing.json run --report-format html
```

---

## Reference file structure

```
ReferenceResults/
├── testing.json              # Configuration
├── test_spec.json            # External test definitions (optional)
├── test_manifest.json        # ID-to-model mapping (auto-generated)
└── Dymola/
    └── windows/
        ├── ref_0001.json     # One file per test
        ├── ref_0002.json
        └── ...
```

References are partitioned by simulator backend and OS because different solvers and platforms produce numerically different results.

Each reference file contains metadata (model ID, simulation params, statistics, diagnostics) followed by the time vector and variable data. `CPUtime` and `EventCounter` are auto-captured in diagnostics when `OutputCPUtime := true;` is set.

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

The project includes `ModelicaTestingLib/`, a small Modelica library used both as a test fixture and as a reference implementation. To regression test it (requires Dymola):

```bash
# Run and compare against stored references
uv run python -m modelica_testing --reference-root ModelicaTestingLib/Resources/ReferenceResults run

# Accept new baselines after intentional changes
uv run python -m modelica_testing --reference-root ModelicaTestingLib/Resources/ReferenceResults run --accept
```
