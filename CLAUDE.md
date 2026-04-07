# CLAUDE.md

## Project Overview

**ModelicaTesting** is a standalone Python tool for regression testing Modelica libraries. It is library-agnostic — it works with any Modelica library that uses the `UnitTests` pattern for tracking simulation variables.

The tool discovers tests by scanning `.mo` files, runs simulations in Dymola (with OpenModelica support planned), compares results against stored references, and reports pass/fail.

## Project Structure

```
ModelicaTesting/
├── src/
│   └── modelica_testing/        # Python package (src layout)
│       ├── cli.py               # CLI entry points: discover, run, compare, export, migrate
│       ├── config.py            # Configuration and path resolution
│       ├── discovery/           # Test discovery: scan .mo for UnitTests, parse .mos
│       ├── simulation/          # Dymola runner, .mat reader, dslog parser
│       ├── comparison/          # Reference comparison (AbsRelRMS logic)
│       ├── storage/             # JSON reference storage, migration from buildingspy
│       ├── reporting/           # Console, JUnit XML, HTML reporters
│       └── tools/               # Verification utilities
├── pyproject.toml               # uv project config
└── CLAUDE.md
```

## Running the Tool

```bash
# Discover tests in a library
uv run python -m modelica_testing discover --library-path /path/to/MyLibrary

# Run tests and compare
uv run python -m modelica_testing run --library-path /path/to/MyLibrary

# Accept results as new baselines
uv run python -m modelica_testing run --accept
```

## Configuration

The tool looks for `testing.json` in the target library root. Key fields:

- `library_path` — path to Modelica library root
- `reference_root` — where reference results live (default: `<library>/Resources/ReferenceResults`)
- `simulator` — `Dymola` or `OpenModelica`
- `dependencies` — paths to dependency libraries loaded before simulation

Reference results are partitioned by `<reference_root>/<Simulator>/<os>/`.

## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test with model ID, simulation params, tracked variables
- **`ReferenceStore`** (`storage/reference_store.py`) — CRUD for per-test JSON reference files + index
- **`comparator`** (`comparison/comparator.py`) — AbsRelRMS error calculation matching Modelica's `AbsRelRMS.mo`

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo`, all paths configurable
2. **Simulator-agnostic** (in progress): Dymola-specific code is isolated in `simulation/`
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with a manifest mapping IDs to model paths
4. **Reference partitioning**: results split by simulator and OS since solvers produce platform-specific results
5. **No hardcoded paths**: the tool does not assume where it lives relative to the library or references
