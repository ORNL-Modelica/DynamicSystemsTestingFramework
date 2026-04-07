# Next Session: Continuing Framework Development

## What Was Done (2026-04-07)

### Standalone Repo Migration
- Moved from `TRANSFORM-Library/testing/` to standalone `ModelicaTesting` repo
- Restructured to Python src layout: `src/modelica_testing/`
- Updated `pyproject.toml` with hatchling build backend for src layout
- Entry point: `modelica-testing = "modelica_testing.cli:main_entry"`
- Runs via: `uv run python -m modelica_testing`

### Test Manifest System
- Added `TestManifest` class in `storage/reference_store.py`
- Manifest lives at `<reference_root>/test_manifest.json` (cross-platform)
- Stable numeric IDs (`ref_0001.json`, `ref_0002.json`, ...) replace abbreviated filenames
- IDs are never reused — obsolete tests are marked, not deleted
- Backward compatible: falls back to legacy `index.json` / `path_abbreviations` if no manifest exists
- Added `cleanup_obsolete()` method to remove stale reference files

### Configuration Changes
- `reference_root` now defaults to `<library_root>/Resources/ReferenceResults/` instead of erroring
- Override via `--reference-root` CLI flag or `testing.json` `"reference_root"` field
- Added `manifest_file` property to `Config`

### Cleanup
- Rewrote `CLAUDE.md` for standalone testing repo (was describing TRANSFORM library)
- Cleaned `.gitignore`: removed irrelevant boilerplate, fixed `*.mo` conflict (gettext vs Modelica), added Dymola artifacts
- Deleted `__pycache__/` from tracked files

## Still TODO

### 1. Isolate Simulator-Specific Code
Currently Dymola-specific logic is scattered across `simulation/`. Target structure:
```
simulators/
├── base.py              # Abstract SimulatorRunner interface
├── dymola/
│   ├── runner.py
│   ├── reader.py
│   ├── log_parser.py
│   └── mos_writer.py
└── openmodelica/
    └── ...
```

### 2. Make UnitTests Pattern Configurable
Currently hardcodes searching for `UnitTests` and reading `unitTests.x[N]`. Make configurable:
- Component name to search for
- Variable pattern to read from results
- Where the component lives

### 3. Eliminate Duplicate Code
- `.mos` generation logic exists in both `discovery/mos_parser.py` and `dymola_runner.py`
- Float array parsing duplicated between `migrate.py` and `verify_migration.py`
- Config building from CLI args has repetitive `hasattr` checks

### 4. Add CLI Commands for Manifest Management
- `modelica-testing manifest show` — display the manifest
- `modelica-testing manifest cleanup` — remove obsolete entries and their files
- `modelica-testing manifest rebuild` — regenerate from discovered tests

### 5. Migrate Existing References
When first running against a library with old-format references (abbreviated filenames + `index.json`), offer to migrate them to the new manifest + `ref_NNNN.json` format.

### 6. Update Top-Level README.md
The comprehensive documentation from the old `testing/README.md` should be adapted for the top-level `README.md`.

## Context

- TRANSFORM has ~330 tests with 1-24 tracked variables each
- The `UnitTests` Modelica component will eventually be its own standalone library
- Dependencies are loaded via `openModel()` in `.mos` scripts before the main library
- Reference results can live in a separate repo from the library being tested
- OS detection matters because Dymola produces platform-specific results
