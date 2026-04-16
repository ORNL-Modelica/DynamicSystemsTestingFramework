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
- Three width modes via `tube_width_mode`:
  - `"rel"` (default in interactive UI): width = fraction of |reference| (e.g., `"tube_rel": 0.02` = 2%)
  - `"band"` (or legacy `"abs"`): width = offset in signal units (e.g., `"tube_abs": 500`)
  - `"absolute"`: upper/lower are literal y-axis bounds, not offsets from reference
- Legacy format (no `tube_width_mode`): width = `max(tube_abs, tube_rel * |reference|)`
- Pass/fail is strict: the actual signal must stay inside the tube at every interpolated point
- Constant tube: `{"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02}` — uniform width over all time
- Time-varying tube: `tube_points` with `{"time", "upper", "lower"}` control points, interpolated via `tube_interpolation` (`"constant"` for stepwise, `"linear"` for linear — default is `"linear"`)
- Mixed-mode control points: each point can use a different width mode. Bounds are resolved to absolute y-values at each control point first, then interpolated — no discontinuities at mode boundaries
- Before the first control point: hold first point values. After the last: hold last point values
- Metrics reported: `tube_points_inside` (fraction 0-1), `tube_worst_violation` (largest distance outside tube), `tube_worst_violation_time`
- NRMSE is still computed for reference even in tube mode
- Constant variables (2-point signals) hide the tube mode selector — NRMSE tolerance is the right tool for these
- HTML reports show "tube (95% in)" style labels; tolerance input disabled in tube mode (tube has its own pass/fail)
- Interactive Plotly reports allow switching modes, editing tube points, and exporting tolerance configs
- Tube visualization: `fill:'toself'` polygon for the shaded band + separate upper/lower line traces for hover readout
- All plot traces share the reference time grid for unified hover alignment
- Interactive tube editing: Shift+click to add, Shift+drag to move, Shift+right-click to delete — no mode toggle, coexists with normal Plotly zoom/pan/scroll
- Tube rendering grid = ref grid ∪ control point times — ensures tube lines pass through CP markers when zoomed in. Pass/fail check uses ref grid only (matches backend comparator)
- Control point times are rounded to 6 significant figures on placement/drag to avoid floating point noise

### Tolerance resolution order
- Per-variable override from test spec (`comparison.variable_overrides`) takes highest priority
- Per-variable override from reference JSON (`comparison.variable_overrides`) is next
- Per-test comparison tolerance from test spec (`comparison.tolerance`)
- Comparison tolerance from reference JSON (`comparison.tolerance`)
- Global config tolerance (`config.tolerance`)
- Default: `1e-4`
- Each `VariableComparison` records `tolerance_used` — the tolerance that was actually applied, shown in HTML reports
- When accepting results, the active comparison settings (tolerance + variable overrides) are saved in the reference JSON's `comparison` section so tolerances travel with the baseline

### Comparison mode resolution via strategy pattern
- Per-variable comparison mode is resolved via `resolve_mode(var_override, tolerance, default_final_only)` in `comparison/modes.py`
- Three modes: `NrmseMode` (default, piecewise NRMSE), `TubeMode` (envelope), `FinalOnlyMode` (final value only)
- Each mode has a typed frozen config dataclass: `NrmseConfig`, `TubeConfig`, `FinalOnlyConfig`
- Resolution: explicit `mode` key in override → that mode; no explicit mode + `default_final_only=True` → FinalOnlyMode; otherwise → NrmseMode
- Explicit `mode: "tube"` is never overridden by the `final_only` flag — per-variable mode always wins

### Constant signal NRMSE: normalize by magnitude
- When signal range < epsilon (constant signals), NRMSE normalizes by `max(|ref_values|)` instead of signal range
- Avoids false failures from float32 quantization on large-magnitude constants (e.g., 512-unit error on 37e9 gives nrmse ≈ 1.4e-8, not 512)
- Falls back to raw RMSE only when magnitude is also near-zero (true zero constant)
- Consistent with how `_compare_final_values` normalizes by `|ref_final|`

### Simulator registry and lazy backend imports
- Backends self-register via `@register(name)` class decorator in `simulators/__init__.py`
- `get_runner(config)` factory looks up the backend by `config.simulator_backend` and instantiates it
- Built-in backends are lazy-imported on first use via `_import_builtin_backend()` — avoids loading all backends at startup
- `DymolaRunner` extracts Dymola-specific settings into an immutable `DymolaConfig` dataclass at init

### Variable naming from UnitTests expressions
- Simple case (`x={a, b, c}`): parsed into individual expression names `["a", "b", "c"]`
- Complex case (`x=cat(1, eta, lambda)`): can't decompose without knowing array sizes at parse time
- When `len(x_expressions) != n_vars`, all variables fall back to `x[1]`...`x[n]` — avoids misleading names where first var gets the raw expression and the rest get generic names
- Comparator sanitizes names from reference JSON: any name containing newlines or starting with `cat(` is replaced with `x[index]`
- The raw expression is preserved in `TestModel.x_raw` for reference

### Config resolution order
- CLI args > `testing.json` file > defaults
- `testing.json` search: reference_root → repo_root → package_dir → cwd
- All relative paths in `testing.json` (`source_path`, `test_spec`, `dependencies`, `reference_root`) resolve relative to where `testing.json` was found
- `testing.json` can be the single entry point: include `source_path` and run with just `--config` or `--reference-root`
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
- `html_report.py` builds a context dict from `TestComparison` and renders through Jinja2 templates
- Two report variants generated per test: `comparison.html` (static matplotlib plots) and `interactive.html` (Plotly.js interactive charts)
- Per-test reports open `interactive.html` by default
- A `comparison_data.json` sidecar is written alongside the HTML, containing the same data plus full trajectory time series (`act_time`, `act_values`, `ref_time`, `ref_values` per variable) for downstream tooling
- The static template uses progressive disclosure: key stats cards at top, condensed variable table, then collapsible `<details>` sections for full variable details, statistics, simulation parameters, diagnostics, and reference info
- Simulation parameters show current vs reference values with change highlighting when they differ
- Statistics sections iterate over whatever keys exist in the dicts rather than hardcoding field names — new stats from future Dymola versions appear automatically
- Trajectory plots open by default; metadata sections are collapsed

### Interactive Plotly report UX
- `interactive.html` uses Plotly.js via CDN for interactive charts — zoom, pan, hover tooltips on all data points
- Three-panel layout per variable: trajectory (actual vs reference), absolute error, and NRMSE — the NRMSE panel shows a tolerance line that updates live
- Error overlay dropdown on each variable plot: overlay signed error, absolute error, or NRMSE on the trajectory chart (right y-axis)
- Live tolerance editing: global test tolerance input at the top recomputes all pass/fail; per-variable tolerance inputs in the variable table override the global value when modified (highlighted orange); summary stats and key cards update live
- Export tolerance config panel (expanded by default): shows a JSON snippet that updates live as tolerances are edited; "Copy to Clipboard" and "Download JSON" buttons for saving the config; the downloaded JSON can be applied via `modelica-testing spec-update`

## Anti-Patterns

### Don't use `fnmatch` for Modelica variable matching
- `pipe.T[1]` matches nothing because `[1]` is treated as a character class matching only `1`
- Use the custom `_pattern_to_regex()` instead

### Backend-agnostic live progress via meta-refresh dashboard
- `ProgressReporter` (`simulators/progress.py`) holds in-memory `TestStatus` per test (`queued` / `running` / `passed` / `failed` / `timed_out`) plus elapsed/detail/worker_id
- Writes `status.json` (structured, for tooling) + `dashboard.html` (auto-refreshes via `<meta http-equiv="refresh" content="2">`) to `work_dir` on every state change — no server needed, works over `file://`
- Atomic write pattern: unique tmp filenames (`status.json.{pid}.{uuid}.tmp`) + a dedicated `_write_lock` serializing the write+replace. Both are required on Windows where `replace` fails when another thread holds the file
- `register(test_key, model_id, report_dir)` accepts a per-test report dir name (`"ref_NNNN"` or `"test_NNNN"`) so the dashboard's model-name link points at the canonical per-test report (`reports/{report_dir}/interactive.html`) — matches `generate_report_suite` naming
- `runner.ref_id_map: dict[model_id, "ref_NNNN"]` is populated by the CLI before `run_tests` (from `ReferenceStore.index`); runner consults it at registration time so links work even mid-run before the report is generated
- `finalize()` strips the meta-refresh tag so the final dashboard doesn't keep reloading after the run completes

### Per-phase timing on the persistent runner
- `DymolaWorker.run_test` splits `simulateModel` into explicit `translateModel` + `simulateModel` + `savelog` so each phase is measured independently. Timings (`translation_wall`, `sim_wall`) are fields on `TestRunResult`; total wall is `elapsed`
- `runner.read_result` stashes the breakdown under `stats["timing"]` with keys `translation_wall`, `sim_wall`, `other_wall` (= total − translation − sim), `total_wall`. Values are rounded to 2 decimals at storage time so reference JSON stays clean
- `ProgressReporter.on_phase(test_key, phase)` tracks `"translating"` / `"simulating"` / `"finalizing"` — dashboard status cell shows `running (simulating)` live, so translation time is visible before sim completes
- Console progress line gains `[xlate 1.2s, sim 63.0s]` detail on success
- Report's stats-section builder now iterates every top-level dict in `stats` (not just `translation` + `simulation`) and renders each as its own collapsible section. Any future stat category drops in for free; `_build_stats_section` accepts an optional `key_order` so the Timing section preserves operation order rather than sorting alphabetically
- Index page gains sortable `Translate (s)` / `Sim (s)` / `Total (s)` columns plus click-to-sort on all columns (text / numeric via `data-sort-*` attributes)
- `simulation.cpu_time` renamed to `simulation.cpu_time_integration` to disambiguate from the `CPUtime` diagnostic-variable final (those measure different scopes — integration step vs. full simulation)

### Verify sim completion via dsfinal.txt + reached-stop-time
- `dsres.mat` is written incrementally by Dymola — a killed-mid-sim leaves a partial-but-valid-looking file. Existence alone isn't sufficient proof of completion
- `mat_reader.read_mat_time_extents(mat_path)` cheaply reads just row 0 of `data_2` (time) via memmap and returns `(first, last)`
- Success criteria (applied in both batch and persistent runners): translation didn't abort AND `dsres.mat` exists AND `dsfinal.txt` exists AND `last_time ≥ stop_time − tol`
- Failure messages are specific so users know why: `"Translation failed"` / `"No result file produced"` / `"Simulation aborted (no dsfinal.txt)"` / `"Stopped early at T=4.7 of 10.0"` — plus last few ERROR lines from dslog appended
- Lenient timeout: the watchdog kills but then checks disk; a sim that completed just past the deadline gets credit rather than being wasted

### Persistent Dymola workers via Python interface
- `simulators/dymola/interface_loader.py` auto-discovers the `dymola` archive (`.whl` for ≥2025, `.egg` for older) under platform install roots (`C:\Program Files\Dymola *\Modelica\Library\python_interface/`). Wheels are extracted once into a user cache dir; eggs are added to `sys.path` directly (zipimport). Override via `--dymola-interface` / `dymola_interface_path` / `DYMOLA_INTERFACE_PATH`. Diagnose with `modelica-testing check-dymola`
- `PersistentDymolaRunner` subclasses `DymolaRunner` and overrides only `run_tests`; inherits `read_result` and config extraction. Each worker is a `DymolaWorker` wrapping one `DymolaInterface` instance
- Reliable per-worker PID tracking: pulled directly from `DymolaInterface._dymola_process.pid` (the internal `subprocess.Popen` handle), so we own the kill target without snapshot diffs
- Parallel startup: monkey-patch Dymola's `dymola_interface_internal.dymola_lock` to a no-op (broad lock that holds for the whole `__init__` including `_check_dymola`'s ~7s ping wait), and add a narrow lock around `_find_available_port` so two workers can't pick the same random port. Without this patch, N workers serialize on Dymola's lock and startup is N×7s
- Timeout watchdog: `run_test_with_timeout` spawns a daemon runner thread, joins with the test's timeout. On expiry: `close(grace=1s)` attempts graceful shutdown, then `psutil.Process(pid).kill()` on tracked PIDs. Inner thread finishes cleanup out-of-band (short `join(0.5)` is enough; the thread is daemon and its result is already discarded)
- Worker restart: after a timeout/exception, `w.dymola = None`. Next iteration of `_worker_loop` sees `is_alive() == False` and calls `_try_restart(w)` — new `DymolaInterface` + library reload. Cap at `MAX_RESTARTS_PER_WORKER = 3`; after that, tests dispatched to that slot get a clean "Worker dead; restart exhausted" failure
- Noise suppression: Dymola's `DymolaLogger._PrintMessage` uses `print(msg)` to stdout, so stderr filters don't catch its urllib-retry noise. We monkey-patch `DymolaLogger._PrintMessage` instead — during kill-window grace periods, lines matching `WinError 10054/10061` or `urlopen error` are silently dropped. Outside the window, full passthrough

### Persistent test_keys (manifest accumulates across runs)
- `assign_test_keys()` (`simulators/base.py`) is the single allocation point. Loads existing `batch_manifest.json` if present, builds `model_id → test_key` reverse lookup, reuses existing test_keys for known models, assigns the next sequential `test_NNNN` for new models, stamps `last_run_at` on tests being run
- A model always gets the same `test_NNNN` for its lifetime — reruns naturally overwrite their own slot, prior dirs of unrelated tests stay intact
- Per-test work directories are `rmtree`'d only for tests being run *this invocation* (`for test, test_key in test_items`), not for everything in the manifest
- Both `base.SimulatorRunner.run_tests` and `DymolaRunner.run_tests` go through the same helper to keep behavior consistent across backends
- Manifest entry shape: `{model_id, ref_id, last_run_at}`. Legacy plain-string-value format still loadable via `BatchManifest.load()`
- Orphan handling: `run` and `compare` notify (one line) when the manifest contains entries for models no longer discovered. `manifest cleanup --orphans` lists them with their on-disk dirs (work + report); `--apply` removes entries and dirs. Never auto-prunes — discovery is too fragile (transient parse errors, missing deps) to trust as a delete trigger

### Index-page batch actions feed the CLI filter
- Index template (`reporting/templates/index.html`) has a checkbox column + action panel. All selection state lives client-side in the DOM (rows get `.selected` class + a checked input)
- Bulk selectors (`+ Failed`, `+ Sim Failed`, `+ No Baseline`, `+ With Warnings`, `+ Stale`) operate over **visible** rows so the existing filter buttons act as a pre-filter for selection
- Stale detection reuses the `last_run_at` heuristic (>60s older than newest); the row's `data-stale="1"` attribute is set by the same JS that renders the relative-time column
- Three export formats: clipboard comma-list, downloaded `selected.txt` (one model_id per line — directly consumable by `--filter @file`), and a copyable command string that auto-switches between inline `"A,B,C"` (≤3 models) and `@selected.txt` (more)
- Live command preview in a textarea so users see exactly what they'll get before clicking Copy
- No server, no API — composes with the static HTML report design (works over `file://`)

### --merge expands report scope to the full manifest
- `run --merge` keeps the *run* scope as `tests` (filtered) but expands the *read/compare/report* scope to every model in `manifest.manifest`
- `runner.read_results` already handles `rr=None` for non-rerun tests by reading whatever `dsres.mat` exists in the test_dir on disk — so prior results merge naturally with fresh ones, no special path needed
- `--rerun` always sets `args.merge = True` since rerunning failed tests in isolation defeats the goal (you want to see them in context of the whole suite)
- Stale-data visibility: `last_run_at` is rendered as a relative time on the index (with ISO tooltip); rows >60s older than the newest run are greyed out and tagged "Stale: from a prior run; rerun to refresh"

### Configurable batch size with queue dispatch
- `Config.batch_size` (CLI: `--batch-size N`). Unset → one big batch per worker (current behavior, minimizes library reloads). Set → many small batches, all submitted to the `ThreadPoolExecutor`; workers pull next batch as they free up
- Crash/timeout blast radius is bounded by `batch_size` — a hung test only takes down its batch, not its worker's whole share
- `worker_id` for the dashboard comes from the actual pool thread slot via `threading.current_thread().name` (regex-matched suffix), not the batch index — stable attribution across many batches per worker
- Tradeoff: smaller batches reload Dymola's library more often. Sweet spot is ~3-10 depending on per-test runtime

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

## Forward hooks

### `comparison_data.json` sidecar is the stable contract for future interactive UI
- Every per-test report directory writes `comparison_data.json` alongside `comparison.html` / `interactive.html` — the full render context serialized to JSON
- Any future interactive viewer (client-side JS single-page app, tolerance playground, tree-editor) can be built against this file without touching the Python side
- **Preserve this** when extending reports: new context fields go into the dict, old fields stay JSON-serializable
- If you ever need richer client interaction (live tolerance editing, tree drag-and-drop), the upgrade path is JS in the existing templates reading the sidecar — not a GUI rewrite
