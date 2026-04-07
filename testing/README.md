# Modelica Testing System

Standalone regression testing for Modelica libraries. Discovers test models by scanning for `UnitTests` blocks, runs simulations in Dymola, and compares results against stored references.

Library-agnostic — works with any Modelica library that uses the `UnitTests` pattern.

## Requirements

- Python 3.10+
- Dymola (for running simulations)
- Dependencies managed automatically via `uv`

## Quick Start

Run from any directory — point at your library:

```bash
uv run python -m testing discover --library-path /path/to/MyLibrary
```

Or run from the library root (auto-detects):

```bash
cd /path/to/MyLibrary
uv run python -m testing discover
```

## Architecture

The testing system is fully decoupled from the library it tests. You configure three things:

1. **Library path** — where the Modelica library lives
2. **Dependencies** — paths to other Modelica libraries it depends on
3. **Reference root** — where reference results are stored (can be a separate repo)

### Reference result layout

References are partitioned by simulator and OS:

```
<reference_root>/
├── Dymola/
│   ├── linux/
│   │   ├── index.json
│   │   ├── Blocks_EasingRamp_Test.json
│   │   └── ...
│   └── windows/
│       ├── index.json
│       └── ...
└── OpenModelica/
    └── linux/
        └── ...
```

### Typical setup: separate repos

```
my-library/              # The Modelica library
├── MyLibrary/
│   └── package.mo
├── testing.json         # Points to reference repo, dependencies
└── runAll_Dymola.mos

my-library-tests/        # Reference results (separate repo)
├── Dymola/
│   ├── linux/
│   └── windows/
└── OpenModelica/
    └── linux/
```

## Configuration

### testing.json

Place in the library root or pass via `--config`:

```json
{
  "library_path": ".",
  "simulator": "Dymola",
  "mos_file": "runAll_Dymola.mos",
  "reference_root": "/path/to/my-library-tests",
  "dependencies": [
    "/path/to/SomeDependency",
    "/path/to/AnotherLibrary"
  ],
  "path_abbreviations": {
    "Some.Very.Long.Package.Path.": "SVLPP_"
  }
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `library_path` | Path to library root | Auto-detect from cwd |
| `simulator` | `Dymola` or `OpenModelica` | `Dymola` |
| `mos_file` | Name of the .mos simulation script | `runAll_Dymola.mos` |
| `reference_root` | Where to store/find references | `<library>/Resources/ReferenceResults` |
| `dependencies` | Paths to dependency library roots | `[]` |
| `path_abbreviations` | Shorten long paths in filenames | `{}` |
| `work_dir` | Simulation output directory | `./testing_output` |
| `os` | Override OS detection | Auto-detect |

### CLI flags override config file

```bash
uv run python -m testing run \
  --library-path /path/to/MyLibrary \
  --reference-root /path/to/my-tests \
  --config /path/to/testing.json
```

## Commands

### Discover tests

```bash
uv run python -m testing discover
uv run python -m testing discover --filter "MyLib.Fluid.*"
uv run python -m testing discover --package MyLib.Fluid
```

### Run tests

```bash
# Run and compare against stored references
uv run python -m testing run

# Run and save results as new baselines
uv run python -m testing run --accept

# Run subset
uv run python -m testing run --filter "*_Test" --package MyLib.Blocks

# Parallel with timeout
uv run python -m testing run --parallel 4 --timeout 300

# Compare only final values
uv run python -m testing run --final-only --tolerance 1e-3
```

### Report formats

```bash
uv run python -m testing run --report-format console  # Default
uv run python -m testing run --report-format junit    # JUnit XML for CI
uv run python -m testing run --report-format html     # HTML report
```

### Compare without re-running

```bash
uv run python -m testing compare
```

### Export references

```bash
uv run python -m testing export --format json
uv run python -m testing export --format csv
```

### Regenerate .mos file

```bash
uv run python -m testing discover --regenerate-mos
```

## How It Works

### Test discovery

Scans `.mo` files for `UnitTests` block instantiations. Extracts:
- Tracked variables from `UnitTests(n=N, x={var1, var2, ...})`
- Simulation parameters from `experiment()` annotations
- Overrides from the `.mos` file (highest priority)

### Simulation

Generates per-test `.mos` scripts with numeric result file names (`test_0001.mat`). Each test runs in its own Dymola process with a configurable timeout. Dependencies are loaded via `openModel()` before the main library.

### Comparison

Mirrors the `AbsRelRMS.mo` logic: absolute and relative errors with machine-epsilon filtering, RMS aggregation. Supports full trajectory comparison (default) or final-value-only mode.

## CI Integration

```yaml
- run: uv run python -m testing run --report-format junit
- uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: test-results.xml
```

## Dymola path

```bash
uv run python -m testing run --dymola-path "/path/to/dymola"
```
