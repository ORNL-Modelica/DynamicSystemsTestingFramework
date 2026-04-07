# ModelicaTesting

Standalone regression testing for Modelica libraries. Discovers test models, runs simulations, compares results against stored references, and reports pass/fail.

Library-agnostic — works with any Modelica library. Tests can be defined in-model (via `UnitTests` components) or externally (via `test_spec.json`), or both.

## Requirements

- Python 3.10+
- Dymola (for running simulations)
- Dependencies managed via [uv](https://docs.astral.sh/uv/)

## Quick Start

```bash
# Discover tests in a library
uv run python -m modelica_testing discover --library-path /path/to/MyLibrary

# Run tests and compare against stored references
uv run python -m modelica_testing run --library-path /path/to/MyLibrary

# Accept results as new baselines
uv run python -m modelica_testing run --accept

# Run a subset
uv run python -m modelica_testing run --filter "*_Test" --package MyLib.Blocks
```

## Defining Tests

### Option 1: In-Model (UnitTests component)

Add a `UnitTests` component inside your Modelica example model:

```modelica
model MyTest
  // ... model equations ...
  Utilities.ErrorAnalysis.UnitTests unitTests(
    n=2,
    x={pipe.T[end], pump.m_flow}
  );
end MyTest;
```

The testing tool scans `.mo` files for `UnitTests` instantiations and extracts the tracked variables.

### Option 2: External Test Spec

Define tests in a `test_spec.json` file without modifying Modelica code:

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "pump.m_flow"]
    },
    {
      "model": "MyLib.Examples.HeatExchanger",
      "variables": ["medium.T*", "shell.h_*"],
      "stop_time": 500
    },
    {
      "model": "MyLib.Examples.SimplePump",
      "variables": []
    },
    {
      "model": "MyLib.Examples.FullSystem",
      "variables": ["*"]
    }
  ]
}
```

Variable patterns:

| Pattern | Meaning |
|---------|---------|
| `"pipe.T[1]"` | Exact variable name |
| `"pipe.T*"` | Glob — matches `pipe.T[1]`, `pipe.T[2]`, etc. |
| `"pipe.T[*]"` | All array indices |
| `"medium.T*"` | All variables starting with `medium.T` |
| `[]` | Simulate only — no variable comparison |
| `["*"]` | Track all variables |

Point the tool at your spec file:

```bash
uv run python -m modelica_testing --test-spec test_spec.json discover
```

Or reference it from `testing.json`:

```json
{
  "test_spec": "test_spec.json"
}
```

### Option 3: Both

When a model appears in both `UnitTests` and `test_spec.json`, variables from both sources are merged (deduplicated). Simulation parameters from the spec override UnitTests defaults. The `.mos` file has highest priority for simulation parameters.

## Configuration

### testing.json

Place in the library root or pass via `--config`:

```json
{
  "library_path": ".",
  "simulator": "Dymola",
  "mos_file": "runAll_Dymola.mos",
  "reference_root": "/path/to/references",
  "test_spec": "test_spec.json",
  "dependencies": [
    "/path/to/SomeDependency"
  ]
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `library_path` | Path to library root | Auto-detect from cwd |
| `simulator` | `Dymola` (OpenModelica planned) | `Dymola` |
| `mos_file` | Name of the .mos simulation script | `runAll_Dymola.mos` |
| `reference_root` | Where to store/find references | `<library>/Resources/ReferenceResults` |
| `test_spec` | Path to external test spec file | None |
| `dependencies` | Paths to dependency library roots | `[]` |
| `work_dir` | Simulation output directory | `./testing_output` |
| `os` | Override OS detection | Auto-detect |

### CLI flags override config

```bash
uv run python -m modelica_testing run \
  --library-path /path/to/MyLibrary \
  --reference-root /path/to/my-refs \
  --test-spec /path/to/test_spec.json
```

## Commands

### discover — Find tests

```bash
uv run python -m modelica_testing discover
uv run python -m modelica_testing discover --filter "MyLib.Fluid.*"
uv run python -m modelica_testing discover --package MyLib.Fluid
uv run python -m modelica_testing discover --regenerate-mos
```

### run — Simulate and compare

```bash
# Run and compare against stored references
uv run python -m modelica_testing run

# Accept results as new baselines
uv run python -m modelica_testing run --accept

# Parallel with timeout
uv run python -m modelica_testing run --parallel 4 --timeout 300

# Compare only final values
uv run python -m modelica_testing run --final-only --tolerance 1e-3
```

### compare — Compare without re-running

```bash
uv run python -m modelica_testing compare
```

### Report formats

```bash
uv run python -m modelica_testing run --report-format console  # Default
uv run python -m modelica_testing run --report-format junit    # JUnit XML for CI
uv run python -m modelica_testing run --report-format html     # HTML report
```

### manifest — Manage test IDs

Tests are assigned stable numeric IDs stored in `test_manifest.json`. IDs are never reused.

```bash
# Show all registered tests
uv run python -m modelica_testing manifest show

# Rebuild manifest from discovered tests
uv run python -m modelica_testing manifest rebuild

# Remove reference files for obsolete tests
uv run python -m modelica_testing manifest cleanup
```

### export — Export reference data

```bash
uv run python -m modelica_testing export --format json
uv run python -m modelica_testing export --format csv
```

### convert — Change reference file format

```bash
# Old abbreviated filenames -> numeric IDs + manifest
uv run python -m modelica_testing convert to-manifest

# Numeric IDs -> human-readable filenames
uv run python -m modelica_testing convert from-manifest
```

### migrate — Import from buildingspy

```bash
uv run python -m modelica_testing migrate /path/to/old/ReferenceResults
```

## Reference Results

### Layout

References are partitioned by simulator and OS:

```
<reference_root>/
├── test_manifest.json          # ID -> model mapping (shared)
├── Dymola/
│   ├── linux/
│   │   ├── ref_0001.json
│   │   ├── ref_0002.json
│   │   └── ...
│   └── windows/
│       ├── ref_0001.json
│       └── ...
└── OpenModelica/
    └── linux/
        └── ...
```

### Typical setup

```
my-library/                     # The Modelica library
├── MyLibrary/
│   └── package.mo
├── testing.json
├── test_spec.json
└── runAll_Dymola.mos

my-library-references/          # Reference results (can be a separate repo)
├── test_manifest.json
└── Dymola/
    └── windows/
        ├── ref_0001.json
        └── ...
```

## How It Works

### Test discovery

Scans `.mo` files for `UnitTests` component instantiations and/or reads `test_spec.json`. Extracts tracked variables and simulation parameters. Merges with `.mos` file overrides (highest priority).

### Simulation

Generates per-test `.mos` scripts with numeric result file names (`test_0001.mat`). Each test runs in its own Dymola process with a configurable timeout. Supports parallel execution.

### Comparison

Mirrors the `AbsRelRMS.mo` logic: absolute and relative errors with machine-epsilon filtering, RMS aggregation. Supports full trajectory comparison (default) or final-value-only mode.

## CI Integration

```yaml
- run: uv run python -m modelica_testing run --report-format junit
- uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: test-results.xml
```
