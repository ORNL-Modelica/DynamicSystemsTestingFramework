# Patterns and Anti-Patterns

## Proven Patterns

### Dymola `.mos` script structure: startup → per-test → shutdown
- `startup.mos`: `cd()`, load dependencies, load main library, setup commands
- `test_NNNN.mos`: `cd()` to test subdir, `simulateModel(...)` with `resultFile="test_NNNN"`
- `shutdown.mos`: `Modelica.Utilities.System.exit()`
- `batch_NNNN.mos`: `RunScript()` calls chaining startup + all tests + shutdown
- Each test gets its own subdirectory to prevent file conflicts (`dsin.txt`, `dslog.txt`, `dsfinal.txt` are per-simulation)

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

### Diagnostic variables are stored but never compared
- `CPUtime` and `EventCounter` are auto-extracted from mat data when present
- Full trajectories go in `diagnostics` section of reference JSON (for plotting)
- Final values go in `statistics` (for structural change warnings)
- `EventCounter` changes trigger a warning; `CPUtime` does not (too noisy)

## Anti-Patterns

### Don't use `fnmatch` for Modelica variable matching
- `pipe.T[1]` matches nothing because `[1]` is treated as a character class matching only `1`
- Use the custom `_pattern_to_regex()` instead

### Don't run Dymola per-test in separate processes
- Library loading overhead (30-60s) dominates. Batch execution loads once per worker.

### Don't store simulation artifacts in shared directories during parallel runs
- `dsin.txt`, `dslog.txt`, `dsfinal.txt` are overwritten by each simulation
- Per-test subdirectories (`test_NNNN/`) are required for parallel execution

### Don't use `np.interp` directly on time series with duplicate times
- Returns the post-event value at duplicate time points
- Causes false comparison errors for identical signals with events
- Use `_dedup_time_series()` to select pre- or post-event values explicitly

### Don't put `--reference-root` on both parent parser and subcommands
- argparse subcommand defaults (`None`) override the parent parser's value
- Top-level args that apply globally should only be on the parent parser
