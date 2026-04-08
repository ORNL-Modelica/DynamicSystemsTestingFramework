# Decisions Log

## D1: Standalone repo, not embedded in TRANSFORM-Library

- **What**: ModelicaTesting is its own repo with `src/` layout, not a subdirectory of any Modelica library.
- **Why**: The tool is library-agnostic. Embedding it in one library's repo couples release cycles and makes reuse awkward.
- **Trade-offs**: Requires a separate install step (`uv run`) and version coordination. Acceptable given early stage.

## D2: Numeric test IDs with manifest

- **What**: Reference files are named `ref_0001.json`, `ref_0002.json`, etc. A `test_manifest.json` maps IDs to model paths. IDs are never reused — obsolete tests are marked, not deleted.
- **Why**: Modelica model paths like `TRANSFORM.Fluid.Pipes.Examples.GenericPipe_withWall_Counter_wTraceMass` exceed Windows MAX_PATH (260 chars) when used as filenames. Abbreviated names were fragile and collision-prone.
- **Trade-offs**: Requires the manifest as an extra artifact. Manifest is shared across simulators/OS; reference files are partitioned per simulator+OS.

## D3: Reference partitioning by simulator and OS

- **What**: References live at `<reference_root>/<SimulatorBackend>/<os>/ref_NNNN.json`.
- **Why**: Different solvers (Dymola vs OpenModelica) and platforms (Windows vs Linux) produce numerically different results. A single baseline can't serve all combinations.
- **Trade-offs**: More files to manage. The manifest is shared (IDs are stable across platforms), only the reference data differs.

## D4: Batch execution for Dymola

- **What**: Tests are grouped into batches. Each batch runs in one Dymola session: load libraries once, simulate N tests, exit. Per-test subdirectories prevent file conflicts.
- **Why**: Dymola startup + library loading takes 30-60s. Per-test processes made a 100-test suite take hours; batch mode cuts it to minutes.
- **Trade-offs**: A batch timeout kills all remaining tests in that batch. Dymola's working directory management requires explicit `cd()` calls in `.mos` scripts.

## D5: NRMSE as comparison metric (not AbsRelRMS)

- **What**: Pass/fail uses `NRMSE = RMSE / signal_range`. For constant signals (range ~ 0), raw RMSE is used directly.
- **Why**: Simpler and more interpretable than Modelica's AbsRelRMS. Signal range normalization makes the tolerance meaningful across variables with different magnitudes.
- **Trade-offs**: Constant signals need special handling (the `is_constant` flag). A single tolerance value works across most cases.

## D6: Piecewise comparison at event boundaries

- **What**: Duplicate time values in results indicate Modelica events (discontinuities). The comparator splits trajectories at these boundaries and compares each segment independently, using pre-event values for segment ends and post-event values for segment starts.
- **Why**: `np.interp` on duplicate times returns the post-event value, which causes false errors when comparing identical signals across event boundaries. Piecewise comparison preserves discontinuities.
- **Trade-offs**: More complex interpolation logic. Interior segments need hybrid handling (pre-event dedup for bulk, post-event override at first point).

## D7: External test spec (`test_spec.json`) alongside UnitTests

- **What**: Tests can come from in-model `UnitTests` components, an external `test_spec.json`, or both (merged). The `source` field tracks provenance (`"unit_tests"`, `"spec"`, `"both"`).
- **Why**: Not all models have or should have `UnitTests` components. External specs allow testing models without modifying `.mo` files, and support glob patterns for variable selection.
- **Trade-offs**: Two sources of truth to reconcile. Merge logic uses spec values as overrides when both exist.

## D8: `testing.json` for library-specific configuration

- **What**: Each library has a `testing.json` with simulator selection, paths, dependencies, and setup commands. Config file search order: reference_root, repo_root, package_dir, cwd.
- **Why**: Different libraries need different simulators, dependencies, and settings. CLI flags override file config; file config overrides defaults.
- **Trade-offs**: Auto-creates a default `testing.json` if none found, which can surprise users.

## D9: No backward compatibility during development

- **What**: Format changes (manifest, reference JSON, CLI flags) are clean breaks with no legacy fallback code.
- **Why**: Early-stage repo with no external consumers. One-time migration utilities are provided instead of maintaining dual paths.
- **Trade-offs**: Existing references must be migrated. The `convert` and `migrate` commands handle this.

## D10: Diagnostic variables (CPUtime, EventCounter) auto-captured

- **What**: `CPUtime` and `EventCounter` are automatically extracted from simulation results when present (requires `OutputCPUtime := true;` in Dymola). Full trajectories are stored in a `diagnostics` section of the reference JSON. Final values are added to `statistics` for simple change detection.
- **Why**: CPU time and event counts are critical for diagnosing performance regressions and model changes, but shouldn't cause pass/fail. Storing trajectories enables plotting; storing finals in statistics enables structural warnings.
- **Trade-offs**: Diagnostics are stored but never compared via NRMSE. EventCounter changes trigger a structural warning; CPUtime does not (too variable between runs).

## D11: Config-relative path resolution

- **What**: All relative paths in `testing.json` (`package_path`, `test_spec`, `dependencies`, `reference_root`) resolve relative to where `testing.json` was found, not relative to the library or cwd.
- **Why**: When references live in a separate repo, config and test specs sit together in that repo. Resolving relative to the library root would require fragile cross-repo relative paths. This also enables a single `--config` or `--reference-root` flag to drive everything.
- **Trade-offs**: None significant. CLI flags still accept absolute paths and override config file values.

## D12: `testing.json` as single entry point

- **What**: `testing.json` can contain `package_path` pointing to the library under test. With this, a single flag (`--config` or `--reference-root`) is sufficient to run — no `--package-path` needed.
- **Why**: Reduces command-line boilerplate. The config file already knows everything about the test setup; requiring the user to also specify the library path is redundant.
- **Trade-offs**: `package_path` in the config is relative, so moving the config file breaks the path. CLI `--package-path` still overrides.
