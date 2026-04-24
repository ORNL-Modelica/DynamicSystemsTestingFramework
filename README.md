# Dynamic Systems Testing Framework (DSTF)

Formerly ModelicaTesting. Standalone regression testing for Modelica libraries (and more — DSTF now supports FMU, Julia/ModelingToolkit, and arbitrary-Python backends as well). Discovers test models, runs simulations, compares results against stored references, and reports pass/fail.

Library-agnostic — works with any Modelica library. Tests can be defined in-model (via `UnitTests` components) or externally (via `test_spec.json`), or both.

## Requirements

- Python 3.10+
- Dymola (for running simulations)
- Dependencies managed via [uv](https://docs.astral.sh/uv/)

## Installation & Invocation

The package ships a console script named `dstf`.

```bash
# End users: install as an isolated tool
uv tool install dstf     # then run plain: dstf ...

# Developers: editable install inside the project
uv pip install -e ".[dev]"
uv run dstf ...          # canonical dev form
python -m dstf ...                   # equivalent fallback
```

All examples below use `uv run dstf`. Drop the `uv run` prefix if you installed via `uv tool install` / `pipx`.

## Quick Start

```bash
# Discover tests in a library
uv run dstf --package-path /path/to/MyLibrary/MyLib discover

# Run tests and compare against stored references
uv run dstf --package-path /path/to/MyLibrary/MyLib run

# Run with explicit reference location
uv run dstf \
  --package-path /path/to/MyLibrary/MyLib \
  --reference-root /path/to/my-refs \
  run

# Accept results as new baselines
uv run dstf --package-path /path/to/MyLibrary/MyLib run --accept

# Run a subset
uv run dstf run --filter "*_Test" --package MyLib.Blocks
```

`--package-path` points at the directory containing `package.mo`. If omitted, the tool auto-detects from the current working directory.

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
uv run dstf --test-spec test_spec.json discover
```

Or reference it from `testing.json`:

```json
{
  "test_spec": "test_spec.json"
}
```

### Option 3: Both

When a model appears in both `UnitTests` and `test_spec.json`, variables from both sources are merged (deduplicated). Simulation parameters from the spec override experiment annotation defaults.

## Configuration

### testing.json

Auto-created on first run if not found. Looked for in the parent of the package directory (repo root), the package directory itself, or the current working directory.

```json
{
  "simulator": "Dymola 2025",
  "simulators": {
    "Dymola 2025": [
      "C:/Program Files/Dymola 2025/bin64/dymola.exe",
      "/opt/dymola-2025/bin/dymola.sh"
    ],
    "Dymola 2024x": [
      "C:/Program Files/Dymola 2024x/bin64/dymola.exe"
    ]
  },
  "reference_root": "/path/to/references",
  "test_spec": "test_spec.json",
  "dependencies": [
    "/path/to/SomeDependency"
  ],
  "show_ide": false
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `simulator` | Simulator name from `simulators` map | `Dymola` |
| `simulators` | Named entries mapping to executable paths per OS | `{}` |
| `reference_root` | Where to store/find references | `<repo>/Resources/ReferenceResults` |
| `test_spec` | Path to external test spec file | None |
| `dependencies` | Paths to dependency library roots | `[]` |
| `show_ide` | Show simulator GUI instead of headless | `false` |
| `work_dir` | Simulation output directory | `./testing_output/<lib>/<sim>/<os>` |
| `os` | Override OS detection | Auto-detect |

The `simulators` map supports multiple versions and platforms. The tool picks the first path that exists on the current machine:

```json
{
  "simulators": {
    "Dymola 2025": [
      "C:/Program Files/Dymola 2025/bin64/dymola.exe",
      "/opt/dymola-2025/bin/dymola.sh",
      "dymola"
    ]
  }
}
```

### CLI flags override config

```bash
uv run dstf run \
  --package-path /path/to/MyLib \
  --reference-root /path/to/my-refs \
  --simulator "Dymola 2024x" \
  --simulator-path "C:/Program Files/Dymola 2024x/bin64/dymola.exe"
```

## Commands

### discover — Find tests

```bash
uv run dstf discover
uv run dstf discover --filter "MyLib.Fluid.*"
uv run dstf discover --package MyLib.Fluid
```

### run — Simulate and compare

```bash
# Run and compare against stored references
uv run dstf run

# Accept results as new baselines
uv run dstf run --accept

# Parallel with timeout
uv run dstf run --parallel 4 --timeout 300

# Compare only final values
uv run dstf run --final-only --tolerance 1e-3

# Show Dymola GUI for debugging
uv run dstf run --show-ide --filter "MyLib.SomeTest"
```

### compare — Compare without re-running

```bash
uv run dstf compare
```

### Report formats

```bash
uv run dstf run --report-format console  # Default
uv run dstf run --report-format junit    # JUnit XML for CI
uv run dstf run --report-format html     # HTML report
```

### manifest — Manage test IDs

Tests are assigned stable numeric IDs stored in `test_manifest.json`. IDs are never reused.

```bash
# Show all registered tests
uv run dstf manifest show

# Rebuild manifest from discovered tests
uv run dstf manifest rebuild

# Remove reference files for obsolete tests
uv run dstf manifest cleanup
```

### export — Export reference data

```bash
uv run dstf export --format json
uv run dstf export --format csv
```

### convert — Change reference file format

```bash
# Old abbreviated filenames -> numeric IDs + manifest
uv run dstf convert to-manifest

# Numeric IDs -> human-readable filenames
uv run dstf convert from-manifest
```

### migrate — Import from buildingspy

```bash
uv run dstf migrate /path/to/old/ReferenceResults
```

## Reference Results

### Layout

References are partitioned by simulator backend and OS:

```
<reference_root>/
├── test_manifest.json          # ID -> model mapping (shared)
├── Dymola/
│   ├── linux/
│   │   ├── ref_0001.json
│   │   └── ...
│   └── windows/
│       ├── ref_0001.json
│       └── ...
└── OpenModelica/
    └── linux/
        └── ...
```

### Simulation output

Simulation artifacts are partitioned to prevent conflicts when testing with multiple simulator versions or platforms:

```
testing_output/
└── TRANSFORM/
    ├── Dymola/
    │   └── windows/
    │       ├── test_0001.mos
    │       ├── test_0001.mat
    │       └── ...
    └── Dymola_2024x/
        └── windows/
            └── ...
```

### Typical setup

```
TRANSFORM-Library/              # The Modelica library repo
├── TRANSFORM/
│   └── package.mo              # <-- point --package-path here
├── testing.json
└── test_spec.json

TRANSFORM-References/           # Reference results (separate repo)
├── test_manifest.json
└── Dymola/
    └── windows/
        ├── ref_0001.json
        └── ...
```

## How It Works

### Test discovery

Scans `.mo` files for `UnitTests` component instantiations and/or reads `test_spec.json`. Extracts tracked variables and simulation parameters from experiment annotations, with spec overrides applied on top.

### Simulation

Generates per-test `.mos` scripts with numeric result file names (`test_0001.mat`). Each test runs in its own Dymola process with a configurable timeout. Supports parallel execution.

### Comparison

Mirrors the `AbsRelRMS.mo` logic: absolute and relative errors with machine-epsilon filtering, RMS aggregation. Supports full trajectory comparison (default) or final-value-only mode.

## CI Integration

```yaml
- run: |
    uv run dstf \
      --package-path ./MyLibrary \
      --reference-root ./references \
      run --report-format junit
- uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: test-results.xml
```
