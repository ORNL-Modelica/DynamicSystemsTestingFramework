# CLAUDE.md

## Project Overview

**ModelicaTesting** is a standalone Python tool for regression testing Modelica libraries. It is library-agnostic — it works with any Modelica library that uses the `UnitTests` pattern for tracking simulation variables, or with external test specifications (`test_spec.json`).

The tool discovers tests by scanning `.mo` files and/or reading `test_spec.json`, runs simulations via Dymola (batch mode), compares results against stored references using NRMSE, and reports pass/fail.

## Project Structure

```
ModelicaTesting/
├── src/
│   └── modelica_testing/        # Python package (src layout)
│       ├── cli.py               # CLI: discover, run, compare, export, migrate, manifest, convert, add
│       ├── config.py            # Config dataclass, path resolution, testing.json loading
│       ├── discovery/           # Test discovery: scan .mo for UnitTests, parse test_spec.json
│       ├── simulators/          # Abstract runner + Dymola backend (batch .mos, .mat reader, dslog parser)
│       ├── comparison/          # NRMSE comparison with piecewise event handling
│       ├── storage/             # JSON reference storage with numeric ID manifest, migration
│       ├── reporting/           # Console, JUnit XML, HTML reporters, plot generation
│       └── tools/               # Verification utilities
├── docs/                        # Design decisions, patterns, architecture, constraints
├── pyproject.toml               # uv/hatchling project config
└── CLAUDE.md
```

## Running the Tool

```bash
# Discover tests in a library
uv run python -m modelica_testing discover --package-path /path/to/MyLibrary

# Run tests and compare against references
uv run python -m modelica_testing run --package-path /path/to/MyLibrary

# Run with interactive review (accept/skip/plot per test)
uv run python -m modelica_testing run -i

# Accept all results as new baselines
uv run python -m modelica_testing run --accept

# Compare without re-running simulations (uses last results)
uv run python -m modelica_testing compare
```

## Configuration

The tool looks for `testing.json` near the library root or reference root. Key fields:

- `simulator` — named entry like `"Dymola"` or `"Dymola 2025"`
- `simulators` — map of simulator names to candidate executable paths
- `simulator_setup` — list of Modelica commands run after library loading (e.g., `"OutputCPUtime := true;"`)
- `dependencies` — paths to dependency library roots loaded before simulation
- `reference_root` — where reference results live (default: `<repo>/Resources/ReferenceResults`)
- `test_spec` — path to external test definitions file

Reference results are partitioned by `<reference_root>/<SimulatorBackend>/<os>/`.

## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test with model ID, simulation params, tracked variables, source
- **`SimulatorRunner`** (`simulators/base.py`) — abstract interface; `DymolaRunner` implements batch execution
- **`ReferenceStore`** (`storage/reference_store.py`) — CRUD for per-test JSON reference files via `TestManifest`
- **`comparator`** (`comparison/comparator.py`) — NRMSE comparison with piecewise event boundary handling

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo`, all paths configurable
2. **Simulator-agnostic**: Dymola-specific code isolated in `simulators/dymola/`; abstract `SimulatorRunner` interface
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with a manifest mapping IDs to model paths; IDs never reused
4. **Reference partitioning**: results split by simulator backend and OS since solvers produce platform-specific results
5. **Batch execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes
