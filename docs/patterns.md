# Patterns and Anti-Patterns

## Proven Patterns

### Dymola `.mos` script structure: startup → per-test → shutdown
- `startup.mos`: `cd()`, load dependencies, load main library, framework settings (`OutputCPUtime`, `Advanced.UI.TranslationInCommandLog`), user setup commands
- `simulate.mos`: `cd()` to test subdir, `clearlog()`, `simulateModel(...)`, `savelog("translation_log.txt")` — result saved as `dsres.mat` (Dymola default)
- `shutdown.mos`: `Modelica.Utilities.System.exit()`
- `batch_manifest.json`: written before simulation, maps `test_key -> {"model_id": "...", "ref_id": "ref_NNNN"}`
- `reference_manifest.json`: written before simulation, maps ref IDs to model names
- `batch.mos`: `RunScript()` calls chaining startup + all tests + shutdown
- Each test gets its own subdirectory to prevent file conflicts (`dsin.txt`, `dslog.txt`, `dsfinal.txt`, `dsres.mat`, `translation_log.txt` are per-simulation)
- Two log files per test: `dslog.txt` (simulation runtime) and `translation_log.txt` (translation/structural stats) — merged into one `statistics` dict

### Variable pattern matching treats `[]` as literal
- Modelica uses `[]` for array indices (e.g., `pipe.T[1]`), not as glob character classes
- Custom `_pattern_to_regex()` only treats `*` and `?` as wildcards; all other characters (including `[]`, `()`, `.`) are escaped
- Standard `fnmatch` breaks on Modelica variable names — don't use it for variable matching

### Event boundary handling in time series
- Duplicate time values = Modelica events (pre-event and post-event values at same instant)
- Dymola may produce 2 or 3 duplicate time points per event — `_find_event_boundaries` groups consecutive duplicates into `(first_dup, last_dup)` tuples
- `_dedup_time_series(keep="first")` gives pre-event values; `keep="last"` gives post-event
- First segment: interpolate with pre-event dedup (correct at end boundary)
- Last segment: interpolate with post-event dedup (correct at start boundary)
- Interior segments: pre-event dedup for bulk, override first point with post-event value
- Downsampling must preserve both pre- and post-event points at duplicate times

### Reference JSON field ordering
- Metadata fields first (`model_id`, `test_id`, `last_updated`, `simulation`, `statistics`)
- Data fields last (`n_vars`, `time`, `variables`)
- Makes files human-readable when opened — you see context before scrolling through numbers

### Tube-based comparison mode
- Alternative to NRMSE: the tube defines an upper/lower envelope around the reference trajectory
- Configured per-variable via `variable_overrides` with `"mode": "tube"`
- Width formula: `tube_width = max(tube_abs, tube_rel * |reference|)` — the `max` prevents the tube from collapsing to zero when the reference crosses zero
- Pass/fail is strict: the actual signal must stay inside the tube at every interpolated point
- Constant tube: `{"mode": "tube", "tube_abs": 500, "tube_rel": 0.02}` — uniform width over all time
- Time-varying tube: `tube_points` with `{"time", "abs", "rel"}` control points, interpolated via `tube_interpolation` (`"constant"` for stepwise, `"linear"` for linear — default is `"linear"`)
- Before the first control point: hold first point values. After the last: hold last point values
- Metrics reported: `tube_points_inside` (fraction 0-1), `tube_worst_violation` (largest distance outside tube), `tube_worst_violation_time`
- NRMSE is still computed for reference even in tube mode
- HTML reports show "tube (95% in)" style labels for tube-mode variables in the variable table

### Tolerance resolution order
- Per-variable override from test spec (`comparison.variable_overrides`) takes highest priority
- Per-variable override from reference JSON (`comparison.variable_overrides`) is next
- Per-test comparison tolerance from test spec (`comparison.tolerance`)
- Comparison tolerance from reference JSON (`comparison.tolerance`)
- Global config tolerance (`config.tolerance`)
- Default: `1e-4`
- Each `VariableComparison` records `tolerance_used` — the tolerance that was actually applied, shown in HTML reports
- When accepting results, the active comparison settings (tolerance + variable overrides) are saved in the reference JSON's `comparison` section so tolerances travel with the baseline

### Config resolution order
- CLI args > `testing.json` file > defaults
- `testing.json` search: reference_root → repo_root → package_dir → cwd
- All relative paths in `testing.json` (`package_path`, `test_spec`, `dependencies`, `reference_root`) resolve relative to where `testing.json` was found
- `testing.json` can be the single entry point: include `package_path` and run with just `--config` or `--reference-root`
- `test_spec.json` is permanent, not temporary — it defines what to test; references define what to expect

### Ref files are the source of truth, not a manifest
- Each `ref_NNNN.json` contains `model_id`, `test_id`, `status`, `date_added`, `last_updated`
- The in-memory `RefIndex` is rebuilt by scanning ref files at startup — no persistent manifest to maintain or sync
- `date_added` is set once on first store, preserved on updates; `last_updated` changes every store
- `status` field: `active` (normal), `skip` (temporarily excluded from runs), `obsolete` (pending deletion via `manifest cleanup`)
- Diagnostic variable list is configurable via `diagnostic_variables` in `testing.json` (default: `["CPUtime", "EventCounter"]`)

### Reference JSON values use precision-aware rounding
- `_to_json_list()` checks the numpy array dtype and rounds accordingly
- float32 (older Dymola): 7 significant digits — removes float32→float64 promotion noise
- float64 (newer Dymola): 15 significant digits — preserves full double precision
- Python's `%g` format auto-switches to scientific notation for very large/small values

### Two-phase .mat reading: names first, then selective data
- Phase 1: `list_dymola_mat_variables()` reads only the `name` matrix from the MAT4 headers — fast even for 36MB files
- Phase 2: `_compute_needed_variables()` resolves patterns against the name list to determine exactly which variables are needed
- Phase 3: `read_dymola_mat(variable_names=needed)` memory-maps `data_2` and reads only the needed rows
- For 76,992 variables where only 10 are needed, this reads ~0.01% of the trajectory data
- Critical for WSL2 where 9P filesystem I/O is slow for large files

### Stale artifact protection
- Test directories are cleaned (`rmtree` + recreate) before each simulation run, preventing stale `dsres.mat`, `dslog.txt`, or `translation_log.txt` from a previous run from being misread as current results
- Translation log is checked for "Translation aborted" as defense in depth — even if stale artifacts survive, an aborted translation is caught

### Translation log parsing separates simulation and initialization
- The "Translated Model" section has two levels: simulation-level stats and a nested "Initialization problem" subsection
- Parser splits at "Initialization problem" line, parses each half independently
- Initialization fields use `init_` prefix: `init_nonlinear`, `init_linear`, `init_mixed_systems`, `init_numerical_jacobians`, `init_homotopy_nonlinear`
- System sizes stored as `list[int]` with summary fields: `_count`, `_total`, `_max`
- Structural change warnings use summary fields (not raw lists) for clean display

### Diagnostic variables are stored but never compared
- `CPUtime` and `EventCounter` are auto-extracted from mat data when present
- Full trajectories go in `diagnostics` section of reference JSON (for plotting)
- Final values go in `statistics` (for structural change warnings)
- `EventCounter` changes trigger a warning; `CPUtime` does not (too noisy)

### Data-driven HTML reports via Jinja2 templates
- `html_report.py` builds a context dict from `TestComparison` and renders through a Jinja2 template (`templates/comparison.html`)
- A `comparison_data.json` sidecar is written alongside the HTML, containing the same data for downstream tooling
- The template uses progressive disclosure: key stats cards at top, condensed variable table, then collapsible `<details>` sections for full variable details, statistics, simulation parameters, diagnostics, and reference info
- Simulation parameters show current vs reference values with change highlighting when they differ
- Statistics sections iterate over whatever keys exist in the dicts rather than hardcoding field names — new stats from future Dymola versions appear automatically
- Trajectory plots open by default; metadata sections are collapsed

## Anti-Patterns

### Don't use `fnmatch` for Modelica variable matching
- `pipe.T[1]` matches nothing because `[1]` is treated as a character class matching only `1`
- Use the custom `_pattern_to_regex()` instead

### Don't run Dymola per-test in separate processes
- Library loading overhead (30-60s) dominates. Batch execution loads once per worker.

### No-baseline plot generation
- When a test has no reference baseline, variable plots are still generated showing the simulated trajectory
- Plots display a "NEW" badge and use a single panel (no error panels since there is no reference to compare against)
- Enables visual review of new tests before accepting them as baselines

### Don't store simulation artifacts in shared directories during parallel runs
- `dsin.txt`, `dslog.txt`, `dsfinal.txt`, `dsres.mat` are overwritten by each simulation
- Per-test subdirectories (`test_NNNN/`) are required for parallel execution

### Don't use `np.interp` directly on time series with duplicate times
- Returns the post-event value at duplicate time points
- Causes false comparison errors for identical signals with events
- Use `_dedup_time_series()` to select pre- or post-event values explicitly

### Don't put `--reference-root` on both parent parser and subcommands
- argparse subcommand defaults (`None`) override the parent parser's value
- Top-level args that apply globally should only be on the parent parser
