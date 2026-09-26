# Decisions Log

## D1: Standalone repo, not embedded in TRANSFORM-Library

- **What**: ModelicaTesting is its own repo with `src/` layout, not a subdirectory of any Modelica library.
- **Why**: The tool is library-agnostic. Embedding it in one library's repo couples release cycles and makes reuse awkward.
- **Trade-offs**: Requires a separate install step (`uv run`) and version coordination. Acceptable given early stage.

## D2: Numeric test IDs

- **What**: Reference files are named `ref_0001.json`, `ref_0002.json`, etc. Each file contains `model_id` and `test_id` as metadata. IDs are never reused — obsolete tests are marked, not deleted.
- **Why**: Modelica model paths like `TRANSFORM.Fluid.Pipes.Examples.GenericPipe_withWall_Counter_wTraceMass` exceed Windows MAX_PATH (260 chars) when used as filenames. Abbreviated names were fragile and collision-prone.
- **Trade-offs**: Requires scanning ref files at startup to build the in-memory index. Reference files are partitioned per simulator+OS.

## D3: Reference partitioning by simulator and OS

- **What**: References live at `<reference_root>/<SimulatorBackend>/<os>/ref_NNNN.json`.
- **Why**: Different solvers (Dymola vs OpenModelica) and platforms (Windows vs Linux) produce numerically different results. A single baseline can't serve all combinations.
- **Trade-offs**: More files to manage. The manifest is shared (IDs are stable across platforms), only the reference data differs.

## D4: Batch execution for Dymola

- **What**: Tests are grouped into batches. Each batch runs in one Dymola session: load libraries once, simulate N tests, exit. Per-test subdirectories prevent file conflicts.
- **Why**: Dymola startup + library loading takes 30-60s. Per-test processes made a 100-test suite take hours; batch mode cuts it to minutes.
- **Trade-offs**: A batch timeout kills all remaining tests in that batch. Dymola's working directory management requires explicit `cd()` calls in `.mos` scripts.

## D5: NRMSE as comparison metric (not AbsRelRMS)

- **What**: Pass/fail uses `NRMSE = RMSE / signal_range`. For constant signals (range ~ 0), RMSE is normalized by signal magnitude (`max(|ref|)`) instead. Falls back to raw RMSE only when magnitude is also near-zero.
- **Why**: Simpler and more interpretable than Modelica's AbsRelRMS. Range normalization makes tolerance meaningful across variables with different magnitudes. Magnitude normalization for constants avoids spurious failures from float32 quantization noise on large values (e.g., 512-unit error on a 37e9 constant is only 1.4e-8 relative).
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
- **Why**: Early-stage repo with no external consumers.
- **Trade-offs**: Existing references must be regenerated. Migration utilities were provided during the transition and have since been removed.

## D10: Diagnostic variables (CPUtime, EventCounter) auto-captured

- **What**: `CPUtime` and `EventCounter` are automatically extracted from simulation results when present (requires `OutputCPUtime := true;` in Dymola). Full trajectories are stored in a `diagnostics` section of the reference JSON. Final values are added to `statistics` for simple change detection.
- **Why**: CPU time and event counts are critical for diagnosing performance regressions and model changes, but shouldn't cause pass/fail. Storing trajectories enables plotting; storing finals in statistics enables structural warnings.
- **Trade-offs**: Diagnostics are stored but never compared via NRMSE. EventCounter changes trigger a structural warning; CPUtime does not (too variable between runs).

## D11: Config-relative path resolution

- **What**: All relative paths in `testing.json` (`source_path`, `test_spec`, `dependencies`, `reference_root`) resolve relative to where `testing.json` was found, not relative to the library or cwd.
- **Why**: When references live in a separate repo, config and test specs sit together in that repo. Resolving relative to the library root would require fragile cross-repo relative paths. This also enables a single `--config` or `--reference-root` flag to drive everything.
- **Trade-offs**: None significant. CLI flags still accept absolute paths and override config file values.
- **Note**: Originally named `package_path`; renamed in D57 (Phase 4.D).

## D12: `testing.json` as single entry point

- **What**: `testing.json` can contain `source_path` pointing to the library under test. With this, a single flag (`--config` or `--reference-root`) is sufficient to run — no `--source-path` needed.
- **Why**: Reduces command-line boilerplate. The config file already knows everything about the test setup; requiring the user to also specify the library path is redundant.
- **Trade-offs**: `source_path` in the config is relative, so moving the config file breaks the path. CLI `--source-path` still overrides.
- **Note**: Originally named `package_path` / `--package-path`; renamed in D57 (Phase 4.D).

## D13: In-memory index replaces persistent manifest

- **What**: `test_manifest.json` is removed. The mapping from model IDs to ref file IDs is built in memory by scanning `ref_NNNN.json` files at the start of each run. Each ref file contains `model_id`, `test_id`, `status`, `date_added`, and `last_updated` as metadata fields.
- **Why**: The manifest was a persistent index that easily got out of sync with the ref files (e.g., after manual migration of 300+ files). Since the ref files already contain all the information, the manifest was redundant. Scanning 300 small JSON files takes under a second.
- **Trade-offs**: Slight startup cost to scan files. No way to track metadata (like date_added) outside the ref files themselves — but that's actually better since the ref files are the source of truth.

## D14: ModelicaTestingLib as top-level Modelica library (superseded)

- **Update (Phase 1.1, 2026-04-15)**: Relocated to `examples/modelica/ModelicaTestingLib/`. Once the framework becomes multi-ecosystem (FMU, Julia, Simulink, data-file), a single top-level directory named after one ecosystem is misleading. The `examples/<ecosystem>/` layout scales to additional demo sources and matches convention (FMPy, BuildingsPy).
- **What (original)**: A small Modelica library (`ModelicaTestingLib/`) lived at the project root. It contains a reusable `UnitTests` component, example models (SimpleTest, EventTest, ConstantTest, NoUnitTest), and its own reference results under `Resources/ReferenceResults/`.
- **Why (original)**: Serves dual purpose — test fixture for the pytest suite (real `.mo` files for discovery/parsing tests) and reference implementation showing how to set up `UnitTests` in a library. Top-level placement made it easy for users to find.
- **Trade-offs (original)**: Top-level directory in the repo that isn't Python code. Resolved by moving under `examples/`.

## D15: Dymola framework settings hardcoded in runner

- **What**: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are always set in the Dymola runner's `startup.mos`, not in `testing.json`'s `simulator_setup`.
- **Why**: These are framework concerns — every test run needs CPU time diagnostics and translation statistics. Making users configure them is error-prone and adds boilerplate to every `testing.json`.
- **Trade-offs**: Users can't disable them. Acceptable since they have no effect on simulation results, only on what's logged/captured.

## D16: Translation log capture via clearlog/savelog

- **What**: Each test's `.mos` script wraps `simulateModel` with `clearlog()` before and `savelog("translation_log.txt")` after. Combined with the `Advanced.UI.TranslationInCommandLog` flag, this captures translation statistics (nonlinear/linear system sizes, continuous states, state names) per test.
- **Why**: `dslog.txt` only contains simulation runtime stats (CPU time, steps, events). Structural info (system sizes, state counts) comes from the translator and was previously lost. The translation log is essential for detecting structural model changes.
- **Trade-offs**: Adds two extra lines per test `.mos` script. `clearlog()` prevents contamination between tests. The flag name (`Advanced.UI.TranslationInCommandLog`) is Dymola 2025x+; older versions may use `Advanced.TranslationInCommandLog`.

## D17: Auto-derive numberOfIntervals from simulation results

- **What**: When neither `numberOfIntervals` nor `outputInterval` (Modelica `Interval`) is set on a test model, `store_reference()` counts unique time points from the actual result and stores the derived `numberOfIntervals`. On subsequent runs, this value is passed to `simulateModel()` for consistent output grids.
- **Why**: Dymola defaults to 500 intervals when nothing is specified. Without storing this, a future run could produce a different number of output points if someone changes Dymola defaults, causing false comparison failures.
- **Trade-offs**: The derived count may differ slightly from the Dymola default due to events adding extra time points. `numberOfIntervals` takes precedence over `outputInterval` if both are somehow set.

## D18: Custom MAT4 reader replacing scipy

- **What**: `mat_reader.py` uses a custom binary parser (~100 lines) with `numpy.memmap` instead of `scipy.io.loadmat`. Reads MAT4 headers via `struct.unpack`, loads small matrices (`name`, `dataInfo`, `data_1`) eagerly, and memory-maps `data_2` for selective row access. scipy is no longer a dependency.
- **Why**: `scipy.io.loadmat` loads the entire `data_2` matrix into memory. For large models (76,992 variables, 36MB file), this took 397 seconds — making the read phase appear to hang. The custom reader extracts only the ~10 needed variable rows via memmap, reducing this to under a second. DyMat and BuildingsPy were investigated but both use the same `loadmat` call internally.
- **Trade-offs**: We own the parser instead of delegating to scipy. Acceptable because MAT4 is a simple, stable format (unchanged since the 1990s) and the parser is straightforward. Drops ~40MB of installed dependency weight.

## D19: Jinja2 templates for HTML reports

- **What**: HTML report generation was refactored from inline f-string HTML to a Jinja2 template (`reporting/templates/comparison.html`). The Python code builds a context dict and renders through Jinja2. A `comparison_data.json` sidecar is written alongside the HTML.
- **Why**: Separating data from presentation makes the template editable without touching Python logic. The JSON sidecar enables downstream tooling (dashboards, CI integrations) to consume structured data without parsing HTML. Auto-detecting statistics fields (iterating dict keys instead of hardcoding) means new Dymola version stats appear without code changes.
- **Trade-offs**: Adds `jinja2>=3.1` as a dependency. Template syntax is less familiar than Python string formatting, but the template is self-contained and easier to maintain than scattered f-strings.

## D20: Translation log parsing — initialization section and integer system sizes

- **What**: The log parser now captures the "Initialization problem" subsection separately from simulation-level stats. System size lists (`nonlinear`, `linear`, etc.) are stored as `list[int]` with computed summary fields (`_count`, `_total`, `_max`). Initialization fields use the `init_` prefix.
- **Why**: The initialization section contains distinct nonlinear/linear system sizes and Jacobian counts that were previously missed entirely. Storing sizes as integer lists (not comma-separated strings) enables aggregation and programmatic analysis. Summary fields make structural change detection and HTML display practical without parsing long lists.
- **Trade-offs**: More fields in the statistics dict. The summary fields are redundant (derivable from the lists) but worth storing for display convenience.

## D21: Structured test_spec.json with separated simulation and comparison settings

- **What**: `test_spec.json` entries now use a structured format. Simulation parameters (`stop_time`, `tolerance`, `method`, etc.) live under a `simulation` key. Comparison settings (`tolerance`, `variable_overrides`) live under a `comparison` key. Both are optional — a minimal entry needs only `model` and `variables`. The old flat format (stop_time, tolerance at top level) is replaced.
- **Why**: Simulation tolerance (solver accuracy) and comparison tolerance (NRMSE threshold) are fundamentally different concepts. Mixing them at the top level was confusing. The structured format makes intent clear and enables per-test and per-variable comparison overrides.
- **Trade-offs**: Breaking change to `test_spec.json` format. Acceptable under D9 (no backward compatibility).

## D22: Multi-level tolerance resolution

- **What**: Comparison tolerance is resolved in priority order: per-variable override (spec) > per-variable override (reference JSON) > per-test comparison tolerance > reference JSON comparison tolerance > config.tolerance > default (1e-4). Each `VariableComparison` records `tolerance_used`.
- **Why**: Some variables (e.g., temperatures near zero) need looser tolerances than the test-level default. Per-variable overrides avoid raising the tolerance for the entire test. Storing comparison settings in the reference JSON means tolerances travel with the baseline — someone cloning the repo gets the same pass/fail behavior without needing the original test_spec.json.
- **Trade-offs**: More complex resolution logic. The `tolerance_used` field in reports makes it transparent which tolerance was applied.

## D23: Tube comparison — strict envelope with three width modes

- **What**: A tube comparison mode alongside NRMSE. Configured per-variable via `variable_overrides` with `"mode": "tube"`. Three width modes via `tube_width_mode`: `"rel"` (fraction of |reference|, default in interactive UI), `"band"` (offset in signal units, legacy `"abs"`), `"absolute"` (literal y-axis bounds). Legacy format (no `tube_width_mode`): `width = max(tube_abs, tube_rel * |reference|)`. Pass/fail is strict — the actual signal must stay inside at every point. Supports constant and time-varying tubes (`tube_points` with linear or stepwise interpolation).
- **Why**: NRMSE is a single aggregate metric that can mask localized violations — a signal might have excellent NRMSE but briefly spike outside acceptable bounds. Tubes provide pointwise guarantees. Relative mode is the most intuitive default (e.g., 2% tolerance). Band mode is useful when the tolerance has a physical unit (e.g., ±500 Pa). Absolute mode is useful when bounds are known a priori (e.g., a temperature must stay between 290 and 310 K). Time-varying tubes allow tighter tolerances during steady-state and looser ones during transients.
- **Trade-offs**: Strict checking means a single point outside the tube fails the variable. This is intentional — tubes are meant for hard bounds. NRMSE is still computed alongside tube results for reference. Interactive Plotly reports allow switching modes, editing tube points, and exporting tolerance configs.

## D25: Interactive Plotly reports via CDN

- **What**: `interactive.html` is generated alongside the static `comparison.html` for each test. It uses Plotly.js loaded from CDN (`cdn.plot.ly`) for interactive charting. Per-test report links open `interactive.html` by default.
- **Why**: Static matplotlib PNGs don't support zoom, pan, or hover — critical for inspecting time series with thousands of points. Plotly.js provides these interactively in the browser without any Python server. CDN loading avoids bundling a 3MB+ JS library in the repo or generated reports.
- **Trade-offs**: Requires internet access to load Plotly.js from CDN (reports won't render charts offline). Acceptable because reports are typically viewed on developer machines with network access. A future enhancement could offer a `--bundle-plotly` flag to embed the library for offline use.

## D24: Stale artifact protection

- **What**: Test directories are cleaned (`rmtree` + recreate) before each simulation run. The translation log is also checked for "Translation aborted" as defense in depth.
- **Why**: Stale `dsres.mat` or `dslog.txt` from a previous run could be misread as current results, causing silent false passes. This is especially dangerous when a simulation fails silently (no crash, just no output) and old artifacts remain.
- **Trade-offs**: Slightly slower startup (directory recreation). Acceptable for correctness.

## D26: Tube bounds resolve-then-interpolate

- **What**: When computing tube bounds from control points with mixed width modes (e.g., point 1 is band, point 2 is relative), each control point is first resolved to its final absolute y-bound, then those resolved values are linearly interpolated across the reference time grid.
- **Why**: The alternative — interpolating raw values between control points and applying modes after — creates discontinuities at mode boundaries. For example, interpolating a band value of 0.25 toward a relative value of 1.0 produces a smooth raw curve, but applying the mode stepwise causes a jump when the mode switches from band to relative. Resolve-first ensures the tube envelope is always a smooth interpolation between the user's intended bounds.
- **Trade-offs**: The interpolated bound between two control points is a straight line in y-space, which may not match what you'd get from interpolating in width-space for a single mode. This is acceptable — mixed-mode points are inherently about defining specific bound values at specific times.

## D27: Variable naming fallback for complex expressions (superseded by D132)

- **What**: When a UnitTests component uses a complex expression like `cat(1, eta, lambda)` for its tracked variables, all variables fall back to `x[1]`...`x[n]` naming. The comparator also sanitizes names from stored reference JSON (newlines, `cat(` prefix).
- **Why**: The parser can decompose simple `x={a, b, c}` into individual names, but `cat()` requires knowing array sizes at parse time (which array has how many elements). Guessing is worse than admitting we don't know. Showing `cat(1, eta, lambda)` as the first variable's name and `x[2]`...`x[n]` for the rest is misleading — the expression describes the whole array, not one element.
- **Trade-offs**: Users lose meaningful names for `cat()` variables. The raw expression is preserved in `TestModel.x_raw` and could be surfaced as a tooltip or header in future UI improvements.

## D28: Pluggable comparison modes via strategy pattern

- **What**: Comparison logic uses a `ComparisonMode` ABC (`comparison/modes.py`) with three implementations: `NrmseMode`, `TubeMode`, `FinalOnlyMode`. Each has a typed frozen config dataclass. `resolve_mode(var_override, tolerance, default_final_only)` factory converts per-variable override dicts to mode instances.
- **Why**: Replaces if/elif dispatch in `compare_test()`. Per-variable mode selection is type-safe. Adding new comparison strategies (Frechet distance, spectral coherence, x-direction tolerance) requires only a new class — no changes to the comparator orchestration. Also fixes a bug where `config.final_only` silently overrode explicit `mode: "tube"` settings.
- **Trade-offs**: Adds indirection (dict → dataclass → mode). `TubeConfig.to_dict()` bridges back to the flat dict format for the existing `_compare_tube()` internals.

## D29: Comparator functions take scalar args, not Config

- **What**: `compare_test()` and `compare_all()` take `default_tolerance: float` and `final_only: bool` instead of `config: Config`. The `comparison/comparator.py` module no longer imports Config.
- **Why**: Only two Config fields were used. Passing the full object coupled comparison logic to the Config API, making unit tests require mock Config construction. Scalar args are simpler to test and make the dependency explicit.
- **Trade-offs**: CLI layer must extract values from Config before calling. Minimal burden.

## D30: Simulator registry with self-registration

- **What**: `simulators/__init__.py` provides a `@register(name)` decorator and `get_runner(config)` factory. `DymolaRunner` is decorated with `@register("Dymola")`. Built-in backends are lazy-imported on first use.
- **Why**: Replaces hard-coded `if backend == "Dymola"` in the CLI. Adding a new backend (e.g., OpenModelica) requires only implementing the runner class with `@register("OpenModelica")` — no changes to the CLI or factory.
- **Trade-offs**: Lazy import adds a small indirection. Built-in backend names are still listed in a hardcoded dict for the lazy import, but this is a single line per backend.

## D31: Simulator-specific config via DymolaConfig

- **What**: `DymolaConfig` frozen dataclass in `simulators/dymola/runner.py` holds `show_ide`, `simulator_setup`, `diagnostic_variables`. Constructed via `DymolaConfig.from_config(config)` at runner init.
- **Why**: These fields on Config are meaningless for non-Dymola backends. Extracting them into a typed dataclass documents what's Dymola-specific and gives the runner a clean, immutable config object. Config itself is unchanged (fields still loaded from testing.json) to avoid format disruption.
- **Trade-offs**: Slight duplication — fields exist on both Config and DymolaConfig during transition. Acceptable until a second backend motivates removing them from Config.

## D32: Report directories use ref/test IDs, not model names

- **What**: Per-test report directories are named `ref_NNNN` (when a reference exists) or `test_NNNN` (for no-baseline tests) instead of sanitized model names.
- **Why**: Long Modelica model names (e.g., `TRANSFORM.Fluid.ClosureRelations...CHFtransition_F1D`) exceeded Windows' 260-character path limit after sanitization. Ref/test IDs are short and already available. The index page provides the human-readable mapping.
- **Trade-offs**: Directory names are no longer self-describing. Acceptable because users navigate via the index page, not by browsing directories.

## D33: Per-test report dirs cleared on regeneration

- **What**: `generate_comparison_plots()` does `rmtree(plot_dir)` before recreating. Sibling test reports and the index are untouched.
- **Why**: Stale PNGs from a prior run (different variable set, different mode) would otherwise accumulate. Per-test granularity supports incremental workflows (#38) without nuking the whole report tree.
- **Trade-offs**: Hand-added files inside a test's report dir are lost on regeneration. Acceptable — that directory is generated output.

## D34: Backend-agnostic live progress dashboard

- **What**: `simulators/progress.py` provides a thread-safe `ProgressReporter` that writes `status.json` + `dashboard.html` to `work_dir` on every state change. Dashboard uses `<meta http-equiv="refresh" content="2">` for auto-update — works over `file://` with no server. Each test row links to its work directory; model name links to its per-test report (`reports/{ref_NNNN|test_NNNN}/interactive.html`).
- **Why**: The previous batched parallel mode produced no output until each batch finished — runs looked frozen. JSON + meta-refresh HTML gives live visibility with zero infrastructure. Backend-agnostic means future simulators inherit the dashboard for free.
- **Trade-offs**: Within a Dymola batch, individual test transitions aren't observable — all batch members flip from `queued` → `running` together at batch start, then to their final status when the batch completes. Per-test granularity requires either log tailing or persistent workers (deferred).
- **Atomic writes**: each write uses a unique tmp filename (`status.json.{pid}.{uuid}.tmp`) and a dedicated `_write_lock` serializes the `write + replace`. Without both, concurrent threads collide on Windows where `replace` fails when another thread holds the file open.

## D36: Persistent batch manifest (accumulating test_keys)

- **What**: `batch_manifest.json` accumulates entries across runs. The new `assign_test_keys()` helper (in `simulators/base.py`) loads the existing manifest, reuses the existing `test_NNNN` for any model already known, and assigns the next sequential number for new models. Each entry tracks `last_run_at`. Per-test work directories are only `rmtree`'d for tests being run this invocation; prior dirs are left intact.
- **Why**: Enables the incremental-rerun workflow (#38). Previously a `--filter`'d rerun assigned `test_0001..test_K` to the K filtered tests, colliding with the original full run's directory layout. With persistent keys, the same model always lands in the same dir — reruns naturally overwrite their own slot, leaving the rest of the suite's results undisturbed.
- **Trade-offs**: Renamed/removed models leave orphan entries in the manifest. Acceptable; future cleanup command can prune them. The manifest grows monotonically over time. Stale results stay stale until rerun — `last_run_at` makes this visible.

## D37: --merge flag for incremental rerun + full report

- **What**: `run --merge` (typically with `--filter`) expands the read/compare/report scope to every test in the persistent batch manifest, not just the just-run subset. Tests with prior results are read from disk; the just-run tests have fresh data.
- **Why**: Without this, `run --filter X --report` produces a report covering only X — losing visibility into the other ~99% of the suite. The incremental workflow is the common case for debugging large suites: rerun a few failing tests, see their fresh status alongside the rest.
- **Trade-offs**: Stale results from prior runs are reported as if current. To make this visible, `last_run_at` is shown per test (relative time on the index, ISO timestamp on the per-test report) and rows >60s older than the newest run are greyed out with a "Stale" tooltip.

## D42: Per-phase timing breakdown (translate / sim / other / total)

- **What**: The persistent runner splits `simulateModel` into an explicit `translateModel` + `simulateModel` pair so we can measure each phase separately. `TestRunResult` gains `translation_wall` and `sim_wall` fields (plus `elapsed` for total). Timings are rounded to 2 decimals at storage time so the on-disk reference JSON stays clean. The runner's `read_result` stashes them under `stats["timing"]` so they flow through to reports.
- **Why**: User observed "timeout fires at 60s but sim actually took ~63s" and asked where the time went. `simulateModel` internally does translation + integration + output write; dslog only reports integration CPU time. Without a breakdown users can't tell whether a slow test is translation-bound, sim-bound, or dominated by savelog/RPC overhead. The per-phase measurement surfaces that.
- **Translation-time available before sim**: the phase transition is reported via a new `ProgressReporter.on_phase(test_key, phase)` event (phases: `"translating"`, `"simulating"`, `"finalizing"`). Dashboard status cell shows `running (simulating)` live.
- **Other wall = total − translation − sim**: computed implicitly so users can see savelog / cd / JSON-RPC overhead as a single line item.
- **Disambiguation**: `simulation.cpu_time` renamed to `simulation.cpu_time_integration` so it's no longer confusable with the `CPUtime` diagnostic-variable final (which represents the full simulation CPU time, distinct from Dymola's "integration" measurement).
- **Report generic over sections**: `_build_template_context` no longer hardcodes `translation` + `simulation`; it iterates every top-level dict in stats and renders each as a collapsible section (known keys get friendly titles; unknown keys title-case the key). New stat categories drop in for free.
- **Trade-offs**: Explicit `translateModel` adds one extra JSON-RPC call per test (~ms). Dymola caches the translation internally, so subsequent `simulateModel` calls don't re-translate — this is the standard Dymola pattern and produces identical results to the combined call.

## D41: Persistent Dymola workers via Python interface (now the default)

- **What**: `run` defaults to `PersistentDymolaRunner` which keeps N long-lived `DymolaInterface` processes alive. Each worker loads the library once; tests are dispatched one at a time via a shared `queue.Queue`. Per-test timeouts kill the worker's Dymola via `psutil`; workers auto-restart up to 3 times. Noise from Dymola's internal urllib retries (WinError 10061/10054) is muted during kill windows via monkey-patching `DymolaLogger._PrintMessage`. `--batch` reverts to the legacy batched `.mos` runner.
- **Why**: The batched `.mos` runner has three limitations: no per-test live progress inside a batch, poor load balancing (long tests stall workers), and batch-level crash/timeout blast radius. Persistent workers fix all three — library-load cost paid once per worker lifetime, queue gives natural work-stealing, timeouts kill just the bad test's worker while others keep running. Dymola ships the Python interface with every install (as `.whl` or `.egg`), so there's no extra install burden — the loader auto-discovers and extracts it.
- **PID attribution**: pulled directly from `DymolaInterface._dymola_process.pid` (the internal `subprocess.Popen` handle).
- **Parallel startup**: Dymola's own `dymola_lock` (module-level, in `dymola_interface_internal.py`) is held for the entire `__init__`, including the slow `_check_dymola` ping wait — serializing all worker startups. We monkey-patch `dymola_lock` to a no-op and add a narrow lock around `_find_available_port` (the only genuinely shared step), letting the slow per-worker waits overlap.
- **Run summary**: persistent runs print `(Xs wall, Ys total work, Z.Zx parallel speedup)` so the user can see whether parallelism is helping; same for the report phase.
- **Trade-offs**: Requires the Dymola Python interface archive (ships with Dymola; `check-dymola` diagnoses discovery). `--batch` remains as an escape hatch.

## D43: dsres.mat existence is insufficient — check dsfinal.txt + reached-stop-time

- **What**: A simulation is considered truly complete only when **all** of: translation didn't abort, `dsres.mat` exists, `dsfinal.txt` exists, and the mat's last time value reaches the requested `stop_time` (within 1e-6 tolerance). Failure messages are specific: `Translation failed` / `No result file produced` / `Simulation aborted (no dsfinal.txt)` / `Stopped early at T=X of Y`.
- **Why**: Dymola writes `dsres.mat` incrementally during simulation, so a killed-mid-sim worker leaves a partial file that looks valid but only covers part of the trajectory. Relying on `mat.exists()` alone (the old check) would misreport killed sims as success. `dsfinal.txt` is written at the end of a successful simulation; combining that with a time-extent check catches numerical aborts ("stopped early at T=4.7 of 10.0") too.
- **Applies to both runners**: same logic in batch and persistent runners via `read_mat_time_extents` in `mat_reader.py`. The helper bypasses the full variable-iteration code path and reads only row 0 of `data_2` (time) — cheap.
- **Lenient timeout policy**: when the watchdog fires, we still check disk before declaring TIMEOUT. If the sim genuinely completed (dsfinal.txt + reached stop_time), success wins — a test that finished 1.5s past a 60s deadline gets credit rather than being wasted. Strict-deadline behavior would require an extra flag.

## D40: Batch actions on the index page (client-side only)

- **What**: Index page has per-row checkboxes + an action panel for selecting tests and exporting them as a filter for the CLI: copy comma-list, download `selected.txt`, copy a ready-to-paste `run --filter ... --merge --report` command. Bulk selectors (+ Failed, + Sim Failed, + No Baseline, + With Warnings, + Stale) speed up the common cases.
- **Why**: Closes the loop on the incremental workflow (#35 + #38). Previously users had to hand-build a filter file or remember model IDs across the report and the CLI. Click-driven selection eliminates the bookkeeping. Stays purely client-side — no server, no API, works over `file://` — so it composes with the existing self-contained HTML reports.
- **Trade-offs**: No "rerun directly from the page" — that would require the optional server mode (#29). The smart command-string templating uses `modelica-testing` as the entry point assuming the project is installed; users on `uv run python -m modelica_testing` need to swap the prefix. Acceptable; the model_ids are the part you can't easily produce by hand.

## D39: Orphan cleanup is explicit, not automatic

- **What**: `run` and `compare` print a one-line notice when the batch manifest contains entries for models no longer in `discover_tests`, but never delete anything. `manifest cleanup --orphans` lists orphans + their on-disk dirs (work and report); `--apply` actually removes manifest entries + dirs.
- **Why**: Discovery is fragile in subtle ways — a transient `.mo` parse error, a missing dependency, a partial branch checkout, an upstream library not loaded — any of which temporarily shrinks the discovered set. Auto-pruning would silently delete real test data based on a transient discovery failure. Notice + explicit command gives visibility without that footgun. Matches existing safety stance for `manifest cleanup` of obsolete refs.
- **Trade-offs**: Disk bloat accumulates until the user runs the command. Acceptable; users notice via the notice and can clean when ready.

## D38: --rerun for status-driven test selection

- **What**: `run --rerun [CATEGORIES]` reads prior comparisons (no new sim yet), filters discovered tests to those matching the categories (`failed`, `no-baseline`, `warnings`, `sim-failed`, `passed`; comma-separated; default `failed`), then runs only those. Implies `--merge`.
- **Why**: The most common incremental workflow is "rerun the ones that failed last time". Building a `@failed.txt` filter file by hand is tedious; `--rerun` automates it using the same vocabulary as the interactive review filter (`-i`) for consistency.
- **Trade-offs**: Requires prior results to exist (errors out if not). Reuses `compare_all` so adds a comparison pass before the run; cheap relative to simulation.

## D35: Configurable batch size (queue-dispatched small batches)

- **What**: `Config.batch_size` (CLI: `--batch-size N`). When unset, behavior is unchanged: one big batch per worker (`ceil(total/parallel)` tests each). When set, tests are chunked into many small batches and **all** submitted to the `ThreadPoolExecutor`; workers pull the next batch as they free up.
- **Why**: Current static partitioning has two pain points: poor load balancing (long tests stall a worker while others idle) and large blast radius on failure (one hung test takes down its entire batch via the summed-timeout). Smaller batches fix both. `worker_id` in the dashboard is now derived from the actual thread slot (via `threading.current_thread().name`) rather than batch index, so attribution stays stable across many batches.
- **Trade-offs**: More library reloads (Dymola's 30-60s startup pays per batch). Sweet spot is ~3-10 depending on per-test runtime. `batch_size=1` defeats the purpose — same as one-test-per-process.

## D44: Phase 1 extensibility foundation (capabilities + DatasetType + MetricTree)

- **What**: Six-layer plug-in architecture (Source → Discovery → Backend → Dataset → Metric → MetricTree) documented in `docs/vision.md`, `docs/architecture.md`, `docs/extensibility.md`. Code-level primitives added without changing runtime behavior: `Capability` enum (`PERSISTENT_WORKERS`, `BATCH_FALLBACK`, `FMU_EXPORT`, `EXPERIMENT_INGEST`) and `DatasetType` enum declared on `SimulatorRunner`; `DymolaRunner` populates both. `VariableComparison` gained a `diagnostics: dict` bag for future metrics. `comparison/metric_tree.py` introduces `MetricResult`, `AndCombinator`, `OrCombinator`, `KOfNCombinator`, `WarnCombinator`, and an `implicit_and_tree()` adapter that matches current flat-AND semantics — fully unit-tested but not yet wired into the main comparison pipeline.
- **Why**: Makes the "broaden to FMU / Julia / Simulink / experiments" direction concrete before Phase 2. Declaring capabilities, populating them on the one existing backend, and shipping an unused-but-validated MetricTree abstraction means Phase 2 adds a second backend without inventing contracts on the fly.
- **Trade-offs**: Small amount of "declared but unused" code (capabilities nobody reads, MetricTree nobody invokes) until Phase 2+. Accepted — the alternative is designing abstractions under pressure when the second backend reveals requirements mid-implementation.

## D45: ModelicaTestingLib relocated under `examples/modelica/`

- **What**: Top-level `ModelicaTestingLib/` moved to `examples/modelica/ModelicaTestingLib/` via `git mv` (history preserved). Supersedes D14.
- **Why**: Forward vision adds FMU / Julia / Simulink / data-file demo sources. A single top-level directory named after one ecosystem is misleading once there are peers. `examples/<ecosystem>/` scales naturally and matches convention (FMPy, BuildingsPy).
- **Trade-offs**: Touches every path reference in tests, docs, and any external workflow hardcoding the old location. External consumers (users running against ModelicaTestingLib as a demo) must update paths.

## D46: Neutral `source_type` field on Config (forward, not yet gated)

- **What**: Added `Config.source_type: str = "modelica"` with `testing.json` plumbing. No consumer yet — the field is declared but unused.
- **Why**: When Phase 2 adds an FMU backend, the framework needs to know *what kind of source* the user is pointing at before Discovery and Backend selection can branch. Landing the field now (empty default = Modelica) means Phase 2 can wire consumers to it without a Config schema break.
- **Trade-offs**: `source_type` in `testing.json` is currently ignored. Harmless but must be documented so users don't expect it to do anything yet.

## D47: Hybrid schema for multiple named baselines (Phase 1.7)

- **What**: Reference files support multiple named baselines via a **hybrid schema**: the `primary` baseline remains stored as flat top-level fields exactly as before; additional named baselines (`experiment`, `analytical`, ...) live under an optional top-level `baselines` map. Readers use `ReferenceStore.get_baseline(model_id, name)` (the `Baseline` view) which presents both cases uniformly. Writer preserves any non-primary baselines on rewrite so acceptance of fresh primary results never clobbers them.
- **Why**: The original plan (wholesale restructure into `baselines: {name: {...}}` with primary nested) would have required (a) a one-shot migration utility for every existing ref file, (b) updating ~15 readers across comparator / reporter / CLI that access top-level fields. The hybrid schema achieves the same user-facing capability (add experiment/analytical/cross-backend baselines, provenance per baseline) with **zero existing-file changes and zero reader migration**. Flat files in the wild remain valid indefinitely.
- **Trade-offs**: Asymmetry between primary (flat) and non-primary (nested). A reader that ignores the `Baseline` view and pokes at raw dict sees only primary — it will silently miss additional baselines. Acceptable because all new code uses the `Baseline` view, and the asymmetry is documented in `architecture.md`. An accidental `"primary"` entry *inside* `baselines` is detected and ignored with a warning.

## D48: Reference-FMUs via release-ZIP fetch, not git submodule (Phase 2.1)

- **What**: `scripts/fetch_reference_fmus.py` downloads the pinned Reference-FMUs release ZIP from GitHub and extracts it into gitignored `examples/fmu/reference-fmus-binaries/`. The submodule approach was tried and abandoned — the `modelica/Reference-FMUs` repo ships C source, not prebuilt FMUs; binaries only exist on GitHub release pages.
- **Why**: Building from source would require CMake + a C compiler on every dev and CI machine. The release-ZIP path: (a) gives us the *authoritative* prebuilt FMUs, (b) requires only `urllib`, (c) produces binaries FMPy can consume directly, (d) stays out of git history (binaries gitignored, version tracked via a `.reference-fmus-version` marker), (e) idempotent — skips re-download unless `--force`. Version pinned in the script (`DEFAULT_VERSION`); bumps are one-line changes.
- **Trade-offs**: Requires one-shot network access per clone. Acceptable — same as fetching Python packages. Extracts only FMI 2.0 + 3.0 FMUs (skips FMI 1.0, which FMPy supports less well, and skips the ~13MB `fmusim-*` platform binaries, which aren't a dependency).

## D49: FmpyRunner — second backend, capability contract validated (Phase 2.3)

- **What**: `simulators/fmpy/runner.py` implements FMU simulation via the FMPy Python library. Registers under the name `"FMPy"`. Declares `capabilities = {PERSISTENT_WORKERS}` and `produced_datasets = {TIME_SERIES}`. Simulation persists the FMPy structured-array output to `<test_dir>/result.npz` (matching the Dymola pattern of on-disk result artefacts so the `compare` command re-reads without re-simulating). `read_result` adapts the structured array to the existing `VariableResult` / `TestResult` shape — zero changes to comparator, storage, or reporter.
- **Why**: This is the exercise that Phase 1 abstractions were designed for. It validates that the `SimulatorRunner` ABC, `Capability` contract, result pipeline, and storage layer genuinely work for a non-Dymola backend without framework-level changes. Confirms the extensibility claim in `docs/extensibility.md`.
- **Trade-offs**: `FmpyRunner` uses the default `run_tests` orchestration (no Dymola-specific timeout watchdog or restart logic) — fine because FMPy is in-process and predictable. Solver mapping is simplified (`"Dassl"` → `"CVode"`); not a perfect equivalence but closest match in FMPy's solver set. `fmpy` import is deferred to `__init__` to keep the module importable without the optional extra; the runner errors clearly if actually instantiated without FMPy installed.

## D50: test_spec "fmu" field, reuse source_file for the source path (Phase 2.3)

- **Update (Phase 4.D, D57)**: The "future rename" predicted below happened. `TestModel.mo_file` is now `TestModel.source_file`; `FmpyRunner` reads `test.source_file`. The user-facing `"fmu"` field in `test_spec.json` is unchanged.
- **What**: `test_spec.json` entries accept an optional `"fmu"` field — a path (relative to the spec file) to an FMU binary. `spec_parser.py` resolves it to absolute and stores in `TestModel.source_file`. `FmpyRunner` reads the source FMU from `test.source_file`.
- **Why**: Adding a dedicated `fmu_path` field on `TestModel` would require touching every construction site and every caller. Since the field is semantically "the source file that defines the model" and `TestModel` already treats it as a `Path`, reusing it for `.fmu` is a zero-disruption move.
- **Trade-offs**: The original name (`mo_file`) misled readers looking at FMU tests. Mitigated by docstrings and the runner's explicit `_resolve_fmu_path` helper. Users writing `test_spec.json` only see `"fmu": "path/to/foo.fmu"` — the internal field name doesn't leak.

## D51: MetricTree wired into compare_test; user-authored trees via `metrics` (Phase 3.1–3.3)

- **What**: `comparator.compare_test()` now always produces a `MetricResult` tree and derives `TestComparison.passed` from its root. By default the tree is the flat-AND `implicit_and_tree(comparisons)` that matches previous behavior. A new `"metrics"` block in `test_spec.json` entries parses via `comparison/tree_spec.py` (`LeafSpec` / `CombinatorSpec`, path-bearing validation) and evaluates via `comparison/tree_eval.py` (`evaluate_spec` walks the spec against sim + reference data, reuses `resolve_mode` for leaves, maps spec combinator names to the existing `Combinator` classes). When a spec is present, the tree replaces the implicit AND and the legacy `comparison.variable_overrides` is ignored on that path.
- **Why**: The MetricTree abstraction landed in Phase 1 (D44) as unused code; Phase 3 was the wiring. Splitting it into 3.1 (wire implicit, no schema), 3.2 (parse spec, no evaluation), 3.3 (evaluate + replace) kept each step behavior-preserving or additive, so regressions stay isolated. Leaf params mirror the existing `variable_overrides` field names (`tolerance`, `tube_rel`, `tube_width_mode`, ...) so users transferring a per-variable override to a leaf don't learn a second vocabulary.
- **Trade-offs**: Two paths through `compare_test` (implicit / spec-driven) and two places `variable_overrides` could live (legacy on `TestModel`, per-leaf in the tree). Accepted because the implicit path stays until every test migrates to trees, and the override-vs-leaf overlap is transient — when `metrics` is set, the legacy overrides are ignored, not merged. Documented in `extensibility.md`. The `TestComparison.variables` list is still a flat list (not a tree-shaped structure) — reporter consumes it directly; the tree view is a separate context field. A fully tree-native reporter is a Phase 4+ concern.

## D52: Tree rendered only when user-authored (Phase 3.4)

- **What**: The per-test HTML report's "Metric Tree" section renders only when `test.metric_tree_spec is not None` at the time of `_build_template_context`. Implicit flat-AND trees (no user spec) are suppressed.
- **Why**: For the implicit case, the existing per-variable table already conveys everything the tree would show (a flat list of leaves, all ANDed). Rendering a "tree" with a single AND node over a list of leaves is noise. For user-authored trees — even trivially flat ones — showing the section confirms to the user that *their spec took effect*, which is worth the screen space. Gating on the spec object's presence (not on tree shape) keeps the rule simple and avoids having to classify trees by "interesting-ness".
- **Trade-offs**: A user who authors a flat AND that exactly matches the implicit tree gets a redundant-looking section. Accepted because the signal — "your tree is live" — is more valuable than the redundancy.

## D53: `range` metric — signal-only leaf type (Phase 3.5)

- **What**: Added `RangeMode` / `RangeConfig` (`comparison/modes.py`) + `_compare_range` (`comparison/comparator.py`). Leaf params: `min` and/or `max` (at least one required). Checks every point of the actual signal against the bounds; reference data is not consumed. Registered in `resolve_mode`, `VALID_METRICS`, and `_METRIC_TO_MODE_KEY`.
- **Why**: Phase 3.5 needed a second leaf shape to prove the leaf contract isn't NRMSE-shaped. `range` is the smallest honest candidate — it genuinely validates a pattern no other leaf does: *"this signal should always stay in bounds, independent of any baseline"* (safety-limit tests, sanity checks on derived variables, operating-envelope gates). Picking this over `final-only` (already existed) or event-counting (requires new infrastructure) gave the most contract-stretching per line of code.
- **Trade-offs**: `RangeMode` reuses `VariableComparison` fields awkwardly — `nrmse` carries `max_violation`, `tube_points_inside` carries the fraction-in-bounds. A principled fix would rename/generalize those fields, but doing so touches reporters, the interactive UI, and stored baselines; accepted the overload to land the leaf type cheaply. The `_compare_range` helper ignores `act_time` for scoring (bounds are time-independent) — a future time-varying bounds variant would extend the config and helper, not add a new leaf.

## D54: Diagnostic variables stored as scalar summary, not full trajectory (Cleanup)

- **What**: `ReferenceStore.store_reference` now writes diagnostic variables as `{name, final, min, max}` instead of `{name, values: [...]}`. Reporter renders a "Diagnostics" summary table (current final vs. reference final) in place of the old overlay plot. Existing baselines with the full-trajectory shape still read — the reporter tolerates both and plots the legacy trajectory when present. On re-accept, baselines migrate to the summary shape.
- **Why**: Diagnostic variables are nondeterministic by nature — CPUtime changes every run. Storing the full trajectory guaranteed a spurious git diff on every `--accept`, bloated baselines (CPUtime = one float per output step × N steps), and the trajectory was never a regression signal anyway (the scalar summary is). Users who genuinely want the trajectory can promote the variable to a tracked variable by adding its name to `variables` — it'll be compared like any other.
- **Trade-offs**: Baselines accepted before this change have the old shape; they keep working via the read-compat path. The "Diagnostic Plots" section in the report only renders for legacy baselines — new baselines show the summary table only. Acceptable because the plot wasn't informative (comparing one nondeterministic trajectory against another).

## D55: Backend-aware artifact list via `artifact_files` class attribute (Cleanup)

- **What**: `SimulatorRunner.artifact_files: tuple[tuple[str, str], ...]` — each backend declares its per-test files as `(filename, label)` pairs. `DymolaRunner` lists the Dymola-specific files (`dslog.txt`, `dsin.txt`, `dsfinal.txt`, `simulate.mos`, `dsres.mat`, `translation_log.txt`); `FmpyRunner` lists `result.npz`. Reporter's `generate_report_suite` resolves the list once via `get_runner_class(config).artifact_files` (class attribute — no instantiation, no fmpy import trigger) and threads it through per-test args.
- **Why**: Before cleanup, the reporter hardcoded a Dymola-specific file list. FMU reports showed an empty "Simulation Artifacts" section (no dsres.mat exists); the list couldn't express `result.npz`. Declaring on the runner class puts artifact naming where the backend knowledge lives and makes the reporter backend-agnostic.
- **Trade-offs**: A new helper `get_runner_class(config)` coexists with `get_runner(config)`. Two factories is mild smell but avoids instantiation for read-only needs (FmpyRunner's `__init__` imports fmpy, which errors without the extra). Accepted.

## D56: Multi-baseline MetricTree leaves via `against` (Phase 4.A)

- **What**: MetricTree leaves now select a named baseline via `"against": "<name>"` (default `"primary"`). Evaluator takes `baselines: dict[str, BaselineView]` (new dataclass in `tree_eval.py`) instead of a single `ref_vars_by_name` + `shared_ref_time`. `compare_test` loads every baseline from the reference file via `_extract_baselines(reference)` — primary from the flat top-level fields, additional ones from the `baselines` map (hybrid schema, D47). `ReferenceStore.add_named_baseline(model_id, name, ...)` adds non-primary baselines programmatically (primary stays owned by `store_reference`). Unknown `against` names hard-fail the leaf with a clear label. Per-test HTML report shows `against=<name>` on non-primary leaves. BouncingBall demo (`examples/fmu/test_spec.json`) exercises a `warn`-wrapped leaf scoring against a synthetic sparse `experiment` baseline.
- **Why**: The hybrid schema (D47) stored multiple named baselines since Phase 1.7, but no evaluator consumed them. Phase 4.A makes the vision's *"validation against experiment"* and *"cross-simulator comparison"* patterns composable features on top of MetricTree rather than hardcoded modes. Decomposition (A.1 signature refactor → A.2 spec field → A.3 caller → A.4 write helper → A.5 report → A.6 demo) kept each sub-phase behavior-preserving or additive, same pattern as Phase 3.
- **Trade-offs**: Two factories (`get_runner` / `get_runner_class`) and now two baseline-write paths (`store_reference` for primary, `add_named_baseline` for others) — mild asymmetry but reflects the hybrid schema's actual structure. `add_named_baseline` writes JSON directly (no downsampling, no metadata-merge logic) because non-primary baselines are user-authored and don't need the framework's acceptance machinery. No CLI command yet for importing a baseline from CSV / another simulator — programmatic helper is the first step; CLI comes when there's a concrete consumer ask.

## D57: Modelica-neutral rename sweep (Phase 4.D)

- **What**: Three Modelica-flavored names on framework-shared types renamed to neutral equivalents — hard break, no aliases. `TestModel.mo_file` → `TestModel.source_file`; `TestModel.package_path` → `TestModel.source_package`; `Config.package_path` → `Config.source_path` (CLI flag `--package-path` → `--source-path`; `testing.json` key `"package_path"` → `"source_path"`). Both in-tree `testing.json` files updated; external consumers (TRANSFORM, ModelicaTestingLib) update theirs out-of-band. Sub-phases ran each-tests-green-independently: 4.D.1 `mo_file`, 4.D.2 `package_path` on TestModel, 4.D.3 `package_path` on Config + CLI + JSON, 4.D.4 audit + neutralize `SimulatorRunner` / `simulators/__init__` docstrings that still claimed "Modelica simulations". 309 → 309 throughout.
- **Why**: FMPy is a working backend (Phase 2). Field names like `mo_file` and `package_path` on framework-shared types misrepresent what they hold for FMU tests (`TestModel.mo_file` was already storing `.fmu` paths via D50). The asymmetry would compound as more backends land. CLAUDE.md flagged the rename as pending and D50 explicitly forecast the `mo_file` → `source_file` rename. Doing it before 4.B (cross-backend) means the upcoming work doesn't ship with stale names.
- **What stays Modelica-flavored**: Modelica-specific code paths keep their names — `MoParseResult.mo_file` (the parser is Modelica-only), `find_package_dir` / `read_package_name` (Modelica-package-mo discovery), the `Config.library_dir` accessor (semantically the Modelica package dir; identical to `source_path` for `source_type == "modelica"`), and all of `dymola/` and `mo_parser.py`. High-level branding (CLI description, HTML report title, package name `modelica_testing`) defers to a future tool rename.
- **Trade-offs**: External `testing.json` consumers (TRANSFORM in particular) need a one-line key rename. Acceptable per the user's no-backward-compat stance — clean breaks in this phase, with the only two known users tracked in the repo or by the user directly.

## D58: FMPy backend timeout + phase labels (Phase 4.D cleanup)

- **What**: `FmpyRunner.run_single_test` now (a) honors `test.timeout` / `config.timeout` by running `fmpy.simulate_fmu` inside a single-worker `concurrent.futures.ThreadPoolExecutor` and treating `future.result(timeout=...)` as the deadline, and (b) emits backend-appropriate `on_phase` events (`"loading"` for FMU description read + variable resolution, `"simulating"` for the actual integration). Bonus: fixed a latent kwarg mismatch in the failure path — `progress.on_finish(error=msg)` was wrong (`detail=` is the parameter); call sites only worked because `self.progress` was None in tests.
- **Why**: Closed three asymmetries flagged in `SESSION_HANDOFF.md`. (1) Per-test timeouts were Dymola-only; the docs warned FMPy "runs to completion regardless." Now both backends honor it. (2) Dashboard phase labels were Dymola-flavored — FMPy tests showed no phase at all. The phase mechanism was already generic (`on_phase(test_key, str)` accepts any label); FMPy just wasn't emitting. Now each backend declares its own phase vocabulary. (3) The bug fix was incidental but real — would have surfaced as `TypeError` the first time a real FMPy progress reporter saw a simulation failure.
- **Trade-offs**: The timeout enforcement can't force-kill the C-level FMU computation — when timeout fires, the worker thread is left running until the FMU returns naturally; we just shut down the executor with `wait=False`. For Reference-FMUs (sub-second simulations) this is invisible. A genuinely runaway FMU would leak a thread per timeout — acceptable until someone has one. Subprocess-based isolation would fix it but adds startup cost on every test, defeating the in-process-FMPy advantage. No FMPy timeout test is added — would require either monkeypatching `simulate_fmu` to sleep or a long-running fixture FMU; existing happy-path tests cover that the new plumbing doesn't break normal operation.

## D59: Pluggable in-source test annotations (Phase 5 / PTA)

- **What**: The Modelica `.mo` scan was generalized into a `Recognizer` registry. The previously hardcoded `UnitTests` + `experiment(...)` pattern is now `BundledModelicaUnitTestsRecognizer` (`discovery/mo_parser.py`), one of N registered. Users declare custom recognizers as JSON in `testing.json` (`"recognizers"` list) — `JsonRecognizer` (`discovery/json_recognizer.py`) handles both `component-instantiation` (parameter `component_name` accepting full or tail-suffix qualifications) and `extends` (parameter `class_pattern` as fnmatch glob), with field sources `parameter` (from matched component), `constant` (literal value), and `experiment-annotation` (from standard `experiment(...)` block). Discovery merges results by `model_id`: bundled registers first, user-provided recognizers append, last-writer-wins per field — additive default + explicit `disable_bundled` (list of recognizer names) for the rare opt-out case. `TestModel` gained richer-contract fields: `simulate_only` (wired end-to-end in the comparator: test passes iff sim succeeds; no per-variable comparison; emits a single-leaf `MetricResult` labelled `simulate-only`), plus `requested_fmu_export` and `requested_baselines` as 4.B placeholders. Demo: `examples/modelica/ModelicaTestingLib` gained `Icons/Example.mo` + `Examples/SimulateOnlyTest.mo` exercised by a recognizer entry in the bundled `testing.json`. Decomposition: PTA.1 registry refactor (behavior-preserving) → PTA.2 JSON schema + parser → PTA.3 wire into Config + discovery merge → PTA.4 richer-contract TestModel fields → PTA.5 `simulate_only` end-to-end → PTA.6 demo → PTA.7 docs. Test count: 309 → 358 (+49 new).
- **Why**: The hardcoded UnitTests pattern was the single biggest adoption blocker — a library with its own test convention had to either rewrite every model or fork the framework. PTA addresses this without forcing Python on users (declarative JSON map). Doing PTA before 4.B (cross-backend) means the upcoming work doesn't have to retrofit a second authoring path for runtime requests (`requested_fmu_export` lives in the model where the test logic lives, via a recognizer field). The decomposition is the same shape as Phase 3 / 4.A — each sub-phase compiles and tests green independently, so regressions stay isolated.
- **Match-type vocabulary is per-source-type**: Modelica has `component-instantiation`, `extends`, and (deferred) `class-name-glob` / `annotation`. FMU would declare its own when an FMU recognizer lands. Forcing a unified abstract vocabulary across languages would obscure rather than clarify; each source type owns its parser and its match types. The framework's frame (`name`, `applies_to`, `match`, `fields`) is universal.
- **Additive-default rationale**: Bundled returns None on files it doesn't recognize, so adding a custom recognizer doesn't introduce false positives. Per-field merge with last-writer-wins lets users override individual fields without disabling. Replacement-as-default would silently delete bundled recognition the moment a user added any custom recognizer — a footgun. Disable mechanism handles the rare "ship-bundled-as-dep but don't discover its examples" case.
- **What stays out**: Match composition (`all-of` / `any-of`), folder filter (`paths_include` / `paths_exclude`), additional Modelica match types (`class-name-glob`, `annotation`), additional field sources (`annotation`). All four are concrete future hooks captured inline in `discovery/json_recognizer.py` and SESSION_HANDOFF.md; defer until a concrete need pulls them.
- **Trade-offs**: The recognizer registry is module-level (auto-registers bundled on import); user recognizers are config-scoped (live on `Config.recognizers`) to prevent cross-test leakage. Two scopes is mild asymmetry but reflects the actual lifecycle (bundled is ambient; user is per-config-load). The `JsonRecognizer.recognize()` reads each `.mo` file once per recognizer invocation — slight perf regression on libraries with thousands of `.mo` files where most don't match (vs. the pre-PTA "substring-check first, parse second" pattern), but the regex used by both bundled and JSON recognizers is fast enough that this hasn't surfaced. If it does, the recognizer interface can grow a `quick_check(content) -> bool` short-circuit.

## D60: PTA follow-ups — folder filter, match composition, more match types + field sources

- **What**: Three deferred PTA features landed in one bundled phase.
  - **PTA-follow.1** (folder filter): JSON recognizer accepts `paths_include` / `paths_exclude` (fnmatch globs against the path relative to `config.library_dir`). New `Recognizer.applies_to_path(source_file, base) -> bool` ABC method (default returns True); discovery checks before calling `recognize()`. Lets a recognizer say "only Examples/**" or "skip Internal/**" without per-file content cost.
  - **PTA-follow.2** (match composition): `all-of` and `any-of` match types — recursive composition over child match specs. New `CompositeMatch` carrier; `_find_match(ctx, type)` helper traverses composites for field-source interpreters (e.g., `parameter` source still works inside `all-of` if any child supplied a `ComponentMatch`). Validation builds a union of allowed field sources across all leaf match types via `_collect_leaf_match_types` + `_allowed_sources_for`.
  - **PTA-follow.3** (`class-name-glob` + `annotation`): new match type `class-name-glob` (matches a class whose qualified name `within + name` matches a glob — useful for "find every class under MyLib.Tests.Power" without a per-file annotation). New field source `annotation` (extract a value from any Modelica annotation block, not just `experiment(...)`).
- **Why**: Each was inline-deferred during PTA proper; bundled now because the hooks were natural and adding them later would mean retrofitting the schema. The per-recognizer path filter especially is the single most likely future need (mentioned in user feedback about "extends X AND in folder Y" patterns); landing it now means the next concrete user-driven need can opt in without another schema break.
- **Trade-offs**: `class-name-glob` and `annotation` add Modelica-specific match-type vocabulary that doesn't translate to FMU / Julia recognizers — kept in `json_recognizer.py` for now; if a non-Modelica recognizer needs sibling concepts, refactor into per-source-type plug-in tables. `all-of` / `any-of` use a `CompositeMatch` carrier whose interpretation depends on child types — slight conceptual overhead but isolates from leaf match types. Did *not* add `not-of` (single-child negation) — easy to add if asked, but no concrete need today and the validation-by-leaf-types model needs special-casing for negation.

## D61: Weighted combinator (4.E)

- **What**: `WeightedCombinator` joins the existing `and` / `or` / `k-of-n` / `warn` family. Spec form: `{"combinator": "weighted", "threshold": <float>, "direction": "less" | "greater" (default "less"), "weights": [...], "children": [...]}`. Pass condition is direction-aware: `sum(w_i * score_i) < threshold` for `less` (NRMSE-like, lower is better); `> threshold` for `greater` (tube-like, higher is better). All children must produce a numeric score; if any has `score=None`, the weighted node fails with a clear diagnostic.
- **Why**: Vision-listed; small additive scope (one combinator class + parser changes). Lets users author overall-quality metrics like *"pass if 0.7 · NRMSE(h) + 0.3 · NRMSE(v) < 0.01"* without authoring a custom Python combinator. The `direction` field generalizes cleanly across NRMSE-shaped and tube-shaped metrics — same combinator works for either.
- **Trade-offs**: Per-child weights live on the parent's `CombinatorSpec.weights` (parallel list to `children`) rather than on each child. Considered putting `weight` on each child node, but that would have leaked weighted-specific concepts into every other combinator's child list; per-parent storage keeps non-weighted contexts clean. Children must all produce numeric scores — score-less children (e.g., a `warn`-wrapped leaf, which intentionally returns `score=None`) fail the weighted node. Documented; user must structure their tree accordingly.

## D62: Event-timing + dominant-frequency leaf metrics (4.C)

- **What**: Two new `ComparisonMode` strategies join `nrmse` / `tube` / `final-only` / `range`.
  - `event-timing`: detects events as duplicate-time-point markers (existing Modelica convention via `_find_event_boundaries`); compares pairwise event instants against a `time_tolerance`; passes when (`count_must_match` and counts match) and (max event Δt ≤ tolerance). Useful for state-machine / mode-switch regressions where the *moment* of an event matters more than the trajectory between events.
  - `dominant-frequency`: resamples to uniform grid → FFT → finds peak above `min_frequency` Hz → compares actual vs. reference dominant frequency by relative error against `rel_tolerance`. Useful for oscillator regressions where frequency is the regression signal of interest.
- **Why**: 4.C's purpose was leaf-type breadth; these two probe different shapes — event-timing is signal-pair-comparison-without-trajectories (only event times matter), dominant-frequency adds spectral analysis to the leaf vocabulary. Both registered via the same `resolve_mode` factory + `VALID_METRICS` + `_METRIC_TO_MODE_KEY` plumbing as `range`; no framework changes beyond the added entries.
- **Trade-offs**: Both repurpose `VariableComparison.nrmse` field for their score (max event Δt; relative frequency error) — same overload pattern as `range` (D53). Acceptable because the `score_display` and `criterion` formatters in `plot_comparison.py` translate the field into a mode-appropriate string; reporters work uniformly. Dominant-frequency does its own per-call FFT (no caching) — fine for typical signal lengths; if it surfaces as slow on long traces, add a cache key (test_id, variable, mode params).

## D63: Cross-backend verification chain (4.B)

- **What**: `Capability.FMU_EXPORT` → real abstract method `SimulatorRunner.export_fmu(test, output_dir) -> Path`. `DymolaWorker.export_fmu` implements via `translateModelFMU` over the live Dymola Python interface; `PersistentDymolaRunner.export_fmu` spins up a one-shot worker for the export. New helper `simulators/cross_backend.py::produce_dymola_via_fmpy_baseline` chains: primary backend exports FMU → FmpyRunner simulates the FMU → result stored as a non-primary baseline named `"dymola-via-fmpy"` via `ReferenceStore.add_named_baseline`. CLI's `cmd_run` invokes `_run_cross_backend_chains` after primary `runner.run_tests` for every test whose `requested_baselines` (a PTA.4 field) lists `"dymola-via-fmpy"`. Users author the trigger via a recognizer (`{"from": "constant", "value": ["dymola-via-fmpy"]}` for the `requested_baselines` field), then add a MetricTree leaf with `"against": "dymola-via-fmpy"` to score against the chain output.
- **Why**: This is the long-promised forward bet from `vision.md` — cross-backend verification as a *composable* feature on top of capabilities, not a hardcoded mode. PTA.4's runtime fields (`requested_fmu_export`, `requested_baselines`) anticipated this exactly, so the wiring is small. The chain is best-effort enrichment: failures log + skip, the primary comparison still runs.
- **VALIDATION CAVEAT (important)**: the Dymola `translateModelFMU` step requires Windows + Dymola + the FMI export option in the Dymola license. **Cannot be exercised on Linux WSL** (this dev env). All tests in `test_dymola_export_fmu.py` and `test_cross_backend.py` use a mocked `DymolaInterface` for the export step (the FMPy half is real, runs against a Reference-FMU). Real end-to-end validation must happen on the user's Windows machine — running `uv run modelica-testing --config <ModelicaTestingLib testing.json> run --report` after authoring a test with `requested_baselines=["dymola-via-fmpy"]` should produce a `dymola-via-fmpy` baseline on the ref file and render in the report.
- **Trade-offs**: One-shot worker per export call (rather than reusing a pool worker) — slow if many tests need chains, but keeps the export path independent of pool lifecycle. No CLI for "export FMU only" — the chain is the only consumer. Future optimization: chain-aware worker pool that reuses an idle worker for export. Demo model not added to ModelicaTestingLib because there's no way to validate it on the dev env; PTA.6's `SimulateOnlyTest` already proves the recognizer→TestModel path; users can author a chain demo in their own `testing.json` once Dymola is available.

## D64: Interactive HTML genericized for non-NRMSE leaves

- **What**: `interactive.html`'s "NRMSE" column became "Score" and renders the mode-aware `score_display` produced by `plot_comparison._build_template_context` (same field the static report uses since D54). For variables in non-NRMSE modes (`tube`, `range`, `event-timing`, `dominant-frequency`), the per-variable tolerance input is replaced with `n/a (mode=<name>)` and a tooltip — the slider doesn't meaningfully apply to those modes. The global tolerance label became "Test Tolerance (NRMSE only)" with a hint clarifying scope. JS `computePass` now special-cases all non-NRMSE modes to use the original `v.passed` value rather than recomputing from a NRMSE threshold.
- **Why**: The slider was a bald NRMSE assumption — for tube/range/event-timing/dominant-frequency leaves, recomputing pass/fail from `v.nrmse < tolerance` would have produced misleading status changes when users dragged the slider. The asymmetry is the kind of "looks finished but quietly broken" footgun worth fixing before the reporter becomes a pitch point. Surgical fix preserves NRMSE workflow; non-NRMSE rows just become read-only for the tolerance dimension.
- **Trade-offs**: Non-NRMSE rows can't be re-tuned interactively from the report — users must edit `test_spec.json` and rerun. Acceptable: tube and range thresholds aren't naturally slider-shaped (tube has rel/abs/min-width; range has min/max bounds). A future "expand details" panel per leaf with mode-specific controls would be a reporter rewrite scope, not a surgical fix.

## D65: FMU-pathway semantic gap + cross-backend chain labeled experimental

- **What**: Two labeling changes + one tool, no runtime-behavior change beyond a one-time warning.
  - `simulators/cross_backend.py` (`produce_dymola_via_fmpy_baseline`) marked **experimental** in the module docstring. `cli._run_cross_backend_chains` emits a one-time `logger.warning` when any chain actually fires — "experimental; semantics defined only for autonomous FMU-exportable tests; end-to-end validation pending Windows+Dymola pass."
  - `simulators/fmpy/runner.py` module docstring gained a **"Limitations"** block documenting the shared scope: today the FMPy path calls `fmpy.simulate_fmu` with only `filename` / `stop_time` / `solver` / `relative_tolerance` / `output` / `output_interval` — no `input=` schedule, no `fmi_type=` override (FMPy auto-picks CS if present else ME), no `start_values=`. Autonomous FMUs only (inputs baked in). Reference FMUs (BouncingBall / Dahlquist / VanDerPol) are the validated shape. This does **not** reverse Phase 2's "complete" status — it honestly scopes what "complete" covers.
  - New `scripts/smoke_test_dymola_export.py` — 30-line standalone script the user runs on Windows to validate `translateModelFMU`'s signature + FMI license + cwd-on-Windows in one shot, independently of the test suite. Not wired into CI (no value on Linux). Prints pass/fail.
- **Why**: The grill on D63's deferred validation surfaced a bigger concern than "does export_fmu call the right API": the cross-backend chain assumes "Dymola-exports-FMU + FMPy-default-simulates = meaningful cross-check", which is only true for **autonomous** tests — models with no external inputs, no python driver script stepping the FMU, no need to pick between co-simulation and model-exchange. For a test that is fundamentally "a python script driving an FMU with a scheduled input sequence", the chain would silently produce a baseline whose values don't reflect what the test actually does, and `"against": "dymola-via-fmpy"` would compare apples to oranges. Same semantic gap exists on the primary FMPy path but is less acute because FMPy-as-primary is explicitly opt-in per test (via `source_type: "fmu"` on a config or per-test `"fmu"` field) and was validated end-to-end for autonomous Reference FMUs. Labeling honestly is cheaper than generalizing now — generalization (input drivers, CS/ME choice, start-value overrides, python-driver tests) deserves a dedicated phase driven by real use cases.
- **Defer-window rationale**: Horizon to feature-complete is ~2-4 months with an external milestone landing this week (idea-level, no live demo) and a public demo later. No planned work hooks `export_fmu`. Blast radius is contained: `export_fmu` has one caller (`produce_dymola_via_fmpy_baseline`); the chain has one caller (`cli._run_cross_backend_chains`); both gated on opt-in `requested_baselines`; zero tests currently set it. `translateModelFMU` API has been stable in Dymola for ~10 years. Code is well-defended (empty-return path, glob fallback, exception wrapping). Rollback cost if validation surfaces a bug at month 3: signature-level issue = tens of minutes; cwd/path quirk = half a day.
- **Smoke-test outcome (2026-04-17)**: `scripts/smoke_test_dymola_export.py` ran clean on **Dymola 2026x** against `Modelica.Blocks.Examples.PID_Controller`. Signature passes verbatim (all six kwargs accepted), FMI export license is present, `cd(<Windows temp>)` works with backslashes, a 1.6 MB FMU was produced. One interesting quirk: Dymola sanitizes the output basename with a disambiguation `_0` — `PID_Controller` → `PID_0Controller.fmu`. `DymolaWorker.export_fmu` is immune because it uses Dymola's returned basename verbatim (not a computed name from `model_id`) and has a glob fallback regardless. **Net effect**: the signature/license/cwd dimensions of D63's validation caveat are now locked; only the full chain (export → FMPy-simulate → baseline write) and the semantic-gap generalization remain deferred. Also updated: Dymola 2026x+ ships as a pip-installable wheel — `pip install .\dymola-2026.0-py3-none-any.whl` from the `python_interface` dir is the preferred setup. Smoke script's fallback path list extended to include 2026x.
- **Trade-offs**: "Experimental" labeling is a soft signal — nothing enforces it at runtime. A user who reads the code before the docstring could still wire a chain against a non-autonomous test and get a misleading baseline; the one-time log warning is the only runtime surface for that. Accepting that because (a) `requested_baselines` is authored only by users who opt in explicitly via a recognizer, (b) the failure mode is a misleading comparison, not a crash, and (c) the chain semantics issue is called out in the warning text. The generalization phase ("FMU-path semantic gap closure") is filed as a dedicated follow-on; it bundles: input-schedule wiring in both `FmpyRunner` and chain, CS-vs-ME selection (test-spec field), start-value overrides, optional python-driver test shape (test declares a python entry point rather than a single model ID). Until that phase, both pathways are scoped to autonomous FMUs and the chain is scoped further to also-unvalidated-on-real-Dymola.

## D66: Phase 6-9 design commitments — reporter-as-IDE, baseline-role split, recommender containment, scope-in/out

- **What**: A single decision capturing the structural design of the next phases, resolved through an 8-branch grilling session (Q1 scope boundary, Q2 modularity, Q3 round-trip faithfulness, Q4 MVP scope, Q5 draft-tree preview, Q6 multi-baseline identity, Q7 inference containment, Q8 reporter testing). Six concrete commitments:
  1. **Scope identity (Q1)**. Tool is a **regression testing framework** — single question answered: "does this time-dependent signal match the stored reference within tolerance?" Explicit anti-goals: being a simulator, parameter estimation / calibration, root-cause analysis at the physics layer, design-of-experiments, property/fuzz testing, static analysis, load/perf testing, general-purpose scientific viz, model repo/VCS, ML in the repo, full pytest replacement. Economy-of-tools principle: our artifacts (reports, JSON outputs, JSON-Schema exports, diagnostics) are handoff-ready for downstream tools; we do one thing well and do not grow into adjacent tools.
  2. **Baseline-role split (Q6)**. Replace the flat "named baselines" concept with three distinct roles: **Primary** = stored simulation result, regression anchor, created/overwritten by `--accept`, tree leaves must target it outside `warn` (validator-enforced). **Companion references** = external CSV / JSON / analytical output pointed to by file path; plot-only overlays; never scored against; graceful load-failure degradation; stored as `external` (path) or `frozen` (copied into ref storage). **Soft_checks** = another regression system's primary imported here (or a chain's output like `dymola-via-fmpy`); tree leaves can target via `"against": "<name>"` but validator enforces `warn` wrapping — soft_checks never hard-fail. Picker is view-only multi-select overlay; zero scoring effect. V&V against experimental data is handled by composing instances of the tool (run on experiment → own primary baselines → import as soft_checks into sim tests) rather than experiment-as-primary.
  3. **Reporter-as-IDE boundary (Q2–Q5)**. The interactive HTML is the **primary authoring surface** for acceptance criteria; the CLI is the execution surface. State lives in the browser until an explicit download; no local server, no live-apply. Download is an **RFC 6902 JSON-Patch**; `spec-update` applies via read-modify-write preserving unknown keys and `description` / `info` / `metadata` conventions. `ComparisonMode` stays pure compute (no UI coupling). UI controls **auto-derive from typed Config dataclasses** with custom override slots for complex modes (tube conditionals, range visual handles). Vanilla JS in browser — no framework. Live preview: JS port for nrmse/tube/range/final-only; omit for event-timing/dominant-frequency (CLI-authoritative).
  4. **Phase 6 MVP (Q4)**. Ship 6.1 (per-leaf controls) + 6.4 (full-fidelity `spec-update`) together — ~3-4 weeks. 6.2 (tree-level controls), 6.3 (multi-baseline picker), 6.5 (edit/view toggle), 6.6 (draft-tree preview) follow as separate shippable slices. 6.0 is a performance-budget guardrail (interactive.html under ~5 MB for a 50-var test; decimate trajectories, sidecar for full-resolution).
  5. **Recommender containment (Q7)**. Phase 7 is rule-based only. Input: signal + optional baseline (nothing else). Output: metric tree proposals (nothing else — no model suggestions, no parameter hints). Feature vocabulary is bounded and declared in `recommender/features.py`. Each `ComparisonMode` declares baseline compatibility (`requires_baseline` flag + shape requirements) so recommender filters candidates automatically. Complexity budget: at least one primary-targeting leaf (when baseline exists); at most three leaves total; at most one combinator layer; simpler leaves preferred. Recommender is never runtime-load-bearing. **Phase 8 (ML-backed recommender) is removed** — ML belongs in a separate tool that consumes our handoff artifacts; it is not built in this repo.
  6. **Reporter testing strategy (Q8)**. Python-exhaustive on the data contract (patch schema, `spec-update` round-trip, JSON-Schema export, validator rules). Golden-file HTML structure snapshots catch functional regressions without styling churn. Markdown QA checklist at `docs/qa/reporter_checklist.md` for manual click-through before releases. No JS unit test framework; no Playwright E2E unless reporter becomes a regression source.
- **Why**: The forward roadmap after D65 was a fork between (a) shipping additional leaf metrics on a stable compute core and (b) solidifying the reporter which had drifted behind MetricTree / multi-baseline / six-leaf-modes additions. User confirmed interactive is the primary workflow surface — especially for tube/multi-criteria authoring — so reporter debt compounds each new leaf. Picking path (b) means we commit to reporter-as-IDE deliberately rather than letting it sprawl. The baseline-role split emerged from grilling the multi-baseline picker and discovering that "secondary baseline" was conflating two distinct roles (visual-only overlay vs. warn-wrapped cross-check) — splitting them sharpens the mental model and removes a silent-failure class (tree with no primary-targeting leaves). Recommender containment was pre-emptive scope-discipline: without hard lines, "suggest a metric for this signal" drifts into "predict failures / suggest parameter changes / cluster on embeddings," all of which leak across the tool boundary. Phase 8 removal is the culmination — ML capability is real and valuable, but in a downstream tool consuming our artifacts, not in this repo.
- **Trade-offs**:
  - V&V users who wanted experiment-as-primary get "run the tool on experiment data to produce its own primary, import as soft_check" instead — an extra step but a cleaner regression identity. Documentation must walk through this pattern.
  - Tightening Config dataclass types to `Literal[...]` is a small refactor touching existing modes. Non-breaking for users (JSON values unchanged) but touches internals.
  - Live-preview asymmetry (simple modes have it, event-timing/dominant-frequency don't) is deliberate — admits numerical subtlety honestly rather than shipping a brittle JS FFT.
  - Golden-file HTML snapshots require maintenance when UI changes intentionally. Mitigation: `pytest --update-golden` workflow.
  - Validator rules (primary-required outside warn; soft_check must be warn-wrapped; companions never targeted) are schema-level enforcements — strict but enforce the mental model. Future flexibility (e.g., "I want a hard-fail against a soft_check for a specific test") requires an explicit opt-out field rather than quietly allowing misuse.
  - JSON-Schema export as a first-class artifact adds a maintenance surface (schema must stay in sync with Config dataclasses) — mitigated by deriving the schema from the dataclasses rather than hand-authoring.
  - Companion-freeze mechanism (copy external file into ref storage) is an authoring convenience, not a guarantee — freezing doesn't retroactively version-control companions already in-flight. Documentation notes this.
  - "No ML in repo" forecloses a future where the recommender becomes a data product. Accepting that because ML-backed recommender as an in-repo feature creates a distinct product shape (model training, telemetry, feedback loops) that blurs the tool's identity. Downstream tool is the right home.

## D67: Phase 6 MVP — reporter-as-IDE, full-fidelity round-trip, payload budget, baseline-role split (as-built)

- **What**: The Phase 6 MVP specified in D66 and decomposed in `docs/PHASE_6_PLAN.md` shipped across one session. Seven landing units committed in three commit clusters plus a golden-file fixup.
  1. **6.0 — payload budget**. LTTB decimation (`reporting/decimate.py`, pure numpy) applied at `Config.max_embedded_samples` samples per trajectory to the arrays embedded in `interactive.html`. `comparison_data.json` sidecar preserved at full resolution as the Tier-2 data artifact — pass/fail scoring, stored baselines, and downstream tooling are untouched. Default cap is **1000**, not PHASE_6_PLAN's suggested 2000, because each variable currently embeds four arrays (`act_time` / `act_values` / `ref_time` / `ref_values`); time-array dedup (idea #47) is a bookmarked follow-up that will let the cap rise cleanly.
  2. **Baseline-role split (D66 § 2, now real in code)**. `add_named_baseline` retired; `ReferenceStore` gained `get/add/remove_soft_check` + `get/add/freeze/remove_companion`; on-disk layout splits into `ref_NNNN.json` (primary, unchanged), `soft_checks/ref_NNNN/<name>.json`, and `companions/ref_NNNN/<name>.json` (+ sibling data file when frozen). `get_baselines` returns primary + soft_checks (companions deliberately excluded — not scorable). New `comparison/validator.py` enforces D66's four rules (tree must have ≥ 1 primary leaf outside `warn`; soft_check leaves require a `warn` ancestor; companion targeting rejected; unknown names rejected). New CLIs: `companion add/list/freeze/remove`, `soft-check list/remove`, `import-baseline`, and a one-off `migrate-baselines` (applied to the bundled BouncingBall `experiment` baseline in this session). `cross_backend.py` rewired to `add_soft_check` with docstring terminology sweep.
  3. **6.1.1 — auto-derive UI machinery + idea #46 bundled**. New `reporting/ui/mode_controls.py`: `typing.get_type_hints` + `get_origin`/`get_args` walk each `ComparisonMode` Config dataclass, producing a `Schema` of typed `FieldSpec`s. Handles float / int / bool / str / `Optional[X]` / `Literal[...]` / `Optional[Literal[...]]`; complex types degrade to a `passthrough` raw-JSON textarea. Vanilla-HTML renderer; registry keyed by mode name; six bundled modes auto-register at import. `ComparisonMode` ABC stays pure compute (D66 Q2). `TubeConfig` tightened to `Literal[...]` choices. **Idea #46 (time-windowed leaves)** bundled per the explicit checkpoint criterion: `window_start` / `window_end` live on `LeafSpec` (not on any ModeConfig), comparator slices `ref_*` and `act_*` arrays in `tree_eval._evaluate_leaf` before `mode.compare`, so modes stay untouched. Piecewise regression contracts compose through the existing combinator grammar.
  4. **6.1.2 / 6.1.3 / 6.1.5 — JS scorers + cell-replacement wiring**. Variable-table `n/a (mode=...)` cells swapped for the 6.1.1 rendered panels (Python side: `_extract_mode_values` + `_render_mode_controls` in `plot_comparison.py`). Vanilla-JS `const MODE_SCORERS = {nrmse, tube, range, final_only}` registry; `computePass` refactored to use the registry and fall back to `v.passed` for modes without a scorer (`event-timing`, `dominant-frequency`, which get a "CLI-authoritative" badge in their cell per D66 Q3's honest-no-preview stance). New `wireModeControls` / `onModeFieldChange` hook panel `[data-field]` inputs to `perVarModeConfigs[idx]` + `updateVarStatus`. `_compare_range` stashes `min_value` / `max_value` in diagnostics so range panels pre-fill bounds.
  5. **6.1.4 — custom overrides**. Tube: `_tube_cell_renderer` registered as tube's `custom_renderer` — cell emits `"→ See tube editor below plot"` instead of a redundant duplicate panel; rich editor unchanged. Range: new JS `applyRangeOverlay(idx)` draws two dashed-red horizontal reference lines at `min_value` / `max_value` via `Plotly.relayout`, synced to panel inputs. v1 is input → plot only; drag-to-edit (plot → input) deferred.
  6. **6.4.1 – 6.4.3 — RFC 6902 `spec-update` round-trip**. New `discovery/patch_apply.py` — pure stdlib RFC 6902 applier, RFC 6901 JSON-Pointer escapes (`~1` → `/`, `~0` → `~`), whitelist defaults to `/comparison` + `/metrics`, supports `replace` / `add` / `remove`. `cmd_spec_update` auto-detects patch vs. legacy dict; new path stages (a) dry-run apply → (b) re-parse and validate any mutated metric tree via `validator.role_lookup_from_store` → (c) commit. Legacy flat-dict format preserved for one transition cycle. Hand-authored `description` / `info` / `metadata` and any unknown keys survive byte-compat. Reporter JS: new `buildPatchData()` diffs `perVarTolerances` / `perVarModeConfigs` against originals; emits `{"model", "patch": [ops]}`; `jsonPointerEscape` handles `/` / `~` in variable names; download filename `spec_patch.json`.
  7. **6.4.4 – 6.4.5 — validator wiring + JSON-Schema export**. Validator wired inside `cmd_spec_update`'s dry-run-then-commit flow (partial writes impossible). New `reporting/schema_export.py` + `cmd_export_schema` — derives JSON-Schema draft 2020-12 from Config dataclasses + MetricTree grammar via the 6.1.1 introspection; `$defs` for each mode + leaf + combinator + tree_node; emits to stdout or `--output`. Serves the D66 economy-of-tools principle (handoff artifact for IDE autocomplete, LLM-authored specs, alternative tools).
  8. **6.4.6 + QA** — 27 new tests across `test_patch_apply.py` / `test_spec_update_cli.py` / `test_export_schema.py` covering unknown-key preservation at every nesting level, whitelist enforcement, JSON-Pointer escape rules, and schema shape. New `docs/qa/reporter_checklist.md` — D66 Q8 manual click-through checklist for pre-release verification.

  Test count trajectory: **404 (session start) → 459 (6.0 + baseline-role split) → 496 (6.1.1 + #46) → 504 (6.1.2/3/5 + 6.1.4) → 531 (6.4)**.

- **Why**: The D66 grilling committed to six concrete structural shapes. Shipping them one per step as ad-hoc additions would have let the reporter-IDE boundary drift back toward the flat-named-baselines + wipe-on-spec-update state that D66 replaced. Bundling into a single MVP session forced end-to-end consistency: the auto-derive Config introspection (6.1.1), the scorer registry and panel wiring (6.1.2/3/5), and the JSON-Schema export (6.4.5) all feed off the same dataclass shapes, so tightening `TubeConfig` to `Literal[...]` pays off three times. Similarly, the patch applier (6.4.1) and the validator wiring inside `cmd_spec_update` (6.4.4) were designed together so partial-write hazards never exist. Idea #46 (time-windowed leaves) was bundled because the 6.1.1 checkpoint criterion ("shared cross-mode subschema without leaking into each mode's Config") was exactly the shape `LeafSpec.window_*` wanted — hoisting it to the LeafSpec kept the ComparisonMode ABC pure per D66 Q2.

- **Trade-offs**:
  - Default embedded-samples cap is 1000 rather than PHASE_6_PLAN's nominal 2000. Consequence: a 50-variable × 5000-sample test clears the 5 MB budget with margin, but LTTB's visual fidelity is tighter for signals with high-frequency transients. Idea #47 (time-array dedup) unlocks the 2000 default at the same budget; tracked as 6.0.1 follow-up.
  - Drag-to-edit range handles (6.1.4 stretch) deferred to v2. Input → plot stays one-way for now; users who need fine-grained bound editing use the input fields. Cheap to revisit with Plotly's `editable: {shapePosition: true}` config.
  - Window UI surfacing on auto-derived panels deferred. `LeafSpec.window_start/end` parse from JSON and slice at evaluation time, but no browser field renders them yet — scalar field version is straightforward (two number inputs on every panel); range-brush version is the richer stretch. Candidate for inclusion alongside 6.0.1.
  - Tube's auto-derived panel is suppressed by a `custom_renderer` pointing the user to the rich editor below. The auto-derived fallback data still exists in the registry and will serve as the canonical panel if we ever remove the rich editor; right now we have two tube UXs, reconciled by cell-vs-editor separation.
  - Legacy flat-dict `spec-update` format still accepted for one transition cycle. Will be retired next session unless a consumer calls out blocking need.
  - Golden-file HTML snapshots need refreshing whenever `interactive.html` structure changes intentionally — `UPDATE_GOLDEN=1 uv run pytest tests/test_interactive_html_snapshot.py` is the workflow. Demonstrated here: the commit-B goldens were stale after commit-C's `buildPatchData` change, requiring a fixup commit (`e2dafd9`). Acceptable friction for the pass/fail safety net.
  - D66 removed Phase 8 (ML recommender in-repo). D67 preserves that stance. The forward recommender work (Phase 7) stays rule-based and sits behind the MetricTree abstraction as a pure input-to-tree function.

- **Follow-up queue (tracked in `docs/ideas.md`)**:
  - #47 time-array dedup in `interactive.html` (6.0.1)
  - #48 lazy-fetch full-res on zoom (6.0.2)
  - #49 per-test `max_embedded_samples` override (6.0.3)
  - Window UI on auto-derived panels (6.1.1 polish)
  - Drag-to-edit range handles (6.1.4 polish)
  - Phase 7 rule-based recommender (D66 § 5)
  - FMU-path semantic-gap closure (D65 deferred) + idea #45 python-driven tests user-code backend

- **Retirement**: `docs/PHASE_6_PLAN.md` retired on this pass — D67 is the canonical as-built record; git history preserves the working plan for anyone curious.


## D69 — OpenModelica batch backend (2026-04-22)

- **Decision**: Add `OpenModelicaRunner` as the third `SimulatorRunner`, using `omc` as a subprocess driven by generated `.mos` scripts. Analogous to Dymola's batch fallback — no persistent workers, no FMU export (both deferred). First Modelica-source backend other than Dymola; validates the multi-backend abstraction at long last.

- **Why**:
  - `SimulatorRunner` has existed since Phase 1, but FMPy consumes prebuilt FMUs, not `.mo` files. The multi-backend claim had never been exercised from Modelica source until this session.
  - Day-to-day dev against `ModelicaTestingLib` previously required Windows + Dymola. OpenModelica + `omc` on Linux removes that bottleneck for the primary dev machine.
  - OM's `.mat` is DSresult-compatible with Dymola's — the existing MAT reader works unchanged after a mechanical hoist.
  - `omc` is a self-contained standalone binary — zero new pip dependencies for the MVP path.

- **Scope**:
  - `Capability.BATCH_FALLBACK` only.
  - Static artifact names via `fileNamePrefix="result"`: `result_res.mat`, `result.log`, `result_info.json`, plus `simulate.mos` + `omc_stdout.txt`.
  - `Config.dependencies` entries classified: bare names ⇒ `loadModel(Name)` (uses OM's installed-packages store under `~/.openmodelica/libraries/`), path-like (POSIX-absolute or Windows-drive-rooted or ends-in-`.mo`) ⇒ `loadFile(path)`. MSL auto-injected if not present — empty-deps `testing.json` works for both backends.
  - Phase timings lifted from the REPL-echoed `record SimulationResult … end SimulationResult;` block (parsed via `log_parser.parse_omc_stdout`). An earlier sentinel+`print()` design proved silently unreliable — `res` isn't in scope for follow-up prints in omc's non-interactive mode. The record echo is free and deterministic.
  - `variableFilter` regex anchors `^(...)$` (OM's default is partial-match POSIX ERE, which would over-match unanchored); escapes per-name via `re.escape`; expands user globs via `_pattern_to_regex`; includes `unitTests.x[i]` + diagnostics explicitly. Without this, a modest test balloons to thousands of variables in the `.mat`.
  - Validated end-to-end against `examples/modelica/ModelicaTestingLib/` on Linux: 5 primary baselines under `ReferenceResults/OpenModelica/linux/`, HTML report renders with per-phase timings.

- **Unified `testing.json` via auto-detect**:
  - Added `_auto_detect_simulator` + `_looks_like_path` in `config.py`. When `testing.json` omits the top-level `"simulator"` key and no `--simulator` CLI override is set, auto-pick iterates the `simulators` map in insertion order and returns the first entry whose binary resolves (either an explicit path in the list, or — only if none of the entries look like pinned paths — the backend's canonical binary on PATH via `BACKEND_BINARY_NAMES`).
  - "Pinned path" detection uses an explicit regex for Windows drive letters (`^[A-Za-z]:[\\/]`) plus POSIX-absolute check — `PosixPath.is_absolute()` returns `False` for `C:\...` on Linux, so a naive check would fall through to `shutil.which("dymola")` and silently pick whatever's on PATH (often a WSL symlink).
  - `BACKEND_BINARY_NAMES = {"Dymola": "dymola", "OpenModelica": "omc", "FMPy": ""}` replaces the old `shutil.which(backend.lower())` fallback. Dymola happened to match by accident; OpenModelica's binary is `omc`, and FMPy doesn't have a binary at all (simulator_path stays `None`).

- **Bugs found during the sweep + fixed**:
  - `outputInterval` kwarg doesn't exist on OM's `simulate()` — converted to `numberOfIntervals` in `mos_generator`.
  - `unitTests.x[i]` weren't reaching either the variableFilter regex or the `_compute_needed_variables` set for unit_tests-sourced tests — threaded them through explicitly.
  - **`common/mat_reader.py` transpose heuristic was wrong for MATs with ≤ 4 vars**. The `if data_info.shape[0] < data_info.shape[1]: transpose` rule only fired when `n_vars > 4`. All Dymola fixtures in the suite have > 4 vars, so this latent bug had never triggered. OM's tight `variableFilter` exposed it. Fix: always transpose when `shape[0] == 4` (the DSresult format invariant).
  - `Config.__post_init__` previously did `shutil.which(backend.lower())` for PATH-fallback. Introduced `BACKEND_BINARY_NAMES`.

- **Rejected / deferred**:
  - `PERSISTENT_WORKERS` via OMPython / `OMCSessionZMQ` — follow-up.
  - `FMU_EXPORT` via `buildModelFMU` — follow-up (cross-backend chain is already experimental even on Dymola).
  - `check-openmodelica` CLI subcommand (peer of `check-dymola`) — nice-to-have.
  - Auto-install of MSL — one-time manual step documented in the runner module docstring.
  - Windows-side OpenModelica testing.
  - Separate `testing.linux.json` — rejected in favor of auto-detect on a single unified `testing.json`. The framework's `simulators` map was already plural; just lacked the auto-pick rule.

- **Files**:
  - `src/modelica_testing/simulators/common/{__init__,mat_reader}.py` (hoisted)
  - `src/modelica_testing/simulators/openmodelica/{__init__,runner,mos_generator,log_parser}.py` (new)
  - `src/modelica_testing/simulators/__init__.py` — `"OpenModelica": ".openmodelica"` in builtins
  - `src/modelica_testing/config.py` — `BACKEND_BINARY_NAMES`, `_auto_detect_simulator`, `_looks_like_path`
  - `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json` — unified config
  - `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/ref_{0001..0005}.json`
  - Tests: `tests/test_openmodelica_{mos,log_parser,runner}.py`, `tests/fixtures/results_openmodelica/`, and a new `TestAutoDetectSimulator` class in `tests/test_config.py`.

- **Commits**: `6895d43` (hoist) → `296f0c3` (mos_generator) → `5f99d91` (log_parser + fixture) → `e312e45` (runner + registry) → `d7875c3` (integration tests + MAT fixture) → `863c1e1` (sweep + unified config + baselines).

- **Spec / plan**: `docs/superpowers/specs/2026-04-22-openmodelica-backend-design.md` (commit `101d0a8`); `docs/superpowers/plans/2026-04-22-openmodelica-backend.md` (commit `8d31e37`).

- **Test count**: 637 → 679 (+42: 17 mos + 8 log_parser + 6 runner unit + 4 runner integration + 7 auto-detect).


## D70 — Persistent-worker OpenModelica via OMPython (2026-04-22)

Follow-up explicitly deferred in D69. Mirrors the Dymola persistent-runner
pattern (`PersistentDymolaRunner` + `DymolaWorker`), with OM-specific
adaptations.

- **Scope**: `PersistentOpenModelicaRunner(OpenModelicaRunner)` + a new
  `OpenModelicaWorker` class. Each worker holds one long-lived
  `OMPython.OMCSessionZMQ` instance; MSL + library + dependencies load
  once per worker rather than once per test. Shared `queue.Queue` +
  one dispatch thread per worker (same shape as Dymola). Per-test
  timeout watchdog with psutil hard-kill + disk-fallback + up-to-3
  worker restarts (directly ported from the Dymola persistent runner).

- **Out of scope**: FMU export via `buildModelFMU` (still D69-deferred —
  wiring OM into the `Capability.FMU_EXPORT` cross-backend chain needs
  its own pass); `check-openmodelica` CLI subcommand (trivial, deferred
  as a separate ticket).

- **Decisions**:
  - **OMPython is an optional extra, not a hard dep.** Added as
    `om = ["OMPython>=3.5"]`. Batch path still works without it. The
    CLI's `_get_runner(persistent=True, backend="OpenModelica")`
    probes via `load_omc_session()` (lazy import) — on ImportError
    wrapped as RuntimeError, it prints a fallback notice and keeps
    the batch runner. Same shape as the Dymola Python-interface
    fallback.
  - **Phase labels are cosmetic, not real-time.** OM's `simulate()`
    bundles build + run in one call with no mid-call progress hook
    (unlike Dymola's `translateModel` + `simulateModel` split).
    Emitting `translating` → `simulating` → `finalizing` around the
    single `sendExpression` call gives the dashboard something to
    display but the label doesn't track actual internal state.
    The **per-phase wall timings** in the final report remain accurate
    because they come from the returned `SimulationResult` record
    (`timeFrontend`, `timeBackend`, …, `timeSimulation`) — same
    parsing as the batch path.
  - **No stdout regex parsing in persistent mode.** OMPython converts
    the `SimulationResult` record to a Python dict directly;
    `_parsed_from_record(rec, notices)` bypasses `parse_omc_stdout`
    and builds the same `ParsedOmcOutput` the batch path produces.
    Shared internals (`_TIMING_KEYS`, `ParsedOmcOutput`) are imported
    from `log_parser` — tight coupling across sibling modules in the
    same backend subpackage is fine.
  - **Synthetic `omc_stdout.txt` artifact.** Batch mode writes the
    raw omc REPL output; persistent mode has no subprocess to capture
    stdout from. The runner synthesizes a diagnostic text artifact
    (`// PersistentOpenModelicaRunner worker=N` header + the simulate
    expression + a pretty-printed `record SimulationResult …` +
    `getErrorString()` output) so the report's artifact list stays at
    parity with the batch runner.
  - **`build_simulate_args` extracted from `build_simulate_mos`.**
    DRY refactor — the kwarg-list assembly (`stopTime`, `tolerance`,
    `method`, `numberOfIntervals`, `variableFilter`, …) is a pure
    `list[str]` builder used both by the `.mos` generator (batch)
    and the persistent runner's `sendExpression("simulate(…)")`.
  - **Auto-inject MSL.** Same heuristic as batch: if `Modelica` isn't
    in `config.dependencies`, the worker prepends it before
    iterating. Lets a single `testing.json` with empty `dependencies`
    work across Dymola + OM + FMPy.
  - **PID tracking via `session.getpid()`.** OMPython exposes the
    omc subprocess PID as a method on the session. Tracked for
    psutil hard-kill on timeout, same pattern as Dymola's
    `dymola._dymola_process.pid`.
  - **Graceful close = `sendExpression("quit()")` with 5 s grace;
    psutil kill as fallback.** No module-level startup lock to
    patch, no HTTP-retry noise to suppress — OMPython is quieter
    than Dymola's embedded Java process, so the Dymola
    `_install_dymola_log_filter` / `_suppress_stderr_noise` /
    `_patch_dymola_for_parallel_startup` scaffolding is intentionally
    absent here.

- **Rejected alternatives**:
  - **Splitting `buildModel` + separate-exe simulate** to get real
    translating/simulating phase transitions: would require parsing
    OMPython's `buildModel` return tuple + platform-specific exe
    invocation + its own error-recovery path. Cosmetic labels cost
    nothing and retain accurate per-phase wall timings.
  - **Hard-depending on OMPython (bumping from optional to required)**:
    would break batch-only use cases (CI without network, users on
    air-gapped workstations, OMPython-incompatible Python versions).
    Optional + auto-fallback keeps the batch path first-class.
  - **Worker pool via multiprocessing rather than threads**: same
    reasoning as Dymola — workers spend most of their time blocked
    on the remote omc subprocess, so threads suffice; multiprocessing
    adds serialization + launcher complexity for no throughput gain.

- **Validation** (2026-04-22, Linux / omc 1.26.3 / OMPython 4.0.1):
  - 20 unit tests (mocked `FakeSession`) + 1 integration test (real
    OMPython + real omc) + 4 CLI-wiring tests pass.
  - End-to-end smoke against `examples/modelica/ModelicaTestingLib/`
    (6 tests, parallel=2): **persistent = 12 s wall / 11 s total
    work; batch = 16 s wall / 24 s total work**. Persistent simulation
    phase alone: 8 s wall. The total-work ratio (11/24 ≈ 0.46×) is
    the headline number — each persistent worker amortizes its ~2 s
    library load across ~3 tests; batch pays it per test. TRANSFORM
    (326 tests, ~50–100 s compile each) is the intended beneficiary;
    not re-run this session.
  - All 5 primary baselines on ModelicaTestingLib still compare
    PASS against the committed `ref_0001…0005.json` (bit-for-bit
    backend parity with batch — same `variableFilter`, same
    `simulate()` args, same MAT writer in omc).
  - Known OMPython noise: `Result of 'getErrorString()' cannot be
    parsed!` printed by OMPython's pyparsing layer when the error
    buffer is empty. Harmless — our wrapper catches the exception
    and returns `""`. Not worth suppressing globally; if a future
    pass wants to mute it, logging-config is the right spot.

- **Files** (new):
  - `src/modelica_testing/simulators/openmodelica/persistent_runner.py`
    (OpenModelicaWorker + PersistentOpenModelicaRunner)
  - `src/modelica_testing/simulators/openmodelica/session_loader.py`
    (`load_omc_session()` + `describe_om_session()` diagnostic helper)
  - `tests/test_openmodelica_persistent.py` (25 tests: 20 unit + 4
    CLI-wiring + 1 integration + helpers)

- **Files** (modified):
  - `pyproject.toml` — `om = ["OMPython>=3.5"]` extra, `ompython`
    pytest marker.
  - `src/modelica_testing/simulators/openmodelica/runner.py` —
    `capabilities` gained `PERSISTENT_WORKERS`; module docstring
    updated.
  - `src/modelica_testing/simulators/openmodelica/__init__.py` —
    refreshed to describe both modes.
  - `src/modelica_testing/simulators/openmodelica/mos_generator.py` —
    extracted `build_simulate_args`; `build_simulate_mos` now calls it.
  - `src/modelica_testing/cli.py` — `_get_runner` swaps to
    `PersistentOpenModelicaRunner` when `persistent=True` and
    backend=="OpenModelica", with RuntimeError fallback; `--batch`
    help text mentions both backends.
  - `tests/test_openmodelica_runner.py` — capability assertion
    updated to expect both BATCH_FALLBACK + PERSISTENT_WORKERS.

- **Test count**: 679 → 706 (+27).

## D71 — Feature-showcase tests + NB overlay parity + simulate-only render fix (2026-04-23)

Grab-bag session consolidating loose ends after D70 lands. Three
independent items; no architectural change; pure fill-in.

- **Four feature-showcase tests added to `ModelicaTestingLib`**:
  `TubeToleranceTest` (exercises `mode: tube` with time-varying
  `tube_points`), `FrequencyTest` (exercises `dominant-frequency`
  leaf), `MetricTreeTest` (explicit `metrics` tree with
  `warn`-wrapped NRMSE child), `RangeCheckTest` (exercises
  `range` leaf with `min_value` / `max_value`). Each confirmed
  both numerically and by inspecting the generated interactive
  HTML — the per-variable panel renders its mode-specific UI in
  every case. Baselines committed under
  `ReferenceResults/OpenModelica/linux/ref_0006…0009.json`; Dymola
  / FMPy / Windows baselines are fresh on each platform's first
  run via the sibling-backend overlay pre-accept workflow. New
  models and spec entries live in `examples/modelica/
  ModelicaTestingLib/`; no framework code change.

- **Range leaf now accepts `min` / `max` aliases** in addition to
  the canonical `min_value` / `max_value`. `resolve_mode` in
  `comparison/modes.py` falls through — long form wins when both
  are present. Motivation: feature test author reached for the
  shorter name and the mode silently rejected the spec. Additive,
  non-breaking.

- **Sibling-backend overlay parity across baseline and
  no-baseline code paths**. Pre-fix, the overlay picker (check-
  box toggles over each sibling reference) rendered only on the
  *baseline* trajectory block of the per-test HTML. When a test
  had no local baseline yet (the canonical "fresh backend/OS"
  state), overlays were attached as legend entries on the NB
  plot but there was no picker UI — users could only toggle via
  Plotly's legend click, which was inconsistent with the baseline
  flow. Fix: `_build_template_context` now calls
  `attach_overlays_to_trajectories` on **both** the baseline and
  `nobaseline_trajectories` lists; the NB template section gained
  an `overlay-picker` block mirroring the baseline section;
  `wireOverlayPickers` generalized to handle both plot-id prefixes
  (`plot-{idx}` + `nb-plot-{idx}`) via a shared
  `setOverlayVisible(plotId, role, name, visible)` helper. Same
  styling (soft_check purple dotted, sibling-backend blue dashed,
  companion green dashdot).

- **`simulate_only` + no-baseline rendering fix** (triggered by
  user report: `SimulateOnlyTest` showed "Simulation failed" in
  the per-test HTML and `NO_REF` on the index, even though the
  dslog confirmed the simulation succeeded). Three bugs on one
  code path:
  1. `comparator.compare_all` short-circuited to
     `has_reference=False` when `store.get_reference()` returned
     `None`, *skipping* `compare_test` entirely. So the
     `simulate_only`-true short-circuit inside `compare_test` (which
     sets `metric_tree.label = "simulate-only"` and `passed=True`)
     never ran. Fix: call `compare_test` with `reference={}` for
     simulate-only tests lacking a stored baseline; set
     `has_reference=False` after so downstream renderers still know
     the baseline wasn't loaded.
  2. `plot_comparison._build_template_context` computed
     `sim_failed = (len(comparisons)==0 and n_nobaseline==0)` —
     true for any simulate-only test (no per-variable comparisons,
     no NB trajectories), which then rendered the misleading
     "Simulation failed" summary banner. Fix: skip the heuristic
     when `test.simulate_only=True`; expose `is_simulate_only` to
     the template; add a new summary branch emitting
     "Simulate-only: simulation succeeded" (green PASS pill).
  3. Index-page status classifier in `_build_per_test_args`
     checked `has_reference` before `simulate_only`. Fix: test
     for `simulate_only` first, emit PASS/FAIL based on
     `comp.passed`, before the NO_REF fall-through.
  All three fixes are in a single code path (the "no stored
  reference + simulate-only test" case); the fix could live in
  any one layer in isolation but hardens all three defensively.

- **Regression tests** in `tests/test_simulate_only.py`:
  - `test_compare_all_simulate_only_without_baseline_passes` —
    uses a `_FakeStore` returning `None` for
    `get_reference` / `get_soft_checks` / `get_companions`;
    asserts `passed=True`, `sim_success=True`, `has_reference=False`,
    `metric_tree.label == "simulate-only"`.
  - `test_compare_all_simulate_only_sim_failure_still_fails` —
    when the simulation itself fails, simulate-only follows the
    regular sim-fail path (`passed=False`), not PASS.
  `tests/test_overlay_loader.py` gained
  `test_works_on_nobaseline_trajectory_shape` to lock in the NB
  overlay attachment path.

- **Validation**: end-to-end on the OpenModelica suite (10 tests
  in `ModelicaTestingLib`, including `SimulateOnlyTest` with no
  baseline by design): **all 10 show PASS on the index**;
  `SimulateOnlyTest`'s per-test HTML renders the green
  "Simulate-only: simulation succeeded" pill.

- **Files** (modified):
  - `src/modelica_testing/comparison/comparator.py` —
    `compare_all` guard; `compare_test` passthrough for
    simulate-only when reference absent.
  - `src/modelica_testing/comparison/modes.py` — `resolve_mode`
    for range accepts `min` / `max` aliases.
  - `src/modelica_testing/reporting/plot_comparison.py` —
    `_build_template_context` simulate-only guard + overlay
    attachment on NB trajectories; `_build_per_test_args`
    simulate-only status branch.
  - `src/modelica_testing/reporting/templates/interactive.html`
    — simulate-only summary branch; NB `overlay-picker` block.
  - `src/modelica_testing/reporting/templates/interactive.js`
    — `wireOverlayPickers` generalized to both plot-id prefixes;
    `setOverlayVisible(plotId, role, name, visible)` helper.
  - `tests/test_simulate_only.py` — +2 regression tests.
  - `tests/test_overlay_loader.py` — +1 NB-shape test.

- **Files** (new): feature-showcase models in
  `examples/modelica/ModelicaTestingLib/Examples/` and matching
  `Resources/ReferenceResults/OpenModelica/linux/ref_0006…0009.json`;
  entries in `Resources/ReferenceResults/test_spec.json`.

- **Test count**: 706 → 674+2 skip (environment-dependent)
  after the `.venv` / miniforge pytest-env drift was reconciled.
  Nominal count with the `ompython` + reference_fmus markers
  enabled and dev env fully provisioned remains in the 700+
  range; the two skips are the real-OMPython integration test
  and a reference-FMUs-gated test.

## D72 — Wrap-in-combinator + combinator-kind editing in reporter (#52, 2026-04-23)

Closes the one remaining A-tier reporter-as-IDE debt. Every authoring
operation on a MetricTree is now clickable from the browser; JSON
editing becomes escape hatch only.

- **Scope**: three structural-edit ops on the `WORKING_TREE`
  (path-addressed via `_findPathContext`):
  - **`wrapWorkingNode(path, kind)`** — always produces
    `kind(target)`, a single-child parent regardless of target's
    shape. Works uniformly on leaves and combinators. Root wrap
    replaces `WORKING_TREE` with the new parent.
  - **`unwrapWorkingNode(path)`** — replaces a combinator with its
    single child. Refuses non-combinator targets or combinators
    with != 1 child (user should trim siblings first).
  - **`changeCombinatorKind(path, newKind)`** — flips the node's
    `combinator` field; seeds kind-specific fields when switching
    *into* k-of-n or weighted, strips them when switching *out*.
    **Refuses change-to-warn on multi-child targets** — the grammar
    won't validate. User should use wrap-in-warn (always legal, since
    wrap always produces a single-child parent) for that intent.
  All three call `markStructureDirty()` on success, which the existing
  wholesale `/metrics` replace patch carries to disk on save.

- **Decisions**:
  - **All five combinator kinds editable**: `and` / `or` / `warn` /
    `k-of-n` / `weighted`. Weighted + k-of-n get **sensible
    seeded defaults** when a combinator switches into them:
    k-of-n seeds `k = max(1, n-1)`; weighted seeds
    `weights = [1.0]*n`, `threshold = 1.0`, `direction = "less"`.
    Users can then tune the numbers inline without ever needing
    a seed UI modal.
  - **Single-select wrap only (v1)**. Multi-select (wrap leaves A+B+C
    in an AND) is deferred — would require marquee selection or
    Ctrl-click + group-parent inference. Two-click workflow still
    available: wrap A in AND, then add/move siblings in.
  - **Wrap always produces `kind(target)`** — no auto-insert AND
    gymnastics. My first cut had special-case logic for
    `warn`-wrapping multi-child combinators (auto-insert AND between
    warn and children) but I'd conflated two cases: `wrap-in-warn`
    on a multi-child AND just produces `warn(and(...))` which is
    already valid (warn has 1 child: the AND). Auto-insert was only
    needed for the *change-to-warn* case, which we now reject
    outright.
  - **Symmetric policy on warn**: change-to-warn rejects multi-child
    (flip the `combinator` field would break grammar). Wrap-in-warn
    always succeeds (it creates a new warn parent with a single
    child — the existing target — regardless of target's shape).
  - **UI placement**: kind dropdown in combinator header replaces the
    static `combinatorLabel()` text; inline params (`k` input;
    weighted's `threshold` / `direction` select + per-child `weights`
    row) render alongside. Wrap (`⊕`) button on every node next to
    `+` / `−`; unwrap (`⊖`) only when a combinator has exactly 1
    child (otherwise the op would fail anyway). Child-count suffix
    `[N]` still shown — it's a quick read without scrolling.
  - **Wrap popup**: `⊕` opens an inline popup (same pattern as
    remove-confirm) with a kind dropdown + Confirm / Cancel. ESC +
    click-away dismiss. No full-screen modal — feels heavier than
    the op warrants.
  - **Internal grammar names** for kind labels (`and`, `or`, `warn`,
    `k-of-n`, `weighted`) — matches the JSON spec exactly so users
    authoring trees see what they'll emit. Friendly renames ("All
    pass", "Any pass", ...) is a later UX pass.
  - **No live validation halos.** D66 validator already runs at patch
    apply time (D67); invalid states surface on save with a clear
    error + the spec stays byte-preserved until the user fixes it.
    Live halos would be reasonable but out of scope for v1.
  - **No new patch op types.** Wrap / unwrap / kind-change all flow
    through the existing wholesale `/metrics` replace envelope —
    same machinery as add-leaf and remove-leaf.

- **Rejected alternatives**:
  - **Surgical JSON-Patch at `/metrics/<pointer>/combinator`** (vs.
    wholesale replace). Would produce tighter patches (a 3-op
    patch for a kind change with seed + strip), but the spec-update
    pipeline validates the *whole tree* on apply anyway (D66
    constraint: ≥ 1 primary leaf outside warn), so "tighter patches"
    don't buy us atomicity we don't already have. Wholesale replace
    is also what structural adds/removes use — one patch envelope
    for all structural edits.
  - **Drag-into-combinator for structural moves**. Moves are
    implicit via remove + add for now; drag is a later polish pass.
  - **Multi-select wrap**. See "Decisions" above.
  - **Friendly combinator labels**. See "Decisions" above.

- **Files** (modified):
  - `src/modelica_testing/reporting/templates/interactive.js` —
    `_seedCombinatorParams` / `_stripCombinatorParams` /
    `_findPathContext` / `wrapWorkingNode` / `unwrapWorkingNode` /
    `changeCombinatorKind` mutation helpers. `renderCombinator`
    replaces the static label with `kindSelect` + `childCountLabel`
    + `combinatorParamControls` (for k-of-n / weighted). `wrapButton` +
    `unwrapButton` + `openWrapPopup` / `closeWrapPopup`. Wrap button
    also rendered by `renderLeaf`.
  - `src/modelica_testing/reporting/templates/interactive.html` —
    CSS for `.node-btn-wrap`, `.node-btn-unwrap`,
    `.node-kind-select`, `.node-child-count`,
    `.node-combinator-params` (+ k/threshold/weight input sizing),
    `.wrap-popup` (+ `-label` / `-kind` / `-yes` / `-no` variants).
    Golden structural hashes refreshed.
  - `tests/test_interactive_playwright.py` — +17 browser-driven
    tests covering every flow: kind dropdown enumeration; change-kind
    and→or / and→k-of-n (auto-seeds k) / and→weighted (auto-seeds
    weights + threshold + direction) / k-of-n→and (strips k) /
    change-to-warn refused on multi-child; wrap leaf in warn via
    popup + popup cancel + popup ESC; wrap combinator (root) in
    warn produces single-child warn; unwrap button hidden on >1-child
    combinator / shown on 1-child / replaces combinator with lone
    child / root-single-child unwrap promotes child to root; wrap
    emits wholesale `/metrics` replace; per-variable mount re-renders
    after full-tree kind edit; weighted weights edit updates only
    the targeted index; wrap button rendered on leaves.

- **Validation**: end-to-end smoke against
  `examples/modelica/ModelicaTestingLib/` on Linux — all 10 tests
  still PASS; new UI renders on every combinator in every generated
  report (kind dropdown, child count, wrap/unwrap buttons, popup
  styling). Existing 35 Playwright tests unchanged.

- **Test count**: 674 → 726 (+52, includes +17 #52 tests + existing
  Playwright count that was previously skipped without pytest-playwright
  installed into the miniforge pytest). The raw `+17 new tests` figure
  is the author-intended delta.

## D73 — Leaf-state persistence across structural re-renders + reset button (2026-04-23)

Hotfix surfaced by user immediately after D72: live-edited leaf values
(tolerance, window bounds, range min/max, ...) reset to their authored
defaults when any structural edit (+/− / wrap / unwrap / change-kind)
triggers a re-render. Two bugs on one code path; a third affordance
added while touching this area.

- **Bug 1 — path migration**. `leafState` is path-keyed (`leafState[
  '/metrics/children/0'] = {params, window, ...}`). Structural edits
  call `rebuildPaths(WORKING_TREE, '/metrics')` which rewrites every
  node's `.path`. A leaf whose path shifted (e.g., `/metrics/
  children/1` → `/metrics/children/0` after an earlier sibling is
  removed) loses its state — the old key is orphaned, the new key
  is undefined, and `renderLeaf` falls back to server-rendered HTML
  defaults. Pre-existing bug since path-keyed leafState was
  introduced; exposed by #52 because wrap/unwrap shift paths across
  nesting layers, amplifying the surface.
  - **Fix**: new `migrateLeafStatePaths()` — snapshot `[{leaf,
    oldPath}]` via `walkLeaves` *before* rebuild (captures each
    leaf's current stale path), run `rebuildPaths`, then two-pass
    migrate: first read + delete every old-path entry, then write
    every new-path entry. Two passes avoid chain-shift clobber
    (remove A from [A,B,C,D] shifts B/C/D leftward; iterating them
    in new-index order is actually safe, but the explicit
    read-then-write pattern removes the ordering coupling entirely).
    Leaf object identity (the JS reference) is preserved across
    structural edits, so a Map-by-reference isn't needed — the ref
    is the thing that tells us "this is the same leaf with a new
    path."
  - `markStructureDirty` now calls `migrateLeafStatePaths()` instead
    of `rebuildPaths` directly; migration handles the rebuild
    internally.

- **Bug 2 — DOM-vs-state drift on re-render**. Even when paths don't
  shift (change-kind is the clearest case — tree shape stays the same,
  only the combinator field changes), every re-render re-inserts the
  server-rendered `leaf.mode_controls_html` string verbatim. The
  string was generated once at report time with the *original* values
  baked into `value="..."` attributes. Live edits in `leafState`
  aren't pushed back onto the DOM — the input resets to the authored
  value, even though `leafState[path].params.tolerance` still holds
  the edit. Pre-existing; visible to users only when something
  triggers a re-render, which was rare before #52.
  - **Fix**: new `refreshLeafInputsFromState()` — post-render pass
    that walks every `.node-leaf` in the DOM, and for each
    `[data-field]` input under `.mode-controls` or `.window-controls`,
    sets the input's value from `leafState[path].params[field]` or
    `.window[key]` respectively. Handles checkboxes, passthrough
    JSON textareas, and plain numeric/string inputs via
    `_setInputValue`. Called at the tail of
    `renderAllNodeTreesFromWorking`, so every structural edit
    (and any future re-render trigger) benefits without explicit
    opt-in.

- **New affordance — `↻` reset button per leaf** (bundled per user
  ask: "we could have a reset circle arrow button so help a user
  reset though"). `resetButton(leaf)` placed in the leaf header next
  to `⊕` wrap and `−` remove. Click → `resetLeafToOriginal(path)`
  restores `leafState[path].params` / `.window` from `original_params`
  / `original_window` (captured at init time in `initLeafState` and
  at append time in `appendLeafToWorking`). **Does not flip
  `structureDirty`** — reset is a value-revert, not a structural
  edit; patch emission stays scalar-granular unless the user has
  separately touched the tree shape. CSS: `.node-btn-reset` uses a
  neutral slate-grey (distinct from the purple wrap/unwrap pair + the
  green add + red remove).

- **Decisions**:
  - **Migration by object identity + stale path snapshot, not by
    Map-by-reference**. JS object keys can't be references directly,
    but walking a tree pre-rebuild to capture `{leaf, oldPath}` gives
    us the exact same mapping in a form we can iterate. Simpler than
    introducing node UUIDs or a parallel WeakMap.
  - **Two-pass migration (read/delete, then write)**. The in-order
    migration would work for the only two shift shapes we produce
    (leftward sibling-shift on remove; deepening on wrap;
    shallowing on unwrap). Formalizing as read-then-write kills the
    ordering argument entirely and makes the helper robust to any
    future shift shape.
  - **Post-render value refresh, not re-generate HTML**. We could
    instead regenerate `mode_controls_html` from leafState on every
    re-render (as `renderModeControlsHtmlJs` already does for
    freshly-added leaves). Rejected: the server-rendered HTML
    carries labels, help text, and structure we'd have to re-emit
    JS-side. Setting `input.value` on existing DOM is cheaper and
    preserves focus/cursor position when the user happens to be
    typing during a re-render.
  - **Reset doesn't flip structureDirty.** Reset is a VALUE revert.
    Structure, if already dirty, stays dirty; if clean, stays clean.
    Rejecting the alternative ("reset clears structureDirty") —
    structural edits and value edits are independent axes of state.

- **Rejected alternatives**:
  - **Node UUIDs** instead of path-keyed leafState. Cleaner
    long-term, but requires touching every reader of leafState (~30
    call sites) plus a migration path for persisted state. Two-pass
    path migration is ~15 lines and fixes both bugs.
  - **Regenerate mode_controls_html on every render**. See above.
  - **Store leafState on the node object itself** (`leaf._state = {...}`).
    Same structural risk as leaf.path if node identity is ever lost
    (e.g., if someone adds deep-clone anywhere). Path-keyed with
    migration is simpler to audit.

- **Files** (modified):
  - `src/modelica_testing/reporting/templates/interactive.js` —
    `migrateLeafStatePaths`, `refreshLeafInputsFromState`,
    `_setInputValue`, `resetLeafToOriginal`, `resetButton`.
    `markStructureDirty` rewired through migration;
    `renderAllNodeTreesFromWorking` now calls the refresh pass.
    Leaf header gains `↻` button.
  - `src/modelica_testing/reporting/templates/interactive.html` —
    CSS for `.node-btn-reset`. Structural hashes refreshed.
  - `tests/test_interactive_playwright.py` — +10 browser-driven
    tests covering: edit survives sibling removal (no shift);
    edit survives earlier-sibling removal (leftward shift + path
    migration); edit survives wrap (deepening); edit survives
    unwrap (shallowing, tube leaf via window_start since tube
    suppresses mode-controls); edit survives change-kind (no shift
    but re-render); window edit survives structural edit; reset
    button renders per leaf; reset restores params to original;
    reset restores window to original; reset doesn't dirty structure.

- **Validation**: full regression clean; end-to-end `ModelicaTesting
  Lib` smoke — all 10 tests still PASS; new `↻` button visible on
  every leaf header in the generated report; `migrateLeafStatePaths`
  + `refreshLeafInputsFromState` + `resetLeafToOriginal` present in
  the emitted `interactive.js`.

- **Test count**: 726 → 736 (+10).

## D74 — Leaf-mode UX pass: visibility sync, labels/help, multi-peak frequency, event-timing + spectrum plots (2026-04-23)

Follow-on from a user UX review of the six leaf modes. Five independent
items in two PRs — **Phase 1** (labels/help/range/visibility sync) was
a small clarity pass, **Phase 2** (multi-peak FFT + spectrum subplot +
event-timing plot contribution) was the bigger lift.

### Phase 1 — labels, help, visibility

- **Visibility checkbox clarified + DOM-synced**. Existing behavior:
  `leafState[path].visible` toggles plot contributions for the leaf
  (tube polygon / range lines / final-time marker / window band); does
  NOT affect scoring. Two fixes:
  - **Tooltip**: "Show this leaf's plot overlay (does not affect
    scoring)." — kills the "does it disable the leaf?" confusion.
  - **Cross-mount DOM sync**: clicking the checkbox in the full-tree
    mount now flips the matching checkbox in every per-variable
    mount (and vice versa) via a new `syncSiblingVisToggles(leafPath,
    checked, sourceInput)` helper. Pre-fix, state was shared via
    `leafState` but the other mount's `input.checked` stayed stale
    until the next full re-render.
  - **Semantic choice**: kept as plot-only (user picked A). "Disable
    leaf = remove from scoring AND plot" would need serialized state
    + validator changes; deferred until there's a real use case. A
    separate "disable" button alongside the visibility checkbox is
    queued as a future affordance.

- **Field metadata on every `ModeConfig`**. Every field of every
  dataclass in `comparison/modes.py` now carries
  `field(metadata={"label": ..., "help": ...})`. Flowthrough:
  - `derive_schema` already reads `f.metadata.get("label" | "help")`
    onto each `FieldSpec`.
  - `Schema.to_dict` → `emit_mode_schemas()` → `MODE_SCHEMAS` embedded
    in the report's JS.
  - Both `render_schema_html` (server, for pre-render HTML) and
    `renderSchemaFieldJs` (JS, for newly-added leaves) emit
    `title="..."` from `f.help` on the label. Browser shows on hover.
  - Verified end-to-end: NRMSE's tolerance tooltip ("Pass iff NRMSE =
    RMSE / signal_range stays below this value…"), range's bounds
    labels ("Lower bound (optional)" / "Upper bound (optional)"),
    event-timing's "Time tolerance (s)", dominant-frequency's
    "Relative tolerance" all render as tooltips in the browser.

- **Combinator kind dropdown gets help + per-option tooltips**.
  `COMBINATOR_HELP` map seeds the `<select>` title plus per-`<option>`
  titles (best-effort — browser support for `<option title>` is
  patchy but no worse than no tooltip). Disabled `warn` option (when
  children.length != 1) carries a specific tooltip pointing users to
  the ⊕ wrap button.

- **Range left as absolute-only**. Time-varying / relative bounds
  rejected as scope creep: time-varying is already expressible via
  windowed leaves under an AND (`range[0..5]:[-1,1] AND
  range[5..10]:[-0.5,0.5]`); relative bounds is what tube is for.
  Range is the "static safety envelope" leaf, preserving the
  semantic split. Label clarity ("Lower bound (optional)" /
  "Upper bound (optional)") is all that was needed.

### Phase 2 — multi-peak frequency + spectrum subplot + event-timing plot

- **`DominantFrequencyConfig` gained `n_peaks: int = 1`**. Default
  preserves single-peak behavior for existing specs. `resolve_mode`
  reads `n_peaks` from the override dict; `DominantFrequencyMode.
  compare` forwards it through.

- **`_compare_dominant_frequency` rewritten for multi-peak**. New
  `_compute_fft_spectrum(t, v)` helper (shared with the reporter's
  diagnostics embed) + `_find_top_n_peaks(freqs, spectrum, n, floor)`
  (pure-numpy local-maxima detection; sorts by amplitude first to
  filter spectral noise, then by frequency for predictable pairing).
  Algorithm: FFT both signals, detect local maxima above
  `min_frequency`, keep top-N by amplitude, sort those N by frequency,
  pair by index, `max(rel_err_i)` must be ≤ `rel_tolerance` or the
  leaf fails. Fails-with-reason when either side has fewer than N
  peaks ("expected N peaks, detected ref=…/act=…").

- **Spectrum embedded in diagnostics**. Capped at
  `_SPECTRUM_EMBED_CAP = 512` bins so the reporter's subplot has
  source data without the payload ballooning. Keys:
  `ref_spectrum_freq`, `ref_spectrum_mag`, `act_spectrum_freq`,
  `act_spectrum_mag`, `ref_peaks_hz`, `act_peaks_hz`,
  `paired_peaks: [{ref_hz, act_hz, delta, rel_error, passed}]`.

- **Dominant-frequency plot editor — spectrum subplot on activate**.
  `MODE_PLOT_EDITORS['dominant-frequency']` activates a Plotly subplot
  inside the leaf's editor slot (both full-tree and per-variable
  mounts, independently). Shows reference (solid blue) vs actual
  (dotted red) spectra + peak markers + shaded ± relative-tolerance
  bands around each reference peak. Per user's choice: activated-
  only, not always-visible. `deactivate` purges the Plotly instance.

- **Event-timing plot contribution on the main trajectory plot**.
  `MODE_PLOT_CONTRIBUTIONS['event-timing']` emits vertical dashed
  gray lines at every reference event, vertical solid blue lines at
  every actual event, and a shaded tolerance band (±`time_tolerance`)
  around each reference event. Event detection JS-side via
  `_detectEvents(time)` using the Modelica duplicate-time-sample
  convention. CLI remains authoritative for pass/fail (pairing
  algorithm stays Python-side); the contribution is purely visual.

- **`FieldSpec.ui_min` / `ui_max` soft caps**. New metadata keys
  (`"ui_min"`, `"ui_max"`) threaded through `derive_schema` onto
  `FieldSpec`. `_render_field` (Python) and `renderSchemaFieldJs`
  (JS) both emit `min="..."` / `max="..."` attrs on number inputs.
  **Soft cap**: if the current value exceeds `ui_max`, the rendered
  `max` attr is raised to `max(ui_max, current_value)` — browser
  spinner doesn't clamp down, and users who write `n_peaks: 15` in
  the spec don't get silently trimmed to 10. `n_peaks` gets
  `ui_min=1, ui_max=10`; everything else unaffected.

### Decisions

- **Peak pairing = amplitude-then-frequency**, matching user's mental
  model: amplitude filters noise first, frequency-sort gives
  predictable pairing for PRBS / known-frequency-set tests.
- **Failure mode on under-detection**: hard fail ("expected N
  detected M"). Alternative was "pair only the M we found and
  score those" — rejected because the user declared N peaks
  explicitly; silently narrowing the contract is a worse UX than
  a loud fail.
- **Spectrum subplot activated-only, not always-visible**: matches
  tube editor pattern; keeps report lighter; subplot mounts in both
  full-tree and per-variable mounts on activate.
- **Event-timing overlays on main plot (no subplot)**: events are
  intrinsically time-domain; co-rendering with the trajectory is
  more useful than a separate subplot.
- **JS detects events client-side**: duplicate-time convention is
  universal, simple to implement (3 lines), and avoids bloating
  the embedded diagnostics with per-event arrays. CLI stays
  authoritative for the *pairing algorithm* which is trickier.

### Rejected alternatives

- **Semantic change to visibility = "disable leaf"**. Would need
  serialized state and validator tweaks. Kept plot-only; disable
  button deferred.
- **Time-varying or relative range bounds**. Achievable via existing
  composition (windowed range leaves under AND); keeping range
  scalar-only preserves the tube-vs-range semantic split.
- **Per-peak tolerance** (`rel_tolerance: [0.01, 0.02, 0.005]`).
  Possible future enhancement; single uniform tolerance covers the
  stated PRBS / multi-modal use cases.
- **Click-on-spectrum-to-pin-peak** interactive authoring. Deferred
  to E.vNext; current flow is "look at subplot, tune n_peaks +
  tolerance, rerun CLI."
- **Peak width / Q-factor comparison**. Different leaf type (damping
  regression). Deferred.

### Files

**Python**:
- `src/modelica_testing/comparison/modes.py` — every ModeConfig field
  gained `metadata={"label": ..., "help": ...}`. `n_peaks` added to
  `DominantFrequencyConfig`. `resolve_mode` reads `n_peaks`.
- `src/modelica_testing/comparison/comparator.py` —
  `_compute_fft_spectrum`, `_find_top_n_peaks` helpers;
  `_compare_dominant_frequency` rewritten for multi-peak +
  spectrum-in-diagnostics.
- `src/modelica_testing/reporting/ui/mode_controls.py` —
  `FieldSpec.ui_min` / `ui_max`. `derive_schema` reads them from
  metadata. `_render_field` emits `min` / `max` attrs.
- `src/modelica_testing/reporting/plot_comparison.py` —
  `_extract_spectrum(vc)` helper; `_augment_tree_view` attaches
  `spectrum` to dominant-frequency leaves; `_extract_mode_values`
  includes `n_peaks`.

**JS**:
- `src/modelica_testing/reporting/templates/interactive.js` —
  `syncSiblingVisToggles`, visibility tooltip update.
  `COMBINATOR_HELP` + kind-select tooltips.
  `_detectEvents` + event-timing plot contribution.
  `MODE_PLOT_EDITORS['dominant-frequency']` + `_renderSpectrum`.
  `renderSchemaFieldJs` honors `ui_min` / `ui_max` soft caps.

**Template**:
- `src/modelica_testing/reporting/templates/interactive.html` —
  structural hashes refreshed.

**Tests**:
- `tests/test_event_and_freq_modes.py` — +6 tests for multi-peak
  (match, shift, amplitude-filter, under-detection fail, factory,
  spectrum embed).
- `tests/test_interactive_playwright.py` — +2 tests for visibility
  (cross-mount sync, scoring-unaffected).

### Validation

- Full regression clean (744 pass / 1 skip, 0 regressions).
- End-to-end smoke against `ModelicaTestingLib` — all 10 tests still
  PASS; browser-verified tooltips render, `_detectEvents([0,1,2,2,3])
  = [2]`, FrequencyTest's dominant-frequency leaf carries
  `spectrum: true`.

- **Test count**: 736 → 744 (+8: 6 Python + 2 Playwright).

### Follow-on — `MultiFrequencyTest` added to `ModelicaTestingLib`

New showcase model for the multi-peak feature: composite signal summing
1/3/7 Hz sinusoids with distinct amplitudes (3, 2, 1) over 4 seconds.
Distinct amplitudes make the amplitude-rank → frequency-sort pairing
unambiguous. Spec entry uses `n_peaks=3`, `rel_tolerance=0.02`. Browser-
verified end-to-end: all 3 peaks detected at 0.998 / 2.994 / 6.986 Hz
(FFT bin resolution), paired deltas zero on self-regression, spectrum
subplot mounts with 4 Plotly traces per editor slot (ref spectrum + act
spectrum + ref peaks + act peaks). Baseline committed as `ref_0010.json`
under `Resources/ReferenceResults/OpenModelica/linux/`. Suite now 11
tests, all PASS. Also fixes a flake in
`test_remove_leaf_popup_closes_on_escape` (same `setTimeout(..., 0)` race
with the ESC listener that `test_wrap_popup_closes_on_escape` hit in
D72 — fix is the same `wait_for_function` on `_cleanup` handler
attachment). No test-count change from the flake fix alone.

## D75 — Declared-peaks dominant-frequency + PointPlotEditor abstraction (2026-04-23)

Replaces D74's top-N-by-amplitude algorithm with a **declarative table
of expected frequencies**: user authors `peaks: [{freq, tolerance,
tolerance_mode}, ...]`; algorithm looks for the strongest local maximum
in each peak's tolerance window on the actual spectrum. Leaf passes iff
every declared peak has a match. Also extracts the shared Shift-modifier
interaction from the tube editor into a reusable
`createPointPlotEditor`.

### Scope (single PR, per user)

- **Algorithm rewrite**: `_compare_dominant_frequency(ref_t, ref_v,
  act_t, act_v, peaks=None)` in `comparator.py`. New
  `_find_strongest_peak_in_window` helper. Per-peak tolerance + mode
  (`rel` = fractional, `abs` = Hz). Unmatched peaks fail with reason
  `"no peak in tolerance window"`. Empty `peaks` list fails with a
  pointer at the reporter's Detect button.
- **Config simplification**: `DominantFrequencyConfig.peaks` replaces
  `rel_tolerance` + `min_frequency` + `n_peaks`. Clean break — no
  backward compat.
- **`createPointPlotEditor` factory** (~140 lines new JS): Shift+click
  /drag/right-click wiring extracted. Consumers supply `getAnchors`,
  `onClickAdd`, `onDragStep`, `onDragEnd`, `onRemove` callbacks; the
  factory owns mousedown/mousemove/mouseup/contextmenu, px→data
  conversion, nearest-anchor hit testing, RAF-throttled commits, and
  cleanup. Anchors are `{pt, x, y, ...metadata}` with `pt` as the
  stable reference the consumer mutates — drag tracks the ref, not
  an index, so sorts mid-drag don't lose the target.
- **Tube editor migrated** to the factory (behavior-preserving;
  ~90 lines of mouse plumbing deleted from tube IIFE, replaced with a
  ~20-line hook translating tube's (pt, bound) anchors to the generic
  shape). All 9 existing tube Playwright tests still green.
- **Dominant-frequency editor v2** (~350 lines): spectrum subplot +
  declared-peak diamond markers (colored by CLI pass/fail) + shaded
  acceptance bands + Shift-interactivity via the factory + declared-
  peaks table with `+ add peak` and `🔍 Detect peaks from reference`
  buttons. Auto-derived mode panel suppressed (custom editor owns the
  slot, same pattern as tube).
- **Live JS scorer**: `MODE_SCORERS['dominant-frequency']` ports the
  peak-in-window scan (mirrors Python's
  `_find_strongest_peak_in_window`). Dominant-frequency is no longer
  CLI-authoritative; event-timing still is.
- **Spec + baseline updates**: `FrequencyTest` and `MultiFrequencyTest`
  specs rewritten to the declared-peaks format (`peaks: [{freq: 1.0,
  tolerance: 0.01, tolerance_mode: "rel"}, ...]`). Baselines re-
  accepted.

### Decisions

- **Replace, not add**. Declared peaks is strictly better for
  regression (the use case); the discovery case is covered by the
  Detect button which wraps the old top-N algorithm as a bootstrap.
- **Per-peak tolerance + mode**. `rel` default (1% is the common
  ask); `abs` for Hz-specific bands. Per-row in the table.
- **Hard fail on unmatched peak**. User declared a peak, we couldn't
  find it — that's the regression signal. Partial-match-with-warn is
  achievable via `warn`-wrapping the leaf.
- **Detect button REPLACES the table**. Users on a fresh test want a
  known-good starting set. If they've already authored peaks, they
  re-run CLI, not re-detect.
- **Clean break — no backward compat** (per user's standing
  preference). Old specs with `n_peaks`/`rel_tolerance` silently fall
  through to the "no peaks declared" fail-with-hint path, nudging
  users to the new format.
- **JS scorer ported**. Live feedback on tolerance edits matches the
  workflow users have for tube/range/nrmse.
- **PointPlotEditor stays inline in `interactive.js`**. Separating
  into its own module would need a bundler; factory is ~140 lines
  and self-contained.

### Rejected alternatives

- Keep top-N as an alternate mode alongside declared. Detect button
  covers it; extra mode is surface area for no benefit.
- Per-peak tolerance as a parallel array on the config rather than
  per-row. Couples shape to `peaks`; per-row is cleaner and maps to
  table rows directly.
- Click-on-spectrum-to-declare without a table. Plot interaction is
  convenience over the primary table UI, not a replacement.
- `createPointPlotEditor` as a separate .js file. Repo ships
  `interactive.js` as a single static file — adding a module would
  require a bundler.

### Files

**Python**: `comparison/modes.py` (config simplified), `comparison/
comparator.py` (algorithm + new helper), `reporting/plot_comparison.py`
(`_extract_spectrum`, `_extract_mode_values`, `_leaf_score_display`
updated; `cli_authoritative` drops dominant-frequency).

**JS** (`reporting/templates/interactive.js`): new
`createPointPlotEditor` factory; tube editor migrated; declared-peaks
editor rewritten; `MODE_SCORERS['dominant-frequency']` live-scorer
added; `skipModeControls` suppresses auto-derive panel for
dominant-frequency leaves.

**Spec**: `FrequencyTest` + `MultiFrequencyTest` rewritten in
`test_spec.json`; baselines re-accepted under
`Resources/ReferenceResults/OpenModelica/linux/`.

**Tests**: `test_event_and_freq_modes.py` rewritten
(`TestDominantFrequencyMode` class: 8 declared-peaks tests replacing 6
old top-N tests). `test_mode_controls.py::test_dominant_frequency`
updated for passthrough `peaks` field. `test_interactive_playwright.py`
+2 browser-driven tests (editor activates with table; Detect button
populates the table from reference spectrum).

### Validation

- Full regression clean (746 pass / 1 skip, 0 regressions).
- Tube behavior identical post-refactor (all 9 tube Playwright tests
  green).
- End-to-end smoke on `ModelicaTestingLib` — all 10 testable tests
  PASS; browser-verified `MultiFrequencyTest` report shows 2 spectrum
  subplots + 2 peak-editor tables + 3 declared peaks (`[1.0, 3.0,
  7.0]` Hz) in leaf state + 2 Detect buttons across the two mounts.

- **Test count**: 744 → 746 (+2; net: +8 Python + 2 Playwright new,
  −6 Python old multi-peak tests = +4 author-intended, plus
  refactor-adjacent noise).

## D76 — Live JS FFT + window-scoped detection + per-peak provenance (2026-04-23)

Closes the Q1 loop from the D75 user review: "does Detect respect the
window? does it use ref or actual?" — both questions. The window is
now a **detection-scoping tool** (interactive, live-recomputed in the
browser) in addition to its existing scoring role. Detect source is
user-selectable. Per-peak metadata records which window each peak was
identified in.

### Scope (single PR — Commit 2 of the Q1/Q2 split)

- **Radix-2 FFT ported to JS** (`_fftRadix2` + `_computeFftSpectrum` +
  `_findTopNPeaksJS` + `_findStrongestPeakInWindowJS` + helpers). ~190
  lines. Algorithm pinned to zero-pad-to-next-pow-2 + iterative
  Cooley-Tukey; mirrors Python's `_compute_fft_spectrum` +
  `_find_top_n_peaks` + `_find_strongest_peak_in_window`. Peak
  LOCATIONS match within one bin; magnitudes may differ slightly
  (sinc interpolation from zero-padding vs numpy's arbitrary-N rfft).
  Fine for visualization + detection; CLI stays authoritative for the
  committed pass/fail.

- **Live spectrum subplot**. `getLiveSpectrum(leaf, source)` reads
  the trajectory (ref or actual) from
  `VARIABLES_BY_NAME[leaf.variable].trajectory`, slices by
  `leafState[path].window`, runs the JS FFT. Called on every
  `refreshEditor` — edit the window, the subplot recomputes. Fallback
  path to the CLI-embedded spectrum for edge cases with no trajectory
  data (unusual report shapes).

- **Detect source dropdown**: `Source: [Reference | Actual]` next to
  the Detect button. In-memory per-leaf selection (not persisted;
  default Reference). Detect uses `_findTopNPeaksJS` on the selected
  source's live windowed spectrum, seeds top-3 peaks into the table.

- **Per-peak `derived_from_window` metadata**. Each peak added via
  Shift+click, `+ add peak`, or Detect gets a
  `derived_from_window: {start, end}` stamp copied from the leaf's
  current window (absent when window is unset). Round-trips through
  `nodeToSpec` via the catch-all params-forwarding loop — no new
  patch-apply whitelist entries needed. Table has a "Src window"
  column showing `[start, end]` or `—`, with a hover tooltip
  explaining that scoring uses the leaf-level window; the metadata
  just records where the peak was originally identified.

- **Live match column** replaces CLI-paired_peaks in the table.
  `evaluateMatches(leaf, actSpec)` computes per-declared-peak match
  status live via `_findStrongestPeakInWindowJS` on the current
  windowed actual spectrum. "Match (live) → 4.0023 Hz (Δ 2.3e-3)"
  or "no match" — updates on every state mutation.

- **Live JS scorer** (`MODE_SCORERS['dominant-frequency']`) now uses
  the live JS FFT instead of the CLI-embedded spectrum. Pass/fail
  reflects window + tolerance edits in real time.

- **"Multi-window scoring" handled by composition**. User's original
  question: can I declare freq X in window [0,5] AND freq Y in
  window [5,10] on the same leaf? Answer: use two
  `dominant-frequency` leaves under an `and` combinator, each with
  its own window. Grammar already supports this (D71 windowed
  leaves + D72 combinator kind editing). No per-peak scoring-window
  was added — keeps the per-leaf window invariant intact.

### Decisions

- **Detect source = dropdown, default Reference**. Reference is the
  common case (seed table from the regression target); Actual covers
  the "fresh test, no reference yet" workflow.
- **Detect replaces the table** (not appends). Consistent with D75.
  The source-dropdown + window make it a live operation; users
  exploring can change source/window and re-Detect as many times as
  they want before committing.
- **Live match column** shows the current pass/fail per peak, not
  CLI-frozen paired_peaks. Users editing tolerances/freqs/windows see
  the effect immediately.
- **Radix-2 zero-pad** over arbitrary-N via Bluestein's. Radix-2 is
  ~60 lines in JS; Bluestein's is ~200. The peak-location accuracy
  matches within 1 bin either way — we only care about detection, not
  precise spectral density.
- **Metadata on peaks, not a separate structure**. `derived_from
  _window` lives on the peak dict itself (`{freq, tolerance,
  tolerance_mode, derived_from_window}`). No new schema; survives the
  patch round-trip automatically via the params-forwarding loop.
- **Per-peak scoring window NOT introduced**. User's "check same freq
  in multiple windows" use case is expressible via composition — one
  leaf per window under AND. Avoids multiplying the leaf's state
  model.

### Rejected alternatives

- **Naive O(n²) DFT** instead of radix-2. Simpler but ~50-100ms for
  1024 samples — too slow for interactive window brushing on larger
  signals.
- **Server-side FFT recompute on window edit**. Would need a backend
  server; standalone HTML / file:// URLs are a core invariant.
- **Per-peak `scoring_window` field**. Over-complicates the leaf
  state; composition handles it without new primitives.

### Files

**JS** (`src/modelica_testing/reporting/templates/interactive.js`):
- `_fftRadix2`, `_computeFftSpectrum`, `_findTopNPeaksJS`,
  `_findStrongestPeakInWindowJS`, `_dedupeMonotonicTimes`,
  `_sliceToWindow` new helpers (~190 lines).
- `MODE_PLOT_EDITORS['dominant-frequency']` rewritten:
  `getLiveSpectrum(leaf, source)`, `evaluateMatches(leaf, actSpec)`,
  source dropdown, `derived_from_window` stamping on add/detect,
  "Src window" table column.
- `MODE_SCORERS['dominant-frequency']` ports to live FFT.

**Tests** (`tests/test_interactive_playwright.py`):
- `test_detect_respects_leaf_window_and_source` — two-segment fixture
  (ref: 2 Hz in [0,2]s, silence after; actual: silence in [0,2]s,
  5 Hz after). Verifies window [0,2] + Reference → detects 2 Hz;
  window [2,4] + Actual → detects 5 Hz. Both add
  `derived_from_window` metadata.
- Updated `test_declared_peaks_detect_button_populates_table` fixture
  to use a long-enough trajectory so the live FFT runs.

### Validation

- Full regression clean (749 pass / 1 skip, 0 regressions).
- End-to-end on the real `MultiFrequencyTest` report: window [1, 3]
  + Detect-from-Actual → peaks at `[0.977, 2.930, 6.836]` Hz (close
  to authored 1/3/7 given the 2-second window's 0.5 Hz bin
  resolution); all 3 peaks carry
  `derived_from_window={start: 1, end: 3}`; Source dropdown rendered
  across both mounts (2 total).

- **Test count**: 746 → 749 (+3: 2 new declared-peak FFT tests + 1
  updated fixture rewrite).

## D77 — Julia / ModelingToolkit backend (2026-04-23)

Fourth `SimulatorRunner` alongside Dymola, FMPy, OpenModelica. First
non-Modelica-syntax source — proves the framework's simulator-neutral
abstraction on a genuinely different runtime.

See commit `d6c43f8` for the full story. Summary:

- User writes `.jl` files exporting `build_mtk_system()` → `(sys, u0, ps)`.
- Framework's `run_test.jl` driver invokes one Julia subprocess per test;
  captures result as JSON (`{time, variables: {name: [...]}}`).
- `JuliaRunner` reads it back into `TestResult`. `capabilities =
  {BATCH_FALLBACK}`.
- `examples/julia/` self-contained demo with pinned `ModelingToolkit`,
  `OrdinaryDiffEq`, `JSON3`. Two sample tests: `SimpleRamp` (D(x)~2)
  and `Frequency` (second-order oscillator producing 1 Hz sine).
- End-to-end: `modelica-testing run --config examples/julia/testing.json`
  produces 2/2 PASS; CLI-computed passed == live JS scorer passed
  (D76's bin-aligned FFT works unchanged on Julia output).
- `julia` pytest marker gates 3 integration tests; auto-skipped when
  Julia / `Manifest.toml` missing.
- Dyad deferred (compiles to MTK; likely works via same path, untested).
- MTK FMU export deferred.

Companion (not sibling): Julia tests aren't siblings of Modelica tests
— same model, different source language is a different code, not the
same code on a different simulator. Cross-library visual overlays
should use the companion mechanism (D78 wires this).

Test count: 749 → 752 (+3 gated integration).

## D78 — Persistent-worker Julia + cross-library companions (2026-04-23)

Two related follow-ons to D77:

### Persistent Julia workers

Mirrors the Dymola / OpenModelica persistent-worker pattern. Long-lived
`julia --project=... run_persistent.jl` subprocess reads JSON-per-line
test requests from stdin, writes JSON responses to stdout. Package load
(`using ModelingToolkit, OrdinaryDiffEq, JSON3`) happens **once per
worker** at startup; per-test cost reduces to `include + structural
_simplify + solve` — typically seconds per test after the first.

**Why it matters**: batch-mode Julia pays its `using` cost (~15-40s of
cache reads, not full recompile) on every test. For an N-test suite
that's ~NX the cost that could be amortized. The user's insight was
exactly right — persistent workers reclaim both the `using` cost AND
per-model JIT codegen (cached within the long-lived session).

**Warmup phase**: workers emit a `{"event": "ready", "pid": ...}`
pulse on stdout once `using` completes. The dispatcher waits for this
pulse before accepting test requests, so the first test's wall-clock
reflects just model-specific compile/solve — not the one-time package
load that would have happened regardless. A one-line warmup message
prints before the ready-wait: "first run may take 2-3 min while MTK +
OrdinaryDiffEq load from cache."

**Decisions**:
- **stdin/stdout pipe over JuliaCall**: simpler than embedding Julia in
  Python. No extra deps, no C++ build step. Slight cost in latency
  (JSON serialize/deserialize per test) — negligible next to compile+solve.
- **`Base.invokelatest(build_mtk_system)` after `include`**: forces the
  freshly-redefined function generation, avoiding stale compiled
  `build_mtk_system` from a prior test in the same worker.
- **Warmup pulse as a discrete event**, not a side effect: the dispatcher
  blocks on the pulse so first-test timing isn't contaminated by
  startup. Matches OpenModelica's pattern (sendExpression handshake).
- **Worker restart cap of 3**: identical to Dymola / OpenModelica.
- **`psutil` hard-kill on timeout**: subprocess groups cleaned on
  worker death — Julia doesn't spawn children in our driver but
  defensive.

**Rejected alternatives**:
- **JuliaCall (in-process)**: requires libjulia + .NET-style embedding.
  Heavier. No value given our latency profile.
- **One persistent worker per test**: defeats the purpose — amortizing
  `using` requires REUSING the worker across tests.
- **Shared on-disk precompile cache via `PackageCompiler.jl`**: generates
  a custom sysimage that includes MTK pre-baked. ~30-60 min one-time
  build, saves the 15-40s per-worker-startup. Deferred until real
  multi-suite use demands it.

**Validation**: `examples/julia/` 2-test suite:
- Batch mode: 39s wall (pays `using` on both tests).
- Persistent mode: 24s wall (11.5s labeled "warmup", 12.5s for both
  tests inside the session).
- Per-test sim time inside the session: ~3s for Frequency, ~9s for
  SimpleRamp (first test eats per-model JIT, second is ~1s).
- Scales to N tests: `using` overhead stays constant; per-model JIT
  dominates from test 1 onward.

### Cross-library companions (ModelicaTestingLib ↔ examples/julia)

Wired four cross-implementation companion overlays via the existing
companion-path machinery. No new plumbing needed — `add_companion`
already stores path strings verbatim, and the loader already resolves
relative paths against the ref_dir. Portable commits without absolute
paths.

- `JuliaMtkExamples.Frequency` ← modelica-om-frequency
  (points at ModelicaTestingLib FrequencyTest's OM baseline)
- `JuliaMtkExamples.SimpleRamp` ← modelica-om-simple
  (points at ModelicaTestingLib SimpleTest's OM baseline)
- `ModelicaTestingLib.Examples.FrequencyTest` ← julia-mtk-frequency
  (points at Julia/MTK Frequency's Julia baseline)
- `ModelicaTestingLib.Examples.SimpleTest` ← julia-mtk-simple
  (points at Julia/MTK SimpleRamp's Julia baseline)

**Sibling vs companion** (per user clarification from this session):
- **Sibling** = same code, different simulator (Dymola OM FMPy auto-
  discovered at `<reference_root>/<OtherBackend>/<os>/ref_*.json`).
- **Companion** = different implementation, visual-only overlay
  (manually wired via `companion add` as a portable-relative-path
  pointer). Never scored; gracefully degrades if the target file
  disappears.

Julia tests are NOT siblings of Modelica tests (different source
language = different code). The companion mechanism is the right
venue, and it works via `add_companion` + relative paths today.

End-to-end verified: activating the Modelica `FrequencyTest` report
shows the Julia-MTK baseline as a toggleable overlay (119 time points
spanning [0, 4]s, role=companion, name=julia-mtk-frequency) alongside
the existing Dymola/windows sibling-backend auto-companion.

### Test count: 752 → 752 (no new Python tests; the four companion
references are checked into example data and exercised by the existing
`examples/julia/testing.json` smoke flow).

## D79 — JuliaMtkTestingLib full library structure + 5 new ports + observables fix (2026-04-23)

Follow-on to D77 / D78. Promoted `examples/julia/` from a flat two-test
demo into a proper library directory mirroring `ModelicaTestingLib/`,
ported five additional tests, fixed a driver bug discovered along the
way.

### Scope

**Structural reorganization**. New layout mirrors Modelica's:

```
examples/julia/JuliaMtkTestingLib/
├── Project.toml, Manifest.toml        (Julia package root)
├── Examples/
│   ├── SimpleRamp.jl
│   ├── Constant.jl                    (new)
│   ├── Frequency.jl
│   ├── MultiFrequency.jl              (new)
│   ├── RangeCheck.jl                  (new)
│   ├── TubeTolerance.jl               (new)
│   └── MetricTree.jl                  (new)
└── Resources/ReferenceResults/
    ├── testing.json, test_spec.json
    └── Julia/linux/ref_0001..0007.json
```

Per user's scope-(a) pick: **test-fixture demo, NOT publishable Julia
package**. No `src/JuliaMtkTestingLib.jl` module file — framework only
needs `include + build_mtk_system` per file, and module ceremony would
buy nothing. Additive later if external consumption emerges.

**Test count**: 2 → 7. Covers 5 of the 10 ModelicaTestingLib tests.

### Driver fix — observables are not unknowns in MTK

`structural_simplify` classifies algebraic outputs — e.g., `y ~ a1 *
x1 + a2 * x2 + a3 * x3` in MultiFrequency — as **observables**, not
unknowns. The original driver (D77) collected only `unknowns(sys)`.
Observables were silently missing from the result JSON, so framework-
side `read_result` saw no variable named `y` and the comparator fell
to a no-data path.

Both `run_test.jl` (batch) and `run_persistent.jl` now collect both:

```julia
unk = ModelingToolkit.unknowns(nt.sys)      # differential states
obs = ModelingToolkit.observed(nt.sys)      # algebraic `lhs ~ rhs`
```

and materialize via `sol[u]` / `sol[eq.lhs]`. Defensive: skips
already-captured names, MTK-internal helper symbols (`ˍ`-prefixed),
and `sol[...]` exceptions (parameter-only expressions).

### Spec nit — metric tree without variables

MetricTree-style entries (only `metrics` field, no top-level
`variables`) were treated as simulate-only since `variable_patterns`
resolved empty. Added explicit `"variables": ["x", "y"]` to the
MetricTree entry. Longer-term candidate: auto-derive
`variable_patterns` from leaf variables in the metric tree. Deferred.

### Cross-library companions re-wired (14 total)

D78's 4-companion set pointed at pre-move paths. Removed stale
pointers; registered fresh companions both directions for every
matched pair:

* Every Julia test → ModelicaTestingLib OM companion (7).
* Every matching Modelica test → JuliaMtkTestingLib companion (7).

Portable relative paths (6 `..` segments either direction; resolve
against ref_dir). Browser-verified: Julia MultiFrequency report
renders `modelica-om-multifrequency` overlay (502 time points,
`role=companion`).

### Deferred (consciously)

* **EventTest** — MTK uses `@discrete_events` / callbacks vs Modelica
  `when...then`; syntactically different. Port when needed.
* **IntervalTest** — Modelica-specific output-interval semantics.
* **NoUnitTest** — Modelica discovery-fixture only.
* **SimulateOnlyTest** — spec_parser doesn't parse `simulate_only`
  from test_spec.json; ~20 lines to wire for Julia source path.

### Rejected alternatives

* **Full publishable Julia package** with `src/` module. No value
  for the fixture use case; additive later.
* **Auto-derive variable_patterns from metric tree**. One-line spec
  fix was cheaper; auto-derive has edge cases (globs,
  against-baseline refs) that aren't trivial.
* **Observables as promoted states** (user workaround via `D(y) ~
  ...`). Unnatural; fix the driver once.

### Validation

* `run --config .../testing.json` → **7/7 PASS** on self-regression.
* Full suite: **752 passing, 1 skipped, 0 regressions** (unchanged
  count since D78 — 3 integration tests cover the expanded library
  via the existing smoke flow).
* Browser-verified cross-library overlay rendering.


## D80: Python-driven tests — validating the backend abstraction

- **What**: Added a fifth `SimulatorRunner` backend (`PythonRunner`) that
  runs arbitrary Python scripts as test sources. User-facing contract:
  a `.py` file exports `simulate(stop_time: float, tolerance: float) -> dict`
  returning `{"time": [...], "variables": {name: [...]}}`. Batch-only
  subprocess per test; no persistent variant yet.
- **Why**: Primary motivation was validating that the backend
  abstraction (`TestModel`, `TestResult`, `SimulatorRunner`,
  `Recognizer`, `spec_parser`) is truly language/simulator-agnostic
  rather than secretly Modelica-shaped. Secondary motivation:
  unlocks pyomo / scipy / custom-solver / CSV-loader / HTTP-fetch use
  cases that were impossible before.
- **Architecture**: Copies the Julia D77 subprocess+JSON-over-disk
  contract unchanged. New `src/modelica_testing/simulators/python/`
  package with `runner.py` + `run_test.py` driver. New fixture library
  `examples/python/PythonTestingLib/` with `SimpleRamp.py` (scipy ODE)
  and `ConstantCsv.py` (CSV loader). The CSV-loader example is the key
  architectural validation — it contains zero numerical-integration
  code and still produces a passing test via a baseline-free range
  check.
- **Refactor pass** (minimal, per the plan's scope discipline):
  `spec_parser.py`'s `julia_rel` variable renamed to `source_rel` to
  reflect that the `"source"` JSON field is generic across non-Modelica
  backends. No other renames — the remaining Modelica-flavored
  `TestModel` fields (`x_expressions`, `x_raw`, `x_reference`,
  `error_expected`, `number_of_intervals`, `output_interval`, `method`)
  are left alone because the Python backend simply ignores them and
  the D77 Julia backend already does the same. Renaming them would be
  churn without improving generality.
- **Deferred**: Persistent-worker Python (D77→D78 progression to
  follow once a real perf ceiling hits). Alignment/fitting of
  experiment data (see `docs/ideas.md` #49). Dynamic-data fetching is
  not deferred — any user writes an HTTP client in their `.py` file
  today.
- **Validation**:
  - `SimpleRamp` + `ConstantCsv` both PASS on self-regression.
  - Full suite: 761 passing + 1 skipped, 0 regressions.
  - Driver-in-isolation tests cover success, missing simulate(),
    exception in simulate(), malformed return type.

### Rejected alternatives

- **Base class for subprocess+JSON runners** (factor out shared code
  from JuliaRunner and PythonRunner). Tempting but premature per YAGNI:
  two backends with ~30% shared code isn't enough to justify an
  abstraction. If a third subprocess-JSON backend arrives (e.g., R,
  Octave, Matlab), extract then. Note the `/simplify` skill would
  flag this candidate on review.
- **Support `importlib` over subprocess** (run user code in-process
  for speed). Rejected: loses the process isolation that makes
  framework-level timeouts reliable and prevents user-code crashes
  from taking down the test runner.
- **Built-in CSV/parquet loaders as capability-flag-gated first-class
  sources**. Rejected: users with CSV data write one 5-line `.py`
  file; framework complexity budget better spent elsewhere (D66
  economy-of-tools).
- **Alignment/fitting in-framework as part of #45**. Deferred to
  ideas.md follow-up. Without a concrete user demand it's easy to
  build generic alignment machinery that fits nobody; wait for real
  use case.

## D81: Rename ModelicaTesting → DSTF (Dynamic Systems Testing Framework)

- **What**: Python package `modelica_testing` → `dstf`; CLI command
  `modelica-testing` → `dstf`; pypi project name `modelica-testing`
  → `dstf` (SUPERSEDED by D124 — the PyPI name is
  `dynamic-systems-testing-framework`, because `dstf` is taken there by an
  unrelated project; the import package and the CLI command are unchanged);
  brand in docs "ModelicaTesting" → "Dynamic Systems
  Testing Framework (DSTF)".
- **Why**: After D80, the tool supports five simulator backends (only
  one of which is Dymola-specific) plus arbitrary Python scripts and
  CSV-loader test sources. The "ModelicaTesting" name was identified
  at project inception as a working name pending multi-backend
  stabilization (see CLAUDE.md's D44–D80 summary); that condition is
  now met. "DSTF" was chosen for the short form because it's
  trivially pronounceable and doesn't over-commit on any single
  backend language.
- **Scope**: Package, CLI, pypi name, docs brand, bash examples in
  docs. Explicitly out of scope: GitHub repo rename (user's
  decision), example library names (`ModelicaTestingLib` genuinely
  is a Modelica library — keeping that name is accurate), config
  filename `testing.json` (already tool-neutral), backward-compat
  aliases (forbidden by standing "no backward compat" policy).
- **Preserved history**: `docs/superpowers/plans/*` and
  `docs/superpowers/specs/*` left as-written — they are frozen
  snapshots of past planning and rewriting them would falsify the
  record. Same rule for D1-D80 in `decisions.md` — each entry
  reflects the state when written.
- **Validation**: Full suite 761 + 1 skipped, same as pre-rename.
  All five backend smokes PASS (Dymola skipped on Linux, Julia 7/7,
  OpenModelica 10/11, FMPy skipped without reference-fmus-binaries,
  Python 2/2).

### Rejected alternatives

- **Keep a `modelica-testing` CLI alias for one release**. Standard
  deprecation ladder would normally apply, but the project's
  standing "no backward compat during this development cycle"
  policy (user preference, see memory) supersedes — clean break.
- **Rename to `dynamic-systems-testing` (long form)**. Python
  imports would be `from dynamic_systems_testing import ...`
  — noisy. Short form `dstf` is clean and the user explicitly
  asked for it.
- **Rename example libraries too** (`ModelicaTestingLib` →
  `DSTFModelicaLib` or similar). Rejected: `ModelicaTestingLib` is
  genuinely a Modelica library authored to exercise DSTF; its name
  accurately describes its content.
- **Rename the on-disk repo directory `ModelicaTesting/`**.
  Deferred to the user; it's a local checkout concern and git
  doesn't care. Renaming the GitHub repo is a separate user
  decision.

## D82: Event-timing HTML editor (Medium scope)

- **What**: Added a declared-events editor for the ``event-timing``
  comparison mode to the interactive HTML reporter. Users can author
  a list of expected event instants with per-event tolerances via a
  table UI in the leaf's editor slot, including a "🔍 Detect events"
  button that scans the chosen signal (Reference or Actual) for
  duplicate-time samples and populates the table. The Python scorer
  gained an ``events: Optional[list[dict]]`` field on
  ``EventTimingConfig`` — when set, declared events become the
  authoritative reference-side event set; each one claims the nearest
  actual-side auto-detected event within its own tolerance.
- **Why**: Event-timing was the last remaining mode without a
  dedicated UI surface. The user's roadmap item #3 asked for parity
  with the dominant-frequency declared-peaks editor (D75/D76).
- **Scope: Medium** — table + add + delete + detect + live match
  column. Deliberately NOT included: draggable diamond markers on
  the trajectory plot (Full scope; deferred until someone heavily
  uses it) and a live JS pass/fail rescorer (stays CLI-authoritative
  because event pairing is non-trivial).
- **Semantic change (declared-events path)**: When ``events`` is
  None (default), the existing auto-detect + pair-by-index path is
  unchanged — no regression for existing event-timing tests. When
  set, the algorithm switches to "each declared event claims the
  nearest actual event within its own tolerance", matching
  dominant-frequency's declared-peaks semantics.
- **Validation**: 5 new Python tests + 8 new Playwright tests.
  Full suite goes from 776 → 791 passed. 0 regressions.

### Rejected alternatives

- **Live JS pass/fail rescorer**. Rejected: event-pairing is complex
  enough (nearest-neighbor + tolerance-per-event + count_must_match)
  that porting it would add latent bugs and require keeping two
  implementations in sync. Current UX: users edit events in the
  browser, export the patch, rerun CLI for authoritative results.
- **Draggable diamond markers via PointPlotEditor**. Deferred.
  Could fit cleanly since ``_pointEditor`` already handles tube's
  two-axis drags; events would only need one-axis (time) drags. Skip
  until a user hits a concrete workflow where the numeric inputs feel
  slow.
- **Shared helper with dom-frequency peaks editor upfront**.
  Rejected per the plan's YAGNI guard — wrote both fresh, audited
  overlap afterward (see ideas.md follow-up).


## D83: Baseline-free modes score without a reference (2026-04-24)

- **What**: When a test has no reference baseline on disk AND every
  leaf in its metric tree is baseline-free, the comparator now
  proceeds to score the test instead of short-circuiting to the
  NO_REF state. "Baseline-free" is a per-mode property exposed via a
  new ``ComparisonMode.is_baseline_free()`` method:
  - ``RangeMode``: always baseline-free (bounds declared in config).
  - ``EventTimingMode``: baseline-free iff ``events`` is declared
    (the declared-events path from D82).
  - ``DominantFrequencyMode``: baseline-free iff ``peaks`` is
    declared (the declared-peaks path from D75).
  - Every other mode (NRMSE, tube, final-only): stays ``False``.
- **Why**: Idea #59. Fresh tests using range / declared-events /
  declared-peaks previously showed an unhelpful orange "NO_REF"
  badge, misleading users into running ``--accept`` to create a
  baseline that none of those metrics ever consult. The test's
  pass/fail is entirely determined by the leaf config and the
  actual simulation data — the reporter should say so.
- **Scope: Small (polish)**. Mixed trees (any leaf that needs a
  reference) keep the legacy NO_REF behavior — no partial-run
  support in this fix. The default ``False`` on the base class
  means all existing modes keep their semantics unchanged.
- **Fallout**:
  - ``compare_all`` now passes ``reference={}`` through to
    ``compare_test`` for baseline-free tests, same pattern as
    ``simulate_only``.
  - ``compare_test`` (implicit-AND path) and ``tree_eval._evaluate_leaf``
    both now tolerate ``ref_var is None`` for baseline-free leaves
    and run them with empty reference arrays.
  - ``_compare_dominant_frequency`` no longer unconditionally requires
    ``len(ref_values) >= 4`` — the reference FFT is only computed
    when actually needed (no declared peaks, or for reporter diagnostics).
  - ``resolve_mode`` now forwards the ``events`` override key to
    ``EventTimingConfig`` (was missing — a latent D82 gap that
    would have prevented declared-events from flowing through the
    implicit-AND path).
- **Validation**: 5 new Python tests in
  ``tests/test_comparator.py::TestBaselineFreeNoRef``. Full suite
  goes from 791 → 796 passed. 0 regressions.

## D84: `points` mode (final-only → points + multi-point + abs values + x-tolerance)

- **What**: Rename `final-only` to `points` and extend to a fully-
  capable point-based comparison mode. New schema accepts a list of
  declared checkpoints; each can have an explicit absolute target
  (baseline-free) or fall back to `ref(time)`, an absolute or
  relative y-tolerance, and a symmetric x-tolerance (`time_tolerance`)
  that turns the strict-time check into a 2D box check.
- **Why**: Three needs that the legacy final-only didn't serve —
  multi-point checking ("at t=1 the transient settles, at t=5 steady
  state matches, final value also OK"), absolute-value checking
  ("temperature reaches 350 K at t=3.2 ± 0.5"), and timing-uncertainty
  ("temperature reaches 30 °C at t≈3 — different solvers may hit at
  2.97 or 3.02"). Also closes the cross-metric consistency gap left
  by event-timing (D82) and dominant-frequency (D75) both having
  declared-list editors while final-only didn't.
- **Empty-list compatibility**: `points = None` or `[]` behaves
  identically to legacy final-only (act[-1] vs ref[-1] within
  tolerance). Existing tests continue to work without per-point
  authoring.
- **Clean break**: `"mode": "final-only"` and `"mode": "final_only"`
  in test specs no longer resolve — they fall through to NrmseMode
  (the no-mode default), which is wrong-but-different. Per the
  standing "no backward compat" policy. Verified: zero example test
  specs in this repo used `final-only`, so user-facing migration cost
  is zero. CLI flag `--final-only` and `config.final_only` likewise
  renamed to `--default-points` and `config.default_points`.
- **Editor surface (Medium scope)**: declared-points table in the
  leaf's .node-editor slot — Time | x-tol | Value | Mode | Tolerance |
  Match (live) | ×. Buttons: `+ add point`, `📸 Snapshot from ref`.
  Zero-point fast path shows an italic "final · uses ref[-1] ·
  tolerance N" placeholder row.
- **Plot decoration**: diamond markers at `(t_center, resolved_value)`
  + a translucent rectangle covering the box. When `time_tolerance=0`
  the rectangle degenerates to a vertical line segment.
- **Baseline-free integration (D83)**: `PointsMode.is_baseline_free()`
  returns True iff `points` is non-empty AND every point has an
  explicit `value`. Tests configured this way run via D83's
  short-circuit — no baseline file needed on disk.

### Rejected alternatives

- **Keep `final-only` and ship `points` as a separate mode** — adds
  a redundant editor, redundant docs, two overlapping mental models.
  The user explicitly preferred unification ("avoid method bloat in
  the HTML"). Empty `points` list IS final-only; renaming + extending
  collapses cleanly.
- **No null-time sentinel; require explicit numeric `time`** — couples
  spec to simulation `stop_time`. Any change to stop_time silently
  invalidates "final value" checks. `time: null` decouples cleanly.
- **Per-point partial scoring for mixed-baseline leaves** — score
  absolute points even when ref is missing, skip ref-relative ones
  with a warning. Creates PASS/FAIL ambiguity ("test passed when half
  the checks didn't run"). All-or-nothing baseline-free trigger
  preferred — users who want fully baseline-free commit to setting
  `value` everywhere.
- **Draggable plot markers as MVP** — same call as event-timing D82.
  Numeric table editing covers essentials; markers are polish that
  layers on later if usage shows demand. Logged as a follow-up in
  `ideas.md`.
- **Pyfunnel-style continuous x-tolerance integration** — separate
  tool for a separate problem (continuous-trajectory bounds vs
  discrete-checkpoint bounds). Tracked as ideas.md #25; can coexist
  with points-mode x-tolerance.

### Validation

- 20+ new CLI tests in `TestPointsDeclaredPath`: implicit-final
  preserved, single-point pass/fail, baseline-free absolute, per-point
  tolerance, abs/rel mode, null-time sentinel, multi-point, x-tolerance
  box pass/fail, x-tolerance=0 degenerate, interpolated endpoints.
- 17 new Playwright tests in `tests/test_interactive_points.py`:
  scorer (window-aware, declared paths, box check, window clipping),
  plot decoration (no-points + per-point markers + box dimensions),
  editor (mount, render, add, delete, placeholder, Snapshot button).
- Cross-metric matrix from range-fix Task 6 still passes after
  `'final-only'` → `'points'` rename.

## D85: Points editor polish + range autorange fix + cross-library points parity (2026-04-25)

- **What**: Eight commits on top of D84 closing out the points-mode
  user-facing UX, fixing a long-standing range plot quirk, and
  filling the example-coverage gap that D84 left behind.
- **Why**: D84 shipped points mode with the JS scorer + plot
  decoration + table editor scaffold, but a real-browser pass
  surfaced four distinct UX bugs the Playwright suite hadn't
  caught (single-slot mount, missing shift-modifier parity, no
  plot-level resize, mode-switch dataloss). The range double-click
  reset never included declared bounds because Plotly's autorange
  is trace-only. And no library had a points-mode example test, so
  a downstream consumer of the rename couldn't see the new mode in
  action.

### Range double-click autorange (commit `5a02d9f`)

- Plotly's autorange computes y-extent from trace data, ignoring
  shape positions. The `range` mode renders bound lines as shapes,
  so a `max_value` placed far outside the trajectory was invisible
  to autorange — double-click reset clipped to the trajectory and
  hid the bound. Workaround: emit one transparent-marker scatter
  trace per declared bound at the bound's y-value. Autorange picks
  it up; the marker is invisible (`marker.color: rgba(0,0,0,0)`).
  Resolves the "Range autoscale edge case" item that's been on
  `docs/qa/reporter_checklist.md` since Phase 6.

### Cross-library points parity (commit `8c71d1a`)

- D84 had zero example coverage. Added a `PointsCheckTest` in each
  fixture library (Modelica + Julia + Python + the FMU reference
  spec) using the same x = sin(time) signal — all five per-point
  knobs exercised: implicit ref-relative target, explicit absolute
  with rel y-tolerance, ref-relative with `time_tolerance` x-box,
  explicit absolute trough, ref-relative endpoint. FMU spec uses
  Dahlquist (e^-t, clean exponential decay) for variety. Linux
  references regenerated locally for OpenModelica, Julia/MTK,
  Python (scipy), and FMPy. Dymola/windows ref deferred to user's
  next Windows session.

### Points editor — multi-slot mount + shift-modifier parity (`62bcfaf`)

- The rich points table was using `querySelector` (singular) on
  activate, so it only mounted into the first `.node-editor` slot
  — the full-tree mount up top, not the per-variable section the
  user was actually looking at. Switched to `querySelectorAll` +
  per-slot `.points-editor` sub-container, mirroring the tube
  editor's slot model. All slots now stay in lock step.
- Wired `MODE_PLOT_EDITORS['points']` into `createPointPlotEditor`
  factory. Closes ideas.md #61 — diamond markers now have full
  shift+click/drag/right-click parity with tube and dom-frequency.
  Same abstraction; no duplication.
- Suppressed the auto-derived `mode_controls_html` for points
  (added to the `skipModeControls` list alongside tube and
  dom-frequency) so the rich table is the sole config surface.

### Points box resize + reliability (`2c80568` + `f19dc6e` + `057cf59`)

- Three iterations on the box-edit interaction, all triggered by
  real-browser regression-by-regression feedback:
  1. **`onClickAdd` + `onRemove` weren't calling commit().** Add /
     delete updated `leafState` and re-rendered the table, but the
     plot kept the stale diamond / tolerance box visible until the
     next unrelated re-render. Fixed by adding `commit()` after
     `refreshEditor` in both callbacks.
  2. **Replaced the corner-anchor system with a plotly_relayout
     listener.** PLOT_CFG has `edits.shapePosition: true` globally
     (range mode needs it for bound-line drag). The previous
     attempt at `editable: false` per shape was a no-op when the
     global flag is on. Plotly's native shape-drag would steal
     subsequent shift+clicks meant for the diamond. New design:
     the box IS native-draggable, and on every `plotly_relayout`
     for a `points_box:*` shape we re-derive `pt.time_tolerance`
     and `pt.tolerance` from the new bounds and re-render. Drag-
     to-resize works; drag-to-translate snaps back (size unchanged
     → same canonical bounds → no-op mirror).
  3. **`plotly_relayouting` (live) doesn't fire for shape edits.**
     Only `plotly_relayout` (release) does. The "live mirror as
     you drag" UX is therefore unreachable through Plotly events
     alone — would require disabling shape-edit globally and
     reimplementing range + points box drag through custom mouse
     handlers. User accepted the snap-on-release UX in lieu of the
     refactor.
  4. **Edge-vs-translation detection rewritten twice.** First by
     event-key presence (broken: Plotly emits all four bounds even
     on edge drag). Then by canonical-state delta (broken: pixel-
     to-data conversion noise of ~5e-6 misclassified each
     translation as a tiny edge drag, accumulating box growth over
     many drags — "the box keeps on getting bigger and bigger").
     Final: size-based check with 0.1%-of-size or 1e-3 absolute
     threshold — robust to FP noise, well below any real user-
     intent drag (typically several pixels).

### Mode-switch tolerance conversion (`ae3cab9` + `a05b829`)

- Switching `tolerance_mode` between abs and rel kept the stored
  numeric value unchanged. Since rel interprets the value as a
  fraction of `|target|`, the visible box size jumped wildly on
  every mode toggle — and for points where target ≈ 0 (e.g.,
  `sin(t)` at t=0 in the new fixture test), the rel box collapsed
  to height 0 and vanished entirely. Fixed by converting on the
  switch:
  - abs → rel: `rel_tol = abs_tol / |target|`
  - rel → abs: `abs_tol = rel_tol * |target|`
  Visible box size stays the same; user's intent of "swap how the
  tolerance is stored" is honored.
- Near-zero target protection: the abs→rel direction is rejected
  if the resulting rel fraction would exceed 10 (= 1000% — beyond
  that the relationship is meaningless). Catches both true zeros
  and solver-near-zero values like sin(0) ≈ 1e-7 from numerical
  precision (which the user actually hit and reported as "goes
  off to huge number"). Mode dropdown reverts to abs on rejection;
  no data is lost.

### Validation

- 12 new Playwright tests in `test_interactive_points.py` (multi-
  slot mount, shift+click add, shift+right-click delete, native
  shape-edit invariants, edge-drag detection from full-snapshot
  payloads, FP-noise-robust translation, mode conversion both
  directions, near-zero protection, plot-update-on-delete, live-
  event resize, corner-drag symmetric resize).
- 1 new test in `test_interactive_range_window.py` (autorange
  anchor traces).
- Full suite: 825 → 837 (+12), 0 regressions.

### Rejected alternatives

- **Disable Plotly's `edits.shapePosition` globally and reimplement
  range + points box drag through custom mousedown/move/up.** Would
  give true live mirror during the drag (the user's original
  desideratum). Not done because (a) the snap-on-release UX is
  acceptable to the user once the underlying math is correct, and
  (b) the refactor is large enough to warrant its own arc. Logged
  as a known limitation in `docs/qa/reporter_checklist.md`.
- **Per-shape `editable: false`.** Plotly's global
  `edits.shapePosition` overrides per-shape opt-out; the attribute
  was a silent no-op. Verified empirically.
- **Threshold tuning of 1e-6 or 1e-9 for noise rejection.** Too
  tight; let pixel-coord conversion noise through. 0.1% of size
  with a 1e-3 absolute floor is the operating point that survived
  user testing.
- **Block abs→rel only when target is exactly zero.** Doesn't catch
  solver-near-zero values like 1e-7. Output-sanity check on the
  resulting rel fraction (cap at 1000%) is more flexible —
  legitimate small-target conversions still go through.
- Full suite at +29 tests, 0 regressions.

## D86: Tube-mode taxonomy clean-up — `absolute` → `abs`, drop legacy aliases (2026-04-25)

- **What**: Three named tube modes, no aliases, hard-validated at
  the comparator entry point.
  - `band` — constant offset in signal units (`ref ± width`)
  - `rel`  — fraction of `|reference|`
  - `abs`  — literal y-axis bounds (was `absolute`); requires
             `tube_points` with explicit `upper`/`lower`
- **Why**: The previous taxonomy had three load-bearing pieces of
  back-compat cruft inherited from when tube-mode was first
  introduced (commit `3389f57`, "tube mostly working", 2026-04-10):
  - String `"abs"` aliased silently to `"band"` because the original
    tube design used `"abs"` to mean "constant absolute offset"
    before `"absolute"` was added later for "literal y-bounds". The
    namespace got reshuffled but the alias stayed.
  - Unset `tube_width_mode` + both `tube_abs` and `tube_rel` set
    fell into a `max(abs, rel*|ref|)` formula that nothing in the
    repo or TRANSFORM-UnitTests used.
  - The verbose `"absolute"` was awkward next to two-letter `band`/
    `rel`. Reclaiming `"abs"` for the truly-absolute (literal
    y-bounds) interpretation is more semantically clean — `abs`
    here means "absolute coordinates" rather than "absolute offset".
- **Silent-flip risk**: `"abs"` flips meaning (band → literal
  y-bounds). A pre-rename spec authored against the old taxonomy
  wouldn't error — it'd silently behave differently. Mitigation: the
  comparator now hard-raises `ValueError` on any
  `tube_width_mode` outside `{"band", "rel", "abs"}`, and the
  constant-tube shorthand additionally rejects `"abs"` (literal
  y-bounds need both bounds, requires `tube_points`). External
  consumers that do exist (TRANSFORM-UnitTests, internal example
  libraries) were verified to use zero `"abs"` or `"absolute"`
  strings before the change.

### Surface area

- `src/dstf/comparison/modes.py` — `Literal` type updated, help
  text rewritten (the previous "band = max(abs, rel*|ref|)"
  description was actually documenting the *removed* legacy
  fallback, not what `band` did).
- `src/dstf/comparison/comparator.py` — single up-front validator;
  removed legacy `"abs"→"band"` alias (was 3 LOC), removed
  unset-mode + both-abs+rel fallback (was 7 LOC), removed two
  unreachable `elif` branches in the offset/literal split (was
  10 LOC). Net `-15 +12 LOC`; cyclomatic complexity down a step.
- `src/dstf/reporting/templates/interactive.js` — width-mode
  dropdown option flipped.
- `tests/test_comparator.py` — 10 call sites that relied on the
  legacy fallback now declare an explicit mode.
  `test_max_of_abs_and_rel` was renamed to
  `test_min_width_floors_rel_at_zero_crossing` and rewritten to
  use the modern `tube_min_width` parameter — preserving the
  underlying "prevent zero-width at zero crossings" coverage with
  the modern API instead of the removed legacy one.
- `tests/test_mode_controls.py`, `tests/test_export_schema.py` —
  hardcoded `"absolute"` enum expectations updated.

### What was NOT renamed

- The dataclass field `tube_abs` (the value used by `band` mode for
  the constant offset) keeps its name. Renaming it cascades through
  every spec and reference JSON in the wild and is not worth the
  blast radius. Awkwardness: `tube_abs` is the value field for
  `band` mode in the new taxonomy. Documented in the help text.
- The per-point shorthand `{"time": t, "abs": w, "rel": r}` inside
  `tube_points` is still legacy-flagged in the docstring but
  preserved — no consumers were changed in this commit.

### Validation

- 837 + 69 playwright passing post-rename, 0 regressions.

## D87: Tech-debt sweep — dead code + declared-items editor extraction (2026-04-25)

- **What**: Two-arc tech-debt pass after D80–D86 closed out the
  reporter-as-IDE feature work.
- **Why**: D80–D85 added significant code surface (Python backend,
  points editor, three editor migrations). D86 cleaned up tube-mode
  taxonomy. Natural consolidation point before moving on to feature
  work.

### Arc 1: Dead-code sweep (commit `12d9195`, -46 LOC)

Three orphans + one dangling pipeline. All confirmed by grep across
src/ and tests/, verified against test_interactive_playwright.py for
live JS references:

- `cli.py:148` — redundant local `import sys` (sys is at module top).
- `interactive.js` — orphan `interpOnRef` (linear interp helper,
  replaced by `_interpLinear` everywhere; verified zero callers).
- `interactive.js` — orphan `updateSummary` (superseded by
  `updateSummaryFromMap` which integrates pass-recompute; the older
  function lacked the integration and was left behind during a refactor).
- `SPEC_PATH` — 4-file dangling pipeline (cli.py kwarg →
  plot_comparison.py param → interactive.html template →
  interactive.js const). Comment said "Save to Spec functionality"
  but the JS read the value and discarded it; feature never wired up.

The Python files came up remarkably clean — most subagent "defensive
code" findings turned out to be load-bearing back-compat shims
(legacy tube `"abs"` mode, flat-vs-nested stats schema), not safe to
remove without user buy-in. The legacy tube alias was addressed
separately in D86.

### Arc 2: createDeclaredItemsTable extraction (-108 LOC net)

Extracted `createDeclaredItemsTable` shared helper for the three
declared-list editors (event-timing, points, dominant-frequency).
Each editor now declares column accessors + match-evaluation logic
+ optional extras; the helper handles per-slot mounting, table
shell, +add / ✕ scaffolding, refresh lifecycle, and empty-row
placeholder.

- Helper: 143 LOC (one new function, mounted next to
  `createPointPlotEditor` as a sibling shared primitive).
- event-timing editor: 268 LOC → 185 LOC (-83 LOC).
- dom-frequency editor: 551 LOC → 479 LOC (-72 LOC). Spectrum
  subplot rendering and `_peakEditor` plot integration kept inside
  the IIFE; helper is invoked only for the table portion.
- points editor: 620 LOC → 486 LOC (-134 LOC). Mode-switch
  tolerance conversion (the abs↔rel half-extent preservation logic)
  retained as `_modeCell` cell factory inside the IIFE.

Net file delta on `interactive.js`: -108 LOC (4870 → 4762). Adding
a 4th declared-list mode now costs ~80-100 LOC of column specs
instead of ~250 LOC of from-scratch table code.

### Honest re-estimate vs handoff

The previous session's handoff implied 600-1000 LOC could come out
of the editor extraction (citing a 74% overlap measurement from
D82). Actual recovery: ~108 LOC. The 74% figure was for the
*table-shell scaffolding specifically* (mount/slot/refresh/+add/✕),
not the whole editor. Mode-specific code — column counts (4/7/6),
match-evaluation algorithms (claim-pairing / minDeltaInBox /
strongestPeakInWindow), and plot integrations (createPointPlotEditor
+ box-relayout listener for points; spectrum subplot for dom-freq)
— genuinely diverges and resists abstraction. Real value of this
refactor is uniformity for future declared-list modes, not LOC
count.

### Behavior changes captured in tests

- **event-timing now multi-slot**: Previously mounted with
  `querySelector` (singular), so only the full-tree mount got the
  editor — same bug points was carrying pre-D85. Now uses the
  helper's `querySelectorAll` slot finder, mirroring points and
  dom-frequency. 4 unit tests in `test_interactive_event_timing.py`
  updated to scope row counts via `.first.locator('tbody tr')`,
  consistent with how points tests already scope.
- The empty-row placeholder for points retains its
  `points-implicit-row` class via the helper's new
  `emptyRowClassName` spec field. Default-tolerance text in the
  placeholder is regenerated per refresh via a factory function so
  it stays current.

### Validation

- 837 + 69 playwright passing, 0 regressions.

### What was NOT extracted

- `createPointPlotEditor` (shift+click/drag/right-click) — already
  shared across tube/points/dom-frequency. Out of scope for this
  arc.
- Plot integration (Plotly subplot, box-relayout listener) — stays
  per-editor since the integrations are too divergent.
- Tube editor — 635 LOC, but it's a different *kind* of editor
  (control-point list with synced/unsynced modes, asymmetric tube
  support), not a declared-list editor. Different abstraction
  candidate.

## D88: Quality-floor hardening — ruff + tiered mypy + coverage ratchet + CI (2026-06-05)

- **What**: Establish the project's first static-analysis / coverage /
  CI quality floor, and close the highest-risk test gaps it surfaced.
  Decided via a `grill-with-docs` session; this entry is the spec, and
  it is being executed in the same session.
- **Why**: 18.5k src LOC carried **zero** lint, type-check, or coverage
  tooling (dev deps were just `pytest` + `scipy`). On a codebase that
  size, the cheapest, highest-leverage hardening is the quality floor
  that doesn't exist yet — and you can't sensibly choose *which* tests
  to add, or refactor safely, without first measuring where coverage
  is thin. Sequencing was therefore locked as **quality floor → measure
  → targeted tests**, ahead of any architecture-refactor pass.

### Coverage baseline that drove the scoping (measured this session)

`pytest-cov` over the hardware-free suite: **58% total**, but the
headline is deflated by un-runnable simulator code. The real shape:

- **Domain core well-covered**: `comparison/` 81–98%, `discovery/`
  77–97%, `storage/reference_store.py` 84%.
- **Hardware-gated (uncovered because the simulator isn't in CI, not a
  test gap)**: Dymola 19/21%, Julia 0%, FMPy 30%, Python runner 26%,
  `cross_backend` 0%, `base.py` 47%. These are `omit`-ed from the
  metric so the number reflects CI-runnable code.
- **The genuine gap — pure, testable, externally-contracted code at
  0%**: `reporting/junit_report.py` (JUnit XML is a contract with
  external CI), `reporting/console_report.py`, `reporting/html_report.py`;
  plus `plot_comparison.py` 11%, `cli.py` 26%, `patch_apply.py` 77%.

### Decisions (locked)

1. **Lint/format — ruff.** Big-bang `ruff format` over the whole tree
   as one isolated commit, SHA recorded in `.git-blame-ignore-revs` so
   `git blame` skips it (neutralizes the only downside). Curated rule
   set `F,B,E,W,I,UP,SIM` with safe auto-fixes — **not** `--select ALL`
   (buries real bugs in style noise), **not** format-on-touch (leaves
   mixed style, can't enforce in CI).
2. **Types — mypy, tiered not whole-codebase.** Scoped to the
   typed-dataclass core (`comparison/`, `discovery/`, `storage/`,
   `config.py`) on a lenient baseline with `ignore_missing_imports` for
   the optional backend libs (`fmpy`, `OMPython`, Dymola interface,
   `julia`). The registry/strategy dispatch, Dymola-logger
   monkey-patching, and JSON-dict glue have poor type signal — left
   lenient/excluded. Ratchet outward only if it earns its keep.
3. **Coverage — pytest-cov with a ratchet floor.** `omit` the
   hardware-gated execution paths; set `--cov-fail-under` at the
   post-test baseline minus ~2% (no-backsliding gate, not an
   aspirational target).
4. **CI — wire the quality floor now.** New `quality.yml` (lint + mypy
   + ~730 hardware-free tests + coverage) on push/PR; ~1–2 min/run, no
   simulators. The **FMPy e2e** CI (`PHASE_2_5_CI_PLAN.md`) stays
   deferred exactly as before — independent concern, cost-gated on
   private-repo minutes. Plus `.pre-commit-config.yaml` for local
   feedback.
5. **Tests this pass — close the 0% formatters.** Write
   `junit_report` / `console_report` / `html_report` suites (+
   `patch_apply` edge cases) **before** locking the ratchet, so it
   captures the higher baseline. Bounded scope — not a general
   coverage campaign.

### Explicitly deferred (named, not forgotten)

- **JS quality floor.** `interactive.js` (4,774 LOC — the single
  largest file, reporter-as-IDE engine, only 5 Playwright tests, and
  its `buildPatchData` output is an RFC-6902 contract with
  `patch_apply.py`) is the least-hardened surface. Deferred to its own
  grill because the toolchain choice (ESLint + vitest + c8 vs.
  expanding the in-pytest Playwright net) drags a node ecosystem into a
  `uv`/Python repo and deserves dedicated scoping.
- `cli.py` (26%) and `plot_comparison.py` (11%) coverage — larger,
  lower-ROI (matplotlib rendering is awkward to test).
- Architecture-refactor pass (`improve-codebase-architecture`) — the
  original prompt; intentionally sequenced *after* the safety net.

### As-built (2026-06-05)

Shipped on branch `chore/quality-floor` in 5 commits (config → format →
lint → types → tests → CI). Outcomes vs spec:

- **ruff**: whole-tree format (1 commit, in `.git-blame-ignore-revs`) +
  575 lint findings → 0. Real bugs beyond style: dead code removed
  (`spec_path`, `pos`); 5 B904 exception-chains; and **a latent UI bug** —
  `mode_controls._classify_type` only recognized `typing.Union`, so any
  mode-config field written in PEP-604 `X | None` form silently lost its
  `optional` flag in the reporter. Now accepts `types.UnionType` too.
- **mypy** (tiered: comparison/discovery/storage/config): 22 → 0. Notably
  4 `numpy.bool` values passed where `builtins.bool` expected (would
  serialize oddly into baseline JSON), now `bool()`-wrapped.
- **Re-export hazard**: F401 stripped `comparator.py`'s algorithm-function
  re-exports (a documented facade consumed by modes/tree_eval/tests);
  restored under `# noqa: F401`. Lesson: facade modules need the noqa or
  an `__all__` before an import-cleanup sweep.
- **Coverage**: 58% → **68%** on the hardware-free suite after the
  omit-list. junit_report 0→100%, html_report 0→100%, console_report
  0→97%, patch_apply 77→86%. +30 tests (737 → 767). Ratchet `fail_under
  = 66` in `[tool.coverage.report]`.
- **CI**: `.github/workflows/quality.yml` (lint + format-check + mypy +
  hardware-free pytest + coverage) on push/PR; `.pre-commit-config.yaml`
  for local parity. FMPy e2e (`PHASE_2_5_CI_PLAN.md`) untouched.
- **JS quality floor deferred** to its own grill (interactive.js, 4.7k LOC).
- Follow-up noted: `html_report.py` hard-codes the title "Modelica Test
  Report" — stale post-D81 rename (fixed → "DSTF Test Report").

**CI confirmed green** (2026-06-05): `quality` workflow passes on
`pull_request` in ~30s — all steps (uv sync → ruff lint → ruff format
check → mypy → pytest + 66% coverage ratchet). Took three iterations on
the runner: fixed a PEP-668 `--system` install error (→ `uv sync` +
`uv run`), then two failures the local `-m "not playwright"` filter had
masked — Julia smoke tests that shell out to `dstf run` instead of
self-skipping (→ deselect all backend markers), and a `.gitignore`
artifact pattern (`dslog.txt`) that had been silently excluding a
committed test fixture, so its test could never have passed on a fresh
clone (→ negation + track the fixture). Both genuine bugs CI surfaced
that local runs could not.

## D89 — Full-review fix wave (2026-07-06): 75 findings, 7 batches, verified on TRANSFORM

A whole-codebase review (7 parallel subsystem reviewers + adversarial
spot-verification; method + findings in `CODE_REVIEW_2026-07-06.md`)
surfaced ~75 real bugs the 768-test suite didn't cover. All fixed the
same day in 7 batches, each red-first tested:

- **A — verdict integrity**: "nothing to compare" (empty window/baseline/
  trajectory) now hard-FAILS everywhere (was vacuous PASS or crash);
  clipped/out-of-range checkpoints fail; explicit tolerance 0 honored
  (`is not None` chains); failing baseline-free tests count as FAILED on
  every surface (console exit code, JUnit, dashboard badge, `--rerun
  failed`) via new `TestComparison.evaluated`; ref↔actual pairing by name
  (index fallback for unnamed only); declared FFT peaks claim distinct
  actual peaks above a 1% amplitude floor; declared-event matching is
  optimal interval assignment; unknown mode strings raise.
- **B — config safety**: malformed testing.json raises `ConfigError`
  (was: silently OVERWRITTEN with defaults); documented `tolerance` key
  now actually read; relative work_dir resolves against the config dir.
- **C — discovery**: spec-over-annotation merge uses None-sentinels +
  `finalize_defaults()` (a spec value equal to the default no longer
  loses to the annotation); comment/string-stripped .mo parsing with
  balanced-paren experiment capture; RFC 6902 add-at-index inserts;
  duplicate spec entries: first-wins + warning everywhere.
- **D — worker pool/backends**: restart-exhausted workers exit the
  dispatch loop (was: drained the queue fail-marking everything); worker
  raises recorded, not thread-killing; Dymola batch timeout tree-kills +
  salvages completed tests (was: could hang forever on dymosim);
  `TimeoutExpired.stdout` bytes decoded; Julia drivers check retcode +
  final time (diverged solves no longer become baselines) + stale-
  definition poison guard; mat_reader refuses binNormal, guards col-0.
- **E — reference store**: `_downsample` never drops first/last/event
  samples (was: silently truncated baseline tails); atomic writes
  everywhere; O_EXCL ID allocation (cross-process safe); cleanup removes
  orphaned role dirs; role names validated; NaN/Inf baselines rejected.
- **F — reporter round-trip**: CLI verdicts authoritative on load with a
  decimation banner (was: silently re-scored from LTTB data); deep-copied
  editor state so in-place tube/point edits actually export; `'abs'`
  token unified; JS scorers now mirror Python exactly (node-executed
  parity harness, 10 → 34 cases); `spec-update` materializes implicit
  trees so per-leaf patches on flat-override tests round-trip;
  comparison_data.json is strict JSON.
- **G — CLI**: `-i`/`--rerun` categories validated pre-run; `--accept`
  exit code honest + scoped to the rerun subset; `-i`+`--accept`
  mutually exclusive; `fnmatchcase`; dot-boundary `--package`; atomic +
  quarantining batch manifest; schema export wires mode `$defs` via
  if/then `$ref`s. Bonus find: `-i` had been crashing outright on a
  stale `dymola.mat_reader` import since the common/ refactor.

Suite: 768 → **985 passed** (+217 regression tests in
`tests/test_review_fixes_*.py`), ruff + tiered mypy clean. Verified
end-to-end on TRANSFORM-Library (native Linux Dymola 2026x, 4 persistent
workers, from WSL): 326/326 simulated, 321/326 compare-passed, exit 1.
The 5 failures are real model drift vs old-Dymola baselines (structural
warnings corroborate: IRIS nonlinear 3→11 / events 13→16, pumps events
20→7) — owner adjudication needed, not framework bugs.

**Next steps** (recorded 2026-07-06):
1. Adjudicate the 5 TRANSFORM failures: `dstf --config
   TRANSFORM-UnitTests/ReferenceResults/testing.json run --rerun failed
   --report` (or `-i failed`) → re-accept or investigate.
2. Execute the .jl driver changes on a Julia-equipped machine — retcode
   guard, poison sentinel, json_escape are text-pinned only here.
3. Run the Playwright suite on a browser-equipped machine —
   interactive.js changed substantially (node parity harness covers the
   scorers, not the editors' DOM behavior).
4. Delete the buildingspy leftovers in TRANSFORM-UnitTests per its
   TODO-cleanup.md once confirmed unused.
5. Consider `jsonschema` as a dev extra to un-gate the schema-validation
   test; consider a Windows-partition run to exercise `<os>=windows`.


## D90 — Run-provenance metadata on reports (2026-07-07): which backend/version produced the results

**Trigger.** The 5 TRANSFORM drift failures (D89) reproduce on *both* WSL
Dymola 2026x and Windows Dymola — so they're version/model, not
platform. But the report showed *what* the verdicts were and never *what
produced them*: no backend, no simulator version, no OS anywhere in
`status.json`, the dashboard, or the per-test report. The backend/OS
existed only as the `<Backend>/<os>/` storage path, never surfaced. That
made "did the tool change (Dymola 2025x→2026x, MSL bump)?" a fresh
investigation each time. This is the prerequisite for the drift
differential: stamp each run so a 2025x re-run and a 2026x baseline are
unambiguous side by side.

**Design.** New `RunMetadata` (`simulators/run_metadata.py`):
`backend` · `simulator` (the configured label) · `os` · `tool_version`
(actual, tool-reported) · `dstf_version` · `generated_at`. Built once per
run from `Config` and serialized into `status.json` under a `metadata`
key; the reporters read it straight back (`build_dashboard_context`
already reads only `status.json`, so the dashboard banner came for free).

Two-tier version capture, both **best-effort — provenance must never
abort a run**:
- Config identity (backend/simulator/os/dstf) is always available and is
  the reliable floor.
- Actual `tool_version` is the gold standard when obtainable. Batch/
  subprocess backends report eagerly via `describe_tool_version()`
  (FMPy → `fmpy.__version__`, Python → the *configured* interpreter's
  `--version`). Persistent backends can't name their version until a
  worker is live, so they leave it `None` and the `run_tests` template
  patches it in after startup via `_probe_worker_version(live_workers)`
  → `progress.update_metadata(tool_version=...)`. Dymola:
  `ExecuteCommand("DymolaVersion()")`; OpenModelica: `getVersion()`
  (wire-string unwrap); Julia: `julia --version`. Every probe is wrapped
  (`_safe_tool_version` / `_safe_probe_worker_version`) so a failure
  degrades to the configured label, never an aborted run.

**Library (MSL) versions.** The user's *second* drift suspect was an MSL
bump, so the persistent template also captures loaded-library versions
alongside the tool version via a parallel `_probe_library_versions`
hook, stamped into `RunMetadata.library_versions` (e.g. `{"Modelica":
"4.1.0"}`) and rendered as a `· MSL 4.1.0` segment in the banner.
OpenModelica uses `getVersion(Modelica)` (after an idempotent
`loadModel(Modelica)`). Dymola exposes **no** scripting getter for a
library's version (probed empirically: `getVersion`/`getModelicaVersion`/
`DefaultModelicaVersion`/`getDependentLibraries` all fail or return names
only, and `getClassText` omits the top-level `version=` annotation) — so
we read the `DYMOLA` install-root env var it sets on both platforms
(`getEnvironmentVariable("DYMOLA")` → `[root, exists]`) and glob
`$DYMOLA/Modelica/Library/Modelica X.Y.Z/`, picking the newest by
dotted-int. Best-effort → `{}` on any failure.

**Surfaces.** Banner on `dashboard.html` (under the title) + per-test
`interactive.html` (threaded through `generate_report_suite` →
`_read_run_metadata` → `generate_comparison_plots`), plus a console
`Backend: …` header line. Banner shows the configured label and, when the
reported version differs, appends it in parens — e.g.
`Dymola (Dymola Version 2026x, 2025-10-10) · linux · DSTF 0.1.0`.
Backward compatible: pre-existing `status.json` without `metadata` → no
banner (the `{% if run_metadata %}` guard keeps the interactive
structural-hash snapshot stable).

**Verified end-to-end (live, this box):** Python →
`Python 3.12.12`; Dymola 2026x → `Dymola Version 2026x, 2025-10-10`
(the exact string the 2025x/2026x differential needs — includes build
date) **+ MSL 4.1.0**; OpenModelica → `OpenModelica
1.27.0~dev.beta.3-2-g3d4d539` **+ MSL 4.1.0** (confirms both the
getVersion wire-unwrap and getVersion(Modelica)). The full 5-test
TRANSFORM drift set was re-run under 2026x here (reference half of the
differential): all 5 FAIL vs the old baselines, banner stamped `Dymola
(Dymola Version 2026x, 2025-10-10) · MSL 4.1.0`, rasterized + eyeballed.
Pumps + MSLFluid drift 0.5–0.7 NRMSE; the two IRIS models drift 0.035 by
the *identical* amount (one shared component). Julia/FMPy probes are
unit-tested at the parse layer only (no Julia executor / FMUs here).
Suite 985 → **1033 passed** / 23 skipped (+48 tests across
`test_run_metadata`, `test_tool_version`, and additions to
`test_progress_reporter` / `test_dashboard_render` /
`test_review_fixes_reporter`), ruff + mypy clean, coverage ~78% (floor
66).

**Still open** (does not block the feature): the 5 TRANSFORM drift
failures still await adjudication — the differential is now set up: the
2026x reference half is captured + version-stamped here; run the 2025x
Refresh 1 half on Windows and compare the two `Dymola`/`MSL`-stamped
reports. If 2025x ships the same MSL and passes → 2026x-version effect;
if MSL differs → the MSL bump is the likely cause; if both fail with the
same MSL → a model change predating both.


## D91 — `.mo` literal-stripper rewritten single-pass: `//` inside a string desynced parsing (2026-07-07)

Surfaced while adjudicating the D90 TRANSFORM drift differential.
`MSLFluid.InverseParameterization` "drifted" — but the real cause was
that DSTF simulated it to **1.0 s instead of its `experiment(StopTime=10)`**
(the editor ran the full 10 s). Root cause: `_strip_modelica_literals`
(`discovery/mo_parser.py`) blanked comments + strings as **three
independent regex passes in sequence** — block-comment, then line-comment
(`//[^\n]*`), then string. That is unsound: the languages interleave. The
`__Dymola_Commands(... ="modelica://…/plotResults.mos")` URL sits **before**
the experiment annotation, and the line-comment pass ate the `//` inside
that string literal, blanking the string's closing quote. Every following
string then parsed off-by-one-quote, so the real `experiment(StopTime=10)`
fell inside a phantom string and was blanked → `_find_experiment_span`
returned `None` → StopTime silently defaulted to 1.0.

This is a D89 (finding 48) follow-on: that change *introduced* the
stripper to stop comments shadowing annotations, but the 3-pass form was
context-blind. Fixed by replacing it with a **single-pass lexer** that
tracks one of code / string / line-comment / block-comment at each
position and blanks accordingly (escaped `\"` handled; Modelica block
comments don't nest). Same offset-preserving contract (1:1 length,
newlines kept) that findings 48/50 rely on.

Impact beyond the one test: **any** model with a `//` inside a string
literal (ubiquitous — `modelica://` URIs in `__Dymola_Commands`,
`Documentation`, `img src`) *before* an annotation the scanner needs
(experiment / UnitTests / within) could be silently mis-parsed. The
symptom is quiet (truncated sim, default StopTime), so other TRANSFORM
tests may have been affected — a full re-run is the way to tell.

Verified: 3 red-first regression tests (`//`-in-string, `"`-in-comment,
`/*`-in-string) in `TestFinding48StrippedParsing`; the real
InverseParameterization.mo now parses StopTime=10.0; end-to-end under
Dymola 2026x it runs the full 10 s and **PASSES** (worst NRMSE 2.4e-07,
was a spurious FAIL). Suite 1033 → **1036**, discovery suite 116/116,
ruff + mypy clean. So of the 5 D90 "drifts", InverseParameterization was
never real — it was this parser bug.


## D92 — Sim-failure without a baseline was masked as NO_REF (2026-07-07)

Surfaced running OpenModelica over TRANSFORM (D90 curiosity run): 91/329
models fail to simulate under OM, but the dashboard showed `SIM_FAIL 0 /
NO_REF 94` — hiding every failure as "no baseline." Root cause in
`compare_all` (`comparison/comparator.py`): the no-baseline short-circuit
(`reference is None → NO_REF, passed=True`) ran BEFORE any sim-success
check. The `result is None` guard above it only catches backends that OMIT
a failed test (Dymola: no `.mat` → absent from results). OpenModelica's
`read_result` instead returns a `success=False` TestResult for a missing
`.mat`, which is NOT None — so it fell through to the no-baseline branch
and every OM compile/solver failure on a model without an OM baseline was
mislabeled NO_REF with `passed=True`.

Fix: add a `if not result.success:` → SIM_FAIL branch in `compare_all`
BEFORE the no-baseline short-circuit (mirrors `compare_test`'s own line-152
guard, which the short-circuit was pre-empting). +3 red-first tests
(fail-no-baseline → SIM_FAIL; success-no-baseline still NO_REF; missing
result still SIM_FAIL). Suite 1036 → **1039**, ruff + mypy clean. OM/
TRANSFORM report corrected to `221 PASS / 14 FAIL / 87 SIM_FAIL / 7 NO_REF`.

Related-but-separate (logged, not fixed here): `dstf compare` reads
results from disk and `BatchManifest.save()` doesn't persist the run's
success flags, so a test the run marked failed but which left a partial
`.mat` reads back as success (why compare shows 87 SIM_FAIL vs the run's
91). A fresh `dstf run` labels all 91 correctly. Fixing that needs the
manifest to persist per-test success — deferred.


## D93 — `compare --accept` (accept without re-simulating) + status.json verdict guard (2026-07-08)

Owner ask: accepting the results you just ran shouldn't require a full
re-simulation (a ~44-min round-trip on TRANSFORM). `run --accept` was the
only path.

`compare` already reads the last run's on-disk results (`read_last_results`)
and `accept_results` is defensive (skips `success=False`, skips missing
results, `store_reference` rejects NaN/Inf). So added `--accept` to the
`compare` subcommand — terminal, mirroring `run --accept` (finding 30:
partial accept exits nonzero). Verified end-to-end on the Python example:
run (no baselines → NO_REF) → `compare --accept` READS 3 result files (no
sim) + stores → `compare` → 3/3 PASS.

The one hazard: the on-disk read can be fooled by a partial/stale `.mat`
into reporting success for a test the run actually marked failed — which
would let `compare --accept` bless a garbage baseline (the D92-deferred
footgun; the manifest doesn't persist per-test success). Rather than change
the manifest format, `read_last_results` now consults the run's authoritative
`status.json` and forces any test recorded failed/timed_out back to
`success=False` (`_apply_status_verdicts`, no-op on legacy work dirs without
status.json). That both guards accept AND fixes the deferred verdict gap:
the OM/TRANSFORM `compare` now reports `221/14/91 SIM_FAIL/3 NO_REF` (was
`.../87/7` — the 4 partial-`.mat` tests), matching a fresh `run`.

+6 tests (`test_compare_accept.py`). Suite **1044**, ruff + mypy clean.
Follow-on still open: persisting run results in the manifest would make the
status.json cross-check unnecessary and is the cleaner long-term fix.

## D94 — Data-integrity trio: frozen-JSON companion self-overwrite, corrupt-spec wipe, last non-atomic writers (2026-08-31)

Pre-v1.0 review (see the plan doc referenced in the session memory) found two
data-loss bugs, both untested. (1) `freeze_companion` for JSON companions
copied the data to `<name>.json` — the exact path the metadata write then
replaced, destroying the frozen data (`get_companions` even documented a
`.data.json` scheme that was never implemented). Fixed: frozen JSON data now
lives at `<name>.data.json` (`_frozen_data_basename`); `get_companions` skips
the sibling; `add_companion` rejects names ending `.data` so a companion's
metadata can never occupy another's data path. No shim for previously frozen
JSON companions — their data was already destroyed by the bug; the reader
degrades to "invalid" without crashing. (2) The three spec load-modify-write
helpers swallowed `JSONDecodeError` and wrote `{"tests": []}` over a corrupt
`test_spec.json` — one typo in a hand-authored spec (a promised seam) and the
file was gone. Fixed: shared `_load_spec_for_edit` raises `ConfigError`
(caught cleanly in `cli.main`) and writes nothing; writes go through
`_atomic_write_text`, as do `export_csv` and `apply_patch` (the last
non-atomic writers). `_atomic_write_text` gained a `newline` passthrough so
csv's embedded `\r\n` isn't doubled on Windows; a raw-bytes assertion pins
that, since `csv.reader` can't see it on Linux. Review notes for later: move
`_atomic_write_text` to a neutral util module during the storage split (the
discovery→storage import is a latent cycle), and a pinning test for the
legacy corrupted-frozen shape would be nice. +14 tests, 8 failing on pre-fix
source.

## D95 — Explicit tolerance 0 (and any finite value) honored end-to-end (2026-08-31)

The comparator deliberately honors an explicit tolerance of 0 as the
strictest setting (is-not-None chains, D89-era finding 7), but two entry
points silently dropped it: `_build_config` gated `--tolerance` on
truthiness, and the reporter's `_paramNumber`/`getGlobalTolerance`/
`getDefaultTolerance` treated `> 0` as "set" — so typing 0 in the browser
kept the pill scoring at the old tolerance while the exported patch carried
0, which the CLI then honored. Divergence class: pill disagrees with
`dstf run` on the same spec. Fix: CLI uses `is not None`; the JS helpers pass
ANY finite number through (adversarial review caught that clamping negatives
would recreate the same divergence — a typo'd `-0.01` must fail loudly in
the pill exactly as the CLI would), with unset/cleared (`null`/`''`/blank)
still falling back — the guard matters because `Number(null) === 0` would
otherwise turn a cleared field into "strictest". Per-item authoring
validators (event tolerance cell, points-table tolerance) accept 0, reject
negatives visibly. Parity fixtures gained a `tolerance_used` override so
zero/negative cases are non-vacuous (the fallback would otherwise equal the
tested value), + 3 parity rows and 2 CLI tests.

## D96 — Persistent-worker lifecycle: tree kill, rescue accounting, pinned interfaces (2026-08-31)

Four fixes from the pre-v1.0 backend review, all in the persistent-worker
watchdog path. (1) The per-test timeout kill killed only the tracked
dymola/omc PID — the name filter would have excluded `dymosim`/the compiled
model binary anyway, and children were never enumerated — so the process
actually simulating survived, burning CPU and holding result files open
(Windows rmtree PermissionError on the next run). The batch runner had fixed
exactly this (finding 22); the persistent path hadn't. Now both workers
delegate to a shared `common/proc_kill.kill_process_tree` (children first;
`name_hint` guards only the tracked parent against PID reuse; never raises —
an exception here would misreport the timeout as a worker exception). Marked
DEBT: children resolve from the live parent, so a parent that died on its own
leaves orphans unreachable — the child-pid snapshot belongs in the Phase C
watchdog hoist. Coverage includes one real-process test (spawn parent+child,
assert both die); fakes pin ordering and guards. (2) An OM disk-rescued
timeout left `worker_killed=False`, so the (benign) restart charged one of
the worker's 3 strikes — genuine deaths then hit exhaustion earlier. NOTE the
plan's original framing overstated this as a direct cascade; rescued
successes reset the budget (`base.py` reset on success), so the defect was
strike-creep, not immediate exhaustion. Now mirrors Dymola. The OM
worker-EXCEPTION path deliberately stays uncounted-as-benign — a worker that
keeps raising should consume its budget (matches Dymola's rationale). (3)
`run_test` in both workers pins the interface handle at entry, and the
Dymola broken-worker probe's `self.close()` is guarded by `self.dymola is
dymola` — an abandoned watchdog thread could otherwise RPC against, or
close, the RESTARTED worker mid-next-test (the "Mismatch request/response
ID" poisoning). (4) Julia `start()` recreates the stdout queue so a killed
worker's stale "ready" pulse can't satisfy the new worker's handshake
(stderr buffer deliberately kept — diagnostically useful); batch-Dymola
`run_tests` now stamps run metadata into ProgressReporter/status.json like
the base paths (third copy of that prologue — collapse target for the Phase
C dedup). +10 tests, 9 verified failing pre-fix.

## D97 — Browser test lane recovered: D88 autofix had silently broken it (2026-08-31)

Installing Playwright to verify D98's JS work revealed the browser lane had
been broken since D88: the ruff F401 autofix (c41be89) stripped
`playwright_browser` from the per-mode files' imports — a pytest fixture
import is invisible to pyflakes because only the parameter name appears in
tests — and every environment since lacked Playwright, so 51 of the browser
tests errored at setup with "fixture not found" and nobody saw it. Fixes:
(1) the fixture now lives in tests/conftest.py (lazy playwright import;
conftest placement cannot be autofix-stripped), local copies deleted. (2)
The documented `-m "not playwright"` incantation had NEVER worked — no
`playwright` marker existed, so the filter was a no-op and the real gate was
module-level importorskip. The marker is now registered and applied
(module-level pytestmark on the four browser files; per-test on the parity
suite's one browser test; test_interactive_html_snapshot stays unmarked —
it is headless), so `-m "not playwright"` deselects 121 browser tests and
`-m playwright` runs exactly them. (3) One stale test surfaced by the
revived lane: test_points_scorer_window_clips_points asserted that a leaf
window silently drops out-of-window declared checkpoints — the pre-parity
JS behavior. Python's authoritative rule (verified directly): a ref-relative
checkpoint outside the window-sliced reference range FAILS loudly
("checkpoint time outside reference range", the anti-clamp rule, review
2026-07-06 finding 2). Renamed to
test_points_scorer_window_excluded_checkpoint_fails_loudly with the
assertion inverted. Lanes after: 1076 passed hardware-free, 121 passed in
the browser lane. Lesson recorded in the conftest docstring: fixture
imports between test modules are an autofix hazard; fixtures shared across
files belong in conftest.

## D98 — Reporter trust batch: six pill-vs-CLI divergences closed (2026-08-31)

The reporter's contract is that the live pill agrees with `dstf run`; the
pre-v1.0 review found six places it could lie. (1) The dominant-frequency
live scorer FFTs the LTTB-decimated embedded arrays while the CLI FFTs full
resolution — different pow-2 bin grids whenever a trajectory exceeds
max_embedded_samples, so a high-frequency peak could fail live yet pass on
the CLI (both sides' comments claimed bit-identical parity). Chosen fix:
decimation-aware CLI-authoritative badge — plot_comparison marks a
dominant-frequency leaf cli_authoritative iff its variable's embed was
decimated (JS reads the same full_samples stamp the decimation banner
uses; recomputePassStates skips gated leaves so the pill keeps the CLI
verdict). Undecimated variables keep exact live scoring. ORDER CONSTRAINT
commented at the call site: the mark runs after the comparison_data.json
write — the sidecar carries full resolution, so its tree deliberately keeps
cli_authoritative=false. (2) The event-timing live Match column used greedy
nearest-neighbor pairing while Python uses optimal interval matching
(right-endpoint sort, smallest unclaimed wins — the D89-wave fix); the JS
comment claiming "same pairing rule" was false. Ported. (3) Shift+click
add-point cancelled on >1e-10 DATA-unit mouse drift — trackpad jitter made
it a no-op; now a 5-screen-pixel slop from the raw event coords. (4) Tube
with tube_width_mode unset: scorer assumed 'rel', plot drew
max(abs, rel·|ref|) — drawn band and scored band differed; one shared
DEFAULT_TUBE_WIDTH_MODE now feeds scorer, plot, and editor (known residual:
'abs' without tube_points draws a band while both sides fail the leaf —
verdict-agreeing). (5) Malformed passthrough JSON was stored as a raw
string and exported into the spec with no visible error; now refused with
the textarea marked invalid, last-good value kept. (6) A structural export
froze inherited defaults (config-level tolerance) into per-leaf params via
nodeToSpec, silently detaching those leaves from config.tolerance; params
are now exported only if spec-authored or user-edited (shared _paramEdited
backs both the structural strip and the granular patch diff — ops verified
identical pre/post refactor). Coverage: Node-harness trust test for 2-6,
4 Python tests for the marking rule including one end-to-end through the
real _build_template_context (review finding: hand-built contexts can
drift from _augment_tree_view's real shape), and the revived browser lane
(D97) exercising the whole surface in Chromium. Remaining accepted debt:
no automated JS test pins the recomputePassStates gate itself
(manually verified in review); un-augmented leaves (spec/eval drift
guard) keep live scoring as before.

## D99 — Small-fixes batch: ID high-water mark, parser typing, guard scoping, schema gaps (2026-08-31)

Five pre-v1.0 review items. (1) "IDs never reused" only held while ref files
existed: `manifest cleanup` deleting the highest obsolete ref let
`next_id() = max(scan)+1` re-allocate that ID to a different model,
silently re-pointing external artifacts keyed on ref_NNNN. Each partition
now persists `.id_high_water.json` (additive dot-file, invisible to every
`ref_*.json` glob — the layout seam is unbroken; absent file → scan-only,
old partitions keep working; write failure warns, never fails an accept).
Adversarial review caught that a read-check-then-write bump is
last-writer-wins and could REGRESS the mark under two racing `--accept`
processes — and a write-verify-retry does not close it (the higher writer
can verify before the lower writer's overwrite). Robust form: every bump
folds in a fresh disk scan; since register() O_EXCL-claims the ref file
BEFORE bumping, any mark a slow writer could clobber corresponds to a ref
already visible on disk, so writing max(claimed, scan) makes the race
harmless. (2) mo_parser's errorExpected still used the truncating
number-class regex finding 56 removed elsewhere (`1e-3*2` → silently
`1e-3`); now `_parse_number` + warn + default. json_recognizer typed its
numeric fields: non-numeric strings destined for float fields warn + stay
unset (annotation/spec layers can still fill them) instead of pushing a
raw string into runners; bool is rejected (float(True)==1.0 defeated the
naive guard); int fields (n_vars, number_of_intervals) get an
exact-integer check. (3) migrate-baselines' role-dir skip was a
posix-substring test — inert on Windows paths; now path components
RELATIVE to the reference root (absolute parts would also match a
role-named directory ABOVE the root and silently skip everything). (4)
export-schema gained number_of_intervals / output_interval and the
canonical `source` key (fmu documented as legacy alias, precedence noted).
(5) Leaf/comparison count mismatch in the reporter now logs instead of
silently dropping augmentation; read_mat_time_extents refuses binNormal
layouts like its siblings. +19 tests.

## D100 — OM success path refuses partial results; omc failure modes verified live (2026-08-31)

Empirical grounding first (omc 1.27.0-dev, WSL): both canonical mid-run
failure modes — an assert() violation and an integrator failure
(DASSL error-test cascade at a singularity) — leave a PARTIAL result_res.mat
on disk but return an EMPTY resultFile in the SimulationResult record, so
the parser's `bool(result_file)` clause already fails them; and neither
produces the capital-F "Failed" the log parser's case-sensitive substring
looks for (that clause's provenance is unknown; its live risk is actually
the inverse — a benign warning containing "Failed to ..." could fail a
successful run — left for the cleanup pass). The gap that remains is a
success-reporting record alongside a truncated trajectory (future omc
behavior, torn write). Following the D93 accept-integrity precedent (a
partial .mat blessed by --accept poisons a baseline), both OM success
paths (batch + persistent) now verify the trajectory reached stop_time via
a shared `mat_reaches_time` helper in common/mat_reader — the same
rule + tolerance the persistent disk rescue already used, deduped onto the
helper so the two paths cannot disagree about the same file. Refusal is
loud: "partial result refused" with the reached-vs-requested times. One
batch-test fixture that staged a one-byte garbage .mat with a success
record now correctly fails the gate and was upgraded to a real fixture
mat. +3 tests.

## D101 — Metric + combinator registries: the Metric-layer extension seam is real (2026-08-31)

`docs/extensibility.md` promises a registry per layer; before this,
only backends and recognizers had one — metrics and combinators were closed
if/elif chains duplicated across four files (resolve_mode, VALID_METRICS /
_MODE_TO_METRIC in tree_spec, _build_combinator / _METRIC_TO_MODE_KEY in
tree_eval, schema_export's mode list), so "adding a metric" meant editing
all of them. Now `comparison/registry.py` holds MetricDescriptor (name,
config_cls, build, accepted_datasets, score_direction) and
CombinatorDescriptor; builtins self-register on package import (idempotent
under importlib.reload); spec parsing, evaluation, resolve_mode's dispatch
and error text, the auto-derived report controls, and export-schema all
derive from it. The four duplicated name sources are deleted. Behavioral
equivalence for the six builtins was verified by adversarial review down
to a byte-identical exported schema against the pre-change commit and a
byte-identical unknown-metric error message. A toy third-party metric in
tests/test_metric_registry.py proves the seam end-to-end (parse → evaluate
→ derived controls → exported schema) with zero framework edits; the
negative half is pinned too (unregistered → parse rejects).

Review-driven decisions folded in: `has_plot_editor`/`_UI_EXTRAS` deleted
(unconsumed, and wrong for two of five modes — JS owns that fact);
`mode_cls` deleted from the descriptor (unread; `build` closes over the
class); `score_direction` made load-bearing immediately by pulling the
planned W-4 forward — the validator now REJECTS a `weighted` node whose
leaf children mix directions (summing lower-is-better NRMSE with
higher-is-better tube fraction against one threshold is meaningless). The reporter's
CLI-authoritative badge rule generalized from `mode == "event-timing"` to
`mode not in _JS_SCORED_METRICS`, so a Python-registered metric is badged
honestly instead of silently not re-scoring. Dataset-incompatibility
hard-fails at EVALUATION time as a labeled leaf (extensibility.md's
"fails at config resolution" promise corrected — the kind is only known
once a backend produced data); both hard-fail sentinels now carry the
leaf's real metric in `mode`. extensibility.md's Metric/Combinator
sections rewritten to the shipped API (the old @register class sketch
never existed), including the JS boundary: live re-scoring and the
add-leaf dropdown are template-JS surfaces, so third-party metrics are
CLI-authoritative in the report. Known limits recorded there. +32 tests.

## D102 — Entry-point plug-ins; Capability flags made load-bearing (2026-08-31)

Two halves of the same promise ("plug-ins install alongside DSTF as a
separate package; the framework gates on declarations, not class names").
(1) `dstf.plugins.load_plugins()` loads the single entry-point group
`dstf.plugins` (one group, not one per layer — a plug-in package
routinely ships a backend AND its metrics); module entries import,
callable entries are invoked, a CLASS entry is rejected with guidance
(instantiating it with no args is never what the author meant). Loaded in
`cli.main` before dispatch and exported as `dstf.load_plugins` for
library use. A broken plug-in is a HARD ConfigError naming the entry
point — a metric that quietly failed to register changes scoring
semantics. Per-entry completion tracking so a caught-and-retried load
cannot re-invoke the healthy plug-ins and blame them for duplicate
registration (review finding). `plugins.py` joined the mypy tier.
(2) Capability flags were validated at registration but consulted
nowhere; now `_get_runner` gates persistent dispatch on
PERSISTENT_WORKERS, the cross-backend chain gates on FMU_EXPORT (before
creating its work dir) instead of catching NotImplementedError, and
`produced_datasets` threads into comparison as the evaluator's
dataset_kind. Honesty fixes that fell out: batch `DymolaRunner` no longer
claims FMU_EXPORT (its implementation was a raising stub — the flag and
the implementation live on `PersistentDymolaRunner`, declared explicitly
rather than inherited so the batch class's BATCH_FALLBACK isn't a false
claim there either); `Capability.EXPERIMENT_INGEST` deleted (declared and
consulted by nothing); dead `TestModel.requested_fmu_export` and
`BatchManifest.batch_id` deleted (batch_id was never persisted — old
manifests load unchanged). Capability-vs-hook biconditional pinned per
backend by iterating the registry (not a hardcoded five-name list), so a
sixth backend is covered automatically. Docs: extensibility.md gained the
real plug-in packaging section; vision.md swept for the deleted names.
+15 tests. Both suites + lint/format/mypy green.

## D103 — Demo aligned to the documented flagship example; import-baseline arg-shadowing bug (2026-08-31)

MetricTreeTest is specified as "an NRMSE bound on the
start-up transient and a tube on the steady-state window against the
primary, plus a warn-wrapped leaf scoring the same trajectory against an
OpenModelica soft_check" — but the shipped demo was and[nrmse(x),
warn(nrmse(y))] on sin/cos: no windows, no tube, no soft_check anywhere in
the example tree, and a pure sinusoid has no transient/steady-state to
window. Fixed by making the demo BE that example: MetricTreeTest is
now a first-order step response (tau=0.5, settled ~2.5s) feeding a slower
lag; its tree is and[nrmse(x, window [0,2.5]), tube(x, rel 2%, window
[2.5,10]), warn(nrmse(x, against=openmodelica))]. The OM result is
imported as soft_check "openmodelica" into Dymola/linux AND
OpenModelica/linux (on OM it compares OM to itself — trivially green, but
the shared spec then shows no spurious "baseline not found" warning on
either backend; the run path never validates roles, only spec-update does,
so a missing name would have been warn-absorbed rather than fatal).
Verified end-to-end on live simulators: 12/12 pass on BOTH omc 1.27 and
Dymola 2026x, and the evaluated tree shows the warn leaf scoring a real
cross-simulator NRMSE (1.1e-6), not a sentinel. Dymola/windows could not
be regenerated from this machine: its stale MetricTreeTest primary is set
status="skip" (the designed exclusion) with the soft_check staged; Phase S
re-accepts it on a Windows box.

Also closed the coverage gap ("every metric and every combinator"):
the demo spec now exercises or (IntervalTest: tight nrmse OR loose tube),
k-of-n (SimpleTest, k=1 of 2), weighted (ConstantTest, direction-uniform
nrmse pair — the D101 validator forbids mixing), and event-timing
(EventTest's stepped output, auto-detected events, 10ms tolerance) —
every metric and combinator now runs in the bundled demo on both backends.

Bug found live: `dstf import-baseline` was unusable with a config — its
positional was NAMED source_path, colliding with the global --source-path,
so _build_config treated the ref JSON as the library root and
find_package_dir iterdir()'d a file (raw NotADirectoryError). Positional
renamed to import_source (metavar keeps the CLI face); regression test
pins that the global stays None. The raw-traceback UX for such errors is
the known Phase C item.

## D104 — Authored windows now pre-fill the reporter's window inputs (2026-08-31)

Eyeballing the D103 demo report showed the window inputs empty on leaves
whose spec authored windows (the tube band on the plot honored them, so
scoring was correct — only the input seeding was broken). Root cause: the
extractor read `vc.diagnostics["window"]`, but the evaluator stashes the
window on the MetricResult, never the VariableComparison — so
`window_values` had been empty for every authored window since windows
shipped, masked by the browser fixtures hand-setting `window_values`.
Seeding now comes from the authored `window` on the spec view (already
correct there); the dead extractor is deleted. Regression test drives the
real `_build_template_context` and asserts values mirror the authored
window; re-render verified visually (2.5 boundaries shown, open sides as
placeholders). Lesson repeated from D97: fixtures that hand-set what the
pipeline should produce hide exactly these seams.

## D105 — Reporter authors `against`: baseline selector in the add-leaf modal (2026-08-31)

`against` was display-only in the reporter: the label and the patch export
handled it, but every new leaf was hardcoded to primary — so a patch
adding a leaf with `"against": "openmodelica"`) could not actually
be produced in the reporter (F-12/W-7). Now the template injects the
test's baseline roster as `OVERLAY_ROWS` (the `overlay_rows` context the
report already carried) and the add-leaf modal shows an "Against" select
— primary plus the unique soft_check names — only when soft_checks exist,
so the modal is unchanged for the common case. Companions are never
offered (unscorable, per the validator); every *registered* soft_check is
offered regardless of report-side load status or per-variable coverage —
registered names are legal targets and the CLI evaluation is the
authority. Picking a non-primary name with no `warn` ancestor on the
target node shows a non-blocking notice that spec-update's validator will
reject the patch unless the leaf is warn-wrapped; the JS ancestor walk
reproduces the validator's `in_warn` propagation (the target combinator
itself counts).

Honesty fix that fell out on both sides: a leaf with `against != primary`
is now CLI-authoritative — the JS live scorers only have the primary
trajectory arrays, so re-scoring such a leaf silently compared against
the wrong baseline. That divergence was pre-existing for CLI-provided
against-leaves (the D103 demo's warn leaf); `_augment_tree_view` and
`_leafIsCliAuthoritative` both gate on it now, without disturbing D101's
metric rule or D98's decimation flip (verified by fresh-context review).
+2 hardware-free tests (real `_build_template_context`), +5 browser tests
(selector presence/absence, companion exclusion, patch round-trip with
primary omitted, warn notice). Known limits recorded, not widened here:
adding any child INTO an existing `warn` violates its exactly-one-child
arity and the modal is silent about it (pre-existing — the add button
predates this change), and plot-side contributions (tube envelope etc.)
still draw against primary for an against-leaf (display-only; the badge
suppresses the scoring dishonesty).

## D106 — `scalar` metric leaf: one number, from a stored statistic or a series reduction (2026-08-31)

Owner-approved for v1.0 as the answer to a review finding: translation
statistics and CPU time were compared only as
structural *warnings*; nothing could hard-fail a test on them. A seventh
built-in metric, `scalar`, scores a single number. Its `quantity` param
has exactly two v1 shapes, chosen so a later `{"expr": ...}` object slots
in as a third without a format break (the owner's ruling: no free-form
expression evaluator in v1): `{"reduce": max|min|mean|final|integral}`
reduces the leaf's tracked series on both sides (window honored, integral
= trapezoid, mean = time-weighted integral/span so a finer output grid
cannot move it — sample mean only when the span is zero); or a bare
dotted string that reads a stored scalar — resolved first in the ref
file's `statistics` block, then as a diagnostics summary (`CPUtime.final`
→ the stored `{final,min,max}` on the baseline side, computed live from
the diagnostic trajectory on the current side; statistics wins a name
collision; a legacy full-array diagnostics entry is summarized on read).
Comparison is abs/rel `tolerance` against the baseline scalar (|ref|=0
falls back to abs and says so), or declared `min_value`/`max_value`
bounds (baseline-free, tolerance ignored). Missing path on either side is
a labeled hard-fail sentinel. A stats-path quantity needs a `metrics`
tree: the flat `variable_overrides` path only has the variable's own
trajectory and now hard-fails such an override instead of quietly
reducing that (second review). `accepted_datasets` = {TIME_SERIES,
SCALARS} — the reserved SCALARS kind is now scorable.

Where the data-source switch lives was the review's one real finding:
the first cut resolved a string `quantity` for ANY leaf as an
evaluator-owned rule "like the window", and a stray `quantity` on an
nrmse/points/range leaf silently collapsed both sides to one identical
sample and passed. The registry now owns it — `MetricDescriptor.
accepts_quantity` (True only for scalar) gates both the evaluator
redirect and the parser's rule that `variable` may be omitted for a
string quantity (it defaults to the path; an explicit `variable` is the
display label). The exported JSON-Schema derives the same rule from the
registry so the two grammars cannot drift.

Reporter: no live JS scorer in v1, so the D101 rule badges scalar leaves
CLI-authoritative; the JS side now derives the add-leaf dropdown from the
registry-fed `MODE_SCHEMAS` (the last hardcoded metric-name list is gone;
Jinja's `tojson` sorts keys, so the dropdown is alphabetical) and badges
any metric without a `MODE_SCORERS` entry, which made the event-timing
special case redundant. Demo: SimpleTest became `and[k-of-n(...),
scalar(final x, abs 1e-3)]` — MetricTreeTest untouched (it is the
flagship example) — restoring "every metric and combinator runs in the
bundled demo", verified live 12/12 on omc 1.27 AND Dymola 2026x with the
leaf scoring from the stored trajectory (no re-accept). Two fresh-context
review rounds (the registry gate, the flat-path guard, schema/parser
agreement pinned by a test that evaluates the EMITTED rule, time-weighted
mean, legacy diagnostics shape on read, sentinel display). +74 tests
(1141 → 1215 hardware-free; 76 browser in the reporter file). Known,
watch-only: a tree `scalar` leaf with no `quantity` raises out of
`resolve_mode` like a tree `range` leaf with no bounds does (closed in
D107).

## D107 — Phase C-i: the three legacy shim families are gone (2026-08-31)

Pre-release stage, an owner ruling, and re-verified seam evidence
(a structural JSON walk over 894 TRANSFORM-UnitTests files and 77 demo
files: zero in-file `baselines` maps, zero `min`/`max` range overrides,
zero symmetric tube points, zero `fmu` spec keys, zero saved flat-dict
exports — the 656 text hits for `min`/`max` in TRANSFORM refs are all
`diagnostics[]` scalar summaries, not overrides). Deleted, each with a
rejection test in its place: (D-1) the flat-dict `spec-update` branch
"preserved for a transition cycle" and `update_test_comparison`, whose
last caller it was — spec-update takes only the RFC 6902 envelope and a
non-envelope file is a ConfigError naming it; (D-2) the in-file
`baselines` map read/write and the `migrate-baselines` command with
`migrate_baselines_tree` — named baselines come ONLY from the store's
`soft_checks/ref_NNNN/` subdir now; a stray `baselines` key still in a
ref file is ignored with one warning naming the file (old data, not old
code — emitted in `ReferenceStore.get_reference`, the only read site that
knows the path) and is dropped on the next accept; (D-3) the `min`/`max`
range shorthand, the symmetric `abs`/`rel` tube-point format (Python
raises naming `upper`/`lower`, the JS scorer returns false where Python
raises, per that file's convention), and the `fmu` spec alias for
`source` (an fmu-only entry warns naming `source`; the schema no longer
documents `fmu`). The FMU demo spec was the only real user of two of the
aliases and was rewritten. `Baseline` stays (soft_checks use it); its
in-memory provenance origin is now `"flat"`, not `"legacy-flat"`.

Structural fix pulled in because the pass raised its stakes: a config
error on a `metrics`-tree leaf (a now-rejected `min`/`max`, a symmetric
tube point, `scalar` without `quantity`) raised out of `resolve_mode` /
`mode.compare` and aborted the ENTIRE `compare_all` with an anonymous
traceback — one stale key would have lost a 331-test run's comparison —
while the flat `variable_overrides` path caught the same error and
failed only that variable. The tree path now does the same: the leaf
becomes a labeled hard-fail sentinel carrying the error, the rest of the
run evaluates. Two consequences the reviews forced: a CONFIG-error
sentinel (flagged `config_error` in its diagnostics — not every error:
an empty-window `_no_data_failure` on a live-scorable metric must stay
live so dragging the window back un-fails it) is CLI-authoritative in
the reporter, because a leaf whose config never built has no
live-scoring state — before this the JS range scorer read absent bounds
as "unbounded" and would have flipped the pill to PASS on the next
re-score; the JS now returns false where Python raises, pinned in the
parity suite (a range leaf freshly added in the modal therefore shows
FAIL until a bound is typed, matching what `dstf run` would do). And the
sentinel's reason is actually shown — console ("Not scored: <error>"),
JUnit, and the interactive leaf (pill tooltip + a muted line: "<mode>
leaf not scored — <error>") — which also upgraded the pre-existing
empty-data failures from "NRMSE inf vs tolerance → FAIL" to the real
reason on every surface. Review also re-pinned the one end-to-end path the pass had left
unguarded (`compare_test` with a store that returns a soft_check and an
`against` leaf — the previous positive test had been swapped for a
negative-only one) and deleted a dead `baselines` fallback in
`overlay_loader`.

Read-only real-library check: `dstf discover` on TRANSFORM-UnitTests
reports 331 tests (313 scan + 18 spec) with no warnings — the plan's 326
is library drift (its HEAD moved 2026-08-11; the discovery path is
untouched here), recorded in memory. Live OpenModelica demo 12/12 with
MetricTreeTest's warn leaf resolving the `openmodelica` soft_check from
the subdir — the only remaining named-baseline path. Four review rounds
(the last, mechanical one verified in the main session: the six golden
HTML hashes were refreshed, and the template's whole diff is the one
`.node-error` CSS rule); every new behavioral guard was mutation-checked
(the guard reverted → its test fails). Suites 1215 → 1215 hardware-free
(−16 tests whose subject was deleted, +16 rejection/sentinel/surface
tests), 76 → 77 browser.

## D108 — Phase C-ii: dead code and stale docstrings swept (2026-08-31)

Plan §3.2 D-4…D-8, behavior-preserving with one deliberate exception.
D-4 had already been finished by D101/D102 (verified; one orphaned
`has_plot_editor` comment block removed). D-5: the reporter's Python-side
plot-contribution stub machinery in `ui/mode_controls.py`
(`PlotContribution`, `custom_renderer`, `contribute_to_plot` and the
`register_mode_ui` kwargs that fed them) had no production caller —
the JS `MODE_PLOT_CONTRIBUTIONS` registry was always the real half —
and is deleted with its four tests; `register_mode_ui` stays as the
explicit-registration seam. D-6: thirteen unreferenced CSS rules and
one dead hidden `plot-spectrum` div gone from `interactive.html`,
`.tube-table` collapsed from three definitions to one preserving the
cascade's effective values (`min-width: 300px` came from the FIRST
block — the implementer read computed styles of the built tube table
before/after via Playwright, identical; review re-derived the cascade
property by property against the three originals and agreed), the
`buildExportData` alias inlined; the six
golden hashes were refreshed with the template diff as evidence, and a
structural diff of all twelve rendered demo reports shows nothing but
the removed div. D-7: eleven docstrings/comments that contradicted
shipped code now describe it — persistent workers are the default and
the CLI ABORTS when one is unavailable (no automatic batch fallback);
the JSON recognizer's "deferred" matchers all shipped; the dashboard
refreshes via `<meta http-equiv="refresh" content="5">` (a JS fetch is
CORS-blocked on `file://`), not a 2 s JS fetch as CLAUDE.md claimed;
`_slice_window` returns empty arrays for a degenerate window (its
redundant `mask.any()` branch is gone); `against` is present tense;
And/Or state what they compute (min/max over child scores) and that
they do not consult `score_direction`; `_scan` reads whole files (lazy
header read is idea #64). Two plan items were NOT stale and were left:
the `<name>.data.json` frozen-companion scheme IS implemented (D94), and
`PersistentRunnerBase`'s docstring makes no fallback claim. D-8: the
identical-branch ternary, the literal-filename glob, the dead
`WeightedCombinator` score guard (now a `cast` directly under the
`any(score is None)` early return that justifies it), and `dstf
export`'s accepted-but-ignored `--filter`/`--package` flags are gone —
TRANSFORM-UnitTests' only `dstf` usage is its README (run / compare /
--accept / --parallel / --report), so the CLI seam is untouched. The one
behavior change: `plot_comparison`'s `max_embedded_samples` default is
1000, agreeing with `Config` (both production callers pass it
explicitly). The `test_review_fixes_*` fold-in waits for the C-iv
restructure. Net −250/+116 lines.

## D109 — `register_mode_ui` deleted (amends D108); `sample_mean` reduction (2026-09-01)

D108 kept `register_mode_ui` as "the explicit-registration seam" while
deleting its siblings. The owner asked what it had been for before
deleting it, so its history was traced: born in Phase 6.1.1 (April) as
the ONLY way a mode got a control panel, and within its first two days
it carried three powers — a custom renderer when the auto-derived form
wasn't enough, a has-plot-editor flag, and registering a UI for a config
dataclass with no metric behind it. D101 derived every registered
metric's controls from its config dataclass (`label`/`help`/`ui_min`/
`ui_max` metadata), which made the renderer redundant, and deleted the
plot-editor flag ("JS owns that fact"); D108 deleted the renderer/
plot-contribution hooks as unconsumed. What was
left was a strict subset of the auto-derivation, and its one residual
power was a defect: a UI-only mode appeared in the add-leaf dropdown
but not in the exported JSON-Schema and could be neither parsed nor
evaluated — a dropdown entry that builds an unrunnable leaf. Owner
ruling: delete it, with `_REGISTRY` and the re-exports;
`registered_modes()` is now exactly the metric registry's names, so the
dropdown, `emit_mode_schemas`, `schema_export` and `tree_spec` all read
one list. The home for "my metric wants different controls than its
fields imply" is the dataclass metadata channel or a new
`MetricDescriptor` field — not a parallel name-keyed dict that can
disagree with the registry. Recorded as an amendment, not slipped in as
cleanup, because it reverses a keep-decision made the day before.

Same ruling added `sample_mean` to the scalar leaf's closed reduction
set beside the time-weighted `mean`: the arithmetic mean of the samples,
for parity with tools that report that; documented in one sentence with
`mean` so the difference is one glance. The closed set is a table
lookup, so the D106 format is untouched.

## D110 — Phase C-iii U-2: the per-worker watchdog is one template method (2026-09-01)

The Dymola and OpenModelica persistent workers each carried a private
copy of the same watchdog — spawn the simulation on a daemon thread,
`join(timeout)`, on timeout close the tool and kill its process tree,
try to rescue a result from disk, account `worker_killed` — plus their
own `close()` and `_kill_tracked_pids()`; Julia had a third, poll-based
shape with an inlined psutil kill; the batch Dymola runner had a fourth
tree-kill copy. The copies had already diverged once (C-6/C-7/C-8, fixed
in D96 on each side separately). Now `Worker` owns the template:
`run_test_with_timeout` and `close` and `_kill_tracked_pids` live once
in `simulators/base.py`, parameterised by hooks — abstract `start` /
`is_alive`; `run_test` (the in-process execution the template runs on
the thread); optional `_evaluate_from_disk` (default None), `_quiet`
(default `nullcontext`; Dymola supplies `_suppress_stderr_noise`),
`_detach_interface` + `_shutdown_interface(handle, grace)` (split so the
handle is None-ed synchronously BEFORE the graceful-quit thread starts —
otherwise `is_alive()` and Dymola's in-run identity guard would race a
slow thread), and class attributes `kill_name_hint` / `timeout_kill_grace`
(Dymola 5.0 so dslog.txt flushes; default 1.0) / `exec_thread_prefix`.
`Worker.__init__` declares `pids` and `_n_restarts`, so the dispatcher's
`getattr` and its two `type: ignore` reads are gone; `_dispatch_with_restart`
keeps its signature and stderr wording byte for byte. Julia overrides
only the deadline-poll body and adopts the shared kill, tracks its pid
in `pids`, and reports `worker_killed=True` on timeout. The proc_kill
DEBT note is resolved: descendants are snapshotted while the parent is
alive (`snapshot_children`: pid + create time, name-guarded) so an
orphan whose parent vanished during the graceful close is still found —
and killed only if the pid's current occupant still has the snapshotted
create time, because a pid recycled during the graceful window can be
another worker's brand-new dymosim (review finding; Windows recycles
freed pids almost immediately). The two workers lost ~120 lines each
(Dymola −121/+15, OM −116/+45); docs/extensibility.md §3 carries the
hook table (abstract / hook / template) and states that
`_detach_interface` is required whenever the worker holds a handle,
since `close()` relies on it to flip `is_alive()`.

The live gate earned its keep: forced timeouts on Dymola and Julia left
zero orphans, but on OpenModelica one `omc` survived — this OMPython
build has no `session.getpid()`, so the OM worker had NEVER had a kill
target on its timeout path (the except clause left `pids` empty; the
dead sessions were only ever finished off by the abandoned graceful-quit
thread). Fixed as part of the pass: the pid now falls back to the
session's process handle, with one warning if neither source yields a
pid. Also surfaced and fixed: after Julia's kill, `proc.poll()` could
still read alive, so the dispatcher handed the dying worker the next
test ("crashed mid-test") and the following restart was charged to the
budget — the timeout branch now waits for the process to exit and
detaches the handle before returning. Adversarial review then found
three defects the live runs could not (they only exercise the kill
landing): closing Julia's stdout pipe from the dispatch thread while
the reader thread holds the buffer lock blocks until the child exits
(measured 7.8 s on an 8 s child; a truly hung julia would have hung the
run) — the timeout branch now keeps HEAD's `proc.kill()` belt-and-braces
before the wait and closes only stdin until the process is actually
reaped; the batch Dymola runner's timeout path had lost its
`proc.kill()` fallback behind the name guard (a non-exec wrapper script
fails the guard and nothing dies) — restored; and the orphan-kill reuse
guard above. Also hardened the contract: `close()` raises `TypeError`
when a backend detaches a handle but never overrode
`_shutdown_interface` (the graceful thread's exceptions were silently
swallowed), and its exceptions are now logged — type and message at
WARNING, the stack at DEBUG, because OM's `quit()` on a mid-simulate
ZMQ socket raises as a matter of course on every timeout and a
12-line traceback per timeout would have violated the "run completes
with a summary, no traceback" gate. And the live gate found one more
hole in the design itself: when a Dymola timeout lands during
TRANSLATION, dymosim does not exist at snapshot time — it is spawned
inside the graceful window by the `simulateModel` the abandoned exec
thread already issued, and is orphaned the instant dymola exits. A
single snapshot before or after the graceful wait cannot see it;
`close()` therefore re-samples the child tree every 0.25 s while the
graceful attempt runs (bounded by `grace` plus at most one tick),
pinned by a test that fails when the sampling interval is set past the
window. A third review pass found the test meant to pin the Julia
pipe-close fix was vacuous (its fake stream's assertion was swallowed
by the very `except` under test) — fixed to record the attempt instead
of raising — and that the sampling loop had overshot `grace` by the
walk time; both closed before commit. +22 tests (1216 → 1238
hardware-free; 77 browser); every new behavioral guard was
mutation-checked. Known, watch-only (same exposure as before the
hoist): the create-time reuse guard fails SAFE if the wall clock is
stepped between snapshot and kill (psutil's own `is_running` shares
it); `_detach_interface` is read-then-clear, so a Dymola broken-worker
probe closing on the exec thread can race the watchdog into a double
close (benign); and using OM's ZMQ socket from two threads on the
timeout path is libzmq-documented UB inherited from HEAD — the socket
is deliberately not closed under a parked `recv()`.

## D111 — Phase C-iii U-1: one `run_tests` prologue (2026-09-01)

Three copies of the run prologue — the batch template
`SimulatorRunner.run_tests`, `PersistentRunnerBase.run_tests`, and the
`.mos`-batch `DymolaRunner.run_tests` — each did the same seven steps
(work dir, run-provenance metadata via `_build_run_metadata(
_safe_tool_version())`, `ProgressReporter`, `assign_test_keys`, per-test
`progress.register` with the ref-id report_dir map, `BatchManifest` +
save) and had already drifted once: C-9 was the batch-Dymola copy
losing the metadata line, so `status.json` stamped `"metadata": null`.
The steps now live once in `SimulatorRunner._prepare_run(tests) ->
RunSetup` (work_dir, total, run_metadata, progress, keyed_tests,
manifest); the batch template rebuilds its 1-based 3-tuples from
`keyed_tests`, the persistent base calls `setup_before_workers()` first
so backend state exists before the provenance probe, and the Dymola
batch runner keeps its mos-generation stages between `_prepare_run` and
its header. `_print_run_summary` moved up to `SimulatorRunner` behind a
`run_summary_phrase` property, so the two batch sites print the same
number set the persistent one always did (wall, total work, speedup);
`n_fail` is `total − ok − timeouts` everywhere (stricter than the old
"not success and not timed out" count only when a result is lost).
Finalize is unguarded everywhere, called on the `RunSetup`'s reporter
(a known non-None reference) — the three `is not None` guards were
covering a case `_prepare_run` makes impossible. Pinned by the C-9 test
(tests/test_run_metadata.py) plus a three-site parity test that drives
the real `run_tests` methods with stubbed execution and compares the
resulting `batch_manifest.json` and `status.json` metadata. Verified
live on all six lanes with stamped metadata: Dymola persistent and
`--batch`, OpenModelica, Julia `--batch`, Python, FMPy (via `uv run
--with fmpy`). Surfaced, pre-existing, not fixed here: the batch
`DymolaRunner` and `JuliaRunner` classes have no `describe_tool_version`,
so their `tool_version` is null — Julia's probe joins the JSON-driver
base in U-4; Dymola's batch probe is a small follow-up.

## D112 — Phase C-iii U-3: one variable-extraction path for Dymola and OM (2026-09-01)

`_compute_needed_variables` and `_extract_variables` were ~100-line
near-identical copies in the Dymola and OpenModelica runners, with two
smaller duplicates in their `read_result` callers (the diag→statistics
fold with its float32→7-significant-digit rule, and the timing-stats
block). They now live once in `simulators/common/result_variables.py`
(`compute_needed_variables` / `extract_variables` /
`fold_diagnostics_into_statistics` / `merge_timing_stats`), with
`diagnostic_vars` a required list — the None-default divergence is
gone. Two deliberate unifications: the missing-requested-variable
warning is loud on BOTH backends now (it was Dymola-only; the OM demo's
SimulateOnlyTest line is now identical across backends), and the timing
block uses OM's non-destructive `setdefault().update()` merge — reading
the Dymola log parser settled that this is a no-op there (it writes
only `translation`/`simulation` categories), while OM's per-phase omc
timings genuinely coexist with the wall summary under the merge.
Pattern-resolved names missing from the output stay silent on both, as
before — only explicitly requested variables warn. Verified live:
12/12 on Dymola persistent and OM with `--accept` into scratch roots
showing the statistics blocks unchanged (Dymola: CPUtime/EventCounter/
simulation/timing/translation; OM: timing only, matching the committed
refs).

## D113 — Phase C-iii U-4: the JSON-driver subprocess base (2026-09-01)

The Julia and Python batch runners were ~85% the same file — the same
driver-script subprocess skeleton (source resolve, per-test dir,
progress phases, timeout mapping, identical `subprocess.run` kwargs,
stdout/stderr artifacts, the rc≠0 error precedence result-file error >
stderr tail > generic, wall-time statistics) differing in the driver
path, the tool's argv flags, message nouns and artifact prefixes. The
skeleton now lives once in `simulators/common/json_driver.py::
JsonDriverRunner`, and each runner is its config plus eight class-level
hooks (`driver_path`, `tool_label`, `action_label`, `stdio_prefix`,
`source_suffix`, `result_filename`, `tool_binary`, `build_command`) —
Julia 354 → 148 lines, Python 385 → 133 — the template for any future
driver backend. Two deliberate changes riding along: Julia's
`read_result` gains the numeric hardening Python already had (a
non-numeric payload value fails the test instead of crashing the run),
and `JuliaRunner` gains `describe_tool_version` (the probe moved off
the persistent class, which now inherits it), so Julia batch runs stamp
a real `tool_version` in `status.json` instead of null — verified live
on Julia batch, Julia persistent and Python lanes. Source resolution is
module-level `resolve_source_file` so the persistent `JuliaWorker`
shares it too. One timing shift on the persistent Julia lane: the
version is now probed synchronously in the prologue (≤15 s before
the header) instead of patched in after worker startup — same value,
earlier.

C-iii acceptance (same day): the full TRANSFORM-UnitTests suite — 331
tests, Dymola/linux, --parallel 4, report-only — ran through the
hoisted watchdog/prologue/extraction/driver paths with ZERO sim
failures, timeouts or crashes: 325 passed, 1 failed, 5 no-baseline.
Both non-pass groups are library-side, for the owner: the failure is
PointKinetics_Drift_Test_flat_Xenon at NRMSE 1.54e-04 against the
1e-4 default — marginal drift on a four-day xenon transient whose
final values agree exactly — and the 5 NO_REFs are tests the library
gained since July (326 → 331) that have never had baselines accepted.
No baselines were touched.

## D114 — Phase C-iv split 1: cli.py becomes the cli/ package (2026-09-01)

`cli.py` had grown to 2,147 lines holding 14 commands and every helper
they share. It is now `cli/` split along helper-ownership lines:
`__init__` (exactly `main` + `main_entry` — the console-script and
`python -m dstf` seams, pyproject untouched), `main` (parser + the
`_COMMANDS` dispatch table and its parity test), `common` (config
building, filter/review parsing), `run_cmds` (run/compare, runner
selection, report routing — the coupling cluster), `review` (the
interactive loop), `spec_cmds`, `baseline_cmds`, `diag_cmds`. Every
function moved whole; a normalization diff shows no executable body
line changed. One deliberate asymmetry: `run_cmds` imports the review
loop at module scope while `review` imports `_get_runner` INSIDE the
function — so a monkeypatch of `dstf.cli.run_cmds._get_runner` still
reaches the review path (a module-scope import would freeze the
pre-patch object). `cmd_discover` went to `diag_cmds` (a listing
command with zero coupling to the run cluster). Eight test files
retargeted imports and patch targets to the functions' new homes; no
assertion weakened. Seam evidence: `dstf --help` byte-identical to
`python -m dstf --help`, 14 subcommands, parser ↔ dispatch parity,
demo discover 12, check-dymola OK, live OpenModelica run + compare
12/12. Known, accepted: `from .main import main` in the package
`__init__` shadows the submodule name, so `import dstf.cli.main` then
attribute access reaches the FUNCTION (use `from dstf.cli.main import
X`, as every caller does); and the review loop binds `_get_runner`
once at entry rather than per keypress — a monkeypatch applied after
the loop starts would not be seen (none does).

## D115 — Phase C-iv split 2: reference_store splits along its seams (2026-09-01)

`storage/reference_store.py` (1,232 lines) is now four modules:
`baseline.py` holds the `Baseline` value type and `PRIMARY_BASELINE` —
its own leaf module because the roles code constructs `Baseline` at
runtime while the store needs the roles mixin at class-creation time,
so keeping the type in the store is a hard cycle; `ref_index.py` holds
`RefIndex`, `_atomic_write_text` and the ref-file pattern/high-water
constants (simulators, discovery and reporting retarget their imports —
base.py's lazy-import comment now states the true reason, the old
circularity claim being false once ref_index imports nothing from
dstf); `roles.py` holds the nine soft_check/companion CRUD methods as
`_RolesMixin`, moved verbatim, whose entire store coupling is two
declared members (`ref_dir` and `_ref_file_for_model`); the store keeps
primary CRUD, the accept path, `get_reference` and `_extract_baselines`
and subclasses the mixin, so every public call site is unchanged. Every
moved region byte-identical to HEAD (verified as exact substrings).
Three test files retargeted imports, monkeypatch targets and — the
silent-vacuum risk — the two caplog logger names, which now differ
deliberately: duplicate-model_id warnings come from
`dstf.storage.ref_index`, the stray-`baselines` warning from
`dstf.storage.reference_store`. Live: OpenModelica 12/12 run + compare,
soft-check/companion listings, manifest dump — all through the split
store.

## D116 — Phase C-iv split 3: base.py becomes base + types + persistent (2026-09-01)

`simulators/base.py` (1,717 lines after the U-2 template moved in) is
now three modules plus one relocation: `types.py` (Capability,
DatasetType, the result dataclasses, `resolve_variable_patterns` — it
imports nothing from dstf, which is what keeps the package import
order safe), `persistent.py` (the Worker template and
PersistentRunnerBase, 917 lines), `storage/batch_manifest.py`
(BatchManifest joins the storage layer), and base keeping
SimulatorRunner, `_prepare_run`/RunSetup, the printers and
`assign_test_keys`. The package `__init__`'s `__all__` is
byte-identical — only the sourcing changed — and all five backends
still register. 59 of 61 moved definitions are token-identical; the
two exceptions are one annotation-quoting pair on `BatchManifest.load`
(`"BatchManifest"` → `BatchManifest` under `from __future__ import
annotations`, with TestRunResult behind TYPE_CHECKING — the runtime
import would be a genuine cycle; PEP 563 makes it a no-op). 41 files
retargeted imports, including the watchdog tests' monkeypatch aliases
(kill_process_tree/snapshot_children are looked up in persistent.py
now). Verified live on OpenModelica: clean 12/12 run + compare, and a
forced-timeout case through the moved template — three TIMEOUTs, three
"not counted" restarts, pgrep omc 0 before and after. The lazy
`_atomic_write_text` import in `save()` was hoisted to module level in
the same commit (both live in storage now; review-sanctioned) — the
finding-66 atomic-replace rationale stays.

## D117 — Phase C-iv split 4: reporting split, one status classifier, html_report retired (2026-09-01)

Three coupled pieces. (A) `plot_comparison.py` (1,675 lines) is gone:
`context_builder.py` holds the pure results→context half,
`report_suite.py` the Jinja rendering, LTTB decimation, sidecar and
suite orchestration — moved whole, no thin shim (no cycle forced one),
and the six golden interactive hashes did not move. (B) The per-test
status classification existed in four copies (finding 69 once fixed
the same bug twice); the divergence-table-first rule found console,
JUnit and html_report identical — branch order differs in JUnit but
the arms are mutually exclusive — and those now call ONE classifier in
`reporting/status.py`, with console and JUnit output pinned
byte-identical by embedded goldens. The report-suite copy is a REAL
divergence and stays: its `simulate_only` branch needs the TestModel
(a passing simulate-only test is PASS there, while console/JUnit class
it with the no-baseline rows — a pre-existing incongruity now
documented at the site, queued for C-v adjudication). (C)
`html_report.py` — the third overlapping HTML surface — is deleted;
`--report-format html` writes the DASHBOARD page via
`dashboard_render.render_final_to`, byte-identical to the run's own
dashboard.html. Riding along because the brief's own live check
exposed it: `_output_report` read an `--output` flag that no
run/compare parser ever defined (dead since birth) — the flag now
exists on the shared report parent, so junit/html reports can be
written to a chosen path. Known: dashboard links to per-test reports
are relative, so a page written outside the work dir has dangling
row links (documented in the docstring and usage.md); interactive.js
comments still name plot_comparison.py (frozen behind the golden
hashes; follow-up with hash-diff evidence). Kept, commented, not fixed: the shared latent quirk that
a driver-omitted variable leaves a numbering gap (`enumerate` over the
requested list). Known spelling split: this base uses
`result_filename`; `FmpyRunner` keeps its own `RESULT_FILENAME`. Verified live per backend: normal
demo runs 12/12 (Dymola, OM) and 8/8 (Julia); forced-timeout runs
complete with a summary, restart "not counted", and pgrep counts equal
before and 30 s after on all three.

## D118 — Phase C-iv split 5: interactive.js becomes five fragments (2026-09-01)

The 5,308-line `interactive.js` is five contiguous slices cut on its
own section banners — `scorers.js` (embedded data, runtime state, the
MODE_SCORERS registry, FFT/event detection), `editors.js` (the five
plot editors and the modal), `plots.js` (init + Plotly rendering),
`tree-ui.js` (the recursive tree, structural editing, nodeToSpec),
`export.js` (summary, overlays, patch building) — concatenated at
report-copy time by `report_suite.INTERACTIVE_JS_PARTS` +
`write_interactive_js`, which is now the load-bearing ordering
contract (a reorder silently breaks load semantics; the browser lane
is what would catch it). The page keeps one script tag; no build
step; the shipped artifact is byte-identical to the join. The parity
harness loads `scorers.js` ALONE behind a stub that is nothing but
`window.MT_REPORT` — the smallness of that stub is itself the standing
check that scoring math stays browser-independent; the full DOM shim
survives only for the two Node tests that exercise the bundle. One
ratified code motion: `_interpStep` (nine lines, hoisted declaration,
body byte-identical) moved from the editor region into scorers.js so
the fragment loads standalone — byte order was a means, the standalone
parity target is the end. Strip-comments-and-sort proves identical
code-line multisets; the three stale plot_comparison.py comments were
fixed in the same pass. The goldens did not move — they hash the
rendered HTML, which references the script by src — closing C-iv's
split sequence with the demo live 12/12 and zero console errors over
the concatenated artifact.

## D119 — Phase C-iv closes: review-wave test files folded home (2026-09-01)

The seven `test_review_fixes_*.py` files — named for the review wave
that produced them, not the modules they pin — are gone; their 244
tests moved whole into the per-module files (two new homes were
justified: `test_report_suite.py`, since the reporting split left no
per-module file for that pair, and `test_julia_driver.py` for the
driver pins that deliberately need no julia binary). Every finding
citation and section header moved verbatim — those docstrings are the
incident record. The count invariant was proven mechanically: 1305
collected / 1287 passed / 18 skipped before and after, and the full
browser lane at 129 (77 of them in the reporter file — the review
caught that quoting "77" alone as "the browser lane" was ambiguous),
with all 222 node-ids from the deleted files present unrenamed. The
only body edit anywhere is one helper rename forced by a genuine name
collision (`_mk_result` → `_mk_valued_result`, seven call sites);
two module-level fmpy skip-gates became class/function gates so
folded fake-fmpy tests run while the real-fmpy tests keep skipping —
the 18 skips are the same 18. Noted follow-up: `_tm` now exists in
six test files; a conftest helper is the cleanup when next touched.
This closes Phase C-iv — six restructuring commits, D114–D119, each
review-gated and behavior-preserving.

## D120 — Phase C-v round 1: the CLI batch, honest run verdicts, recorded backends (2026-09-01)

Ten sanctioned behavior changes. Global flags now work on either side
of the subcommand (a `global_parent` whose copies default to
`argparse.SUPPRESS`, so the subparser's namespace copy cannot erase a
pre-subcommand value — the mechanism is pinned in both directions).
Five raw-traceback paths became ConfigError usage lines (missing
@filter file, malformed recognizers entry, malformed metrics block, no
package at the source path, missing positionals on the companion and
soft-check actions). `manifest cleanup` dry-runs by default everywhere,
`--apply` executes. `--parallel`'s two meanings are documented in each
subparser. Timeout messages name `simulation.timeout` as the per-test
override, through one shared `timeout_message`. A failed post-timeout
worker restart gets exactly ONE retried `start()` after a five-second
backoff inside the same budgeted restart, so a licence blip at
--parallel 1 no longer fails the remaining queue. The D93 follow-on
landed, hardened by review: `_prepare_run` stamps this run's scope
`pending` before anything starts, and each verdict is persisted
per-result the moment it lands (the bulk epilogue writer was deleted —
one mechanism, not two), so an interrupted run leaves `pending` on the
tests it never reached and real verdicts on those it did; the reader
treats only recorded verdicts as authoritative and falls back to the
status.json cross-check for `pending` and pre-field entries alike (old
data, not old code). A work dir whose manifest says a run failed
compares as SIM_FAIL, and `compare --accept` accepts only tests whose
last RUN completed successfully — `failed`, `timed_out` and `pending`
(the in-flight-at-abort state) all refuse, while a drifted test whose
run passed but comparison failed stays acceptable (re-baselining drift
is legitimate); field-less pre-D120 manifests keep the status.json
guard. Live-proven with a genuinely interrupted OpenModelica run:
manifest 9 pending / 3 passed, compare honestly 3-of-12 — under the
pre-fix code all twelve would still have read green.
`_dispatch_with_restart` gained the manifest as a fourth argument
(signature change, no shim). `compare` without `--simulator`
adopts the backend the run recorded in its metadata (explicit still
wins) — forcing the wrong backend on an OM work dir reproduces the old
0/12 exactly, which is the whole point. Batch Dymola stamps a real
tool_version by printing `DymolaVersion()` from startup.mos into a
work-dir file read back after the first batch — live-verified in the
main session (12/12, `Dymola Version 2026x, 2025-10-10` in the
metadata). The eight `_tm` test helpers consolidated onto one conftest
`make_test_model` (two thin wrappers keep genuinely different
defaults; the watchdog's stays standalone by design).

## D121 — Phase C-v round 2: the reporter batch (2026-09-01)

Ten items; the two structural ones first. Against-leaves live-score
now: a leaf with `against != primary` resolves its baseline from the
variable's EMBEDDED soft_check overlay (companions are never
scorable), so live re-scoring and the tube envelope use the curve the
CLI scored — mutation-proven by fixtures where the primary and the
soft_check give different verdicts, and live on MetricTreeTest's warn
leaf (perturbing the soft_check flips the pill; perturbing the primary
does not). The D105 CLI-authoritative pin narrows to overlay-absent
(plus the unchanged D98 decimation rule), symmetric between the Python
stamp and the JS rule. And the D117 simulate_only carve-out ends:
`TestComparison.simulate_only` lets the ONE shared classifier call a
passing simulate-only PASS everywhere — console, JUnit and the report
now agree, and the report suite's private classifier copy is deleted.

The rest: the owner-reported cross-report selection leak is fixed by
gating selection restore on a per-report key (work dir + run start;
filters/sort/widths deliberately still shared); the add-leaf `+` is
disabled with a tooltip on a `warn` that has its one child; Plotly is
vendored (cartesian 2.35.0, 1.25 MB — the full bundle phones home for
map CSS and fails the offline gate) ONCE per report suite at
`reports/plotly.min.js` after review pushed back on per-dir copies
(22 MB → 8.3 MB per demo run; ~414 MB → 1.25 MB at TRANSFORM scale;
the dashboard needs no Plotly at all — pinned; single-dir portability
cost documented); legends use variable + metric names, not JSON-pointer
paths; warn-wrapped failures no longer paint the summary red — the
root verdict rules and an advisory chip counts them (the documented
baseline-role semantics applied to the UI); browser-created `weighted`
nodes carry the CLI-authoritative badge and a pending verdict that is
deliberately not a pass; range out-of-window segments are gray traces
(Plotly's shapes[].editable=false empirically does not pin under
global shape editing); rich-editor modes lost their duplicated
passthrough textarea under a general rule that spared event-timing's
panel, whose time_tolerance/count_must_match have no other editor; the
scalar Quantity box fits one line; and the dashboard's Worst-NRMSE
column says "n/a (N unscored)" instead of "—" when a sentinel leaf
poisons the max. Reports are now fully offline-capable (zero console
errors and zero failed requests with the network disabled). Review
pushed two more honesty fixes in before commit: overlay arrays are
decimated under the same cap as the primary, so per-overlay
full-sample counts are now stamped, the decimation banner discloses
decimated overlays, and the banner
discloses decimated soft_checks by name (companions count toward the
totals but are never listed — they cannot be scored against). One
clause of that fix was REFUSED by the implementer and the refusal
ratified: pinning dominant-frequency on overlay decimation would be
STRICTER than the primary's treatment, because the DF scorer FFTs the
actual trajectory only — the baseline never enters the spectrum, which
is also why a decimated primary ref does not pin DF today. The ruling
is pinned by a guard test that fails loudly if the scorer ever starts
reading the reference arrays — the one condition that would flip the
call; and
a root wrapped in `weighted` shows a pending summary matching its
pending pill instead of painting red. Accepted, watch-only: two
same-metric leaves on one variable now share a legend label (the
pointer disambiguated before); pre-`start_wall` snapshots churn their
selection key per refresh (old data tolerated, silently); the vendor
copy's freshness check is size-only; a zero-length embedded overlay
fails closed rather than falling back to the CLI badge; a pinned
against-leaf's tube editor centres on y=0 rather than wrongly on the
primary.

## D122 — Every JSON DSTF writes lands with LF, on every platform (2026-09-03)

`_atomic_write_text` forwarded `newline=None` to `open`,
so Python translated `\n` to `os.linesep`: a baseline accepted on Windows
landed CRLF, the same file accepted from WSL landed LF. Observed in the
user's TRANSFORM-UnitTests tree — `ref_0011.json` and `ref_0222.json`
showed as modified with +6881/-6881 and +231/-231 while being
byte-identical to HEAD once `\r` was stripped. CORRECTION (same day,
verified in that repo): the observation was real but its stated cause was
not. An earlier note recorded "no `.gitattributes` and no `core.autocrlf`, so
nothing normalises it back"; that was true when written and the owner
closed it hours later — `6471d47` adds `* text=auto eol=lf` and `1372809`
renormalizes, both 2026-09-03. That tree now reads clean with a
6,881-CR working copy, because git cleans on compare. Do not repeat the
claim: a consuming repo may or may not normalise, which is exactly why
the writer must not depend on it. The cost is not tidiness: the reference tree is the
seam other people version-control (the stage block names it), and a real
one-value edit inside a 6,881-line whole-file diff is invisible in review.
This repo could never surface the bug itself — its own `.gitattributes`
carries `* text=auto eol=lf`.

The fix is the default, not the call sites. `_atomic_write_text`'s
`newline` narrows from `str | None = None` to `str = "\n"`, which corrects
all ten of its JSON callers at once (`patch_apply`, `spec_parser`,
`batch_manifest`, `reference_store` ×3, `roles` ×3, `ref_index`) and means
a caller added later cannot reintroduce the defect by forgetting a keyword.
Three JSON writers bypass that primitive and took the explicit kwarg
instead: `config.py` (`testing.json`), `cli/common.py`
(`reference_manifest.json`) and `cli/spec_cmds.py` (the exported schema).
The scope is wider than the defect report, which named only the baseline
tree: `spec_parser` and `patch_apply` write `test_spec.json`, also a
promised surface. `reporting/report_suite.py`'s `interactive.js` joins them
for one specific reason — unlike everything else under `reports/` it is
byte-stable across runs, so an archived report tree regenerated from the
other OS would diff wholly for nothing.

Deliberately excluded, all regenerated wholesale with content that differs
every run anyway: report HTML/JSON, simulator stdout/stderr dumps, work-dir
result JSON, progress files, the Dymola/OM `.mos` scripts (forcing LF on a
script a Windows tool reads is a separate question nobody has asked), and
the JUnit XML at `reporting/junit_report.py` — `ElementTree.write`'s
filename branch opens without `newline=`, so that one would need a file
object rather than a kwarg, and it is a CI artifact, not a committed file.
`ref_index`'s ID-claim stub is excluded on different grounds: `json.dump`
with no `indent` emits a single line containing no `\n` at all.

The CSV export keeps its explicit `newline=""`, now belt-and-braces rather
than load-bearing — per CPython's `textio.c` neither `""` nor `"\n"`
translates on write, so the `\r\n` that `csv` embeds survives either way.
That also means the existing `\r\r\n` regression assertion at
`tests/test_storage.py` can no longer fail on any platform; it was restated
to pin the CSV terminator rather than pretend to guard the argument.

Old data, not old code: baselines already stored with CRLF read back fine —
every reader goes through `read_text` (universal newlines) or `json.loads`,
and nothing hashes, seeks or appends over reference-tree content. They stay
CRLF until re-accepted, at which point each flips to LF and produces one
whole-file diff. That is a one-time cost and no migration is offered.

Verification, and its limit. Full suite green (1403 passed, 26 skipped);
the mechanism was proved directly — an explicit `newline` controls the
terminator independently of `os.linesep`, and `Path.write_text`'s `newline`
parameter exists on the 3.10 floor (executed on CPython 3.10.20, not
recalled). No byte assertion can gate this on Linux: CPython compiles the
CRLF write translation in under `#ifdef MS_WINDOWS` rather than reading
`os.linesep`, so setting `os.linesep` at runtime changes nothing. The gates
are therefore call-site assertions — the signature default for the
primitive, and a `Path.write_text` spy for each of the three direct writers
— each mutation-checked by reverting the decision and confirming the test
fails. **What remains unrun: an actual accept on Windows.** The platform
where the defect manifests is the one that cannot be tested from here.

## D123 — The "simulator" vocabulary becomes Backend; three config keys and two CLI flags break (2026-09-03)

The architecture has called the abstraction a **Backend** — a role: test
descriptor in, trajectories out — since the layer model was written, but the
code said "simulator" throughout: the package, the ABC, every concrete class,
the config keys, the CLI flags. Residue from ModelicaTesting; D81 renamed the
project around it rather than through it. The word was not merely dated, it
was false for one of the five — the Python backend runs an arbitrary
`simulate()` — and for its shipped `ConstantCsv` example, which replays a
recorded file and simulates nothing. That a backend need not be a simulator is the
architecture's central claim, so the vocabulary contradicted the design it
was supposed to document.

**Why now rather than after 1.0.** The stage block names the `dstf` CLI and
the `testing.json` format as promised surfaces whose change must be
coordinated. Nothing is published — `origin/main` predates all of this — so
today the coordination is one line in one consumer file. After a 1.0 release
the identical rename becomes a breaking change against a published surface,
and the realistic outcome is that it never happens and the wrong word becomes
permanent. The cost of this decision only ever goes up.

**The break, in three commits.** Internal names first (01e1a43): package
`simulators/` → `backends/`, `SimulatorRunner` → `Backend`, the five
`*Runner` families → `*Backend`, `get_runner*` → `get_backend*`,
`persistent_runner_cls` → `persistent_backend_cls`. Then the promised
surfaces (96a13a6): `simulator`/`simulators`/`simulator_setup` →
`backend`/`backends`/`backend_setup`, `--simulator`/`--simulator-path` →
`--backend`/`--backend-path`, `RunMetadata.simulator` → `backend_name`. Then
docs. No aliases, no read-both shim, no migration utility: the stage rules ban
deprecation paths, and an alias would have made the dead vocabulary permanent
by being convenient.

**`Worker` was not renamed, and "simulator" survives on purpose** where the
referent is a real external tool — a binary on PATH, a long-lived simulator
subprocess, a licence seat, "as reported by the simulator". Dymola is a
simulator; the abstraction is not. Registered backend names, the
`dstf.plugins` entry-point group, the reference-tree layout and the work_dir
path shape are all values or structure, not vocabulary, and did not move.

**The one real hazard was a name collision, not a rename.** `Config.simulator`
and `Config.simulator_backend` were never synonyms: the first is the label the
user configured and may carry a version ("Dymola 2026x"), the second the
canonical family ("Dymola") that selects the reference partition. They are now
`backend_name` and `backend` — one word apart, and trivially merged by a later
refactor. Merging them sends every versioned-name user's lookups to a
directory none of their baselines occupy: whole-suite NO_BASELINE, and an
`--accept` then writes a full parallel reference tree in the wrong place. The
existing `reference_dir` test could not catch it, because its unversioned
default made the two strings equal. A new test pins a versioned name against
both paths and was mutation-checked — collapsing the split fails it.

**A hard break has a failure mode worse than either state, and it needed its
own fix.** An untouched `testing.json` still PARSES with the old keys while
every setting in it silently does nothing — experienced as "the tool ignores
my config", with no diagnostic. `load_config_file` therefore rejects the three
old keys by name and says what each became. That is a diagnostic, not a
compatibility path: the old key never works again, it just stops failing
silently. A parametrized test pins it, because nothing else in the tree failed
if the break were undone — a rebase restoring an old-key read would have kept
the suite green.

**One field was deliberately NOT deleted despite an instruction to delete it.**
`RunMetadata` carries `backend` (family) and the configured label as two
fields by design, and they diverge in practice — TRANSFORM's own config
auto-detects exactly that pair. `meta.get("simulator") or meta.get("backend")`
was therefore "prefer the versioned label, else the family". The FIELD was
never legacy; only the OR-arm was dead, and only because both fields are
always written today. Deleting the field would have erased the version from
every report
banner — the provenance that module exists to capture. The field was renamed
to `backend_name` instead. What *was* deleted is the `or meta.get("backend")`
tail, which was dead for anything this version writes.

Review caught that the first version of this entry claimed dropping that tail
produced an explicit error. It does not, and the truth is worse: a work dir
written before the rename records its backend under the old key, so
`_recorded_backend` returns `None`, the adoption branch is skipped, and
D120's guard stops firing precisely when it is needed — `compare` falls back
to environment auto-detect and will score an OpenModelica work dir against
the Dymola partition, SIM_FAIL for every test, silently. Reading the old key
would be a compatibility path, so instead a stale stamp now warns and names
`--backend`.

**Known consequence, stated rather than discovered:** `compare` against a work
dir produced before this change cannot identify its backend; it now warns and
names `--backend`. Re-run rather than compare, once.

**Verified.** 1410 hardware-free tests; ruff clean; live runs green on Dymola
(12/12), OpenModelica batch (12/12) and Python (3/3) through the renamed key
and flags; and a full 331-test run against TRANSFORM-UnitTests through the
renamed key reproduced the pre-rename verdicts exactly — same test set, 330
pass, the same single `SteamWater_HCSG` 60s timeout, zero regressions. The
word "pre-existing" is anchored on a full pre-rename run captured in the same
session before any file was touched — not on the last recorded 331/331, which
was a `compare` and so could not have re-simulated. That timeout is a slow
model against the 60s default, not a wrong answer, and is unrelated to this
work. **Unrun:** Windows, and the Julia and FMPy backends (no `julia`
on this machine, `fmpy` not installed) — both are covered by the hardware-free
suite but were not exercised live.


## D124 — The PyPI distribution name is `dynamic-systems-testing-framework` (2026-09-04)

D81 declared the PyPI project name `dstf`. That name is not available: it
belongs to "A framework for Deterministic Scheduling Theory" (v0.0.12, last
released 2020-08-06), which is unrelated to this project. So the install
instruction the README has been advertising does not work — `uv tool install
dstf` fetches the 2020 package and then fails with `No executables are provided
by package dstf`.

The distribution name and the import name are separate fields, and only the
first one moves. You install `dynamic-systems-testing-framework`; you still
`import dstf`, the console script is still `dstf`, `python -m dstf` still works,
coverage still measures `dstf`, and the plug-in entry-point group is still
`dstf.plugins`. Hatchling already pinned `packages = ["src/dstf"]`, so the wheel
layout does not move either: the built artifact carries
`Name: dynamic-systems-testing-framework`, a top-level `dstf/` package and the
`dstf = dstf.cli:main_entry` entry point.

The split introduces one hazard worth recording, because it is silent.
`importlib.metadata.version()` is the only thing that reads the distribution
name back, and both call sites (`dstf/__init__.py`, `backends/run_metadata.py`)
catch `PackageNotFoundError` and substitute a fallback string. A future rename
that misses a call site therefore does not fail — it writes `unknown` into the
`dstf_version` provenance field of every result file, and the pre-existing test
asserted only that the field was non-empty, so `unknown` satisfied it. The name
now lives once, as `dstf._DIST_NAME`, and `tests/test_distribution_name.py`
pins it to `pyproject.toml` and refuses to skip when an install of this source
tree exists under a different name — the state in which the old test reported
green.

**Verified.** Built the wheel and installed it into a clean environment:
`dstf --version` and `python -m dstf --version` both report 1.0.0,
`version("dstf")` raises `PackageNotFoundError`, and both internal lookups
resolve. Stored baselines are unaffected — `dstf_version` is display-only, never
compared. **Unrun:** the published install path itself. Nothing is on PyPI yet,
so `uv tool install dynamic-systems-testing-framework` is correct in form and
untested in fact; publishing is tracked in `docs/ideas.md`.


## D125 — The two-tier split supersedes D66's boundary (2026-09-08)

The line between the two surfaces is **mutation**. A surface either changes
something or it does not, and that is the whole rule: zoom, hover, legend and
overlay toggles, error overlays, reading values off a plot, filtering tests,
sweeping a selection, copying a command, and reading the metric tree with its
verdicts and score text are reporting; changing a tolerance, a mode, a window or
an `against`, adding or removing or wrapping leaves, accepting results as
baselines, and running tests are authoring. D66 drew the line in the same place
in words — reporter authors, CLI executes — but put the authoring surface inside
the report, so the report both mutated and computed. It no longer does either.

The report page now computes nothing. Every number it draws comes from an
embedded figure dict Python built or from a string the server rendered. Geometry
is a result of comparison (sub-project 1): each metric emits the primitives that
describe its criterion, the figure layer turns them into traces and shapes, and
the page paints them. Authoring moves to a Python-side tool driven by the same
figure layer and the same metric registry the CLI scores with (sub-projects 3–6,
not yet built); `spec-update` is unchanged and stays the CLI's patch applier —
it applies an RFC 6902 envelope from any source, and only the browser stopped
producing one.

What was deleted, and why. Three registries were re-deriving Python's geometry
in JavaScript — `MODE_SCORERS` in `scorers.js`, the five per-mode editors in
`editors.js`, and the tree builder and patch export in `tree-ui.js` /
`export.js` — and with them the Python that existed only to feed them
(`_JS_SCORED_METRICS`, `cli_authoritative` and its badge,
`_mark_decimated_cli_authoritative`, `_extract_mode_values`, `_extract_spectrum`,
the mode-schema and form emitters in `reporting/ui/`), plus
`tests/test_scorer_parity.py`, which existed to prove the two implementations
agreed. The reason is the failure mode that suite was built to catch and could
not close: a second implementation of scoring means the page can show a passing
tube the CLI failed. A parity test constrains drift where it has a fixture; it
cannot make one implementation be the other. Deleting the second implementation
does.

What the report keeps, each asserted by Playwright on pages generated through
the real render path before the deletion landed: the per-variable figure with
the trace count its dict declares; zoom, hover and legend toggles; the overlay
picker (overlay traces are already in the figure, `legendonly`, and the checkbox
toggles visibility); the error-overlay toggle on the right axis; the test
filter, selection sweep and command copy on the dashboard; the metric tree,
read-only, with the CLI's verdict and `summarize` text per leaf, server-rendered;
statistics, diagnostics and artifact sections; diagnostic and no-baseline plots,
which became figure dicts too; and a leaf's window shown as a band, open-ended
when one end is `None`. The `comparison_data.json` sidecar keeps what the
documented downstream readers use: each `trajectories` entry's shape and full
resolution (one entry per variable now — a variable whose leaves include a
sentinel used to get a second, empty entry of the same name), and the `summary`
block the dashboard reads. Its `tree_view` leaves changed with the page: the
per-leaf numeric fields and form artifacts the browser scorer read are gone
(`nrmse`, `rmse`, `signal_range`, `max_abs_error`, the finals, `tolerance_used`,
the `tube_*` fields, `mode_values`, `mode_controls_html`, `window_controls_html`,
`cli_authoritative`, `spectrum`), and each leaf carries `details` and
`advisory` instead; the top level lost `mode_schemas` and gained `tree_summary`.
The spectrum arrays now live only in the figure. The page's decimation banner
points at the sidecar for the full-resolution input trajectories.

What changed for a user who never opens the tool, all on the CLI's surfaces:
`run -i`'s detail text is mode-aware, because `summarize` on the metric
descriptor replaced the reporter's private score formatter (sub-project 1) and
the console reads the same function, and it now prints each mode's own detail
pairs. A leaf that could not be scored carries its variable's name in the
console and JUnit output with the reason beside it, rather than carrying the
reason where the name belongs — the sentinel's label was the diagnostic, which
made a not-scored leaf unfindable by variable; the missing-variable sentinel,
which used to print only the inf/nan numbers, now says what was missing. A
scalar leaf whose reduction is not finite (a NaN in the samples) is a no-data
failure with a reason; under bounds with only `min_value` declared it used to
pass, because `max(0, nan)` is `0`. The dashboard's copied command is built
from a prefix without a subcommand, so the command menu can offer `run`,
`compare`, `compare --accept` and `run --rerun`. The dominant-frequency
"no peaks declared" text no longer names a browser button.

Payload, measured at the 50-variable budget fixture: **11.9 MB** after the cut,
against 15.8 MB with the trajectory arrays still on the page. D66's ~5 MB budget
no longer applies; the owner dropped it on 2026-09-08 in favour of a 25 MB
ceiling watched as a regression, because a page that carries figure geometry for
50 variables is not the page that budget was written for.

Sources: the design is
`docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.1 the
line, §4.4 the reporter after the split, §5 the build order); the two plans
executed so far are
`docs/superpowers/plans/2026-09-08-geometry-and-figures.md` and
`docs/superpowers/plans/2026-09-08-the-cut.md`. This entry supersedes D66's
"reporter is the authoring surface" commitment; D66's other commitments (the
baseline-role split, recommender containment, economy of tools) stand.

- `src/dstf/reporting/templates/report.js` — the page's one script, replacing
  `plots.js` and the viewing half of `export.js`; still served as
  `interactive.js`. `scorers.js`, `editors.js`, `tree-ui.js`, `plots.js` and
  `export.js` are gone.
- `src/dstf/reporting/templates/interactive.html` — server-rendered metric tree
  (a recursive Jinja macro), summary line, variable pills and decimation banner;
  `MT_REPORT` carries figure dicts only.
- `src/dstf/reporting/report_suite.py` — `INTERACTIVE_JS_PARTS = ("report.js",)`;
  the page context is stripped of trajectory arrays after the sidecar is
  written; `decimated` is computed from the figures' sample stamps.
- `src/dstf/reporting/context_builder.py` — the leaf view carries
  `score_display`, `criterion`, `details`, `error`, `advisory`, `against` and
  `window`; the raw numeric leaf fields and the JS-facing extractors are gone.
- `src/dstf/reporting/ui/mode_controls.py` — `derive_schema` only, for
  `export-schema` and the authoring tool's forms.
- `src/dstf/comparison/tree_eval.py`, `reporting/console_report.py` — sentinel
  comparisons keep the leaf's variable name and carry the reason in
  `diagnostics["error"]`.
- `src/dstf/reporting/templates/dashboard.js`, `dashboard.html`,
  `reporting/dashboard_render.py` — the command menu offers the commands that
  exist today (`run`, `compare`, `compare --accept`,
  `run --rerun failed,sim-failed`); the `dstf author` entry waits for the
  command.
- `tests/test_interactive_report.py` and `tests/test_figure_goldens.py` (new);
  `test_scorer_parity.py`, `test_interactive_html_snapshot.py` and its hash
  goldens, `test_interactive_playwright.py`, `test_interactive_points.py`,
  `test_interactive_event_timing.py`, `test_interactive_range_window.py`,
  `test_interactive_against_baselines.py` deleted; `test_mode_controls.py`,
  `test_export_schema.py`, `test_report_suite.py`, `test_report_size_budget.py`,
  `test_comparator.py`, `test_metric_registry.py`, `test_scalar_metric.py`,
  `test_tree_eval.py`, `test_reporters.py` and the two dashboard suites trimmed
  or adapted.
- `README.md`, `docs/vision.md`, `docs/architecture.md`,
  `docs/extensibility.md`, `docs/usage.md`, `docs/qa/reporter_checklist.md`,
  `CLAUDE.md` — updated to the read-only page and the two tiers.

## D126 — A tree scores what it names; the rest of what a test tracks is warned about (2026-09-09)

A test's `variables` list, patterns included, decides what is tracked: read
from the result, stored in the baseline, plotted. Scoring is the other layer.
With no `metrics` tree the flat path scores every tracked variable by name;
with a tree, only the leaves score. A tracked variable no leaf names is stored
and plotted and never judged, and CI stays green while it drifts. That gap
existed for every hand-authored tree; the authoring tool made it reachable in
one click, because editing a criterion on a test with no tree writes one.

Two questions were open. The tool's tree for a pattern-tracked spec, `"variables":
["tank.*"]`: freeze today's matches into leaves, or leave such tests uneditable?
The owner chose the leaves — the pattern keeps tracking, the tree scores what it
names — on the condition that the gap is never silent. So every comparison run
through a tree carries a structural warning, "Tracked variables without a
criterion", naming each tracked variable no leaf covers, on the console and the
report page like every other structural warning. The flat path never emits it.
`spec-update`, which has no simulation result to resolve a pattern against,
still refuses to write a tree for one.

The tool builds the tree from the variables the flat path actually scored, in
the report's order, so leaf *i* is `/metrics/children/i` on both tiers; a test
tracked by its `.mo` annotation (TRANSFORM's shape, where the spec entry lists
no variables or does not exist) gets its entry created by the same op. It
stages that tree only when the tree would score exactly what the flat path
scored; the ways the two paths differ are enumerated in the Session, each with
its negative test.

- `src/dstf/comparison/comparator.py` — the warning, tree path only.
- `src/dstf/authoring/session.py` — materialization from the scored names.
- `docs/usage.md`, the design spec's §4.6 amendment and §9.

## D127 — The visual system is one token table; the tool's scan cache lives in the user's cache dir (2026-09-15)

The redesign brief (`docs/superpowers/specs/2026-09-15-authoring-tool-redesign-brief.md`)
and `DESIGN.md` name the colours and type both tiers share. They live once, in
`reporting/theme.py`, as a mapping rendered to `:root { --dstf-...: ... }` for the
dashboard, the per-test report and the authoring tool's page, and mirrored into
the tool's Quasar palette. Status fills, baseline-role chips and plot series
never change with a theme; a theme (the redesign roadmap's sub-project 7) will
be another mapping in the same module, named in `testing.json` (owner ruling,
brief §3.10). The shipped
type stack ended in `monospace` with no generic sans-serif, so a Linux browser
without Segoe UI or Roboto rendered every report in monospace; the stack now
ends in `sans-serif`. Two colours the report used that are in no table — the
metric tree's combinator purple and the decimation banner's amber — map onto
the neutral and `warn` tokens.

The tool caches the library scan across restarts under
`$XDG_CACHE_HOME/dstf/scan/` (default `~/.cache/dstf/scan/`), keyed by the
library path and the recognizer set (a cache another DSTF version wrote is
ignored and replaced) — never in the work dir,
which is a run's artifact directory, and `Session.open` still writes nothing
there. The cache validates nothing per file: on the owner's `/mnt/d` mount a
`stat` sweep over TRANSFORM's 3 269 `.mo` files measured 27 s, longer than
parsing them (20 s). A cached open trusts the cache, rescans on a background
thread, and re-merges what changed.

Also here, no decision needed but recorded: the report's error overlays ride on
one right axis per unit family (brief §3.4), shown while a series on it is
visible, by one shared script (`templates/axes.js`); the Session reads a model's
reference and overlays once per reload and decimates its base traces once; the
sibling-backend overlay index is built at open on its own thread; the Session
reports its phases as `loading` events and scores each result as it is read.

- `src/dstf/reporting/theme.py`, `templates/axes.js`, `discovery/scan_cache.py`,
  `authoring/session.py`.
- Brief §3.1, §3.4, §3.8, §3.10; roadmap sub-project 1.

## D128 — The run verdict is applied in the one reader (2026-09-16)

`Backend.read_results` applies the batch manifest's `last_run_status` (via
`run_verdicts`) to every result before storing it and before its `on_result`
callback; `read_last_results` is that reader over the manifest on disk. Until
now only `read_last_results` applied the verdict, and `run_pipeline` read back
through `read_results` directly — so `run --merge` and `--rerun`, whose
merged-in models have no in-memory run result, reported PASS for a test whose
run had timed out while `compare` on the same work dir reported SIM_FAIL, and
`run --rerun failed -i` then offered that run's partial `.mat` for acceptance
as the primary baseline. The guard lives at the read because a call-site
patch leaves a second place to forget; `tests/test_pipeline.py` drives the
reader and the merged pipeline, `tests/test_cli.py` the review loop over a
merged outcome.

The partial-rerun flip over OLD data noted in `docs/ideas.md` (2026-09-14)
is a different cause — `status.json`'s subset snapshot losing rows for
manifest entries written before `last_run_status` existed — and stays open.

## D129 — Selection is the page's; capabilities attach by placement (2026-09-16)

The authoring tool's selection — the checked tests, the variable, the
criterion's path — is the state of a page in one browser tab, kept in
NiceGUI's tab storage, and reaches a capability through the view the page
builds for it: `SessionView(session, selection)`. The Session holds none;
`Session.select`, `Session.selection` and the `selection` event are gone. Two
tabs on one server keep two selections, which the one-tab shell (D125's tool,
sub-projects 4–6) could not — its `Shell.select_one` wrote the Session's
selection, so a second tab's click moved the first tab's plot. The Test page
(redesign sub-project 3) needs the same for its variable and criterion, so
the move was made once, here, where the shell was being rewritten anyway.

With it, `Scope` — where a capability's TAB appeared — became `Placement`,
where its BUTTON attaches: `SUITE` (the Suite page's batch bar), `TEST` (the
Test page's header), `VARIABLE` (a variable section's toolbar), `CRITERION`
(the inline criterion editor); a capability names a frozenset of them and a
`caption(view)`, the sentence its button carries (object, effect, count). The
shell draws one outlined button per capability at each placement and hosts
its panel in a dialog; it still knows no capability by name. `PROJECT` has no
successor because Close project is gone and the project-less state is
unreachable. The contract's spec is amended in §4.6 of the two-tier design;
`tests/test_capability_conformance.py` is the executable form over the four
placements and the five outcomes.

Two behaviours moved with the structure. `Session.open` runs on a thread the
shell owns and every Session event crosses to the UI loop through one queue
an app-level timer drains, so the page appears before a result is read and
rows fill in per `loading` event (brief §3.8); a model whose scoring raised is
announced with `error=True`, and `open` emits `done` and starts the rescan
even then. And the old shell's tests of behaviours the Test page re-hosts
(panels following a rescore, the leaf's config error above the editor, an
error cleared in place, the plot redrawn in place after a drag) were deleted
and named in the roadmap's sub-project 3 and 4 blocks rather than carried as
text: their shape depends on the page sub-project 3 designs. `Session.close`
stays as the library's counterpart to `open` — the Session is also driven
without a page — although no page calls it since Close project went.

## D130 — A test's plot layout is spec data; a result file is read by its backend class (2026-09-20)

**Context.** The redesigned Test page draws any number of plots per test, any
series per plot, tracked or not, and the report must draw the same plots
(brief §3.9, §3.10). Three things stood in the way: a loaded `TestResult`
holds only the tracked variables; spec patches were whitelisted to
`/comparison` and `/metrics`; the report page was one section per variable.

**Decision.** `plots` is an optional key per `test_spec.json` entry —
`[{"kind": "time", "series": [{"var": ...}]}]` — strict inside (unknown keys,
kinds, patterns, duplicates, blank names and empty series are refused by
name; `null` is refused), ignored by scoring, carried on `TestModel.plots`
(`None` = absent = one plot per tracked variable; `()` = no plots), patchable
under `/plots`, described in the exported schema. Any variable of a result
file is listed and read through two classmethods,
`Backend.result_names(test_dir)` and `Backend.result_series(test_dir, names)`,
default "none": a classmethod because the report reads a test's files without
instantiating a backend. Neither raises on a missing, half-written or
malformed file — a run killed mid-write leaves exactly that, and one test's
file must not break a whole report. `plot_figure(views)` draws a plot: one
view is `variable_figure` unchanged; several draw families (the last integer
index) in a hue ramp with a folded legend, every leaf's geometry laid out
against the union of the series' time spans and the panels any series opens,
and no overlays or error traces. Both tiers resolve a layout through
`resolved_plots`; the report page is sections per plot, numbered by position.

**Consequences.** Dymola, Julia and Python result files hold every variable,
so any of them can be plotted; OpenModelica's variable filter and FMPy's
requested outputs keep only the tracked ones, and the tool says so rather
than widening the run. A layout that names a variable a rerun dropped keeps
its plot and names the series as missing. The Session never opens the work
dir of a model a job holds — it serves what it read before the lock — and a
failed job, which no reload follows, drops what was read of its models. A
test with no `plots` key costs the report nothing: its result file is not
opened again. `/variables` stays outside the patch whitelist until tracking
from the Variables drawer needs it.

## D131 — The Test page: three ways to host a capability, one figure per plot (2026-09-20)

**Context.** The Test page is where an author edits a criterion (brief §3.2;
the owner's board of 2026-09-20). Its controls are capabilities, and D129's
placement model draws a capability one way only: an outlined button whose panel
opens in a dialog. The page needs a tree node's `+` / wrap / `−` cluster and a
criterion's editor to be capabilities too, or the shell has to know Edit tree
and Edit criteria by name — which `tests/test_capability_conformance.py
::test_the_shell_names_no_capability` refuses as an AST scan over `shell/*.py`.

**Decision.** A placement is hosted in one of three ways, and the way is the
placement's, not the capability's. `SUITE`, `TEST` and `VARIABLE` stay a
captioned button whose panel opens in a dialog (`ActionBar`) — `VARIABLE`'s
sits in a plot card's footer, so "Add a criterion for x" is a button under the
plot that draws x. `CRITERION` is an inline editor (`InlineHost`): every
capability placed there is built, without a heading and without a button of its
own, into the slot below the criterion's row, so Edit criteria and Edit on plot
read as one editor. `NODE` is new and is a node's controls: the same inline
host, drawn inside a tree row or a criteria row, which is how `+` / wrap / `−`
appear without the shell naming Edit tree. `Selection` gains `node_path` — the
node whose controls are being drawn, a leaf or a combinator — while `leaf_path`
goes on meaning "a criterion". Edit tree moves to `{NODE, VARIABLE}` and its old
rows-and-forms panel is deleted; each of its forms opens in a `ui.menu` anchored
to its button, built when that menu opens from the tree as it is then (a cluster
is ~28 elements, and 40 leaves built eagerly made a 2,387-element page). Edit on
plot moves from `VARIABLE` to `CRITERION` and is scoped to `selection.leaf_path`:
a drag on another leaf's handle is refused by name.

Each plot card owns its `ui.plotly`, its `FigureHook` and its snap-back, and a
panel hosted under that card subscribes through `hook.owned(owner)`; the host
takes the owner back when the editor closes, when it is rebuilt, and — for a
dialog — however the dialog closes, so a panel nobody can see stages nothing
from a drag. The tool's figure is always built `editable=True` and carries rings
for the OPEN leaf only (`rings_for` in `figures/plotly.py`, `editing=` on
`Session.figure` / `Session.plot_figure`). The Plotly `config` therefore never
depends on what is being edited, so opening an editor is a `Plotly.react` on the
same node and the zoom survives it. `scrollZoom` is off in the tool and stays on
in the report: the tool stacks plots full-width down a scrolling page, where one
wheel notch over a plot ends the page's scrolling and the cards below are never
drawn. Each card stamps an incrementing `layout.editrevision` on every figure it
sends, because `uirevision: "keep"` alone makes Plotly keep a shape the user
dragged when the server re-sends the value it sent before — which is exactly the
snap-back after a refused drag (measured in Chromium).

A card is a 320 px slot until it is drawn: one page-level `IntersectionObserver`
over `.dstf-plot-slot` fires one global event per slot near the viewport, which
`ui.on("dstf_plot_visible")` answers by building that plot. The page then follows
the Session by signature: on each event it re-sends only the figures whose
signature — the open leaf, and every leaf on that plot's series with its metric,
parameters, window, baseline and verdict — has moved. A finished, failed or
written job forces every drawn plot instead, since a rerun moves the
trajectories and no leaf's signature sees that. A change of the tree's SHAPE
(paths and variables) or of the plot layout rebuilds the cards, closes the
editor and re-draws the plots that were on screen. The open editor follows its
own leaf: it is rebuilt when that leaf moved behind its back (a Discard, a Save,
an edit staged from another node), and otherwise left alone so a field being
typed in survives.

Both tiers draw a test's default plots by ONE rule, `discovery.plots
.tracked_names`: what the comparison scored, in the test author's order, then
the rest of what the loaded result tracks, each taken under its name. (It took
the comparison's own `variable_label` when this was written; D132 deleted that
relabelling, and the rule is unchanged apart from it.) The report drew one
section per SCORED variable and the tool the union, so a tracked variable no
criterion covers had a plot on one tier and none on the other; the report page
now follows the tool.

**Alternatives considered.** A `controls()` method on the capability contract,
beside `panel()`: it makes "a node's controls" a second thing every capability
must implement, where a placement already says how a capability is drawn.
Rings for every leaf at once: they overlap, and the spike grabbed the wrong
leaf's ring on FakeLib's tree. A CSS-sticky sub-header below the shared header:
Quasar's header is `position: fixed`, so a second row INSIDE it is sticky with
no offset arithmetic and no dependence on the first row's height — at the cost
of one explicit rule, since NiceGUI's own `.nicegui-header { flex-direction:
row }` outranks Quasar's `.column` and otherwise renders the second row to the
right of the first. Redrawing every plot on every Session event: a twelve-plot
page re-sends twelve figures for an edit that moved one. Keying the open
editor's follow on the whole document's pending ops: an edit staged on ANOTHER
test rebuilt this editor and threw away a half-typed table row.

**Not built here**, so nobody looks for it: the key-stats row (its numbers come
from the run's statistics files, which the Session does not read); a plot
toolbar beyond Focus — the overlays and the error traces are already in the
figure as `legendonly` traces and the legend toggles them; "Set window"
(sub-project 4); Add series / Separate / Remove plot / Add plot, the 40 px rail
with the Tests and Variables drawers, and array folding (3c); the running state
(sub-projects 5 and 6).

## D132 — A tracked variable has one identity: the name its result file gives it (2026-09-21)

**What.** A tracked variable is keyed everywhere by the name its result file
gives it, and the text a model wrote for it is a separate, recorded
*expression* that is only ever a label. For a variable of the Modelica
`UnitTests` component that name is `<instance>.x[i]` — `unitTests.x[3]` — and
the text parsed out of `x={...}` (`pipe.T[1]`, `cat(1, y, y1)[2]`) becomes the
expression: stored beside the name in the reference JSON, shown as the display
label on both tiers, and soft-checked on every compare. Overrides, tree
leaves, `plots` series, baseline entries, figure ids, DOM attributes and test
markers are all the name. `backends/types.py::display_label(name, expression)`
is the one label rule; `variable_texts` is the ONE scope rule above it, and
what keeps two variables that share an expression tellable apart.

**One text rule, scoped to the test.** `variable_texts(labels, plot_series)`
returns, per name, a `label` (the `display_label`) and a `text` — the label,
with the name in parentheses when another name in the SCOPE carries the same
label and the label is not already the name. The scope is every name the
test's page can show: the result's tracked variables, the scored comparisons'
variables and every series of the test's resolved plots, plot-only ones
included. `reporting/context_builder.py::test_texts` builds it and both tiers
call it — the report through `_build_template_context`, the tool through
`Session.variable_texts` (`Session.labels` is its `text` half). The two
surfaces that already show the muted name beside their title — the report's
variable heading and the tool's card title — read the plain `label` there;
every other human-facing surface reads `text`. The console, JUnit and the
interactive review are outside the rule and keep `label_with_name`: a line of
CI output is read with no page around it, so there is no scope to
disambiguate within.

The scope is the test because five narrower ones disagreed on one page (the
b147918 review): a criteria row disambiguated over the SCORED list read
`nrmse · a.Q` under a card titled `a.Q` with `unitTests.x[1]` muted beside
it, once the other variable sharing `a.Q` lost its last criterion; a legend
disambiguated over its PLOT read `a.Q` where the row two lines below read
`a.Q (unitTests.x[1])`; and a plot-only variable genuinely NAMED `z` read the
same as a tracked variable whose EXPRESSION is `z`. The `label != name` half
of the rule is what resolves that last one: the plain `z` is unambiguous, so
the one carrying the expression is the one that says its name.

**Families fold over the label.** `figures/families.py` groups indexed names
into an array family by what a person READS, and a label the model wrote must
additionally be a plain indexed name to fold, so `der(x)[1]` and `der(x)[2]`
are two elements of one array and do, while `der(x[1], 2)` does not — a second
derivative's index belongs to the ARGUMENT of a two-argument call, not to what
the call returns.

**Why.** Two layers used to rename a variable behind the scoring's back:
`backends/common/result_variables.py::extract_variables` set the name to the
expression text (or `expr[i]`, or `x[i]`), and
`comparison/comparator.py::variable_label` turned a name holding a newline or
starting `cat(` into `x[N]` for scoring and display. Measured over the owner's library on 2026-09-21 —
331 baselines, 877 variables, read-only — the stored names were 385 plain, 60
expression text, 178 `x[i]`, 210 holding a newline (plain names whose `.mo`
source wrapped the line: `heatTransfer.alphas[\n        1, 1]`, whitespace
kept by the parser), and 44 starting `cat(`. A variable renamed by either
layer could not be addressed: about 70 tests had no editable criteria tree in
`dstf author`, because no leaf could be written against a name that only
existed inside the comparison. One identity, decided by the source of the
data, is what makes a criterion, an override and a baseline entry able to name
the same thing.

**Discovery and extraction.** `discovery/mo_parser.py` captures the component's
instance name and normalizes every parsed expression
(`normalize_expression`: whitespace runs collapse, none after an opening
bracket or before a closing one, one space after each comma — the way a
Dymola result name reads), so a source reformat is not a changed expression.
`discovery/unit_tests.py::unit_test_variables(test)` is the ONE place those
variables are named: `name = f"{instance}.x[{i}]"`, and the expression is the
i-th when there is one per variable, `f"{the one expression}[{i}]"` when one
covers several, else `""`. The backends and the OpenModelica variable filter
ask it instead of spelling `unitTests.x[` themselves.
`VariableResult.expression` and `VariableComparison.expression` carry it from
there.

**The expression is a soft check.** When a baseline entry and the run's
variable both record a non-empty expression and they differ, the test gains a
`StructuralWarning` — the console prints it as
`Expression of unitTests.x[2]: a.Q -> b.Q` — through the same channel as the
structural warnings. It never fails the test. That is
what makes a positional name safe: the position is the key, and the check says
when the quantity under it moved. Against the PRIMARY baseline every variable
of the result is checked; a NAMED baseline is checked only on the variables
some leaf actually scores against it — it is imported whole, so a difference
in a quantity nothing compares with it is not a finding about this run — and
then once per (variable, baseline) pair, not once per leaf. A leaf that
resolves no variable of the result (a `scalar` leaf reading a stored
statistics path, whose `variable` is free text naming the leaf) registers
nothing.

**Overlay matching is the one exception, and the record is the companion's
own** (owner ruling 2026-09-22, replacing the fallback-only rule this
paragraph first carried). An overlay is another source's data — the repo's
example companions are Julia baselines naming the quantity `x` / `y` — and no
other tool can be expected to spell a positional `unitTests.x[2]`. Which of a
companion's variables draws on which of the test's is a fact only whoever
registered it knows, so the companion records it in its metadata JSON:
`"variables": {<test variable name>: <companion variable name>}`, written by
`dstf companion add … --map TEST_VAR=COMPANION_VAR` (repeatable), carried
through `freeze_companion`, read into `Companion.variables` and onto
`Overlay.mapping`. `add` refuses a mapping naming a variable the test's stored
baseline does not hold, or one the companion's file does not hold; two test
variables MAY name one companion variable, which draws the one curve twice.
`add` is the only action that writes the record, so it is the only one that
accepts `--map` — `freeze`, `remove` and `list` refuse it as a usage error
rather than ignoring it — and a model with no stored baseline is told exactly
that, by the same error `add_companion` raises for it, instead of being shown
an empty list of the variables its baseline holds.
Where an overlay HAS a mapping, that is the whole rule in
`attach_overlays_to_trajectories`: a trajectory the mapping does not name gets
no trace from it, even when the overlay holds a variable spelled exactly like
it — guessing behind a recorded answer is how somebody else's curve ends up
under the wrong plot.

**The fallbacks are for a COMPANION nobody mapped** — the ones registered
before there was a mapping to write — and for nothing else (review correction,
2026-09-22). In order: the companion variable whose NAME is the trajectory's
name (the two sources agree on a spelling); whose NAME is the trajectory's
EXPRESSION (the positional name is on the test's side, and the text the model
wrote is what the two share); whose own EXPRESSION is the trajectory's name or
expression (the mirror, for a companion that is itself a DSTF baseline of
another library — which is what the repo's example companions are, and why
none of the seven Julia-side ones drew anything before this change). A step
that finds MORE THAN ONE candidate attaches nothing and records a warning on
the overlay, surfaced by `overlay_summary` on the report's overlay table,
naming the trajectory, the candidates and the `--map` that settles it:
`x={a.Q, a.Q}` is legal, so one expression can belong to two of a companion's
variables. That remedy is worded as `dstf companion add … --map`, which is the
only command there is for it — and, the fallbacks being a companion's alone,
the only overlay that can reach the warning.

**Everything else on the picker matches BY NAME ONLY**: a soft_check, a
cross-backend import (stored as one), and the auto-discovered primary of a
sibling partition are DSTF's own data, keyed by the names this test uses.
A soft_check is also SCORED by name, through `against:`, so the curve drawn
for it has to be the curve scored for it — a spelling rule that found some
OTHER variable of it would draw one thing and score another. The first
version of this paragraph let every unmapped overlay fall back, and a
soft_check holding `other.name` with expression `a.Q` drew on every
trajectory whose expression was `a.Q`.

**A malformed record is a record.** `variables` holding an entry whose value
is not a variable name, or not being an object at all, is a hand-edit, not an
old registration: `storage/roles.py::_read_variable_mapping` drops the entry
and RETURNS the finding beside the pairs, `Companion.mapping_warnings` carries
it to `Overlay.warnings` and the overlay table, and `Overlay.mapping_malformed`
keeps the companion on its own record — it draws what survived reading and
never falls back. A mapping naming a variable the companion's file does not
hold is read the same way at load time: one warning per stale entry, naming
the test variable, the companion variable and the file. Only an ABSENT record
falls back — the key missing, or the empty object a registration with no
`--map` writes.

**The seam, and its coordination.** Reference-JSON variable names and the
`test_spec.json` keys that named an expression both changed, and stored
baselines are a surface other trees depend on. There is NO compatibility
read: an un-migrated baseline meets the existing "the baseline holds no
variable named … — treating as missing baseline" path, whose message gains a
clause naming the command. `dstf migrate-variable-names` is the coordination
— dry-run by default, `--apply` writes; it walks every
`<reference_root>/<Backend>/<os>/` partition (primary refs and soft_checks),
rewrites a UnitTests entry whose stored name normalizes to that index's
expression, gives every other entry `"expression": ""`, and renames the
matching `variable_overrides` keys, `metrics` leaf variables and `plots`
series in `test_spec.json`. A file it cannot match entirely — or cannot read
at all — is reported and left untouched, and it exits 1; that skip stops that
one file and not the run, because no cross-file decision reads a baseline's
content and one stray junk file must not stall a tree of 900. The spec is the
exception: while IT cannot be read nothing at all is written, its keys and the
names the baselines are stored under being one coordinated change. **It is
deleted at the v1.0 tag**: nothing reads the old shape, so once the trees that
matter have been migrated it is a code path nobody exercises.

**Deleted.** `comparator.variable_label`; the seven `name or f"x[{index}]"`
index fallbacks (a nameless result variable is a backend bug, not an
invitation to score it against whatever entry sits at its index); the
`ref_var.get("name") or ref_var.get("expression")` name fallbacks in the
comparator and the overlay loader — nothing has written `expression`-as-a-name
since before v1.0, and the key now means something else.

**The scan cache.** `discovery/scan_cache.py` rebuilds `TestModel`s from disk,
so a change to what a scan MEANS can make a cached entry mean something the
current code would never have written, with no source file changed for a
staleness check to see. Its key is no longer a hand-bumped constant alone: it
carries a digest of the discovery modules' own source bytes
(`json_recognizer`, `mo_parser`, `recognizer`, `test_registry`,
`unit_tests`), because the hand-bumped version was forgotten twice — once for
the instance name and once for expression whitespace.

**Alternatives considered.** A bespoke label→name mapping table, so a baseline
could keep its expression as a name: it makes the mapping a third thing to
keep in step with the model and the store, and the model's own `x={...}` is
already the mapping. Renaming only the variables whose expression is awkward
(`cat(`, a newline): the identity would then depend on how the source happened
to be formatted, which is exactly the coupling this removes. Reading an
un-migrated baseline by falling back to the expression: a compatibility read
that hides the very drift the soft check exists to report, and one the v1.0
tag could never remove.

## D133 — A node's forms open as a row under it; a plot's legend holds its signals (2026-09-24)

**Context.** The owner's board of 2026-09-21 ruled two things about the Test
page (D131): Edit tree's add / wrap / edit forms opened in a `ui.menu` over
the node's row, covering the rows around it; and a plot's legend listed every
criterion's drawing and all three error series beside the signals, inside the
plot area, on top of the trace's start and of the rings an author drags.

**The form row.** A node's forms open as a ROW below the node's row — a tree
row or a criteria row — indented like the criterion editor, with its rule; the
node's row stays lit, and the form's title names the node the way its row reads
(`authoring/tree_text.py`, shared by the rows and the titles). A form shows
only the chosen combinator kind's fields, one sentence saying what the action
does to the tree, and Cancel beside the action; Unwrap lives inside Edit. One
thing is open at a time on the page: a form replaces the open criterion editor,
the editor replaces a form, and a click on a criteria row closes a form open
under it and opens nothing. The page owns that rule (`TestPage.open_row`), and
a capability reaches it through two methods the `Panel` contract gains:
`row(title, build)` and `reveal(leaf_path)` — after an Add, the new criterion's
editor opens under its plot. A form is built when its icon is clicked, from the
tree as it is then, and rebuilt when an edit staged elsewhere moves its node —
the criterion editor's rule applied to a node — so it never shows a value the
node no longer has; it closes with its host, when its row is hidden, and when
its node is gone. `Panel.submit` returns whether the act went through, and a
form closes on that and only then: a refused Wrap, Add or Edit keeps its row
open with what was typed, and the refusal shows under the node's row, above the
form. That error belongs to its row: it goes when THAT row's form opens or
closes or its host rebuilds, and another row's form leaves it alone. Two
rulings of 2026-09-25 complete it: a row that opens below the fold scrolls just
far enough to show itself (`scrollIntoView({block: "nearest"})`), and a row
already in view does not move the page; and a weighted form's threshold starts
empty, its action grey with `enter a threshold` until one is typed, because
threshold 0 with `less` always fails, while its weights start as one `1` per
child. Edit stages only what changed.

Unwrap of a combinator with ONE child gives its place to that child, as
before. With several, they take its place in its parent, in order: `remove` the
node, then `add` each child at the node's index plus its own. That goes through
the validator like any op, so a `warn` or `weighted` parent refuses it. The
root has no parent to take them, so its Unwrap is disabled with that reason.
`+` is grey where a child cannot be added alone — under a `warn`, which takes
exactly one child, and under a `weighted` node, which has one weight per child —
with the reason as its tooltip, and it follows the parent's kind when an edit
changes it.

**Legend b.** In the tool, a plot's legend holds its SIGNALS — actual,
reference, overlays — and nothing else. Each criterion's drawing is shown and
hidden by a swatch on its criteria row, and the three error series are one
Error select per plot that draws one series, on the card's toolbar.
`figures/plotly.py` takes a `Visibility(hidden, error)`: with one (the tool),
every leaf trace and every error trace is out of the legend, a hidden leaf's
traces and shapes are `visible: false`, and only the chosen error series shows,
with its axis. A hidden shape stays in `layout.shapes`, so shape indices do not
move and the figure hook still resolves a dragged ring by its index. The card
keeps the choices, and they are part of its redraw signature, so a swatch click
or a select change is one `Plotly.react`. A zoom survives that redraw, and a
signal the author hid by a legend click stays hidden, because the server never
changed that trace's `visible` and `uirevision` keeps the user's. Both were
measured in Chromium. A newly chosen error series is the exception: its axis's
`uirevision` is the chosen series, so the axis scales to it afresh rather than
keeping a zoom made over another series, or made while it hid.

What draws before a swatch is touched is a property of the metric:
`MetricDescriptor.drawn_by_default`, `True` unless the metric says otherwise.
The builtins `range` and `scalar` say `False`, because their bounds cross the
whole plot. Over that default, a failing criterion draws, and the open
criterion always draws, since the plot is its editing surface. Its swatch is
locked while its editor is open. A FAILING criterion's swatch hides it like
any other (owner ruling, 2026-09-24). The failure stays visible without its
drawing: the row's FAIL pill beside the swatch, the card's pill, the closed
tree line's tally and jump, the test's header badge and the Suite page. A
swatch that worked on one row and was grey on the next, by a verdict that
flips on every rerun, would read as broken.

The legend moved ABOVE the plot area on both tiers: one layout dict,
`LEGEND`, horizontal and anchored at the plot's top, for which Plotly grows the
top margin. The report keeps its criterion entries for now; its eight-entry
legend, measured at 900 px, wrapped to a second row.

**The toolbar gutter.** A legend above the plot met Plotly's toolbar, which
appears across the plot's top on hover: at 900 px the report's eight-entry
legend wrapped to two rows and the toolbar covered two of its entries. The
owner chose a VERTICAL toolbar, on both tiers, over a legend below the plot,
which collided with the x-axis title, and over no toolbar. Standing on the
right edge, the toolbar then covered the error axis's title and tick labels. A
margin reserved inside the figure while an error axis shows was built and
failed. A fixed reserve cannot know how wide the labels will be: an error
around 1e-5, the usual size for a passing test, printed `0.00002000` and
reached the toolbar at every reserve measured. The toolbar and the right-hand
axes were competing for one strip inside the figure. So the toolbar gets a
gutter OUTSIDE the figure, in CSS on each tier (`shell/style.py`,
`templates/interactive.html`): the plot is 34 px narrower than its container,
and `.modebar-container` sits in those 34 px. Measured in Chromium: no label or
title box meets the toolbar, for any error series the tier allows, including
1e-5 labels. Browser tests on both tiers hold that and hold the toolbar inside
its card.

Two more rulings of 2026-09-24 settled what the labels print. Every y axis
uses Plotly's automatic tick labels with `e` exponents (`0.2`, not `0.2000`;
`2e−5`, not `0.00002000`). Every y value in a hover reads four significant
digits with trailing zeros trimmed, d3 `.4~g`, in the y axes' `hoverformat`
and in every trace's own `hovertemplate`; the time axis hovers with `.6~g`.
In the tool's figure, each error axis's `automargin` follows its visibility:
a hidden axis left at automargin kept the right margin it had grown, through
every later react. NRMSE's axis is anchored to the plot there, since the tool
shows one error axis at a time. The report keeps it free and auto-shifted,
since its legend can show signed and NRMSE together.

**The axes.js regression, and why the fix is structural.** When the old shell
was deleted (22d3284), the tool's page stopped loading `templates/axes.js`,
which 39363f4 had added so that a legend click on an error trace shows that
trace's axis. From then on, an error trace clicked on in `dstf author`'s legend
drew against a hidden axis. Loading the script again would
have fixed the symptom and kept the cause: the page following a client-side
restyle the server never hears, one script watching another's plot. After this
change the tool's error traces are not in its legend. The select is on the
server, and the figure it sends says which error axis is visible. No client
code has anything to follow, so the tool needs no axis script. `axes.js` is the
report's alone, where a legend click and the error select are still
client-side restyles. `report_suite.axes_js_source`, which only a test still
called, is deleted, and the prose that said the tool loads the script is
corrected.

**Alternatives considered.** Keeping the menus: an open one covered the
neighbouring rows' icons (3b's gate over a copy of the owner's library), and
the board put the form where its node is. A swatch greyed on a failing
criterion: the ruling above. The report's legend below its plots: it
collided with the x-axis title. A margin reserve: see the toolbar gutter. Tick
labels inside the plot area: they still met the toolbar. Loading axes.js in the
tool again: the regression paragraph.

**Not built here**, so nobody looks for it: the report's criterion entries
leaving its legend (a later change, on the board's table); the "Add a
criterion for x" footer stays a dialog; the choices a card keeps reset when a
criterion is added or removed, because paths shift then; a tooltip can stay
up after its icon is clicked.

## D134 — The layout is edited on the plot you selected, one op on `/plots`; the rail holds two drawers (2026-09-25)

**Context.** D130 made a test's plot layout spec data — the `plots` key — but
the tool could only draw it; nothing in `dstf author` edited it. The owner's
3c rulings (2026-09-25, in chat and on a rendered board) scoped the editing:
3c is layout only, and 3d adds the slice field and the save-time expansion of
a folded row into one criterion per index. The Variables drawer's checkboxes
act on the plot you CLICKED, the first until then, and the drawer names its
target (Q5 A). The drawer does the layout edits: a check adds a series to the
selected plot or takes it off, "+ new plot" puts a row on a plot of its own
below it, and the plot toolbar gains only Add series and Remove plot — the
series chips DESIGN.md had scheduled are dropped (Q6 A). A variable a
criterion scores cannot leave its last plot (Q7 A). Reset layout sits in the
tree card's line while the entry has a `plots` key (Q8 A). A Tests drawer
shows the Suite page's list and switches test (Q9). A name folds on its last
index whenever that index is a single integer, whatever comes before it
(`massPorts[4, 1].C[]` is a family). Save says when it creates an entry. The
header's rarely used actions go into a ⋮ menu, which fixes a 900 px header
that cut off "dashboard" and "Stop server" (Q10 A).

**Decision.** Five moves.

1. *The family rule* (`figures/families.py`, `family_of`): a
   multi-dimensional index refuses a family only when it comes AFTER the last
   single-integer index (`a[1].b[2,3]` has none), and an expression may index
   with an integer list (`_INT_INDEX`, `[4, 1]`, no space just inside the
   brackets); a slice (`[6, :]`) still refuses it.
2. *The layout is data, edited by pure functions* (`authoring/layout.py`,
   UI-free): `current_layout` (the key, else one plot per tracked variable),
   `add_series`, `remove_series` (a plot left empty goes), `new_plot` (below
   the selected one, returning its index), `remove_plot`, `stranded` and its
   one sentence `stranded_sentence`, and `layout_op` — the ONE op a gesture
   stages, `add /plots` when the effective entry has no key, `replace` when it
   has — with `reset_op`, the `remove`. `Session.layout` is the layout a
   gesture edits: unlike `Session.plots` it keeps a series the result lacks.
   `Session.stage` collapses a run of such ops into one pending op.
3. *The drawer's rows are data too* (`authoring/variable_rows.py`, UI-free):
   `variable_groups` groups the result's names (Tracked, then the first dot
   component of the name, "(top level)", Diagnostics), folds families over the
   test's texts and reads each row through its labels, filters by `matcher`,
   and gives each row its state on the selected plot; `cap` keeps the first
   rows, 200 in the drawer, and counts the rest for the drawer to say.
4. *The rail* (`shell/rail.py`): `Rail` puts 40 px of labelled buttons in a
   `ui.left_drawer` with `behavior=desktop`, so an open 320 px drawer pushes
   the plots narrower at every width instead of covering them. `TestsDrawer`
   draws a `TestList` (`shell/test_list.py`, extracted from the Suite page in
   its own refactor commit) without checkboxes; `VariablesDrawer`
   (`shell/variables_drawer.py`) draws the rows. The Test page owns the
   selection (`TestPage.selected`, `select`), the one way an edit is staged
   (`stage_layout`: the selection set before the stage, put back on a
   refusal, which is a notification), `remove_plot` and `reset_layout`.
   `PlotCard`'s toolbar gains Add series and Remove plot, and a click on the
   card outside `.dstf-plot-slot` selects it; `TreeCard` gains Reset layout.
   `TabState` gains `drawer` and `tests_needle`, so the open drawer and the
   Tests drawer's filter survive a switch of test within the tab.
5. *The header* (`shell/header.py`): Save and Discard stay in the row, and a
   ⋮ menu at its end holds Export, Change backend…, Open in another
   backend…, dashboard and Stop server at every width. The row names the
   backend as plain text; Change backend… opens the dialog that holds the
   select, and while it is grey its item carries the sentence that says
   why. The backend select had stayed in the row after Q10, and in two real
   states — a rerun running with an edit pending, and a Save caption naming
   a new entry, the usual one on TRANSFORM — Save took three lines at 800 to
   900 px and two at 1280; so the select left the row (the owner's ruling,
   Q13 A), which reverses DESIGN.md's header line that drew the backend as a
   select. The board said A holds the longest Save caption on one line at
   900 px; that was never measured, and it is false (corrected 2026-09-26):
   the new-entry caption alone is 357 px, and one line needs 960 px even with
   the run indicator and Save's why-sentence hidden. Measured with A built,
   in that busiest state: at 900 px Save and Discard take two lines, nothing
   is cut off and nothing scrolls sideways; at 1280 px every button is one
   line. That meets the owner's earlier ruling (2026-09-25) — at most two
   lines at 900 px, one at 1280, nothing cut off — and not one line at 900.
   The Test page's subheader wraps: kept to one line, it put Accept and
   Rerun past a 900 px window beside a real library's model id, wholly off
   it beside a TRANSFORM-length one, with Rerun squeezed to seven lines. Now
   the id breaks inside itself only when no line holds it, and the TEST
   actions keep to the right, below the id as one group when they do not
   fit beside it. `save_caption` counts the entries a Save creates over the
   document as LOADED, since in the effective one the pending op has already
   made the entry; `plural` takes an irregular plural.

Nothing in the pre-release seams moves: the CLI, `testing.json`, the
reference-JSON layout and the `test_spec.json` format are unchanged. The tool
writes the `plots` key `parse_plots` already defined; a layout staged for a
test that only its `.mo` annotation declares creates that test's first entry,
and Save's caption says "1 new entry".

**Consequences.**

*The collapse rules* (`Session.stage`, its Convention 8 block; the tests in
`TestStageCollapses`). Every layout gesture stages the whole `/plots` list,
so without a collapse five clicks read "5 spec edits". When a single staged
op lands on the model and path of the last pending one: a `replace` after an
`add` or a `replace` is that op with the new value, and a `remove` after a
`replace` is the `remove` — the `replace` needed the member to exist, and
the `remove` takes it. Both hold in RFC 6902 for any path. A `remove` after
an `add` is NOT collapsed: when the member existed before, the `add` replaced
it, so dropping both would leave it in place, and keeping only the `remove`
would fail when it did not exist. Nothing collapses at an append path
(`/-`), which names no member and is left for the effective document to
refuse, and a batch never collapses, into the last pending op or through its
own last op: its ops belong together. A refused stage keeps the op a
collapse would have replaced.

*The family rule changed, and a differential pins the change.*
`test_family_of_over_names_changed_only_where_3c_ruled`
(`tests/test_plot_figure.py`) runs 25,000 generated result-file names
through `family_of` and through `_family_of_after_3c`, a copy of the earlier
rule with only the ruled change: they agree on every name, and every name
the change touched belonged to no family before and now folds with a
multi-dimensional index before its `[]`. The plan's review of this task
parsed all 332 of TRANSFORM's `.mo` files read-only: six expressions change
their `family_of` (the `massPorts[4, 1].C[1]` expressions of `pipe_single` and
`pipe_nParallel`, in three models), and the families of two or more stay ten
before and after, because each such port carries one species — a family of
one. So TRANSFORM's expressions gain no new family of two or more.

*The folded criteria row moved to 3d* (the owner's ruling, 2026-09-25). In
3c a family's criteria stay one row per member; the folded row belongs with
the save-time expansion that writes one criterion per index.

*What the drawer costs.* `TestPage.rebuild` reads the layout and the scored
counts ONCE and hands both to every `PlotCard`: a card that read them itself
cost one layout and one tree read per plot, and the page's existing cost
tests (`test_a_rebuild_reads_the_tree_and_the_tracked_names_once`,
`test_the_page_costs_the_same_whether_it_has_five_cards_or_forty`) hold
unedited. The Variables drawer reads each of the Session's facts once per
build (`VariablesDrawer._read`). `Rail.refresh` is the one way the page's
changes reach it, and the open drawer builds again only when its inputs —
the reads, the selection, the number of plots, the filter, the opened
families — moved (`VariablesDrawer.refresh`, `_signature`): a rerun's job
events that change none of them build nothing. A closed drawer is not
followed; it is rebuilt when it opens. `variable_groups` indexes the layout
once rather than scanning every plot per name, which cost 395 ms over 6,093
names and 300 plots.

*The glob cannot backtrack.* The drawer's glob means what a spec's
`variables` list means (`_pattern_to_regex`: anchored, `[ ]` literal), but
compiled to that one regex, `*a*a*a*a*a*a*a*b` over one 60-character name
took over 20 s on the UI event loop, which blocks every tab. `matcher`'s
`_glob` splits the needle on `*` into quantifier-free pieces: the first
must match at the start, the last at the end, and each middle one is found
left to right, where the leftmost place leaves the most text for the rest.
`test_the_glob_means_what_the_specs_regex_means` holds it identical to the
anchored regex over every glob and text of up to four characters, both case
settings. A typed regex (`.*` on) is the `regex` package's, not `re`'s:
`re` has no timeout, so nothing can stop a pattern that backtracks (both
hold the GIL for a search, `regex` because `concurrent=False` asks it to),
and the owner chose `regex` (in `[author]`), whose `timeout=` does, under
one budget per filter: 1 s of the searching thread's CPU (`thread_time`),
so the filter's waits for the GIL between searches, behind a busy thread
of the tool, never make a plain pattern "too long". That budget does not
bound the freeze, since the filter runs on the UI event loop and every tab
waits out its wall time (17.4 s for a backtracking pattern beside a thread
holding the GIL), so a second limit of 5 s of wall time (`monotonic`),
checked between searches, does. Not bounded: `regex.compile` of
nested fixed-count repeats (`(a{5000}){5000}`) can take seconds or exhaust
memory before any budget applies, where `re` compiled it at once;
accepted as contrived, on the user's own local tool.

*A row acts on the layout as it is now.* A check or "+ new plot" re-reads
the layout when clicked, decides on or off from what the selected plot holds
then, and does nothing for a selection outside it: a row is drawn before the
last change, and a double-click on the last plot's only series raised on its
second click. The check is a button with `role=checkbox` and
`aria-checked`, not a `ui.checkbox`, whose `set_value` fires its own
`on_change`: a repaint would stage an edit. Row markers carry no whitespace
(`variables_drawer.marker`), because NiceGUI splits a marker on it.

*The gate.* The TRANSFORM gate's numbers: see the roadmap's 3c BUILT
paragraph.

**Alternatives considered.** Series chips on each plot (DESIGN.md's earlier
plan): the owner ruled the drawer the one place for adding and removing
series. A drawer that overlays the plots: below Quasar's 1024 px breakpoint
that is what a drawer without `behavior=desktop` does, and it would cover the
plots it edits. Collapsing a `remove` after an `add`: the case above, where
either collapse is wrong for one of the two starting states.

**Not built here**, so nobody looks for it: the slice field, the save-time
expansion and the folded criteria row (3d); moving plots up and down, and
plot titles (`docs/ideas.md`); tracking a variable from the drawer, so
`/variables` stays outside the patch whitelist (D130).

## D135 — A new criterion is valid when it is added; errors read as their reason; only a handle's drag is staged (2026-09-26)

**Context.** The owner's first hands-on look at `dstf author` (the feedback
board's settled block, 2026-09-26) found four defects that no later redesign
step rebuilds, ruled "these bugs get fixed whatever you choose": a criterion
with a time window could not be dragged; a new `range`, `tube`, `scalar` or
`dominant-frequency` criterion arrived misconfigured; errors showed raw
exception text; and the dominant-frequency spectrum squeezed real peaks into
its left edge. The rest of that board waits for the steps that rebuild it.

**Decision.** Four fixes.

1. *Area shapes under lines; a drag on what is not a handle snaps back*
   (`figures/plotly.py::_layout`, `authoring/builtin/edit_on_plot.py`).
   Under `edits.shapePosition` every shape takes a drag and the topmost wins.
   A real-mouse spike in Chromium on the tool's plotly.js 3.1.1: the window's
   rect, drawn after the bound, took every drag on the bound inside the
   window; `layer: "below"` restacks the drawing but does NOT change what a
   grab takes, and a per-shape `editable: false` has no effect. So `_layout`
   draws every rect before every line, on both tiers. The bound's dotted
   `<id>/outside` continuation is full width, so inside the window it lies
   over the bound and a grab there arrives under its name: it resolves to the
   bound. A drag on a primitive that is not a `Marker` or `HLine` of
   `kind="handle"` — a window, a box (`points/box/i` and `peaks/window/i`
   are `kind="handle"` Boxes, so the check is by type), a result line —
   returns nothing, before the open-leaf check; the figure hook's redraw
   snaps it back. A rect still takes a drag: only a handle's is staged.
2. *A metric may seed a new leaf* (`MetricDescriptor.seed`,
   `comparison/seeds.py`): a pure function of the reference series, else the
   actual one, giving the params a leaf needs. Edit tree calls it on add and
   Edit criteria on a metric switch (`seed_data` reads the variable's view).
   `range` brackets the data with a 5% margin, `tube` a band 5% of the span
   wide, `scalar` the final value (within 1% of the baseline's, or between
   bounds when there is no reference), `dominant-frequency` the strongest
   peak, 5% or two FFT bins wide and at most +-50%. A bound or width is
   rounded AWAY from the data at the margin's leading digit: rounded to 6
   significant digits, 101325 +- 0.001 seeded `min_value == max_value` and
   the leaf failed on its own data. `nrmse`, `points` and `event-timing` are
   valid bare and have no seed.
3. *Errors carry a reason* (`PatchError.reason`, `HandleEditError.reason`,
   `authoring/errors.py::user_text`). `str()` keeps the location the CLI
   prints — `validator rejected the patched tree: metrics: ...`,
   `/metrics/children/1: range/max: ...` — and the tool shows the reason,
   since the location is already on the screen beside the row or plot it
   came from. A person's own error (the five `USER_ERRORS`) or a ValueError
   reads as its reason; anything else keeps its class name. A ValueError can
   also be a bug (a JSONDecodeError is one), so the two act `submit` sites
   log whatever is not a `USER_ERRORS`. The validator's five messages are
   sentences ("Every tree needs at least one criterion scored against the
   primary baseline outside a 'warn': a tree of warnings alone can never
   fail.").
4. *The spectrum is ranged to its peaks* (`figures/plotly.py::
   _spectrum_range`). Autoranged, `xaxis2` ran 0..Nyquist, and Nyquist
   follows the sample count, not the physics: TRANSFORM's peaks near
   1.2e-5 Hz sat in the leftmost ~10 px of an axis to ~1.7e-3 Hz. The axis
   now runs to 3x the highest declared or matched peak (its window's upper
   edge), else the strongest detected one, clamped to Nyquist but never
   short of a declared peak; with no peak above zero it keeps its autorange.

Nothing in the pre-release seams moves: the CLI, `testing.json`, the
reference-JSON layout and the `test_spec.json` format are unchanged. A seeded
leaf is ordinary spec data.

**Consequences.** A figure's shape ORDER changed (`figure_event_timing.json`
re-blessed: its window rect comes first); the figure hook resolves a dragged
shape's index through the current figure, and nothing in the report
addresses a shape by index. Every seeded leaf is tested to pass when scored
on the data it came from, through `evaluate_spec`: `resolve_mode` alone
accepts a bare `tube` and a bare `dominant-frequency`. A variable no
criterion scores has no reference in its view even when the baseline file
holds it, so it seeds from its actual result (a scalar then gets bounds).

**Alternatives considered.** `layer: "below"` on the rects and
`editable: false` per shape: measured, neither stops a grab. Ranging the
spectrum to the highest detected peak: the reference's ten detected peaks
include leakage sidelobes, and on a 2 Hz sine that put the axis at 40.8 Hz.

**Not built here**, so nobody looks for it: the "Add a criterion for x"
dialog not opening its editor, the tree `+` wording and placement and the
weighted-combinator hint (the Test page step); rows waiting for the scan
(the Suite table step); the squeezed tolerance column (the tolerance step).
