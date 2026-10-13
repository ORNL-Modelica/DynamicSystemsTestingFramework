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
old partitions keep working; write failure warns, never fails an accept;
a shipped example partition carries none — it has lost no baseline, and
the two an accept on a scratch copy left in the Modelica example were
removed on 2026-10-12, the next id being the scan's either way).
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
   Edit criteria on a metric switch (`seed_data` reads the variable's view,
   cut to the leaf's window when it keeps one across the switch).
   `range` brackets the data with a 5% margin, `tube` a band 5% of the span
   wide — either margin at least 1e-6 of the data's magnitude, since a
   reference stored at 7 significant digits excluded its float32 result
   (TRANSFORM OilWater_PTHX `STHX.tube.mediums[2].h`) — `scalar` the final
   value (within 1% of the baseline's, or between bounds when there is no
   reference), `dominant-frequency` the strongest peak, 5% or two FFT bins
   wide; a peak whose two bins exceed +-50% (the lowest three bins) is a
   trend's leakage, not a frequency — 279 of TRANSFORM's 322 varying
   variables have their strongest peak there — and is not seeded. A bound or width is
   rounded AWAY from the data at the margin's leading digit, or left
   unrounded where that would land on the data: rounded to 6
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
   fail."), joined by a space. A tree the parser refuses carries its
   location apart too: `MetricSpecError` has a `path` and a `message`, as
   `ValidationError` does, and the reason is the message alone.
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

## D136 — One row model and one vocabulary for the dashboard and the Suite page (2026-09-26)

**Context.** The owner ruled on the feedback board (2026-09-26): "The
dashboard and the Suite page share one row model: the same columns and the
same words on both. The dashboard stays a static file, for CI and for
archiving." And, for filtering, typed column filters: status by the count
pills, numbers by `>1e-4` / `1..10`, text by glob, with `.*` and `Aa` once in
the toolbar. Before this, one state had four spellings — the classifier's
`PASS/FAIL/SIM_FAIL/NO_REF`, status.json's `queued/running/passed/failed/
timed_out`, the dashboard's own map of those, and pills reading "passed / sim
failed / no baseline" — and the dashboard spelled its fifteen columns out
twice in the template (header, filter row) and a third time in each row's
`data-*` attributes.

**Decision.** Three modules own it.

1. *One vocabulary* (`reporting/status.py`). The four verdicts plus the
   three live states `QUEUED`, `RUNNING`, `TIMED_OUT`; `STATUS_ORDER` (the
   pills' order and a status sort's), `STATUS_CLASSES` (the CSS class both
   HTML surfaces style a status with), `STATUS_LABELS` (the word a person
   reads; only `TIMED OUT` differs from its key) and `live_status(raw)`. A
   status.json "passed" means the simulation ran, not that the test passed,
   so it maps to no status: such a row reads **SIMULATED**, styled with the
   theme's existing `pending` token ("no verdict yet"). SIMULATED is a word,
   not a status — it is in no pill and no count, and the bar's pending
   segment carries it so a live bar still moves while tests finish.
2. *One row model* (`reporting/rows.py`). `TestRow` (a frozen dataclass),
   `COLUMNS` — each column's key, label, kind (`text` / `number` /
   `status`: what a filter on it means), display text and sort value — and
   the builders: `summary_fields` (the one computation of a row's numbers),
   `row_from_status` (a live row) and `enrich_row` (a sidecar's summary onto
   a row, behind the two D91 guards). The per-test sidecar's `summary` is
   written through `summary_fields` from the `TestComparison` itself:
   `generate_comparison_plots` takes `comparison=` instead of a status pair,
   and gains `status` (the classifier's word) beside `status_text` /
   `status_class`. A sidecar written before carries only `status_text`;
   `enrich_row` reads the status back through `STATUS_LABELS`. The
   dashboard's template loops over `COLUMNS`: every row carries
   `data-<key>` (what a filter reads) and `data-sort-<key>` (what a sort
   reads; empty for no value, which sorts last in both directions) for every
   column. `data-status` stays the status class — the pills', the counts'
   and the live tick's key. The Message column keeps the key `detail` (the
   column-resize follow reads `--col-w-detail`). The Variables column sorts
   and filters on the number passed, as it sorted before: `>2` means more
   than 2 passed, and its header tooltip says so. (Renamed **Criteria**,
   key `criteria`, on 2026-10-12: what it counts is the leaves of the
   test's tree — `3/3` for a test that scores ONE variable three ways,
   `21/21` for twenty elements of an array and one more criterion on one
   of them — and it said "Variables". The number is unchanged. Two more
   places said "Variables" of that count and say Criteria since: the
   interactive review's `Variables: 2/3 passed, 1 failed` is `Criteria:
   2/3 passed, 1 failed`; and the report page's Reference Info row
   `Tracked Variables | 23 current / 21 reference`, which set the run's
   criteria beside the variables its baseline stores as if they were one
   quantity, is two rows, `Criteria | 23` and `Reference Variables | 21`,
   each left out when there is no such count.)
3. *One typed-filter module* (`templates/filters.js`, `DstfFilters.compile(
   kind, query, {regex, case})`), inlined before `dashboard.js`. Text: a
   substring, an anchored glob when the query holds `*` or `?` (every other
   character literal, so `x[1]` is an index — the rule a spec's `variables`
   list and the tool's Python `_glob` use), or with `.*` on a regex. Numbers:
   `>`, `>=`, `<`, `<=`, `=`, a bare number, or an inclusive range `a..b`,
   read off `data-sort-<key>` — the raw value, never its display text, so
   `=1.23456e-5` matches a cell that reads `1.2346e-05`. A query that cannot
   be read marks its input red with the reason as its tooltip and filters
   nothing. The status column has no text filter: the pills filter it, and
   they now read the verdict words (`PASS 330 · FAIL 0 · SIM_FAIL 1 ...`).

Two defects this fixes. The Resolution column had a filter input and no
`data-resolution` on its rows, so any text typed there hid every row;
generating every column's attributes from one list fixes it by construction.
And a final page without sidecars — `dstf run` / `compare` without
`--report` — read PASS for every test that simulated, including one whose
criteria failed: `render_final` / `render_final_to` now take the comparisons
their callers hold, and each row with a comparison gets its verdict through
`summary_fields`, the same computation the sidecar uses; a fresh sidecar
still enriches it after.

**Consequences.** The dashboard's saved-state key moved from
`dstf-dashboard-v1:` to `dstf-dashboard-v2:`: pill and filter keys changed,
and a v1 state restored a pill key no pill carries, hiding every row. A
filter or sort saved under v1 is dropped once. The pills, the bar and the
counts are keyed by status class (`.counter.pass`, `.b-fail` = fail +
sim-fail). The duplicate row numbers `_build_per_test_args` and the report
index computed and nothing read are gone. JS globs and regexes backtrack: the
same semantics as the Python `_glob`, not the same cost (`_glob` exists
because `*a*a*a*b` took more than 20 s), so a runaway pattern can freeze the
browser tab it is typed in — only that tab. Nothing in the pre-release seams
moves: the CLI, `testing.json`, `test_spec.json` and the reference-JSON
layout are unchanged; the sidecar gains a field.

**Not built here**, so nobody looks for it: the tool's Suite table. It will
build `TestRow`s from its Session and give the same `COLUMNS` and
`filters.js` to its grid in S2, whose gate renders the dashboard over a copy
of TRANSFORM's work dir.

**Amendment (2026-09-27): a final page lists every test scored.** The S2
gate over TRANSFORM found the final rows narrowed to the last run's scope:
rows came from `status.json`, which each run rewrites with its own tests
only, so `compare --report-format html --output` over 331 comparisons
showed 5 rows after a 5-test `--filter` run, and a tool Rerun of one test —
and every Save / Discard re-render after it — wrote a 1-row dashboard. A
FINAL page now lists `status.json`'s rows and, after them, every other test
the caller holds a comparison for: in `batch_manifest.json`'s order, then
by model id for a test the manifest does not key. Such a row takes its
work-dir key and baseline from the manifest (its report dir, as a run's
row does: `ref_NNNN`, else the key), then goes the way every final row
goes — a fresh sidecar, else the timing its run kept, then its comparison's
verdict. "Fresh" is against the test's OWN last run — its manifest
entry's `last_run_at`, which `assign_test_keys` stamps with `time.time()`
as that run starts: the clock a sidecar's `written_at` uses, with the
meaning of `status.json`'s `start_wall` (every one of TRANSFORM's 331
entries carries it, ~13 ms after its run's `start_wall`) — not the last
run's `start_wall`, which every sidecar outside the last run's scope
predates (a first cut did that: 638 stale warnings and no Reference link
over a 331-test tool session). A refused sidecar's warning names the clock
it was held to (`last_run_at=` for such a row). An entry without a number
there is no guard, as for a `status.json` without `start_wall`. (A second
cut anchored on `run_stats.json`'s mtime, on the premise that the manifest
kept no per-test time; it does, and a test that kept no `run_stats.json`
went unguarded.) The page's test count is its rows; its status bar names
the last run's count beside it when they differ, and no last run when
there is no `status.json` to name one. A LIVE page is unchanged: the
running run's scope. So `compare`, a `--merge` and the tool's Rerun list
every test (its Save, Discard and Reload write no page: D137 §6); a plain `--filter` run without
`--merge` still lists only its own, because the comparisons it holds are
only its own.

## D137 — The Suite page is an AG Grid table over the row model (2026-09-27)

**Context.** The owner ruled on the feedback board (2026-09-26, v2): the
Suite page keeps its name and becomes a table over the row model D136 made
for both surfaces. Q1 A — group rows inside the table, collapsible, a sort
ordering the rows within each group. Q1a B — "2 selected · Clear", Rerun and
Accept as icon buttons with tooltips (their confirm dialogs stay), and no
"(Dymola)" in Rerun's caption: the header shows the backend. Q1b A —
column filters only, typed (status by the count pills, numbers by `>1e-4` /
`1..10`, text by glob), with `.*` and `Aa` once in the toolbar; icons with
tooltips for the view controls; an EDITED pill and an Edits column. Before,
the page was a list of NiceGUI rows (`TestList`) with a filter box, chips
and checkboxes, rebuilt from Python on a layout toggle.

**Decision.**

1. *AG Grid Community, as NiceGUI bundles it* (`ui.aggrid`, AG Grid 34.2).
   The spike (2026-09-26) measured every requirement without Enterprise:
   a one-row `applyTransaction` in 24–66 ms and ~190 B; a filter keystroke
   22–25 ms with nothing sent to the server; 331 rows first drawn in ~1.1 s
   cold and 2,000 in ~0.8 s. Row grouping is Enterprise, so group rows are
   emulated: a full-width row per package, an external filter (which also
   hides collapsed and, flat, every group row) and `postSortRows` (each
   package's rows under its header, in the sort's order). Enter or Space on
   a focused group row collapses it too; Enter on a focused Test cell opens
   the test's page, as its link does. `ui.table` (Quasar) was rejected: it
   worked only through four NiceGUI private APIs, gave screen readers no grid
   and sorted by mouse only.
2. *The browser filters, sorts and groups; Python sends rows.* The toolbar's
   pills and view icons and the selection's label are drawn by
   `shell/suite_grid.js` into two `div`s Python never updates; it filters
   through D136's `filters.js`, the file the dashboard inlines (one module,
   two surfaces), and both scripts are served from `/_dstf/` and put in the
   page's FIRST HTML — after the handshake NiceGUI inserts head HTML with
   `insertAdjacentHTML`, which never runs a script. A number filter reads a
   row's sort value, never its text; a text filter reads a cell's text only
   when the row has a value. The pills count every row, whatever the
   filters; EDITED is OR'd with the status pills. Each row is the row
   model's `TestRow` built from the Session (`authoring/suite_rows.py`: its
   comparison through `summary_fields`, the result's timing, the staged ops,
   a running job — running beats the comparison, which beats the open's
   scoring failure, else the row waits and reads "…").
3. *The no-update rule and its guard.* NiceGUI's aggrid re-creates the whole
   grid on ANY Python-side update of the element (its `update_grid`): sort,
   selection and every row patched since are lost. `SuiteGrid` is sealed
   after the page's build and its `update()` raises; `.classes` / `.props`
   reach `update()` through an observable whose handler logs, so such a
   caller is blocked and sees only the log. After the build, rows reach the
   grid only as transactions — `SuitePage` collects what each Session event
   touches and `Shell.tick` has every page `flush` once per tick, so the
   Accept path's N + 2 events are one transaction, not 333.
4. *Where the state lives.* The view — pills, filters, `.*` / `Aa`,
   grouping, names, sort, columns, closed groups — is the viewer's, in
   `localStorage` under `dstf-suite-v1:<config file>` (per project; two tabs
   on one project share it; a storage that refuses leaves the table
   working). A stored view is older than the page that reads it: a filter, a
   sort, a column state or a pill whose key the page does not have is dropped
   when the grid is ready and the storage rewritten (`DstfFilters.known`,
   which the dashboard reads its stored filters by too) — a filter stored
   under the Criteria column's earlier key emptied the table and had no
   input to undo it. The resolved config path, because it is known before the open
   and the same after it: keyed by the work dir, the key before an open
   without `--work-dir` was the config path and after it the derived work
   dir. `dstf author` needs `--config`, so there always is one; a reopen on
   another backend keeps the view.
   The selection is the tab's (D129): sent to Python on `selectionChanged`,
   selected again when a grid is ready, and carried with rows added later.
   `TabState` keeps only the selection and the Test page's drawer state.
5. *Rows before the scan.* `Session.open` emits `known` after the spec
   document loads: the models its entries and the last run's
   `batch_manifest.json` name, read without the scan or the spec parser
   (which validates trees). The shell filters them as it filters the
   project's tests and keeps them until the merge's `spec` event (a close
   clears them too; an open that fails takes them back); the Suite page draws
   them waiting — during a reopen the Session's comparisons are still the
   previous project's.
6. *A tool Rerun ends on a scored dashboard.* (Replaced by D150: the `Job`
   and its contract below are gone; a drained queue writes the dashboard,
   and Save, Discard and Reload are no longer refused.) A `Job` may carry
   `after_reload`, called once the Session has reloaded and rescored; Rerun's
   renders `dashboard.html` with the Session's verdicts (the run's own
   `finally` render stays, for a run that fails before the reload). A
   plug-in's job writes no file, and nor does the Session: the dashboard
   is the report of the last `dstf run` / `compare` or tool Rerun; Save,
   Discard and Reload do not rewrite it, as a hand edit of `test_spec.json`
   does not. (Until 2026-09-27 the Session re-rendered it after each, so a
   rerun's page never kept the verdicts of edits no longer pending; the
   re-render kept producing edge cases — a guard for a run started since
   the tool's read that missed `compare` and a CLI accept, a render lost
   at shutdown, a Save's render landing on a rerun's live page — and was
   deleted: a page a Rerun wrote under unsaved edits keeps their verdicts
   until the next run replaces it.) Save, Discard and Reload are
   refused while a job runs, in the Session as in the header: the job's
   reload scores the spec. A job's `after_reload` runs off the UI loop, on
   one background thread, in the order the jobs finished, on a snapshot of
   the comparisons its reload scored — 1.6 s over TRANSFORM's work dir
   with `--report` sidecars; the next job's run waits for it (it must not
   land on that run's live page), and so does the app's shutdown (a daemon
   thread would die mid-write). Both waits are bounded — 60 s for a job,
   5 s at shutdown — and one that runs out goes on with a warning in the
   log: the render writes `dashboard.html` atomically, so what an
   unfinished render leaves is at most a stray `.tmp` beside the last
   whole page, never a torn one. Known limits, not built around: a job
   whose run waits on a render still starts when that render lands inside
   the shutdown's wait, and the process then ends under it; and an
   `after_reload` that itself waits on the background thread waits on
   itself until the 60 s bound runs out.

**Consequences.** The row model's labels: the model column reads "Test" and
the work-dir key column "Key" on both surfaces (keys unchanged). The tool
offers no Key, Worker or Elapsed column, not even in its chooser: they are
a live run's and always "—" there. A capability
may declare an `icon`; a bar that draws icons draws it, and the shell still
names no capability. `TestList` is the Tests drawer's alone. Nothing in the
pre-release seams moves. Not measured here: TRANSFORM's time to first row
through the grid, cold and warm — the plan's gate over a copy measures it.

## D138 — A run's statistics and timing are kept beside its result (2026-09-27)

**Context.** The TRANSFORM gate of the Suite table (D137) read "—" in Sim (s)
on every row of both surfaces, and the owner named sim time as one of the
things a row must show (feedback 2026-09-26). The walls a run measures
(translation, simulation, elapsed) and the statistics its backend parses
lived only on the in-process `TestRunResult`. `BatchManifest.save` writes
the entries, not the results, so every reader without the run in hand — a
`compare`, a reload in `dstf author`, a test `--merge` did not re-run —
called `read_result(test, key, None)` and got no timing; for Dymola, not the
dslog `translation` / `simulation` statistics either. And only Dymola's and
OpenModelica's `read_result` built a `statistics["timing"]` block at all:
FMPy, Julia and the Python backend measured a `sim_wall` and showed none.

**Decision.**

1. *One timing block for every backend.* `run_timing(run_result)`
   (`backends/common/result_variables.py`) builds `translation_wall`,
   `sim_wall`, `other_wall`, `total_wall` (seconds, rounded to 2, only the
   keys the run measured), and `Backend.read_run_result` — the read every
   result goes through in `read_results` — merges it into the result's
   statistics, a failed run's included, on a copy. The two copies in
   Dymola's and OpenModelica's `read_result` are gone; OpenModelica's own
   omc-reported `timing` keys are updated, not replaced. `other_wall` is
   written only when both walls are known: the old block counted a missing
   wall as 0 (OpenModelica without an omc simulation time, a Dymola
   translation failure).
2. *Dymola batch shows no timing.* A `.mos` batch measures no per-test wall;
   its `elapsed` is the batch's average. A block built from it would show a
   time no test took, so `run_timing` builds none, as before.
3. *The file.* `BatchManifest.record_result` — which every backend lane
   calls as a test's verdict lands — writes `<work_dir>/<test_key>/run_stats.json`
   (`storage/run_stats.py`): `{"model_id", "translation_wall", "sim_wall",
   "elapsed", "statistics"}`, absent walls as null, a value JSON cannot hold
   as its `str`. It is written after the manifest is saved and never raises:
   `record_result` is the abort-proof verdict record, and a failed write is
   one `# WARN:` line. A run wipes a test's directory exactly when it re-runs
   the test, so the file is as fresh as the result beside it, under
   `--merge` and `--rerun` too. `status.json` could not hold it: each run
   rewrites it with its own scope only.
4. *Read back as a run result.* With no run result in hand,
   `read_run_result` rebuilds one from the file (`kept_run_result`,
   `success=True`), so `read_result` and `run_timing` run unchanged. The
   verdict stays the manifest's (`run_verdicts`), applied after the read as
   on any reload. Only a test that reaches the read opens its file. No file
   (a run from before this) reads back with no timing and no word; a
   corrupt file, or one written for another model, with no timing and one
   warning. The review loop's `[a]` after `[v]` re-reads through the same
   path, so the accepted baseline keeps the run's statistics.
5. *The dashboard.* A final page's row with no fresh sidecar (no
   `--report`, or a sidecar the stale guard refused) takes Translate / Sim /
   Total from the file, through `run_timing`. A fresh sidecar's walls still
   win; a live page reads no file. The Suite table needed no change: its row
   reads the Session's results, which a reload now reads with their timing.

**Consequences.** Restored Dymola `translation` statistics make the
structural-change warnings appear on `compare`, on a reload in the tool and
on a `--merge`d test, as they already did on an in-process `run`: the
verdict is unchanged, `n_warnings` and the `warnings` category change.
`compare --accept` stores the restored statistics into the reference JSON,
as `run --accept` in process always did — wall times are nondeterministic, so
a re-accept shows them in `git diff` (327 of TRANSFORM's 331 baselines
already carry timing); the four stored without `translation` / `simulation`
/ `timing` (ref_0195–0197, ref_0307) gain them on their next
`compare --accept`. Stored TRANSFORM results were written before this
change: their rows read "—" until those tests are re-run. *Amended
2026-09-27 (the owner: "Fill Sim (s), marked"):* where no wall was kept,
Sim (s) shows the result file's final `CPUtime` (Dymola's diagnostic, which
`read_result` folds into the statistics; every stored Dymola `dsres.mat`
carries it, 329 of TRANSFORM's 331 rows fill), tagged "cpu" with a tooltip
naming it: a CPU time is not a wall — it leaves out what the process waited
on — so a column that holds either says which. A kept wall always wins
(`TestRow.sim_source`); the final page takes the CPU time from the results
the command holds (`render_final(..., results=)`), the Suite from the
Session's. Nothing in the pre-release seams moves: the CLI, `testing.json`, `test_spec.json` and the
reference-JSON layout are unchanged; the work dir gains a file per test.

**Scalar leaves on a statistics path.** A `scalar` leaf whose `quantity` is
a dotted statistics path (`simulation.wall_time`) now scores on `compare`,
on a reload in the tool and on a `--merge`d test, as it did only in an
in-process `run`: the statistics are read back from the file. The review proved it on the Python
example: `simulation.wall_time` scored FAIL "not found" on `compare` before
and PASS after, matching `run`. `compare --accept` stores that path's value
into the baseline, as `run --accept` always did.

*Amended 2026-09-30 (the owner's ruling): Dymola's logs are the fallback
reader.* A Dymola result written before `run_stats.json` existed still has
`dslog.txt` and `translation_log.txt` beside its `dsres.mat` (330 of the
owner's 331 stored TRANSFORM results), yet `read_result` took statistics only
from the run result, so `compare` and the tool showed none of its
`translation` / `simulation` statistics. The parse-and-merge of the two logs
is now one function, `log_parser.parse_test_logs`: the batch run (its
timeout salvage too, which read `dslog.txt` alone), the persistent worker's
disk read and `read_result` call it, the last when the run result carries no
statistics. A log that does not parse (a `1.2.3 s` where a number belongs)
costs that test that log's statistics and one `# WARN:` line naming the
file, never the read of the suite. Kept run statistics still win — they are the run's own record;
the logs are what code that reads files tolerates for a result from before
them. The logs hold no phase walls, so such a result still has no `timing`
block and its Sim (s) reads the marked CPU time. The consequences above
follow for these results too: their structural-change warnings appear on
`compare` and on a reload, and `compare --accept` stores the statistics.
OpenModelica is unchanged: the file beside its result (`omc_stdout.txt`)
holds only omc's phase times — a `timing` block, no counts — so reading it
back is another change, not made here.

**Cost.** A final render reads each row's `run_stats.json` once, whether or
not its walls are shown: ~135 ms over TRANSFORM's 331 files on `/tmp`,
per render (the tool renders once per Rerun, off its UI loop; Save, Discard
and Reload render nothing, D137 §6). Not cached: the file is as fresh as
the result beside it.

**Not built here**, so nobody looks for it: nothing. The gap this entry
first named here — a no-`--report` dashboard of a `--filter` run showing
only that run's rows, even with `--merge` — is D136's amendment.

**Addendum, 2026-10-12 — OpenModelica records the state count.** omc
prints no translation statistics, so an OpenModelica run's statistics were
timing only, and a model that gained a state gave `Continuous states: 2 ->
3` on Dymola and nothing on OpenModelica. omc does write the system's size
as attributes of the root element of `<prefix>_init.xml`, the file it
generates beside the executable. `backends/openmodelica/init_xml.py` reads
`numberOfContinuousStates` into `statistics.translation.
continuous_time_states` — the key Dymola's translation log fills — in
`OpenModelicaBackend.read_result`, the one read the persistent workers,
`--batch` and a later `compare` share; only the root's start tag is
parsed (the file lists every variable). A count is a non-negative decimal
integer, all of the attribute, or it is not recorded: `int()` also read
`-1`, `+3`, `1_000`, ` 3 ` and another script's digits. `0` is one — a
model with no state, whose first state then reads `Continuous states: 0
-> 1`. Executed on real omc, both
backends: the example's `SimpleTest` reads 2, and with a third state
added the run says `Continuous states: 2 -> 3`. Read from the file each
time, so nothing new is kept beside the result. *Old data:* an
OpenModelica baseline accepted before this has no such statistic, and a
statistic one side lacks is no change on either tier (no structural
line, no "changed" row); the first accept after it records the number.
The Modelica example's twelve `OpenModelica/linux` baselines were accepted
again for it on 2026-10-12, with real omc on a scratch copy, so the
shipped ones say what the README does: each now records its state count
(and its `sampling`, at 17 digits — no value moved by more than 6e-14),
and every example test passes on OpenModelica and on Dymola after it.
*Not read:* omc's other counts there (event indicators, algebraic
variables and parameters by type) have no twin among Dymola's keys —
its `free_parameters` and `scalar_unknowns` count other things — and
the sizes of nonlinear and linear systems are not in that file
(`<prefix>_info.json` lists the equations).

## D139 — The Test page: a status strip, one add model, a Save preview and the drawers' one control set (2026-09-27)

**Context.** The owner ruled on the feedback board (2026-09-26, v2) on the
Test page. Q2 A — a full-width status strip directly under the test's row,
inside the part that stays at the top, saying the test's situation and
filling the one next action. Q3 A — every `+` opens the same inline row
where it was clicked; a new criterion lands right after the clicked one;
a combinator's kind is a dropdown changed in place. Q6 A — the
pending-edits drawer is a preview of each test's entry as `test_spec.json`
will hold it. Q7 — one control set on every list (the Suite table's half
is D137). Before, Accept and Rerun sat outlined in the test's row (DESIGN.md
"Buttons"); a plot's "Add a criterion for x" was a VARIABLE button with a
dialog (D131); a combinator's kind and Unwrap lived in its Edit form
(D133); the drawer listed one raw line per op; the Tests drawer had a
substring filter, the Variables drawer a filter and nothing else. The
owner ruled twice more on 2026-09-27, before the build (O1, O2 below), and
accepted the planner's choices ("No objections").

**Decision.**

1. *The strip (Q2 A; reverses DESIGN.md "Buttons").* `authoring/situation.py`
   decides, pure: running, edits, scoring failed, waiting, no result,
   SIM_FAIL, NO_REF, FAIL, simulate-only, PASS — the first that applies
   wins — each with a tone (grey, red, amber) and one sentence
   (`shell/strip.py` draws it). Accept and Rerun leave the test's row for
   the strip. The controller's calls: "N unsaved edits" counted OPS when
   this was written, so the first criterion edit on a flat test read 2
   (the materialization and the edit), as the header's count did — it
   counts what the user did since D155, and reads 1; a saved spec that cannot be scored
   reads "verdict would change scoring failed → PASS"; an unscored test
   while the project is read waits ("Scoring…"); a result counts only when
   the manifest does not list its model as unfinished (what Accept's
   "re-run before accepting" reads); a simulate-only test that ran says
   "Simulate-only: it runs, and nothing is compared." The strip's Discard
   is this test's (`Session.discard(models=)`, planner's choice), labelled
   "Discard" as the board drew it, its tooltip "Discards this test's edits;
   the header's Discard drops every test's." *O1:* there is no
   simulation-settings state — `/simulation` is not stageable
   (`spec-update`'s whitelist is `/comparison`, `/metrics`, `/plots`, and
   widening it changes the TRANSFORM seam); the state waits for a
   simulation-settings editor.
2. *A capability says when it is next.* `Capability.emphasis` maps a
   `Situation` to `Emphasis.NEXT` (drawn filled) or `MENU` (the strip's ⋮);
   a situation it does not name draws it quiet. Read with `getattr`, like
   `icon`, and only by a bar the page tells its situation; the shell still
   names no capability. Accept is NEXT on FAIL and NO_REF and in the menu
   on PASS, edits, SIM_FAIL and no result; Rerun is NEXT on SIM_FAIL and no
   result and in the menu on edits. The strip's verdict clause asks
   `Session.saved_comparison` —
   the saved spec scored afresh, uncached, `None` for an entry only a
   pending op creates: ~12 ms a call on a TRANSFORM copy, 8 ms of it the
   331-test merge.
3. *One add model (Q3 A; reverses D131's dialog for VARIABLE).* `+` on a
   criterion stages the new one right after it; on a combinator, as its
   last child. The plot's "Add a criterion for x" is a `+` in the card's
   criteria header, an inline row like every other: VARIABLE is an
   `InlineHost` now, and `ActionBar` hosts SUITE and TEST only (its
   `figure=` is deleted). After Add the new criterion's editor opens on the
   plot the `+` was on when that plot draws it (`RowTarget.index`,
   `TestPage.reveal(path, index)`), scrolled into view.
4. *The kind dropdown (Q3 A; reverses D133's kind in the Edit form and
   Unwrap there).* `Panel.lead` draws in a combinator row's lead place,
   where its kind is read, and raises on a row without one. The dropdown
   holds and / or / k-of-n / warn / weighted and, below a separator,
   "unwrap — its children take its place" — omitted where Unwrap is refused
   by rule (a root with several children): an option cannot carry a
   why-tooltip. and / or / warn stage at once; k-of-n and weighted open the
   Edit form preset to that kind, which needs k or a threshold first. The
   tune icon stays only on k-of-n and weighted nodes. The Edit form says
   "Changes this node's fields; its children stay." — "…kind and
   fields…" when the dropdown preset it.
5. *The preview (Q6 A).* `authoring/spec_preview.py`: the entry as
   `json.dumps(indent=2)` writes it, before and after, diffed by `difflib`
   with `autojunk` off (on, a 60-leaf entry tightened at two leaves read
   ~400 changed lines (201 replaced) instead of 4); variables read by their label, the
   result-file name muted. *O2:* the `before` is the tree as scoring sees
   it — when a test's ops begin with the materialization `Session.stage`
   prepends (`PatchOp.materializes`), the saved entry with that applied, and
   one note line says Save also writes the criteria out as a tree; a tree
   built from edited overrides, or after a pending op removed the saved
   entry's own tree (`_saved_scoring_tree`), is not marked, so it shows as
   added and there is no note. `stage` keeps a copy of each op's value: a
   caller editing its dict after staging changes neither the preview, the
   export nor Save. A section whose edits leave the entry as saved (a
   series added, then Reset layout: no diff and no tree written out) says
   "These edits leave the entry as saved." and draws no diff box. Built
   only while the drawer is open, and again only when the pending ops, the
   file or an edited test's verdict moved; `Session.saved_comparisons`
   merges the saved spec once per rebuild. Save these / Discard these are
   the header's own; the close button and Esc close it, which under 1024 px
   — where it is an overlay over the header and the strip — are the only
   ways out besides a click behind it. Esc is one `document` keydown per
   page load dispatching to the drawer's column: `ui.on` re-sent the page's
   listeners and failed the Suite's Playwright tests. An Esc while a Quasar
   dialog or menu is open is left to it: the stale-file dialog over the
   drawer closes, and the drawer stays. One already fading out (Quasar's
   `-leave-` classes, ~300 ms (Quasar's default transition-duration)) is
   not open: a second Esc 40 ms after the first closes the drawer. The
   board's per-edit sentences
   ("edit nrmse on pulse.y: tolerance 0.01 → 0.005") are not built
   (planner's choice): each section has its edit count and verdict line.
6. *The drawers' one control set (Q7).* The budgeted matcher moves to
   `authoring/needle.py`; every control is the tab's (`TabState`), drawn by
   `shell/drawer_controls.py`, so a switch of test keeps them. Each menu's
   choices are `menuitemradio` with the current one checked; the arrow keys
   wrap, Home and End jump (Quasar's QMenu has none), Tab closes the menu,
   and the choices are out of the tab order. The Tests drawer: the filter
   with `.*` / `Aa` beside it; sort by package order, name A→Z, or status,
   failures first (FAIL, SIM_FAIL, scoring failed, NO_REF, PASS, unscored,
   then package order — not `STATUS_ORDER`, which lists PASS first); a
   group toggle; Short / Full names — a Short name cut at its end, a Full
   one at its START (the controller's call: TRANSFORM's ids share long
   package prefixes, and cut at the end two of them read alike), the id in
   a `<bdi>` inside a right-to-left span: a bare `direction: rtl` moved a
   leading or trailing `_` to the other end in Chromium, and isolates drawn
   by `::before` / `::after` entered the button's accessible name; status
   pills with EDITED, OR'd as on
   the Suite, counting every test whatever the filter, a scoring failure
   counted and kept as SIM_FAIL (the Suite's rule). Its kept set is cached
   on the needle, the toggles and the set of tests — a pathological regex
   cost 0.79 s of CPU per pass over 331 ids, and events repaint every
   second during a job; a bad pattern or one out of time hides nothing and
   says why. The Variables drawer: sort by result-file order or A→Z by what
   is shown; group by component, flat or tracked first — in each group the
   tracked names fold apart from the result's own, so under Flat a tracked
   `x={pipe.T[1], pipe.T[2]}` and the result's `pipe.T[1:2]` are two rows,
   not one reading `pipe.T[1,1,2,2]`; names as expression, result-file name
   or both — Expression keeps the name, muted, beside a label another name
   of the test carries (the one label rule, `variable_texts`) or another
   row the drawer draws reads (the controller's call: a tracked expression
   `a.Q` beside an untracked variable named `a.Q` that no plot or criterion
   holds is outside the test's scope, and two rows read "a.Q"). A family
   has no one name: what is muted beside it is its names folded
   (`unitTests.x[1:2]`) when they read otherwise, so under Flat a tracked
   `pipe.T[1:2]` and the result's own — TRANSFORM's
   `batemanEquations.Ns[1:4]` twice — read apart, and so do their checks'
   and chevrons' labels and their titles, which carry it under every names
   setting. A family is
   opened by its key and whether it is tracked — not by its row's id, which
   carries the group, nor by its members, which a filter narrows — so it
   stays open across a switch of group and under a filter that keeps some
   of it, and a tracked family and the result's own that share a key open
   apart. Rebuilt over 6,011 names: 164-255 ms in every
   group × order × names mode (the default 166-215 ms).
7. *A figure beside a drawer.* Reported during the review fixes: after
   the pending drawer closed at 1280 px, a figure kept the width it had
   beside it. Measured, it does not: read as the drawer lands, the figure is
   still the drawer's width narrower, and it fills its card ~150 ms later —
   NiceGUI's plotly element observes its size, and Plotly redraws 100 ms
   after it is told — for the pending drawer and the rail's alike. Nothing
   is added; a Playwright test holds both to it.

**Consequences.** DESIGN.md "Buttons" no longer holds on the Test page: its
next action is filled. The contract gains `Capability.emphasis`,
`Panel.lead` (a host's panel must implement it), `RowTarget.index` and
`reveal(path, index)`; `ActionBar(figure=)` is gone. Nothing in the
pre-release seams moves: the preview writes nothing, Save still goes
through `spec-update`'s read-modify-write, and the CLI, `testing.json`,
`test_spec.json` and the reference-JSON layout are unchanged. Watch-only
items went to `docs/ideas.md`. Not measured here: the TRANSFORM gate over
a copy, which the plan leaves to its controller.

**Not built here**, so nobody looks for it: the strip's simulation-settings
state (O1); the preview's per-edit sentences; a fix for the page error
"Resize must be passed a displayed plot div element." after a rebuild in a
page's first ~1 s (the TRANSFORM gate, a Discard just after the page
opened) — NiceGUI's plotly element starts observing its div only after
`await import("nicegui-plotly")`, so it observes a div already removed;
nothing visible breaks, and a page-side guard cannot be ordered ahead of
it (watch-only in `docs/ideas.md`).

## D140 — Plots are derived from the criteria tree; `plots` holds a person's extras (2026-09-28)

**Context.** The owner used the Test page over TRANSFORM and ruled on a
grill and a board over a real test (BranchingDynamicPipes, 2026-09-28: "A
for both"): the page should be about the test's variables and their
criteria, not a layout a person assembles. Q1 b — one test-wide criteria
tree; plots are views of it. Q10 — one plot per scored variable or array
family, derived from the criteria. Q11 c — the plots stacked, plus Focus
with a variable switcher. Q12 — context series: other variables drawn
greyed on a plot, same y axis, not scored, added from the Variables drawer
and removable. Q13 / Q14 — watch plots: a person can make an unscored plot
of a variable, add context to it and remove it; a watch plot whose
variable later gets a criterion becomes its derived plot, keeping its
context. Q15 — the board over a real test before any plan. Q16 — a
derived plot has no Remove: it goes when its last criterion goes. Q17 —
derived plots in tree order (where a depth-first walk first names the
variable), watch plots after, in the order they were made. Q19 — a family
is an array of one quantity. Q20 — variable labels are cut in the MIDDLE
(the head says what it is, the tail its index); model ids keep their
start cut. Two executing reviews then built the plan in archives, and the
owner ruled twice more: O2 — nothing scored is one plot per tracked
variable on both tiers; O3 — a UnitTests variable never folds, every
other array always folds, by its result-file name, with no scale rule.
O3 replaced O1 (fold only on one scale, max|v| within 10x), which is
deleted: the scale question existed only because the fold read UnitTests
expressions as arrays — `check_atm`'s local `x` holds 1 Pa in atm beside
1 atm in Pa.

Before, a test's plots were the `plots` key's layout (D130) or one per
tracked variable; the Variables drawer added and removed series and plots
(D134), a scored variable could not leave its last plot (D134 Q7 A, the
stranded rule), and a plot's criteria rows were its variables' leaves in
variable order (D133).

**Decision.**

1. *The rule (Q1 b, Q10, Q17, O2, O3).* `discovery/plots.py::derive_plots`
   is the one rule both tiers draw by: the leaves `plot_leaves(test,
   compared)` returns — the authored tree's, even with nothing compared
   (a test with no baseline: its tree names what will be scored), else
   the implicit tree of what was compared, else none — in depth-first
   order, a stored-scalar leaf skipped (its `variable` names the leaf, not
   a series); one plot per lone variable or family, placed where the tree
   first names any member; with no tree, one plot per tracked variable or
   array in `tracked_names` order. The fold is by RESULT-FILE NAME
   (`pipe.T[1]..pipe.T[n]` is one plot), never by label, and each of the
   test's UnitTests elements (`is_unit_test_variable`: `<instance>.x[i]`,
   ASCII digits) is a plot of its own whatever its expression reads.
   `Session.plots` never calls `Session.tree()` (which reads the
   baseline). Both tiers call ONE function over it,
   `plots_of_test(test, compared, result, *, listed)` — `Session.plots` and
   the report's `plot_inputs` alike: it asks which names are UnitTests
   elements of every leaf AND every tracked name (a leaf the result lacks
   is an element too), and says a name is missing when the plots cannot
   draw it — not in the file's listing nor in the result in memory, or,
   with no listing (the report lists the file only for a test with
   extras), not in the result. While a job holds a model whose names the
   tool never read, `Session.plots` marks nothing missing: a listing not
   taken is not a file without the series. A run that FAILED has no plots
   on either tier, its extras included (O2, literally); the tool's count
   line reads "no plots: the run failed", and its Variables drawer "The
   run failed: no plots", its checks and "+ new plot" grey. Every line of
   the Test page words its reason by ONE run-state rule,
   `TestPage.run_state`: a test with no result, or whose last run never
   finished it (its manifest entry still pending), reads "this test has no
   result" on the count and tree lines, as the strip says "No result yet",
   and its drawer "No result yet: no plots" over the partial result the
   reader hands in; only a run that finished and failed reads "the run
   failed". A run that succeeded and draws no plot says which of two things
   it is (2026-10-12): "no plots: the result has no variables" for a
   result with nothing in it, and "no plots: this test tracks none of the
   result's variables" where it holds some and the test's patterns match
   none (or it is simulate-only) — the first was said for both, beside a
   Variables drawer listing them.
2. *The new `plots` schema.* A list of extras `{"var", "context"?}`: an
   entry on a scored variable (or a member of its family) adds its
   `context` to that plot; any other entry is a watch plot. No glob, no
   duplicate `var`, no context naming its own `var`, no unknown key, no
   `null`. No migration: no spec used the key (TRANSFORM-UnitTests' spec
   and the four examples' carry none), and `/plots` stays on
   `spec-update`'s whitelist as it was. `migrate-variable-names` rewrites
   `var` and `context`; `export-schema` describes the extras.
3. *The edits (reverses D134's layout editing and the stranded rule).*
   `authoring/layout.py` edits the extras: `add_context`, `remove_context`
   (an entry left with no context on a derived plot is dropped),
   `new_watch` (refuses a duplicate), `remove_watch`; removing the last
   extra writes `remove /plots`, so Reset layout hides again. New plot,
   Remove plot and Reset layout act on watch plots and context only; a
   derived plot cannot lose its subject, so nothing is stranded — `stranded`
   and `stranded_sentence` are deleted, and so is Add series (it did
   nothing visible).
4. *Context in the figure (Q12).* `plot_figure(..., *, context=())`: a
   context view draws one grey (`CONTEXT_COLOR`, pinned equal to the
   theme's text-muted), thinner trace on the subject's y axis, named
   `<label> · context`, its hover the label through `meta`, with no
   reference and no criteria geometry. The overlays and the error select
   apply whenever the SUBJECT is one variable.
5. *The rows (changes D133's).* A plot's criteria rows are the leaves on
   its subject in TREE order, each led by its ancestors' kinds, the root
   first (`and › k-of-n ›`); a stored-scalar leaf is on no card and in no
   report section (`names_a_series`). Both tiers list them by one walk,
   `reporting/tree_rows.py::criteria_rows` (the tool imports it; the
   report never imports the tool), and every crumb on a plot is as wide
   as its longest, in ch (the report's `crumb_ch`, the tool's `PlotCard`),
   so a plot's leaf names start in one column — a fixed 14ch was outgrown
   by a fourth level. For the same column a hidden swatch keeps its box
   (`invisible`, never display:none) and a verdict pill has a minimum
   width with its text centred (the tool's `.dstf-pill`, the report's
   `.node-status`), so PASS and FAIL are one width.
6. *The controller's calls* (said to the owner; reversible on the owner's
   word). C1: context series are listed in a row above the plot, each
   with a `×` ("Remove {text} from this plot") — Plotly's legend cannot
   hold a button. C2: a watch card's `+` is grey while its variable is not
   tracked, until U2. C3: "+ new plot" is grey on a family's row ("A
   watch plot shows one variable: open the family"). C4: the Focus
   switcher's options are plain labels. C5: the count line reads
   "N plots · one per tracked variable or array" with no tree, else
   "N plots · derived from the criteria[ · K watch]", the derived plots
   counted apart from the watch plots, and with no derived plot at all
   "K watch plots · no criteria on a signal yet" (never "0 plots"); whether
   the plots come from a tree is `Session.plots_from_tree`, not whether
   there is a tree view. C6: a
   derived card's missing Remove is an info icon, "This plot exists
   because of its criteria." C7: Reset layout's tooltip says the context
   series and watch plots go. And O3 applied to the Variables drawer
   (changes D139's grouping): its rows fold as the plots do — by name, a
   UnitTests element a row of its own — so the "family names muted beside
   it" of D139 goes; a name-folded family reads its names.
7. *The Test page.* The selected plot follows its subject across a
   rebuild (a watch plot given a criterion moves up); the page reads the
   tracked names once and hands them to every card's `+`
   (`Selection.tracked`), and they are part of the page's `shape`, so a
   variable that becomes tracked rebuilds the cards. Focus gains a select (`focus-switch`, Q11 c)
   through the same path a card's Focus takes; it names the plot, so the
   bar beside it only counts: "Showing 1 of N plots". A variable label — a card's
   title, a criteria row's name, a drawer row — is a `MiddleCut`: the head
   and the tail from the last `[` (else, or when that `[` is more than 12
   characters from the end, the last 12 characters) as two
   inline-blocks in an inline-block, never a flex pair, so innerText and a
   copy are the whole label; the head is capped at the width less the
   tail's `--tail` (its length in ch), the tail at least that wide.
8. *The report (O2).* Its plots are the tool's: the "Simulated Variables
   (No Baseline)" block goes (a no-baseline or simulate-only test draws
   one plot per tracked variable, with its overlay picker); each plot
   section lists its criteria in tree order with their crumbs under a
   `nodes-{i}` mount per PLOT (was one per variable), its context muted
   ("Context: …" — a context series may be scored on a plot of its own,
   so not "not scored"), a watch plot "No criteria yet."; the "criteria
   are under its first plot" and "plot only" notes go. A family's badges
   name their members (`pipe.T[2] FAIL`), as the tool's pills do. A tree
   leaf on a variable the result lacks keeps its FAIL badge and says "Not
   in this result: …", with no chart and no error select — a plot whose
   subject is entirely missing is its heading, badges, note and criteria
   rows, on both tiers. The
   sidecar's `plots` record gains `subject`, `context` and `watch`: it is
   what the report drew.
9. *The pattern order.* `resolve_variable_patterns` returned its names
   sorted, so a test with no tree drew `x[10]` before `x[2]`; it returns
   them in pattern order (explicit names as written, a glob's matches in
   the file's order, the first occurrence kept). An accept now writes
   pattern-tracked `variables` in that order: a stored baseline's variable
   index changes on its next accept. Pairing is by name, so no score
   moves — and neither does a drawing: the report's and the tool's drawn
   reference (and the error overlays taken from it,
   `context_builder.build_trajectories`) paired by INDEX until the U1
   review, so a baseline accepted under the old sorted order drew another
   variable's reference under a name; it pairs by name now, as scoring
   does.

**Consequences.** The report and the tool draw the same plots from the
same two functions, and a criterion added or removed moves a plot on both.
The `plots` key changes meaning; `test_spec.json`'s other keys, the CLI and
the reference-JSON layout do not (item 9 changes the ORDER a baseline's
variables are written in, not the layout). D134's layout editing and D133's
criteria rows no longer hold as written. Not measured here: the
TRANSFORM gate over a copy (plot counts per test before and after, with
the reason for each change), which the plan leaves to its controller.

**Next (U2-U4).** U2 — the add form: one searchable picker over every
variable of the file and the diagnostics, metrics narrowed to the variable,
and Q18 b: a variable is tracked because a criterion names it (runs
record, store and score it; no `spec-update` change; rerun, then accept),
which lifts C2's grey `+`. U3 — the Statistics card, collapsed to one line
naming the changed rows; a missing zero count read as 0. U4 — moving a
subtree: drag and "Move to…", auto-wrapped in `and`. Per-plot log or linear
axes are logged in `docs/ideas.md`, not built.

## D141 — Nothing on the UI loop waits for the open's disk reads (2026-09-29)

**Context.** A TRANSFORM gate opened a Test page first, on a cold scan
cache: the page stayed at "Scoring…" / "no plots: this test has no result"
until a reload. Reproduced over a copy of the gate's run (four of five
runs stuck) and instrumented. The page, open in the gone state while the
scan ran, synced in full on the UI loop the moment `Session.open` set
`project`. That sync held the loop about 5.5 s: about 4 s scanning the
reference index (every `ref_*.json` read whole, lazily, from `_paint`'s
baseline name), the rest waiting on the sibling-backend index's lock,
held by the warm thread D127 starts at the commit point. NiceGUI's ping
budget at its default `reconnect_timeout` of 3 s is 6 s: a 4 s interval
plus a 2 s timeout, its floors (it scales them from `reconnect_timeout`
by 0.8 and 0.4, and 3 s scales below both). The browser dropped the
socket and reconnected to the same client within 0.1 s, and NiceGUI
3.16's outbox replay sent the stale update after the fresh one (logged in
`docs/ideas.md`). A warm-cache open never hits this: the
project exists before any page does.

**Decision.**

1. `Session.open` loads the reference index (`RefIndex.ensure_loaded`)
   on the open thread before the commit point. The loop never scans it,
   and the open thread and the loop no longer both run `_scan`, which
   clears and refills.
2. A read never waits on the sibling-backend index. Until the project's
   `Project.overlays_ready` is set, `_reads_for` loads overlays without
   the sibling-backend ones. Once the index is built, the Session emits
   ONE new `overlays` event naming the models read without it, and the
   next read of each reloads its overlays with the sibling ones and drops
   its decimated bases. The reads are kept, not dropped: they hold what
   was read from the result file, which a model a job holds cannot read
   again until the job's reload. The Test page re-sends its drawn plots on
   the event (no leaf's signature sees an overlay). The flag is the
   project's, not the Session's: a reopen set `project` before it replaced
   a Session-level flag, and a read in between saw the old project's set
   flag and built the new project's sibling index on the loop.
   `Session.overlay_index_ready` reads the open project's. **Changed
   behaviour:** a plot drawn in the first seconds after an open gains its
   sibling-backend overlays a moment later instead of the loop waiting for
   them. The warm thread stays where D127 put it. A warm that fails emits
   nothing, and a warm whose post finds the UI loop gone (a shutdown) logs
   it and ends.
3. A page's build asks `connected_tab_state`: it waits until the client
   really has a connection and returns None when the client was deleted,
   and the page returns. A plain GET (a readiness probe, a curl) makes a
   client that never connects; NiceGUI prunes it a minute later, which
   ended the page's `connected()` wait, and `tab_state` raised.
4. One tick handles each run of adjacent `loading`/`scan` events as its
   last (`shell._folded`): a step carries the running count. One by one,
   every step synced an open Test page in full and re-read `status.json`
   in each page's header; a probe of one tick over 3,269 queued steps
   (TRANSFORM's `.mo` count, a TRANSFORM-sized `status.json`, one Test
   page) took about 13 s, and 0.02 s folded.
5. `dstf author` keeps NiceGUI's default `reconnect_timeout`. A first cut
   of this fix raised it to 10 s (a 12 s ping budget) as a margin. With 1–4
   nothing measured stalls the loop near the 6 s budget, and a longer
   timeout keeps a dropped client alive longer, so more real drops end in
   a reconnect to it — the path on which NiceGUI's replay-order bug
   (`docs/ideas.md`) leaves a page on an older state.

**Consequences.** A cold open's first page reaches its verdict without a
reload. Measured over a copy of the gate's run (cold cache, the page
opened 5 s into the scan, five runs each): before, 3 of 5 stuck, with the
longest loop stall 5.5–6.1 s; with 1–3 at NiceGUI's default
`reconnect_timeout`, 0 of 5, longest stall ≤ 2.8 s (≤ 3.0 s with it at
10 s); with 1–5, 0 of 5 stuck, no socket drop before the browser closed,
longest stall 2.7 s. What remains of it is the Test page's first full
sync after the open (the result file's names read for its plots, the
header's `status.json`), not the scan steps. The sibling-backend overlays
of a test read before the index is ready arrive in a second draw. `tests/test_cold_open.py`.

## D142 — A criterion tracks its variable; one picker adds it; the editor says the values in effect (2026-09-29)

**Context.** Before U2 a criterion could only be added on a variable the
test already tracked — its `UnitTests` variables and its `variables`
patterns — through a select of those names; a watch plot of any other
variable had a grey `+` (D140 C2). What a run recorded, stored and scored
was that tracked set on every backend. The owner ruled on a grill
(2026-09-28, D140 "What U2-U4 will do"): Q8 — one searchable picker in the
add form, with the drawers' filter rules (substring, glob, `.*`, `Aa`),
in sections "On this plot", "Other variables" (every result-file name)
and "Diagnostics"; Q2 — the metric list narrows to the variable, a
diagnostic offering `scalar` only; Q7 + Q18 b — a variable is tracked
because a criterion names it: runs record, store and score it beside the
UnitTests variables and the patterns, with NO `spec-update` or whitelist
change, and after adding one the strip says to rerun (then accept, for a
metric that needs a baseline). The owner's feedback on the editor
("shouldn't all these fields be prepopulated with the correct values? …
tolerance is floating above the others") and on the drawers (2026-09-29:
a row's result-file name and "plot N" on a second line under the label;
the rail's drawers resizable by dragging, the width kept per tab) joined
the step. Two executing reviews then built the plan in archives, and the
owner ruled three more (O1-O3, the plan's Corrections 1): O1 — with pending
edits that name a new variable only Rerun is filled; Preview and save and
Discard are quiet, Accept is in the ⋮; O2 — where the result FILE already
holds the variable (Dymola, the JSON driver — Julia, Python) staging the
criterion re-reads that model's result and scores it at once, no rerun;
O3 — after a Save without a rerun, the sentence "N new variable(s) the
last run did not record: rerun to score it/them."

**Decision.**

1. *Tracking by criteria (Q7 + Q18 b).* `comparison/tree_spec.py::
   tree_variables(spec, diagnostics)` names the variables a tree needs:
   every leaf that resolves a variable, unique, in tree order, the
   diagnostics out (a diagnostic is scored through a quantity path over
   its stored summary, never extracted as a trajectory). Every backend
   reads them through `result_variables.criterion_variables(test,
   diagnostics)`:
   - Dymola and OpenModelica, read side: `compute_needed_variables` names
     them and `extract_variables` gains a third pass (before the
     diagnostics); one the file lacks is warned about like a missing
     UnitTests variable.
   - OpenModelica, simulate side: `build_simulate_args` adds them to the
     `variableFilter`'s literal names, so both lanes (batch `.mos`, the
     persistent worker) record them.
   - FMPy: `_tracked` on the simulate and the read side. With no pattern
     it records fmpy's default set (`fmpy.simulation._get_output_variables`
     in 0.3.29: the outputs, else up to five states, else local
     variables) beside the UnitTests and criterion variables — `output=None`
     records that set, not every variable, so the criterion names alone
     would have taken the outputs off the plot. The read side warns of a
     UnitTests or criterion variable the saved columns lack.
   - The JSON driver (Julia, Python): the read appends them and warns of
     one the payload lacks.
2. *The index rule.* On every backend a result's variables are the
   UnitTests variables, then the patterns', then the criteria's, each
   group's names appended only if not already there. The first two
   groups' indices are what they were, so a stored baseline and
   `migrate_names`, which reads indices 1..N as the UnitTests variables,
   stay valid. FMPy followed this order only after the Part A review.
3. *What the test tracks is not everything the result holds.*
   `backends/common/result_variables.py::tracked_results(test, result)` —
   the UnitTests variables and the patterns matched against the result's
   names (`"*"` every one) — is what the flat path (no `metrics` tree)
   scores (b4a730d), what a tree's "Tracked variables without a criterion"
   warning reads, and the Variables drawer's Tracked group
   (`Session.tracked`). A result read while a criterion was staged (a
   rerun runs with the pending edits, a re-read with the merged TestModel)
   keeps that variable after the criterion is discarded; scored as tracked
   it failed the test for want of a baseline. The flat path's scoring is
   unchanged on TRANSFORM, measured by the Part A mutation review
   (2026-09-29): all 331 TRANSFORM results scored with 493d1b7 and with
   7234ec8 — 0 differences in verdict, scored lists (873 variables) or
   NRMSE. That is measured at 7234ec8; 8d017cf's move of `tracked_results`
   (behaviour-identical, unit-tested) is not re-measured over TRANSFORM.
   TRANSFORM has no `variables` patterns and no trees, so the glob branch
   is unit-tested only.
4. *The re-read (O2).* `Session.stage` re-reads a model whose criteria
   name a variable the loaded result lacks and its result FILE holds, with
   the merged TestModel, and rescores it. The read happens before the spec
   and the tests are replaced, into a dict of its own: a read that raises
   leaves the Session as it was. The background rescan re-reads the same
   way, before it replaces the scan, the tests and the comparisons
   (`_rebuild_tests(scanned=...)`). On both, a scoring that raises after
   the swap (`_score_all`) is not rolled back: the new spec and tests
   stand, scored as far as the raise. The file's listing is kept (the file did
   not change). A name the file lacks (OpenModelica, FMPy, a typo) only a
   rerun records, and the strip asks for that: EDITS_NEW, "N unsaved edits
   · <verdict> · N new variable(s): rerun to score it/them", Rerun the one
   filled action (O1); NEW_VARIABLES, after SIM_FAIL and before NO_REF, O3's
   sentence, Rerun next and Accept in the ⋮ — accepted now, the baseline
   would not hold it. A simulate-only test never reaches either: nothing is
   scored there, and its add form says so with a grey Add.
5. *The picker (Q8).* `authoring/picker_rows.py::picker_rows` is the rows
   as data: On this plot (the plot's subject and context), Other variables
   (every name of the result file, `Session.variable_names`, tracked or
   not), Diagnostics (what the run RECORDED, `result.diagnostics` — a JSON
   driver run records none) — each name once, in the first section that
   has it, so a diagnostic on the plot is under On this plot; filtered on
   the label OR the result-file name. `shell/picker.py::VariablePicker` is
   the drawers' filter box over a list filtered on the SERVER — never a
   `ui.select` over every name: TRANSFORM has a test of 76,992. ↑/↓ move a
   ring from the box, Enter chooses, a click chooses and moves the ring to
   its row. A key carries the box's text as typed: the filter reaches the
   server 250 ms after the last keystroke, and ↓ Enter typed inside that
   chose a row of the list before the filter, which the list then hid. It
   flushes the box's debounced value first, so text deleted inside the
   debounce still redraws the list. The box is a combobox over a listbox,
   the ring's row its active descendant; each section a group named by its
   title, and "no variable matches" a status line outside the list. A row
   shows the plain label with the muted name beside it, and its accessible
   name is `label (name)` — the name once. The Metric select
   follows the choice (Q2): a diagnostic offers `scalar`, any other
   variable every metric that scores a time series, and coming back from
   a diagnostic restores the metric chosen before it.
6. *Values in effect.* A leaf without a tolerance of its own is scored
   with the test's base one; its editor says which on the field's hint
   line — `in effect: 0.01 (the test's tolerance)` — whenever the field is
   EMPTY (at open, after a typed value is cleared, after the leaf's own is
   cleared), and never writes it: the hint names what applies with the
   field empty, the base.
   The source is carried from the one base-tolerance resolution in
   `comparator.py` (tree and flat paths) as `TestComparison.
   tolerance_source`: `test` ("the test's tolerance"), `reference` ("the
   baseline's tolerance", the baseline JSON's) or `project` ("the
   project's tolerance", `Config.tolerance`: the CLI's, `testing.json`'s
   or 1e-4). The value and the source ride `TestComparison.base_tolerance`
   and `tolerance_source` through `Session.tree_view` into the leaf's
   `in_effect` only: the base tolerance, on every leaf whose metric's form
   has the field and that has no `min_value` / `max_value` — a leaf with
   its own tolerance carries the base too, shown once that value is
   cleared; the report's view carries none. A leaf that returned before it
   scored (a variable the result lacks, a baseline the test does not have)
   reports the tolerance it would have applied (`tolerance_used`), not the
   comparison's 0.0. A value in the field takes the hint away; cleared,
   the field says it again.
7. *Aligned fields.* Every one-line field of the editor row — the form's
   numbers, selects and inputs, and the mode, window and `against`
   controls — carries Quasar's `bottom-slots`, so each reserves the hint
   line and their input lines share one y (measured in Chromium at 1280
   and 900: 0.0 px apart; before, a number with a validation rule sat
   20 px above the rest).
8. *The drawer.* A Variables-drawer row is two lines: the chevron, the
   check, the ramp and the label with the row's whole width, then —
   muted — the result-file name where the names mode shows it, "plot N"
   and "+ new plot". The rail's drawer resizes by the panel's right edge,
   a 6 px grip: 240 px at least, at most 60% of the window AND the window
   less the rail and 480 px for the plots column (`rail.max_panel`),
   default 320, kept in `TabState.drawer_width`, so a switch of test keeps
   it. The drag runs in the browser — the panel and the drawer's `width`
   prop follow the pointer through NiceGUI 3.16's internal element store,
   guarded so a NiceGUI that renames it still moves the panel — and the
   release sends the width once; the server holds the same bounds. A
   width the window no longer holds (kept from a wider one) is clamped in
   the browser on load and on a window resize, and sent once. The grip is
   a focusable separator: ←/→ 16 px, with Shift 64, Home and End the
   bounds, one send on the key's release — or, when a key moved it and the
   focus left before the release (→ held, Tab), one send on leaving it.
9. *The controller's calls* (said to the owner; reversible on the owner's
   word). K1: a diagnostic criterion is `{"metric": "scalar", "variable":
   <name>, "quantity": "<name>.final"}`, seeded `tolerance 0 abs` for a
   count (`EventCounter`, or any diagnostic whose every finite sample is
   whole) and `0.1 rel` otherwise; `CPUtime` is a time by name (a tiny
   model's can end on a whole 0.0 or 1.0). Amended by the Part B review: a
   time (`seeds.TIMES`, today `CPUtime`) is seeded INSIDE a `warn` — a
   run's CPU time moves with the machine's load, under `--parallel` most of
   all, and 10% failed tests whose model did not change; a count stays
   bare. Only where it lands in an `and` (an `and` node, or beside a root
   leaf, which makes the root one): a `warn` child passes whatever it
   scores, so under an `or` or a `k-of-n` parent it is added bare, with its
   `0.1 rel` — a warn there would pass an `or` or a `k-of-n` with nothing
   else passing. (Under a `weighted` one the `+` is grey: a child cannot
   be added alone.) `EditTree.act("add")` hands
   `seed_diagnostic` the landing combinator's kind. The Add still opens
   the leaf's row. K2: at most 200 rows, "Showing
   200 of N — narrow the search" when capped. K3: the value in effect on
   the hint line (item 6). K4: `bottom-slots` on every one-line field
   (item 7). K5: the grip's bounds and default (item 8; its widest gained
   the plots' 480 px in the Part A review). K6: a drawer row's second line
   collapses when it holds nothing but the hover-only "+ new plot", which
   then sits at line 1's right. K7: a `Chosen: <label>` line under the
   picker's list, so a choice the filter hides is still named before Add.
   K8: a field with a hint is at least as wide as the hint (Quasar's hint
   line never widens its field; at 140 px it wrapped to three lines): 5.4
   px a character of the 11 px hint font plus 24 px.

**What it reverses.** D140's C2: a watch plot's `+` is live — a criterion
on its variable is what tracks it — and with it went `edit_tree.
_variable_names`, `Selection.tracked`, `PlotCard`'s `tracked` and the
Test page's tracked reads. The plan's K1 said a diagnostic criterion is
not wrapped in `warn`, leaving that default to U3; CPU time added to an
`and` now is.

**Consequences.** A result holds the criteria's variables after the
tracked ones on every backend, so a rerun records a criterion's variable
and an accept stores it; `test_spec.json`, the CLI and the reference-JSON
layout do not change. Known gaps: a diagnostic's criterion has no plot,
so after its Add the tree card opens on its row (B6), and nothing on the
Test page edits its values until U3's Statistics card hosts that editor;
on the flat path a baseline JSON's `variable_overrides` tolerance scores
its variable, while the hint names the test's base. Not
measured here: the TRANSFORM gate over a copy (the picker over the
76,992-name test, the hints on a real test, the drawer), which the plan
leaves to its controller.

## D143 — The Statistics card: every recorded statistic, run vs baseline; a click adds a criterion on one (2026-09-30)

**Context.** A run records statistics beside its result (D138): Dymola's
translation and simulation counts, the walls every backend gets
(`timing.*`), OpenModelica's phase times, and the diagnostics' finals
(`CPUtime`, `EventCounter`) folded in. The report showed them in a table
that tinted any difference; `dstf author` showed them nowhere, and a
criterion scored through one — U2's diagnostic add, `scalar` with
`quantity: "<name>.final"` — had no plot and nothing on the Test page that
edited it (D142's known gap, the add-form plan's B6). The owner's rulings:
the grill of 2026-09-28 (Q1 b — the card is a view of the one tree; Q6 —
its `+` adds a requirement at the END of the root) and the plot-model
board (Q2 A — a Statistics card above the Criteria card, columns
Statistic / Run / Baseline / a `+` or the criterion's pill); then, on
2026-09-29, **a row is "changed" when the criterion its `+` would add
would fail**, and **expanded, the card lists every numeric statistic the
run or the baseline recorded, by section**. Built from
`docs/superpowers/plans/2026-09-29-the-statistics-card.md`, whose
"Corrections 1" (two executing reviews) override its task text, and four
fix batches from the reviews.

**Decision.**

1. *The zero rule, and why it is narrow.* The Dymola parser wrote a
   system-size list's `<key>_count` / `_total` / `_max` only for a
   non-empty list, so a model with no nonlinear systems stored
   `nonlinear: []` and no count, and a `translation.nonlinear_count`
   criterion hard-failed "not found in baseline". `backends/common/
   statistics.py` holds the rule in one place: `summaries` is what the
   parser now writes ALWAYS (0, 0, 0 for an empty list — code that writes
   never leaves a field out), and `read_summary` is what a reader falls
   back to for a side written before that: 0 only where the list is
   present AND empty in the same category. A missing list, or a non-empty
   one without its count, stays missing. `comparison/tree_eval.py::
   resolve_scalar_path` (made public for the rows) reads its last hop
   through it, and a path missing from a side still hard-fails its leaf on
   purpose (`_missing_scalar_path_comparison`: a stats path that silently
   scored as 0 would be a green test measuring nothing). Re-accepting a
   Dymola baseline that holds an empty list writes up to three more keys
   per list into its reference JSON (Corrections A10; additive). The
   report now shows an empty list's summaries as 0, in its table and in
   Key stats. And the rule SURFACES (fix batch 3): a system-size list —
   whole numbers, `statistics.sizes_systems`; `state_names` is none — on
   EITHER side gives its three summary rows on both, each side read as a
   leaf reads it (`read_summary`; nothing, `—` on the card, where a side
   has no list). A row existed only where a side already held the key, so
   289 of TRANSFORM's stored baselines, holding `nonlinear: []` and no
   count, beside a run that recorded no count either, showed no "Nonlinear
   count" row: nothing said the count stays 0, and no `+` could require
   it.
2. *A quantity's kind by its path (S5).* `comparison/seeds.py::
   quantity_kind` says count or time by the PATH, never the value's type
   (a Dymola `result_points` can be written as a float): the `timing`
   section, `CPUtime` and the `*_time` / `*_wall` / `cpu_time*` keys are
   times, every other statistic a count; a diagnostic's summary (`X.final`)
   keeps D142's K1 (by name, else whole samples mean a count), and a
   top-level statistic naming a diagnostic reads the run's samples the
   same way (A5). `seed_quantity` is the one seed — a count exact
   (`tolerance 0 abs`), a time `0.1 rel` inside a `warn` where it lands in
   an `and` — and `seed_diagnostic` delegates to it, so ANY diagnostic time
   is now warn-wrapped there (D142 wrapped `CPUtime` alone).
3. *One row model, one "changed".* `reporting/stats_rows.py::
   statistics_rows` is what BOTH tiers render — the report's Statistics
   table and Key stats, and the card. A number's row is changed when the
   scalar mode's own verdict over the seed `seed_quantity` writes fails, so
   the tolerance lives in the seed alone; a row with no value on one side
   is never changed (S3). The report adopts the rule (S7): a time within
   the seed's 10% is no longer tinted, a count that moved still is. List
   and text statistics (`translation.nonlinear`, `state_names`) are `text`
   rows: the report keeps showing them under its old rule (formatted text
   differs, both non-empty); the card leaves them out (S1). The report
   keeps its row ORDER and sections (A2): keys alphabetical in a section,
   `timing`'s four first, the top-level scalars among the Simulation keys
   (or a Simulation section of their own at the end). Its LABELS become
   the row model's — `Nonlinear count`, `CPU time integration` (`cpu`
   spelled `CPU`), a top-level name as is: `CPUtime`, where the report
   said `Cputime`. Its VALUES keep their precision (`stats_rows.
   report_text`): a time as recorded, with its unit (`0.045845 s`,
   `1.600425 s`, `1234567.89 s` — `:.15g`; until fix batch 4 it was `:g`,
   six significant figures, `1.60042 s` and `1.23457e+06 s`, while the
   docstring said "in full"), a count whole (`1234567`). Only the card
   rounds (`card_text`): a time to 3 significant figures keeping its
   zeros (`0.800 s`), and whole seconds
   from 999.5 s (`1234 s` — `#.3g` wrote `1.00e+03 s`). Fix batch 3 split
   the two (a controller's call, reversible); before it both tiers printed
   the card's rounding. A delta
   (S6) reads `0→3` for a count and `+40%` for a time — one decimal under
   1% (`+0.3%`), `+<0.1%` under 0.05%, `+0.12 s` over a zero baseline —
   whenever both sides differ, changed or not (A7). A leaf reading a path
   no row holds gets a row of its own under "Other scored quantities"
   (S4); `X.final` is the row `X` only for a top-level `X` (S2); a leaf
   reading a list statistic gets such a row too. The builder no longer
   writes into its inputs.
4. *The Session's reads.* `Session.statistics(model_id)` is those rows
   over the last result and the CACHED baseline (never copied, never
   written). Which paths need a row, and which rows are scored, come from
   the entry's OWN tree, staged edits included (`tree_spec.
   statistic_leaves` / `statistic_paths`, through `Session.
   statistic_leaves`) — never `Session.tree()`, which materializes a flat
   entry's implicit tree by reading the baseline (B4), and never the tree
   view: a test with nothing compared (no baseline) has no view, and its
   staged criterion is a criterion all the same. The view only says each
   one's verdict, "pending" where it has none. `Session.tracked` skips the
   names statistics leaves score (B5).
5. *The card.* `authoring/shell/stats_card.py::StatsCard`, above the
   tree card, closed on every page open to one line: the changed COUNTS
   — a list's `_count` / `_total` / `_max` are one entry, in section
   order, at most three named, then `+N more` (B6) — or `no count
   changed`; then, after a `·`, at most two changed times, the farthest
   moved (|relative change|) first, then `+N more` (`1 changed: Nonlinear
   count 0→3 · CPUtime +116%`). That is the owner's ruling of 2026-09-30,
   on the TRANSFORM gate's measurement: CPUtime ran about 2.2x its
   baseline (median 2.24x) on 324 of 331 tests, so a line naming times
   among the counts named CPUtime on every test. Where no count and no
   time changed the line says `all match`, as before the ruling (fix batch
   3 had turned that into `no count changed`; it was restored by the
   controller (fix batch 4)); `no count changed` leads only a line of
   times. The line
   otherwise says `no run result`, `no baseline statistics` or `nothing
   recorded`. The open table still tints a changed time (the seed's 10%).
   Open, every
   count and time under Translation / Simulation / Timing (then any other
   section), columns Statistic / Run / Baseline; a changed row wears the
   report's warn tint, and the open row the selection. A scored row shows
   its first criterion's pill, its requirement in a word or two (`exact`,
   `±10%`, `in [a, b]`, `≥ a`, B11) and `+1` per further criterion (B10);
   a click on it opens that criterion's editor under it. Rows are rebuilt
   only when their paths, the criteria reading them or the root a `+`
   lands in move; otherwise repainted in place.
6. *Placements; the shell names no capability (B1, B2).* A row no
   criterion reads ends in whatever attaches at the new `Placement.
   STATISTIC` (its selection's new `quantity` is the row's): `EditTree`
   draws a `+` that stages `act(add, path=/metrics, quantity=...)` — the
   seed, at the END of a root combinator or as `and[root, new]` beside a
   root leaf — and reveals the new criterion's editor. Its tooltip is the
   seed's requirement ("exact match", "within 10%, as a warning"). It is
   drawn grey with the reason where nothing can take it — a job holding
   the test, a simulate-only test, nothing scored, a `warn` or `weighted`
   root — while `available` stays true for it, so no row carries a
   why-line (a TRANSFORM card would have repeated one 57 times); the card
   redraws its `+`s when a lock comes or goes. A statistics criterion's
   editor is hosted at the new `Placement.STATISTIC_CRITERION`, where only
   `EditCriteria` attaches (at CRITERION, Edit on plot answered with a
   why-line). There `EditCriteria` draws no mode select, no window and no
   quantity field, and puts the quantity back on every edit (B3). At both
   placements it reads the leaf from the tree as AUTHORED (`SessionView.
   tree`, staged edits included), and the values-in-effect hints only
   where a tree view says what the leaf was scored with; the card's open
   editor redraws when the authored leaf moves. Before fix batch 3 it read
   the leaf from the view, and a `+` on a test with no baseline — nothing
   compared, no view — opened an editor with no field, and an open editor
   there never followed an edit staged elsewhere.
7. *One editor page-wide.* `TestPage.open` keeps its shape with a
   sentinel: `(STATS, leaf path)`, `STATS = -1` (B9). Opening the card's
   editor closes a plot's and a form row, and they close it; Focus and
   closing the card close it; a plot rebuild does not (it hangs under no
   plot). The page's shape leaves statistics leaves out: a `+` in the card
   rebuilt every plot card, ending Focus and re-sending every figure. A
   reveal or a jump to a statistics criterion lands in the card.

**What it closes.** D142's known gap (the add-form plan's B6): a
diagnostic's criterion has a home on the Test page — its row — where its
values are edited.

**What it reverses.** The report's any-difference tint on the Statistics
table and Key stats (item 3). The report's statistics labels, to the row
model's (`CPUtime`, where it said `Cputime` — Corrections A2), and Key
stats' values, now read from the row (a baseline's empty list reads its
count as 0, not blank — Corrections A4). The report's value formats: it
printed a value bare (`str(v)`); a time now carries its unit and a count
is whole (item 3). `TestPage._reveal_in_tree` is deleted: its
one case (a criterion with no plot) now opens in the card. So is the
jump's "… is on no plot of this test" notice (with `_variable_of`): every
leaf of a tree view is on a plot or in the card.

**Consequences.** `test_spec.json`, the CLI and the reference-JSON layout
do not change; a re-accepted Dymola baseline gains the zero summaries
(item 1). Known gaps: a genuine rebuild of the plot cards (a `+` beside a
root LEAF moves every path) that unmounts a plot still awaiting NiceGUI
3.16's async mount during plotly.js's first import leaves its resize
observer on a detached div, and the console says "Resize must be passed a
displayed plot div element"; the Playwright tests wait for every plot to be
mounted before such a click. The structural-warning check still skips a
statistic missing on either side (logged in `docs/ideas.md`). A `+` on a
row whose run has no value adds a criterion that fails "not found in
current run" until a rerun — by design. For a result from before
run-side statistics that is its `timing` rows; a Dymola result's
`translation` / `simulation` rows have their values, which its logs give
back (D138, amended 2026-09-30). Where a test was compared but the
Session will not materialize its flat entry's tree (`Session._materialization`'s refusals), a plot
criterion's editor draws no field and says the Session's own refusal.
One method returns a test's tree or why it has none,
`Session.tree_or_reason` (`tree` is its first half), and `stage`'s
refusal of a per-leaf op, a criterion's editor, Edit tree's why and its
add, Edit on plot and a Statistics row's `+` all read it. The reason is
`_materialization`'s own refusal, so the baseline JSON is read once for
the tree and the reason (fix batch 6). Four reasons, each one line:
"nothing is scored" (`session.NOTHING_SCORED`) where no comparison
scored a variable (no baseline, a failed run, no result; `stage`'s
refusal of a per-leaf op on a model not in the project says it too, where
`tree_or_reason` raises a KeyError for that model, as `tree_view` does);
"simulate-only: nothing is scored"
(`session.SIMULATE_ONLY`) for the same on a simulate-only test, which is
also the add form's grey Add there, tree or not; "its last run no longer
holds what it scored — rerun it" (`session.STALE`) where a scored name
is not one the loaded result carries; and for every other refusal (an
unnamed or non-literal scored name, a string `quantity` or an
unregistered mode in an override, a reference-JSON override the spec does not shadow)
`session.NO_TREE`: "its flat criteria can't become a tree without
changing its scores — write a metrics tree in test_spec.json". An edit
that reaches a test with no tree is refused with
`session.no_tree_error`, a `PatchError` whose `reason` is that line: the
tool shows it alone beside the edit, and the error's message names the
model. A criterion's editor says a reason once: its host draws one
why-line per distinct sentence, the first capability's, and Edit criteria
says the Session's reason as its `available` answer, so Edit on plot,
refused for the same reason, draws no second line; a sentence only one
capability says ("nrmse has no on-plot handles") keeps its own line.
Until fix batch 5 Edit tree said "no tree to edit (nothing scored)" and
the `+` "nothing is scored" of a test that WAS scored, and `NO_TREE`
blamed flat settings for every refusal, a last run the loaded result no
longer matches included; batch 5's `NO_TREE` ran to 244 characters, and
a surface that asked for the tree and then its reason read the baseline
twice. Until fix batch 6 Edit on plot read the comparison's tree, not the
Session's: on a test
the Session has no tree for it offered the handles, and every drag and
row edit raised "has no tree to edit" (the defect is older than this
step); a simulate-only test's add form said "This test is simulate-only:
nothing is scored", and `stage` of a per-leaf op on a model not in the
project raised a KeyError from the reason's lookup, where it had been a
`PatchError`. Until fix batch 7 the editor of a test with no tree read
the reason twice, from Edit criteria's panel and Edit on plot's
why-line, and a job holding the test twice likewise. The editor read the view's
leaf before fix batch 3, while the Session refused its every edit, and
between fix batches 3 and 4 said "this leaf is not in the scored tree",
false of a leaf on the plot with its verdict; that line is now only for
a path naming no leaf of a tree (Edit tree wrapped it). A spec entry
whose `metrics` is `null` has no tree (fix batch 5; the defect is older
than this step): `spec_update.has_metrics` is the one question
`needs_materialization`, the D66 validator's entry check, `Session.tree`,
`_materialization` and the materialization's mark all ask, as
`spec_parser` already scored such an entry by the flat path; an empty
tree (`{}`) is a tree, which the validator refuses. Before it,
`tree` was None beside a tree view, a per-leaf `stage` failed "cannot
descend into NoneType", and the validator refused any op that left the
null in place. `spec-update`'s note for a per-leaf op on such an entry,
"entry had no 'metrics' block", stands: a null is no block. Of
the TRANSFORM gate over a copy (the plan's Task 7) only the counts
above are recorded here (items 1 and 5); the rest is the controller's.

## D144 — A criterion or group moves within its test's tree, by a grip or Move to…, as one whole-tree edit (2026-09-30)

**Context.** The tree card edited a tree in place — wrap, unwrap, add,
remove, a combinator's kind and fields (D133, D139) — and nothing moved a
criterion: putting one elsewhere in its tree meant removing it and adding
it again, its settings typed anew. The grill of 2026-09-28 (Q4, Q9) and the
plot-model board asked for a move by drag AND by a "Move to…" menu in the
tree card, a drop on a criterion wrapping both in `and`, and a refused drop
that says why. On 2026-09-30 the owner ruled on four questions, asked with
drawn previews (D1-D4 below). Built from
`docs/superpowers/plans/2026-09-30-move-a-subtree.md`, whose "Corrections
1" (two executing reviews, and the owner's amendment of D1) override its
task text, and one fix batch from the review of its first part.

**Decision.**

1. *Where a node can go (D1, as amended).* Every row of the tree card but
   the root's has a grip (⋮⋮) before its pill, wherever Edit tree is
   offered on it. While dragging, a row's TOP half is "before it" — an
   accent line drawn from the row's indent — and its BOTTOM half is "onto
   it", outlined: onto a combinator appends at its
   end, onto a criterion wraps both in `and` (the criterion first, then the
   moved node). There is no "after": every pixel line means one place, and
   a node goes last at an outer level by a drop onto that outer combinator.
   The root's row is all "onto": nothing sits beside the root. (The plan
   drew quarters — before / onto / after; the amendment replaced them
   before the drag was built.) Move to… lists the same places with every
   position spelled out, in tree order (`tree_move.move_destinations`):
   each combinator's "before <the row there>" … "at the end" — the drag's
   own word, naming a row the person can see (the controller's call after
   the review of the build; it drew "at 1" … "at N" first) — and each
   criterion's "wrap both in and". Where two siblings read the same,
   "before" either would not say which, and their places are numbered
   ("at 2"). `Dest("at", path, i)` is before the child at `i` as the tree
   is NOW, before the move (C2).
2. *What a move repairs, and says (D2, D3).* `authoring/tree_move.py::
   plan_move` computes the whole new tree on a copy: it holds the
   destination by the dicts themselves before anything shifts, takes the
   node out, puts it in, and only then repairs the source's side upward
   (C1). A combinator the move empties is removed, and its parent if that
   empties, up the tree (D2); the root never empties, since it holds the
   destination. Every `k-of-n` on that chain left with fewer children than
   its `k` lowers `k` to them — "k 3 → 2" (D3). A node moved into a
   `weighted` gets weight 1 at its index ("weight 1 given"); one moving out
   takes its weight with it, and that is said too ("weight 2 went with it"
   — the controller's call after the first part's review, on D3's "the
   row says what changed"); a reorder among one `weighted`'s own children
   carries the weight with its child and says nothing (C3). A move whose
   new tree equals the old — two equal siblings trading places — is None,
   no edit (the first part's review).
3. *What it refuses, in this order, each in one sentence (V5, C5).* The
   root ("The root holds the whole tree: move what is in it instead.");
   a node into itself or its own subtree ("A criterion group can't move
   into itself."); then a place where the node already is, which is no
   refusal but None — listed once, as "— where it is", greyed; then into
   a `warn` ("A warning holds exactly one criterion or group: drop onto it
   to wrap both in `and`."); then D4 (item 4); then the validator's own
   sentence (`tree_move.validator_for`: the parser's message, or
   `validate_tree`'s messages joined — the text `Session.stage`'s refusal
   shows), such as no primary criterion left outside a `warn`.
   `MoveRefused` is one of the tool's user errors (`errors.USER_ERRORS`):
   a stale menu's or drag's refusal is said under the row, never logged
   as a bug.
4. *D4, applied to D142's reason (C4, the controller's call).* The owner
   ruled that a move putting a `warn` directly under an `or` or a `k-of-n`
   is refused, and only moves are checked. D142's reason is wider than a
   `warn`: a child that can never fail passes its parent with nothing else
   passing. `tree_move.always_passes` says which children cannot fail: a
   `warn`; an `and` all of whose children cannot; an `or` with one that
   cannot; a `k-of-n` with at least `k` that cannot; never a `weighted`
   or a criterion. A move is refused — "A warning passes whatever it
   scores: under or / k-of-n it would pass its parent with nothing else
   passing." — when the NEW tree pairs an `or` / `k-of-n` with such a
   child and the old tree did not have that pairing, compared node for
   node by reference. A tree that already has one is left as it is, and a
   move elsewhere in it goes through.
5. *One edit (V1).* `EditTree.act(action="move", model_id=..., path=src,
   dest=...)` stages ONE op, `add /metrics` carrying the whole new tree —
   the root add Edit tree already staged where an add beside a root
   criterion wraps both in `and`. No RFC 6902 `move`: `patch_apply`
   supports `add` / `replace` / `remove` only, and `dstf spec-update`
   applies the same envelope, so the patch format does not change. A flat
   test materializes nothing first: the move's tree is its first tree,
   and the preview shows the whole `metrics` block added (C9, the
   controller's call: true, the test gains a tree); a saved tree's move
   previews as the reorder it is. `Session.stage` folds an `add` after an
   `add` at the same OBJECT member — the second replaces what the first
   put there — so two moves in a row are one pending op, the second tree
   (C8); an `add` at a list index inserts, and both stay. A move folded
   onto the `add /metrics` that materialized a flat test's tree replaces
   it, and the op is no longer marked as that materialization
   (`PatchOp.materializes`).
6. *The tree card only (V2).* A plot's criteria rows host the same
   `Placement.NODE` and move nothing: `Panel.can_move` is true only where
   the host's `RowTarget.drag` is, which the tree card alone sets, so they
   draw no grip and no Move to…. The panel's hooks are `Panel.can_move`,
   `Panel.movable(on_drop, zones)` and `Panel.moved(node_path, text)`; the
   shell names no capability (C12).
7. *The drag (C12, C14, C17).* Plain HTML5 drag-and-drop, drawn in the
   browser: only a drag's start and its drop reach the server. At each
   start the page empties the last drag's map and asks the panel once for
   `{row path: {zone: reason}}` (`tree_move.zone_map`: `None` allowed,
   `""` where the node is, else the refusal), so a slow reply never draws
   one drag with another's map; until it arrives a zone draws as allowed,
   and a drop there is the act's to refuse. A refused zone outlines its
   row red, takes no drop, and says its reason in ONE place, the tree
   card's header line, in place of the line's text while it is dragged
   over (C14, the controller's call): said inside the row it reflowed the
   row's icons, and the browser shows no title while it drags. Over no
   row — a form row, a move's line — nothing is drawn. The drag carries
   its own MIME type (`application/x-dstf-node`), never text: a drag of
   text or a file over the tree draws nothing and drops nothing, and a
   node released over a text field types nothing into it. It carries the
   revision of the rows it started on, too (`TreeCard.revision`): a drop
   on rows rebuilt since — an edit staged mid-drag — is refused under its
   row, "The tree changed while you dragged: try again."
   (`tree_move.TREE_CHANGED`, a `MoveRefused`), since its paths name the
   nodes of a tree that is gone.
8. *After a move (V4, C11).* The panel plans the move BEFORE it submits
   (`edit_tree.planned_move`; afterwards `src` names another node) and,
   once the submit went through, calls `Panel.moved`: every open editor
   and form row closes — two criteria on one variable trading places move
   no shape, and an editor left open showed the other one — and, once the
   tree is redrawn, the moved row is scrolled into view and flashed, with
   one line under it saying what changed: "Moved." and each note as a
   sentence — "Moved. k 3 → 2.", "Moved. The empty warn was removed.",
   "Moved. Weight 1 given." It goes with the next form, editor or edit.
   One visually hidden live region in the tree card, there from its build,
   announces a move: told the line's text — a repeat with a zero-width
   character added, so it still changes — while the line is no live region.
   The plan closed the editors before staging; after the submit is
   enough, since the Session's events cross the Shell's queue (C11).
9. *Move to… (C7, C13).* An icon among a row's controls, tooltip "Move
   to…", whose menu is built when it opens, from the tree as it is then
   (see Consequences). Each place reads its node's text — the page's
   labels, cut in the middle — then where in it ("— before range · x").
   The menu cuts the row a "before" names in the middle too, ahead of the
   node's text and with "— before" whole: kept whole, a 100-character name
   ran past the menu's edge. A refused place is greyed with its reason as
   a second line, "where it is" greyed with none; the node's
   own row is not listed, and its subtree's places are, refused. The arrow
   keys move among the places, skipping the greyed ones, Enter chooses,
   and the button shows no tooltip while its menu is open.

**What it enforces that the validator does not.** D4 (item 4), on moves
only. `validate_tree` does not check what sits under an `or` or a
`k-of-n`; D142 keeps a `warn` out of one only in the seed an add writes. A
tree written by hand, or edited otherwise, may still hold such a pairing,
and the tool neither refuses it nor a move elsewhere in it.

**What it reverses.** Nothing.

**Consequences.** `test_spec.json`, the patch envelope, the CLI and the
reference-JSON layout do not change: a move is an `add /metrics` that
any `spec-update` applies. Limits: nothing moves between tests; nothing
moves on a plot's criteria rows (item 6); there is no undo but the
pending edit itself — Discard, or a move back. `move_destinations` and
`zone_map` plan a move to every place in the tree, so they grow with the
square of its size — on a 134-node tree about 0.14 s for the menu and
0.2 s for a drag's zones — which is why the menu is built when it opens
(one built per row with the rows was about 20 s of rebuild on such a
tree) and the zones are asked for once per drag, never per rebuild. The
TRANSFORM gate is the controller's; nothing of it is recorded here.

## D145 — A test with no result is NOT RUN, not a failed simulation (2026-09-30)

**Context.** A test the last run never ran — no entry in
`batch_manifest.json` — and one a run started and never finished — its
entry still `pending`, the stamp a run puts on every test in scope and
overwrites as each result lands — both read SIM_FAIL: the comparator built
`TestComparison(sim_success=False, "No simulation results found")` for the
first, and the reader read the second's result file off disk, which is
missing (a failed result) or partial (a green one). So an aborted run and
a test nobody ran looked like a model that fails to simulate, counted
against the exit code, and the Test page's badge said SIM_FAIL beside a
strip that said "No result yet". The owner's ruling of 2026-09-30, chosen
from drawn options: such a test gets a status of its own, **NOT RUN**.

**Decision.**

1. *The line, and where it is drawn.* "No result exists" and "the
   simulation failed" are told apart in ONE place, `Backend.read_results`,
   which holds the manifest: an entry whose `last_run_status` is
   `pending`, with no result from this process's run and no recorded
   failure for it (`run_verdicts` — the `status.json` row a pending entry
   falls through to), yields NO result — its result file is not read,
   whatever it holds. A model with no manifest entry yields none either.
   `compare_one` makes "no result" (`result is None`) a comparison with
   `ran=False`, and `reporting.status.classify` reads that first: NOT RUN.
   Everything a run REACHED a verdict on stays as it was: a recorded
   `failed` or `timed_out`, a failed result from this run, a result file
   missing after a run that recorded passed, a translation error — SIM_FAIL
   (a timeout is TIMED_OUT while the run is live). A pending entry whose
   `status.json` row says `failed` ran and failed: SIM_FAIL. An entry
   written before `last_run_status` existed is read as before (old data).
2. *The word.* `NOT_RUN = "NOT RUN"` in `reporting.status`'s one table —
   the key is the word, so every surface that shows the classifier's key
   (the tool's badges, the Tests drawer's pills, the strip's verdict
   phrase) reads it; its class is `not-run`, last in `STATUS_ORDER`.
   Nothing else spells it: the Suite's count pills are handed to its script
   from Python (`suite_grid.PILLS`), not listed in it.
3. *The colour.* The existing neutral grey — the `queued` token —
   under the `not-run` class (`theme.STATUS["not-run"]` is that mapping),
   so its pills, row tint and badge are grey while a pill and a filter can
   still tell NOT RUN from a queued live row.
4. *Every surface.* The Suite row and a NOT RUN count pill (after NO_REF);
   the Test page header's badge, which now agrees with the strip's "No
   result yet — rerun to simulate it." (the strip reads NOT RUN as that
   situation); the Tests drawer's pills and its status sort (after PASS,
   before unscored); the dashboard's row, badge and pill (it filters by
   the class, as every pill); the per-test report's sidecar summary; the
   console, which prints `NOT RUN` and a `NOT RUN: n` line; the
   interactive review (`run -i`), which says `NOT RUN` and passes the test
   over. JUnit writes the testcase with `<skipped message="not run"/>` and
   counts it skipped, never a failure or an error.
5. *Not a failure — unless nothing ran.* `reporting.status.FAILURES`
   (FAIL, SIM_FAIL) is what `compare` and `run` exit on (`exit_code`),
   and what the console and the review count as still failing: exit 0
   when everything that ran passed. A command in which NOTHING ran — no
   comparison, or every one NOT RUN (`nothing_ran`) — exits 1 on every
   route (console, `--report-format junit`, `--report-format html`,
   `--report`, `run -i`), as the console's "No comparisons to report"
   always did, and says so in ONE stderr line from one CLI helper over
   `exit_code` (`cli.common._exit_code`): "Nothing ran: no test in scope
   has a result (N NOT RUN) — exit 1.", or, with no test in scope at all,
   "Nothing ran: no test in scope." — with nothing in scope there is
   nothing to count, and "(0 NOT RUN)" counted it anyway (the
   controller's call after the review of the build). NOT RUN arises four ways, and the exit code in each:
   a test never in a run, beside tests that ran — 0 when those passed;
   a run aborted by its process ending (Ctrl+C, a crash) — a later
   `compare` exits by every result the work dir holds — this run's and
   older runs' — and 1 only when it holds none; a persistent pool whose
   workers ALL fail to start — every test in its scope is stamped
   `pending` and `run` itself exits 1 ("Run aborted"); a later `compare`
   exits as after any aborted run (1 when no older result is left); and
   a `compare` over an empty or wrong work dir — 1. A pool that dies mid-run records every test it could not
   reach as failed (`_drain_remaining`): SIM_FAIL, exit 1.

**Consequences.** A pending test's partial result is no longer read by
`compare` or the tool: it has no plots, and the Variables drawer says
what it says for a test never run. `--rerun sim-failed` and `run -i
sim-failed` still select NOT RUN tests: a category selects by the
classifier's status (`cli.common._should_review`), and `sim-failed` takes
SIM_FAIL and NOT RUN (nothing succeeded).

## D146 — The tree card shows where each row sits, folds its groups, and its rows open what they hold (2026-10-01)

**Context.** The tree card's rows were indented 16 px per level, nothing
folded, and a row's click did nothing: a criterion was edited on its plot
only, and a `k-of-n`'s or a `weighted`'s fields behind a `tune` icon. On a
tree several levels deep it was hard to tell what sat under what. The
tree-card grill of 2026-10-01 put thirteen questions to the owner on a
drawn board (Q1-Q13, confirmed); the controller made seven calls, said to
the owner and reversible (Y1-Y7). Built from
`docs/superpowers/plans/2026-10-01-tree-card-structure.md`, whose
"Corrections 1" — two executing reviews, a spike, and two owner rulings
(R1, R2) — override its task text, and from the fix batches of its review.

**Decision.**

1. *Where a row sits (Q1, Q2, Y1-Y3).* One 6 px bar per LEVEL at a row's
   far left, 2 px apart, the row's full height, in place of the 16 px
   indent: a row's content starts after its bars (8 px per level) and a
   28 px chevron place, kept on every row — empty on a criterion — so the
   pills line up. Level i's bar is coloured `--dstf-level-<(i-1) % 4 + 1>`:
   blue, violet, cyan, slate (`#7fb0ec`, `#a990dc`, `#5fbfcf`, `#9aa8b8`),
   never a status's green, red or amber. The tokens are
   `reporting/theme.py::LEVELS`, declared by the tool's pages alone
   (`level_variables`, added in `Shell.page_setup`): in `css_variables`
   they were eight unused properties on every report. The root draws no
   bar and carries a muted `root` tag ("and · 5 criteria · root").
   Hovering a group's row deepens that group's level bar on its row and
   every row under it (`--dstf-level-k-on`: `#2f7fd8`, `#7650c6`,
   `#1f97ab`, `#6b7b8f`); hovering a criterion's row does the same for its
   parent; the root deepens nothing. It is done in the page (no round
   trip) by marking the BAR (`.dstf-lvl-on`), never the row (item 3).
2. *Folding (Q3, Q4, Q8, Q10, Y6, Y7).* A chevron (a 28 px target, an
   18 px glyph, `aria-expanded`, named "Fold …" / "Unfold …") on every
   group's row but the root's — the root folds with the card's Show /
   Hide tree. Everything is open by default. A folded group's row adds
   "· N hidden · x pass · y fail" — every criterion under it, however
   deep, each once, by verdict, with "· z advisory" and "· z unscored"
   when there are any. Expand all / Collapse all are icon buttons beside
   the tally (`unfold_more` / `unfold_less`, named "Expand all groups" /
   "Collapse all groups"): as words they wrapped the line at 900 px.
   Collapse all leaves the root open, and closes a form or an editor under
   a row it hides — one under a folded group's own row stays. The folded
   groups are the browser TAB's, per test (`TabState.tree_folded`: model
   id → paths), so they survive a switch of test and the test opened again
   in the tab.
   While dragging (U4, D144), resting 700 ms on a folded group's row
   (`REST_MS`) unfolds it as its chevron's click would; leaving the row,
   the drop and the drag's end cancel the timer, and a drop onto it still
   appends.
3. *Why folding is attributes set in the page.* A server rebuild of the
   rows bumps U4's revision token (`TreeCard.revision`), which refuses a
   drag in progress. So the page's script folds by ATTRIBUTES alone —
   `hidden` on every row and form slot (`data-slot-of`) under a folded
   group, `data-folded="1"` on its row — and tells the server, which
   records it and sets the same attributes by a props update, never a
   rebuild; the revision stands. The plan's spike measured eight kinds of
   server repaint on the real card: only `row.classes(...)` undoes a
   client change, and it wipes EVERY client-set class on the row; client
   attributes and inline style survive all eight. Hence attributes for
   folding, and the hover's class on the bar, which the server never
   re-classes.
4. *Folds follow their groups (R1).* Every edit of the tree reports where
   each node it kept went, old → new (`Panel.edited`, from
   `tree_move.path_map(tree, ops)` by the ops' own semantics; a move's
   `Moved.paths`), and the folded set is re-keyed through that map before
   the rows are redrawn; a folded group that is deleted loses its fold. A
   folded path that names no group after an edit that said nothing is
   dropped when the rows are rebuilt.
5. *A tree change this tab did not make unfolds every group (the owner's
   ruling of 2026-10-01).* A Discard, a Save that reloads, another tab's
   edit, a spec reload: any `spec` event that no act of this tab produced
   clears the folds of every test it names — of every test when it names
   none (a close). Every action bar and inline host on the Test page
   applies through `TestPage.apply`, and a layout edit through `_own`,
   which note the revisions the act produced in `TabState.own_revisions`.
   The rule is one function, `tabstate.heard_spec`, and EVERY page of the
   tab calls it — the Test page of any test and the Suite page: heard only
   by a Test page of that test, a Discard, another tab's edit or a reload
   heard while the tab showed another test or the Suite page left the
   folds on the paths the change had moved. The record is the TAB's, not
   the page's: a page navigated away from stays in `Shell.pages` until
   NiceGUI's reconnect timeout and hears every event, and with a record
   of its own it heard the new page's edit as foreign. A revision stays in
   the record until a later one is heard, since the Shell hands each event
   to every page before the next. A tab with no live page hears nothing:
   `TabState.seen_revision` is the last revision a page of the tab heard
   or produced, and a page opening on a later Session revision, with one
   in between that no act of the tab produced, clears every fold
   (`tabstate.caught_up`) — it cannot know which tests those named.
6. *What unfolds a path (Q9 as narrowed by R2).* Only TREE events: a
   move's line under its row, a refused act's sentence under a row, a form
   row or a tree editor opened under a row — each unfolds the folded
   groups it is inside, and nothing else. What is done outside the tree —
   an add from a plot's `+`, a criterion opened on its plot, the ⤓, the
   header's named jumps — leaves the folds alone, and a folded group's
   summary updates. (The grill's Q9 said "a jump" too; the owner confirmed
   the narrowing.)
7. *A click on a row (Q5, Q6, Q11, Y4, Y5).* A criterion's row opens its
   editor under the row — `Placement.TREE_CRITERION`, where `EditCriteria`
   attaches (on a statistics criterion it hides what it hides on the
   Statistics card), with no figure — and a second click closes it
   (`TestPage.open == (TREE, path)`, `TREE = -2` beside `STATS = -1`). It
   is the page's one editor (Q11): opening it closes one on a plot or the
   Statistics card and any form row, and those close it; an edit typed in
   it repaints the plot's row, its figure and the tree row's readout. A
   `k-of-n`'s or a `weighted`'s row — the root's too, or a `k-of-n` root's
   `k` could not be edited at all — opens its fields form, the one the
   `tune` icon opened, and a second click closes it; an `and` / `or` /
   `warn` row's click does nothing (its kind is its dropdown). A click on
   a row with a form open under it closes the form. The capability says
   what a group row's click does, `Panel.on_click(handler)`: offered on
   the tree card's GROUP rows only (`RowTarget.clickable`) — a
   criterion's row, in the tree or on a plot, has a click of its own, its
   editor — so the shell names no capability. The chevron, the grip, the
   kind dropdown, the ⤓ and the icon cluster stop their clicks before
   they reach the row.
8. *The keyboard (the controller's call, reversible).* The `tune` icon
   was the only keyboard way to `k` and the weights. A row whose click
   opens something is `role="button" tabindex="0"`, shows a focus ring,
   Enter or Space on it is its click — a key pressed in a control inside
   it is the control's — Space does not scroll the page, and
   `aria-expanded` says whether its editor or fields are open.
9. *"Edit on its plot" (Q12).* Drawn by the tree card beside the tree
   editor's Done while some capability placed at CRITERION and not at
   TREE_CRITERION is available for the criterion on its plot — one that
   drags its handles — and repainted with the editor (a mode switched to
   one with no handles takes it away). It moves the editor to the first
   plot holding the criterion, found when it is clicked: a sync refreshes
   the tree before the cards rebuild, and an index read when the link was
   drawn could name the plot the criterion had been on.
10. *The ⤓ (Q7, Q13).* A `vertical_align_bottom` icon at the start of a
    criterion row's cluster, named "Show on its plot" or "Show on the
    Statistics card" (repainted when a mode switch moves it): it scrolls
    the criterion's row on its first plot to the middle of the viewport
    and flashes it — drawing the plot, and ending a Focus that hides it —
    or opens the Statistics card at its row. No editor opens or moves, and
    no fold opens. The closed line's named jumps keep opening the
    criterion on its plot (Q13); both scroll to a row found by its id.
11. *A long row.* Its name wraps anywhere (a TRANSFORM name has no space
    to break at); its readout keeps one line, cut with an ellipsis, the
    whole in its title, and shrinks with the name down to 80 px and no
    further (`flex: 0 1 auto; min-width: 80px`). Shrinking from 0, a
    200-character name left the readout 71 px at 900 px; kept whole up to
    40% of the row, it left the name 119-143 px there, five lines and
    more — now 290-350 px, three lines. The tests hold a name's box to at
    least 240 px beside the readout's 80. No row is wider than the card,
    and the page never scrolls sideways.

**What it removes.** The `tune` icon ("Change this combinator's fields",
`edit:{path}`): a row's click opens those fields. The 16 px indent per
level: the bars say the depth.

**What it reverses.** Nothing.

**Consequences.** Only the tool's tree card changes: no scoring, spec,
patch or report change, and a plot's criteria rows are unchanged. Folds
are volatile, the tab's, gone with it. Rows do not move by keyboard. The
TRANSFORM gate is the controller's; nothing of it is recorded here.

## D147 — A tolerance is a number or a lower/upper pair (2026-10-08)

**Context.** Every tolerance a criterion carried was one number, applied
the same on both sides of its target: a checkpoint that may overshoot but
must not undershoot, an event that may come late but not early, could not
be said. Step E of the tool's redesign asked for a lower and an upper
tolerance with a link toggle, and handles for both on the plot. The owner
ruled on the shape, its reach, the scalar's drawing and — after a spike
with a real mouse — the handles' form and when they show; the controller
made the calls numbered below, said to the owner and reversible. This is
step E's first half (E1). Built from
`docs/superpowers/plans/2026-10-02-two-sided-tolerance.md`, whose
"Corrections 1" — two executing reviews and two spikes — override its task
text, and from the fix batches of its reviews, which changed more than the
plan held; what follows is the contract as built.

**Decision.**

1. *The shape (the owner's ruling).* A `tolerance` is one number OR the
   pair `{"lower": a, "upper": b}`: one key, no second pair of keys beside
   it. Chosen over two extra keys because now is the time to do it right,
   and a pair object leaves room for more dimensions later. A number is
   both sides. `comparison/sides.py` is the ONE place a number-or-pair is
   read or written: `Sides(lower, upper)`; `parse_sides(value, what)` takes
   a finite real number that is not a bool, a dict whose keys are EXACTLY
   `lower` and `upper` (each such a number), or a `Sides`, and raises a
   `ValueError` naming the field and the value as the JSON file holds it,
   cut to 60 characters, for anything else. That is stricter than the
   `float(...)` it replaces: the string `"0.01"` and `true` were scored as
   tolerances and are config errors now, in every field of item 2 —
   deliberate. (A scan of the owner's spec, `testing.json` and 898
   baselines found 29 tolerance values, none a non-number.) `null` stays
   what it was: an absent key for a row's `tolerance` / `time_tolerance`,
   "no width" for `tube_abs` / `tube_rel`.
2. *Which fields take a pair (the owner's ruling: every tolerance; the
   list is the controller's).* The criterion's `tolerance` on `points` and
   `scalar`; a `points` row's `tolerance` and `time_tolerance`;
   `event-timing`'s `time_tolerance` and an `events` row's `tolerance`; a
   `peaks` row's `tolerance`; a constant tube's `tube_abs` and `tube_rel`.
   The same keys in `comparison.variable_overrides`. NOT `nrmse`'s
   `tolerance` — one ceiling: an unequal pair is that leaf's config error,
   "nrmse has one ceiling: tolerance must be a number" — and not
   `tube_min_width`, a floor under both sides: anything there but one
   finite number is that leaf's config error too, "tube_min_width must be
   one number, got …" (`sides.parse_number`, read where the widths are:
   when the mode is built and by the scorer). It was compared with zero as
   it came, and a pair or a string raised a `TypeError` out of
   `compare_test`.
3. *What the sides mean.* `d = actual − target`; a check passes iff
   `−lower ≤ d ≤ upper`. In a `rel` mode each side is that fraction of
   `|target|`. In time, `lower` is how much EARLIER and `upper` how much
   LATER: a checkpoint's time box is `[t − lower, t + upper]`, an event's
   window likewise. A peak's window is `[f·(1 − lower), f·(1 + upper)]`
   (`rel`) or `[f − lower, f + upper]` (`abs`), from ONE helper the scorer
   and the figure share (`_peak_window`; it was computed twice). A constant
   tube's edges are `ref + upper` and `ref − lower` (`band`), or those
   fractions of `|ref|` (`rel`); `tube_min_width` floors both. Each check
   stays inclusive or strict as it was — declared points, scalar, events
   and peaks inclusive, the implicit final-value check strict — on both
   sides. The scalar's score stays the magnitude of its deviation, so what
   a `weighted` combinator reads does not change meaning.
4. *No sign check in the scorer.* A negative number was never refused and
   is not refused in a pair: a negative number can never be met, and a
   band inverted by a negative side holds nothing and is crossed by
   nothing. A negative `tube_min_width` is no floor at all; a positive
   one floors a negative tube width as it does any other, and that tube
   holds its reference. The EDITOR refuses to write these (item 12).
5. *Identity.* A spec holding only numbers scores bit for bit as it did,
   and `v` and `{"lower": v, "upper": v}` give the same verdict, score
   fields and geometry coordinates, for every mode and field. So the pass
   test is on `d` itself, never on the band's edges (see Rejected);
   `_violation_in_box` judges a checkpoint that way, with the old
   `_min_delta_in_box` still supplying every reported number.
6. *Where a malformed tolerance lands.* It is that criterion's config
   error — the leaf's on the tree path, the variable's on the flat path —
   naming the field (`points[1].tolerance must be a number or …`), and the
   rest of the test is scored. Row tolerances are parsed in the mode's
   `__init__`, where both paths catch, and again at use in the scorer. A
   `ValueError` a mode raises at compare time fails its variable on the
   flat path too, where it used to take the run down.
7. *A base tolerance is ONE finite number (the owner's ruling).* The base
   is what a criterion without a tolerance falls back to:
   `comparison.tolerance` in a spec entry or a reference JSON,
   `testing.json`'s `tolerance`, `--tolerance`. `sides.is_base_tolerance`
   says which values are one: not a pair (nrmse, the default metric, has
   one ceiling), not a string, a bool or a list, not NaN or an infinity.
   Anything else is refused, never read loosely and never skipped for the
   next base down:
   - `comparator.refused_base_tolerance` is the one decision, asked first —
     by `compare_one` right after the reference is read, before the
     no-baseline and baseline-free short-circuits, and by `compare_test`
     for a direct caller — of the base IN EFFECT (spec > baseline >
     project). The test FAILS with no variable scored and the sentence as
     its `error_message`: "the base tolerance must be a number; a
     lower/upper pair belongs on a criterion (test_spec.json
     comparison.tolerance is {…})" — where a pair belongs is said to a dict
     alone. A failed simulation and a test with no result keep their own
     status.
   - `reporting.status.refusal` names that state once, and every surface
     says the reason where it would name failing variables: the console,
     the JUnit `<failure>`, the dashboard row's Message, the per-test page
     ("Not scored: …"), the interactive review ("Error: …", and no
     `[a]ccept`), and the tool — a `REFUSED` situation whose strip reads
     "Not scored: …" and which no capability takes as its next action,
     since accepting would not clear it.
   - Accept refuses it: `storage.accept.accept_refusal` skips the test in
     the one accept path with the sentence, its reference file untouched;
     `store_reference` writes a base tolerance only when it is one, the
     test's or else the old reference's.
   - The project's is a `ConfigError` (one "Config error: …" line, exit 2;
     `Session.open` refuses the project). `null` is unset.
   - `spec-update` and the tool's Save refuse a patch that CHANGES
     `comparison.tolerance` to something that is not one; a bad base the
     patch did not touch does not block it, or nothing could remove it.
     "Unchanged" compares the JSON text, so `1 → true` is a change.
8. *What leaves Python, and what is said.* `VariableComparison.
   tolerance_used` is a `Sides`. Where it leaves as data — the tree view's
   `tolerance`, a leaf's diagnostics — it is `as_written`: the number when
   the sides are equal, else the pair. In a sentence it is `sides_text`:
   the one number as before, else the band's two edges as signed numbers,
   `−1.000e-02 / +5.000e-02` (so a negative lower side reads `+`). With
   uneven sides the criterion sentence says the deviation SIGNED, and of
   the point or event pair furthest outside its own band, else of the
   largest `|Δ|`. Words changed for some numbers-only specs too, never a
   verdict or a score: a `points` or declared-events sentence names the
   row it reports and the sides THAT row was held to wherever they are not
   simply the criterion's one number ("Point at t=0.5: error 2.000e-03 vs
   tolerance 1.000e-03 → FAIL"; "(relative)" after a `rel` row's); a
   checkpoint that was never scored is reported first, with how many
   scored rows fail beside it; declared events that were never found are
   counted as such. Those sentences read new diagnostics keys, written
   only where they apply: a leaf of one number whose reported row defers
   to it — absolute, and of the largest delta — prints what it always
   printed and records what it always recorded.
9. *Geometry (the owner's ruling for the scalar; the kind is the
   controller's).* A new primitive kind, `grip`: the resolved position of
   one SIDE of a tolerance, id `<family>/<edge>/<key>` —
   `points/upper|lower/i`, `points/before|after/i` only where the row
   holds a `time_tolerance`, `points/upper|lower/final`,
   `tube/upper|lower/const` (a tube of constant width; one with control
   points has their rings), `events/early|late/i`, `peaks/lower|upper/i`.
   A grip is never a trace and never drawn in a figure that is not
   editable — four more dots per checkpoint would sit on every report
   page: the report's figure is what the same geometry without its grips
   draws, and six of the seven figure goldens are byte-identical. The
   seventh is the one deliberate change to a report figure: a scalar
   scored against its baseline draws what it allows, two lines
   `scalar/upper` and `scalar/lower` of `kind="handle"`, as its bounds
   form's lines are. A scalar on a statistic has no plot; its sides are
   fields only.
10. *The schema and the forms.* A field kind `sides` in `derive_schema`
    (a `Sides` hint), its default as a spec would hold it, and
    `FieldSpec.side_labels` — `−` / `+` unless the field's metadata says
    (`event-timing`'s time tolerance: `early` / `late`). Row `columns`
    gain the kinds `sides` and `sides?` and an optional third element, the
    column's side labels (a checkpoint's time tolerance: `before` /
    `after`). `export-schema` writes a `sides` field as `oneOf` a number
    or an object with exactly `lower` and `upper`; nrmse's stays a number.
11. *The editor's control (the owner's ruling).* Two number fields and a
    link toggle, `forms.sides_control`, on a criterion's form and in a
    table row. The shape the file holds IS the link state — nothing or a
    number is linked, a pair is not, equal sides or not — and the tool
    keeps no flag beside it. Linked, typing in either field sets both and
    writes the number; unlinked, it writes the pair. Unlinking writes the
    number twice; linking takes the LARGER side for both, so it never
    narrows what the criterion allows (not "never fails a passing
    criterion": a wider `dominant-frequency` window can take in a
    neighbouring peak). With one side blank nothing is written, the blank
    says "required" through the field's own `validation=` — NiceGUI resets
    an `error` prop it did not set — and a table holds its whole list
    back; a side emptied by way of values typed on the way puts back the
    pair the spec held when the field took the focus. A half-typed pair
    outlives a rebuild of its panel through `Panel.kept`, a record the
    browser tab keeps per criterion (`TabState.kept`).
12. *What the editor refuses, and how typing behaves.* A value that can
    never score is refused where it is typed, before the stage
    (`authoring/unscorable.py`): a negative tolerance — a number or either
    side of a pair, the criterion's or a row's — a tube width whose sides
    sum below zero (a negative number, or a pair: the upper bound is under
    the lower one; a pair with one negative side and a sum of zero or more
    is taken) while the leaf's minimum width is no positive number — a
    positive one widens both sides to itself, so the width over it holds
    its reference, and the first build refused every edit of such a leaf,
    the Min width that would repair one included — bounds out of order, a
    scalar's bounds equal. With them, a
    tube's minimum width below zero: it scores, as no floor, and says
    nothing. A refused edit puts the leaf (or the row) back as the field
    found it, and the field keeps what was typed. It is the editor's rule
    alone: the Session, the parser and the scorer take a hand-written
    negative as they always did. A typed negative zero is a zero
    (`TypedNumber`: `-0`, on the way to every negative number, was staged
    as `-0.0`). Every number field is a `TypedNumber`
    (`authoring/typed_number.py` + `.js`): it shows what was typed until it
    is left and takes a server value only while it is not being typed in —
    a stage outlasts the gap between two keys, and the answer to one key
    landed under the next (`0.25` over `0.3` read and staged `0.025`) —
    and the wheel over a focused one scrolls the page, stepping nothing.
13. *An edit that leaves the entry as saved is not pending.*
    `Session.stage`, after the fold and the validation, drops every op of a
    model whose entry with its pending ops applied equals its saved entry
    as JSON reads it (`json_equal`: members in any order, `1` is `1.0`,
    `true` is no number), and rescores it to its saved verdict. Before
    that it drops, for a test with no tree in the file, the ops on
    `/metrics` while the entry's `metrics` is the tree the saved entry
    already scores by — its implicit tree written out — and the test's
    `variable_overrides`, which that tree is built from, are as saved: the
    tree is no edit alone, and none beside a plots edit either (the first
    build asked that every other member be as saved, and Save wrote a
    `metrics` block where only `plots` had changed). With an override
    edited the tree stays: without it the entry would score by another.
    The count, the strip, the Tests drawer's mark, Save and
    the preview read the one pending list; the preview's "These edits
    leave the entry as saved." line is gone with the state it described.
14. *The arrows (the owner's ruling, on a spike's pictures).* In the
    tool's figure a grip is an arrow: a pixel-sized `path` triangle
    anchored at its edge's data value, its apex 10 px outside the edge and
    pointing at it, orange (`#E65100`) beside the rings' green. A window
    spans its panel, so an event's and a peak's arrows are anchored on the
    paper at the panel's mid-height. A ring is a pixel `path` too — a
    pixel circle larger than 10 px has resize zones in plotly.js, and a
    ring pressed off its centre was resized and staged nothing. An arrow
    whose far end would pass a SET axis range (the spectrum's x) is drawn
    on the inside of its edge.
15. *The pointer map.* The editable figure says which of its shapes take
    the pointer, in `layout.meta.dstf`: `grips` (per grip shape, its
    `group`, the `axis` it moves on and its tooltip `label`), `targets`
    (per ring its grips stand around, that group) and `draggable` (the
    names an editor moves: the open leaves' rings, grips and handle
    lines). `authoring/shell/grips.js` marks every other shape inert, so a
    press on a box, a window, a result line or another criterion's bound
    is the plot's own — under `edits.shapePosition` Plotly drags them all.
    The map rides IN the figure, so it is always the map of the figure the
    browser holds, and the page knows no metric's id pattern.
16. *When the arrows show (the owner's rulings).* Hidden at rest; shown
    while the pointer is on or near their checkpoint, event or peak, and
    for the whole of a drag; and points that overlap or stand very close
    show together. Each group has a hover area — the box of its ring and
    its arrows' anchors, 25 px larger on every side; a window's, its edges
    over its panel's whole height — and groups whose areas overlap or
    touch are one CLUSTER: with the pointer in a cluster's areas every
    arrow of every member shows, outside every area none. Two arrows of
    ONE cluster that would be painted over each other — of different
    groups, both on the plot, sharing more than 1 px of what is painted
    of them on both axes — are STAGGERED (the owner's ruling, on a
    spike's pictures): each is moved across its own axis, a time or a
    frequency arrow up or down, a value arrow left or right, by whole
    steps of 9 px. In order along their axis each takes the first of one
    step up (left), one down (right), three up, three down at which it
    clears every arrow already placed and still lies inside its own
    group's hover area — so the pointer never hides it on the way to it —
    and a pair that leans parts the way it leans. Found on every redraw,
    with the marks and the clusters, never on a move of the pointer. The
    move is an attribute of the path that the stylesheet makes a
    `transform`: the path Plotly drags is the one it drew, and a release
    stages the edge where the pointer took it. The mechanism is
    the page's and never the figure's — a drag names a shape by index, and
    a figure changed mid-drag loses it: `grips.js` marks each grip's path,
    and the ones to show, and one static stylesheet rule hides a marked
    grip that is not to show by `display: none`, since Plotly writes inline
    `opacity` and `pointer-events` on every editable shape and a grip
    hidden any other way is still grabbed. Every
    redraw replaces the paths, so a `MutationObserver` marks them again
    before paint, and what is shown is latched from a press to its
    release. A scroll is read as a move: the page keeps ONE pointer
    position for every plot (`grips.js` `pointer`), and in the frame
    after a scroll each plot asks what is under it — once per frame,
    however many scroll events the frame brings — so a checkpoint wheeled
    out from under a pointer at rest hides its arrows and one wheeled
    under it shows them, on a plot the pointer never moved over too. The
    plot's hover label gives way within 20 px of a shown
    arrow or its ring. Where nothing hovers (`hover: none`, `pointer:
    coarse`) the open criterion's arrows are always shown. An arrow whose
    anchor is off the plot area, by more than half a pixel, is not
    displayed at all — one rule: no edge on the plot, no arrow; left
    Plotly's clip alone, one drawn inward was painted whole inside the
    panel — it is of no hover test, and its group's hover area is made of
    what is on the plot. A ring is on the plot while any of it is drawn:
    its centre within its own radius of the area. An arrow whose edge is
    the plot's own edge is drawn whole in the margin (see Rejected, the
    padding).
17. *A release.* `grips.js` reads every release on its way to the server:
    an axis the pointer travelled under 8 px along, press to release, is
    sent as unchanged — 8 px is Plotly's own threshold, under which it
    re-snaps the position it did not move (a sideways drag wrote `0.08`
    as `0.0800462`) — and a position let go outside the plot area is held
    to the axis range of the PRESS, so it lands on the plot's edge. A
    grip moves on ONE axis: its editor is handed the released anchor on
    that axis and the grip's own resolved position on the other.
18. *A dragged side back to the leaf.* One rule, `handles._set_side`:
    where the holder has no tolerance or a number, the drag writes a
    number (linked: both sides move); where it has a pair, that side
    alone; rounded to 12 significant digits like every dragged number.
    The holder is the row's own tolerance where the row has one, else the
    criterion's; a peak's row gets one on its first drag; auto-detected
    events write the criterion's `time_tolerance`. Where the rows that
    defer to the criterion differ in `tolerance_mode`, the dragged row
    takes a tolerance of its own, seeded from the criterion's, because one
    number means a fraction to a `rel` row and signal units to an `abs`
    one. What a mode reads off the reference it reads off the reference AS
    WINDOWED, as the scorer does. `HandleEdit` gains `target` (a scalar's
    baseline scalar) and `span` (the run's first and last time inside the
    leaf's window, which bounds where a checkpoint's ring may be let go);
    `event-timing` gains an editor, `edit_events_handle`. Refused, each in
    one sentence: a side dragged across its target, in its own name ("the
    before side would cross its point's time"; a zero side is taken); a
    `rel` side over a target, reference or baseline scalar of zero; a
    peak's window that would reach 0 Hz, whichever arrow or ring moved it
    there ("already reaches", of one that was there before the drag: a
    pair's lower side under its upper arrow, an `abs` window under its
    ring); a side measured from the reference on a leaf scored against
    another baseline, or with none; a tolerance that would be no finite
    number.
19. *What the plot says.* A refused DRAG's reason is said in the editor
    and on a line directly under the plot — the editor measured a screen
    below the hand. The editor's hint is read off what the plot draws for
    the open criterion (`figures.plotly.drags_of`): rings and arrows,
    arrows alone, rings alone, lines alone, nothing. Each arrow carries a
    tooltip naming the side it sets and a cursor for the axis it moves on.

**Rejected, and why.**

- *Two more keys beside `tolerance`.* The controller's recommendation;
  the owner chose the pair (item 1).
- *A pass test on the band's edges.* `lo ≤ v ≤ hi` with `lo = target −
  limit` is not `|v − target| ≤ limit`: at target 1.0 and tolerance 0.1 an
  actual of 1.1 fails the second (`1.1 − 1.0` exceeds `0.1` by a rounding
  step) and passes the first. It would have broken item 5 at the
  boundary, so the test is on `d`.
- *Rings at the tolerance's edges.* They bury the target's ring whenever
  a box is under about 16 px: with a tolerance of 0.001 a sideways drag
  on the checkpoint rewrote the tolerance and left the time. Hence arrows
  that stand outside the edge and never touch the ring.
- *Hiding a grip by `visibility` or `opacity`, or by changing the figure
  on hover.* Item 16 says why each fails.
- *One group shown at a time.* Built first: the group nearest the
  pointer, then by the arrows' drawn tips too, then with an advantage for
  the shown one. At some spacings a facing arrow still could not be
  reached, the display flickered between two groups, and a press where a
  hidden arrow belonged moved the neighbour's ring. Replaced by the
  clusters, on the owner's ruling.
- *On a shared spot, only the arrow whose edge is nearer the pointer is
  drawn.* Measured on the spike not to work: at one spacing an arrow kept
  4 px of its own, and of three arrows on one spot one was never drawn.
- *A colliding arrow drawn inside its own box.* On the same spike's
  pictures; not chosen — the owner chose the stagger (item 16).
- *Our own axis padding for the arrows.* Invisible points past each grip
  gave an autoranged axis headroom, and squashed the plot: with every
  tolerance past the data, opening a criterion took 22.5 % off the value
  axis's scale. plotly.js already autoranges over its shapes and pads a
  pixel-sized path by its extent — the arrows alone take 14.6 % — so the
  figure pads nothing, and the arrows are un-clipped by the stylesheet
  instead: Plotly writes a shape's clip as an attribute, on its group and,
  while it is held, on the path, and a stylesheet property beats both.

**What was measured.** A 20,000-case sweep with a fixed seed, half of it
with a sample placed exactly on an edge, holds the checkpoint's new
verdict to the old one for equal sides, and a 3,000-case sweep per mode
does for the scalar; 34 recorded cases — every mode, both scoring paths:
fields, geometry coordinates, sentences, tree view — were identical across
the refactor that made a tolerance travel as `Sides`. The controller's
gate, a compare over a copy of the TRANSFORM work dir before and after
the scoring change: 0 of 331 verdicts changed, 0 of 873 leaves' scores
differ, the owner's files hash-identical. The arrows, the clusters, the
8 px rule, the clamp and the un-clip were each tested in Chromium with a
real mouse (plotly.js 3.1.1), a pixel at a time where the claim is a
boundary: anchors 50 px apart are one cluster and 51 px two. The stagger
likewise, over pairs with their facing edges 18 to 40 px apart, event
windows, three arrows on one spot, two facing value arrows and thirty
checkpoints 12 px apart: every arrow pressed at its middle staged its own
side, by the pixels dragged; one pass over 120 arrows took 2.3 ms.

**Known limits, left.**

- A drag on a row that defers to the criterion's tolerance rewrites the
  criterion's, so every deferring row moves (item 18). Nothing yet SAYS
  when a row stopped deferring instead: an act returns a patch, and no
  channel carries a note that is not an error.
- An arrow that no level clears stays at the first level it tried,
  under the one drawn over it: the third on one spot where its group is
  flat — three steps is 33.5 px from the arrow's line, and a hover area
  reaches 25 px past its group's anchors — and the fifth on one spot
  anywhere (reasoned from the four levels, not run). Two RINGS on one
  spot are not staggered: the later-drawn takes the press, at its centre
  and on its edge.
- An arrow can stand on a neighbour's ring and take the press there: the
  stagger parts arrows from arrows, not from rings. With time tolerances
  of 12 px a side, of two checkpoints 25 px apart the second's `before`
  arrow lies over the first ring's centre, and 18 or 15 px apart over
  its far edge; a press on that part of the ring sets the neighbour's
  side. (Both run with a real mouse: the rings on one spot, and the
  arrows over those three spacings and three checkpoints in a row.)
- A browser without CSS `:has` (Firefox before 121) clips an arrow at the
  plot's edge as Plotly draws it. Reasoned from the rules, not run: only
  Chromium was run.
- The Playwright browser tests are not run in CI.

**What E2 still owes.** (Built since, as D149.) The second half of step E, none of it built: the
fields following a drag live (a handle still commits on release); arm a
field, then click the plot; Ctrl+click adding a row that settles on the
data; dragged values snapped to a readable precision (a drag writes 12
significant digits). A live drag needs one thing this build did not: a
`TypedNumber` that has been typed in takes no server value until it is
left, so a field that must follow a drag while it still has the focus
needs a way to accept one (the component's own note: a revision prop it
watches).

**What it reverses.** D135 item 1's "a rect still takes a drag: only a
handle's is staged": in the page, a shape no editor moves now takes no
pointer at all (the server still answers such a drag with nothing, for one
that crosses a redraw). And a ring pressed off its centre moves; it no
longer resizes.

**Consequences.** `test_spec.json` and the reference JSON gain a shape,
additive: a file holding only numbers reads and scores as it did, and the
tool writes a pair only where a person unlinked one. What does break is
loose input: a string or a bool where a tolerance belongs is a config
error, and a base tolerance that is not one finite number refuses its
test — a coordinated change for whoever holds such a file; the scan in
item 1 found none in the owner's. `export-schema`'s output changes for the
fields of item 2. The report's scalar figure gains two lines; no other
report figure changes.

## D148 — Themes: a bundled default and dark, tokens to every surface (2026-10-08)

**Context.** Every page already took its neutrals and its accent from one
token table (`reporting/theme.py`, roadmap §1), and the redesign's brief
(§3.9 / §3.10) ruled a theme is a NAME from a bundled set, switchable for
the session in the tool. Step 7 of the roadmap built the second table. It
had no plan: a prototype (a diff, measurements in Chromium and five sheets
of screenshots) was put before the owner, who chose from the pictures;
items 1-7 are those rulings, 8-14 the builder's calls, reversible. This
entry is the contract as built.

**Decision.**

1. *The dark tokens (the owner's ruling).* Neutral grey, not blue-tinted:
   page `#121212`, surface `#1e1e1e`, surface-hover `#2a2a2a`,
   surface-active `#353535`, border `#484848`, border-hairline `#303030`,
   text `#e4e4e4`, text-secondary `#b8b8b8`, text-muted `#9e9e9e`,
   text-disabled `#7d7d7d`, accent `#64b5f6`, accent-hover `#90caf9`,
   selection `#1d3a57`, shadow `rgba(0,0,0,0.5)`. The page is Quasar's own
   dark page, so a Quasar piece nobody restyled sits on the same ground.
2. *A 15th token in both themes: `on-accent`* — the text on a filled accent
   button, `#ffffff` on `default` and `#0b1a2b` on `dark`. On a dark
   surface no one blue both reads as link text and carries white text.
3. *Status on dark is dimmed.* A filled badge is white on the status fill
   in both themes. A row tint and a pill are the fill laid at 14 % and
   24 % over the dark surface, and the text on them is the status's pastel
   — the light table's pill border; warn's border is its fill, so its
   pastel is `#ffd54f`. The pill's border is the fill at 60 %: that number
   is the prototype's, shown in the pictures the owner chose from but not
   ruled on by name. On `default` nothing about a status changes. Role
   chips are the same in both themes.
4. *Plot series are unchanged, except five that do not read on a dark
   plot,* lightened on `dark` only: the soft-check overlay `#7B1FA2` →
   `#ba68c8`, and four ramp stops of the array families — blue's darkest
   `#0d47a1` → `#1264e2`, green's darkest `#1b5e20` → `#237b2a`, purple's
   third `#7b1fa2` → `#a631d8` and darkest `#4a148c` → `#8a40e4`. The
   shades are the builder's: each stop raised in lightness at its own hue
   and saturation until it clears 3:1 on the dark plot and still stands
   darker than the stop before it; the overlay is Material's Purple 300,
   the light shade of the same family, since it sits in no ramp.
5. *The figure's chrome follows the theme:* a reference event's line
   (`#757575` → `#a8a8a8`), the ring and the handle markers (`#2e7d32` →
   `#66bb6a`) with the ring's fill, a context series (the theme's
   text-muted), and the layout template — paper and plot ground, grid,
   zero line, axis line, ticks and titles, legend, hover label, toolbar.
   Every other literal of the figure layer stays.
6. *The report and the dashboard are themed at generation,* baked, with no
   switch in those pages. The tool has a session switch per browser TAB:
   the header's ⋮ holds an item named for the other theme; a reload of the
   tab keeps its choice; the tab starts from `testing.json`'s `theme`.
7. *`← Suite` wears the accent in both themes.* It was NiceGUI's own link
   blue (`rgb(59 130 246)`), which is no token.
8. *`theme` in `testing.json`* is `"default"` (the default) or `"dark"`.
   Any other value — another name, a path, a non-string — is a
   `ConfigError` naming the two. No CLI flag. `export-schema` is the
   schema of `test_spec.json` and does not change.
9. *The theme is an argument, never a process-wide setting.* The
   dashboard's token block was set on its Jinja environment at import,
   before any `Config` was read; it is rendered per page from a `theme`
   argument now, and every renderer and figure builder takes one:
   `render_live` / `render_final` / `render_final_to`, `ProgressReporter`,
   `generate_comparison_plots`, `plot_page_keys`, `variable_figure`,
   `plot_figure`, the Session's `figure` / `plot_figure`. Each defaults to
   `"default"` so a direct caller is unchanged, and `tests/test_themes.py`
   reads `src`: a call to any of them that leaves `theme=` out fails.
10. *One named palette per theme in the figure layer*
    (`figures/palette.py`): what a figure takes from a theme, read by
    name — never a remap of literals after the figure is built. Its
    colours are literals, as every figure colour is; a test pins the dark
    ones to the dark tokens. `default` has NO template, and its figure is
    the dict it always was.
11. *The switch is live.* The `:root` token block is one `<style>`
    rewritten in place, Quasar's palette variables and its dark mode
    follow, and the Test page re-sends each DRAWN plot in the tab's theme
    through `PlotCard.redraw` — a `Plotly.react` on the same node. The tab's
    choice is in its `TabState`; the Session holds no theme, so two tabs in
    two themes read one Session.
12. *What Quasar draws itself:* a filled primary button's text is
    `on-accent`, in a rule inside NiceGUI's `quasar_importants` layer —
    Quasar's `.text-white` is `!important` there, and a layered important
    beats any unlayered one. Cards, menus, dialogs, drawers, tooltips,
    fields and separators take tokens under `body--dark`.
13. *AG Grid under dark only.* NiceGUI turns the grid's colour scheme with
    `body--dark`, and AG Grid's dark scheme is blue-tinted with white
    text: under `body--dark` the Suite grid's ground, text and controls
    are the theme's. Under `default` the grid keeps AG Grid's light scheme
    as it was.
14. *The window is the project's ground from its first HTML.* Which tab a
    request belongs to is known only once the browser has connected, so a
    page's first HTML carries `html { background: <the project's page> }`
    and the tab's own tokens follow it in the cascade.

**What was measured.** Contrast is WCAG 2's, computed in the tests from
the tables, never copied in. On `dark`: text 13.1:1 on the surface,
text-secondary 8.4, text-muted 6.2, the accent 7.5; `on-accent` on the
accent 7.9, where white is 2.2. Each dimmed status's text on its tint is
6.7 or more and on its pill 5.8 or more (fail is the lowest of both); the
theme's own text on a tint 9.5 or more, text-muted 4.5 or more. Of every
line colour the figure layer draws, five read under 2.2:1 on the dark plot
and are the five of item 4, at 4.7, 3.1, 3.1, 3.2 and 3.1 after; a test
scans a figure of every kind and finds exactly those five in the default
figures and none under 3:1 in the dark ones. The lowest that stays is the
purple ramp's second stop `#ab47bc`, 3.5; the accent blue `#1976D2` of an
actual event's line, a sibling overlay and the blue ramp's third stop is
3.6.

Under `default`, the same fixture project rendered by the commit before
this work and by this one: each per-test page differs by the one line
`--dstf-on-accent: #ffffff;`, and the dashboard by that line and the one
rule that uses it (its footer's primary button read `color: white`). The
figure goldens are unchanged.

In Chromium, on the tool's plotly.js 3.1.1 and the report's 2.35.0: a
plot that is zoomed, has a criterion open and an edit staged from it comes
through the switch as the SAME DOM node with its ranges, its
`layout.meta.dstf`, its edit config and the staged edit; its arrows show
under the pointer and a drag of one stages again; a reload keeps the
tab's theme and a second tab does not get it. The prototype measured the
alternative: `Plotly.relayout` with a template leaves the ring and the
context series in the old colours, and the next server redraw paints the
old ground again. The hover box is read under a real mouse on both tiers
— ground, border and text as computed, with the trace's name in it.
Before item 14 the window of a dark project was white for 0.2-1.1 s of
every page load; after, it is the dark page from the first frame sampled.

**What stays unthemed, and why.**

- Status fills and badges, role chips, and every series colour but the
  five (eight since 2026-10-11, below): they carry meaning (the brief's
  ruling).
- The figure's pass, fail, window and muted regions, the error red and the
  grip's orange (4.4:1 on the dark plot):
  the owner's item 5 names what follows, and these are not in it. (A
  marker's white outline was in this list until 2026-10-11, below.)
- The tree card's level bars: one table for both themes. The hovered
  shade of level 2 reads just under 3:1 on the dark surface (2.96, the
  prototype's measurement); a bar says where a row sits and is not text.
- The Variables drawer's array glyph: the default accent ramp's four
  blocks in both themes. It marks a row as an array and is tied to no
  plot; its darkest block is 1.9:1 on the dark surface, dim but there.
- AG Grid's light scheme on `default` (item 13), and what the browser
  draws itself — a native tooltip, a
  select's list — which follows `color-scheme: dark` and nothing of ours.
- A figure a plug-in capability asks its `SessionView` for is the
  default theme's unless the capability passes `theme`: nothing hands a
  capability its tab's theme yet.
- A tab that switched AWAY from the project's theme shows the project's
  ground for the moment before it connects (item 14).

**Five literals that followed no theme** (2026-10-11, found by a review of
the release tree; `default` renders as it did — every figure of the
fixture report and the theme tests' figure cases compared equal before
and after, and the pages differ by one new variable line). On `dark`:
the actual's event line and the sibling-backend overlay `#1976D2`
(3.6:1 on the dark plot) → `#64b5f6` (7.5), the companion overlay
`#388E3C` (4.0) → `#81c784` (8.3) — series read against each other, so
held to 4.5:1; a marker's outline `white`, a 16:1 halo about every
checkpoint on a dark plot, → the plot's ground `#1e1e1e` (it parts a
marker from the line under it, and is the ground's colour in both
themes); the tint of a region drawn over the plot's whole height, grey
at 9 %, → the dark muted text at 9 %. They are the palette's
`actual_event`, `sibling`, `companion`, `marker_outline` and
`span_fill`. And a 16th token, `shadow-menu` (`rgba(0,0,0,0.15)` /
`rgba(0,0,0,0.6)`): the Suite table's column chooser drew its shadow in
a literal a step darker than `shadow`.

**What was not run.** TRANSFORM: the gate was a fixture project with a
Python backend. Any browser but Chromium. The Playwright tests are not
run in CI.

**What it reverses.** The roadmap's §7 and DESIGN.md said status fills
and darks and the plot series keep their values in every theme; items 3
and 4 make the two exceptions above, on `dark` only. `figures/plotly.py`
said its traces follow no page theme: the context grey, the ring and the
reference event line do now, through the palette.

**Consequences.** `testing.json` gains an optional key; a file without it
reads as it did. A project that sets `"theme": "dark"` gets dark pages on
its next `run` or `compare` — the pages on disk are not rewritten until
then. `render_*`, `ProgressReporter` and the figure builders gain an
optional `theme` argument; `css_variables` and `quasar_palette` take a
theme's NAME where they took a table; `HANDLE_RING`, `HANDLE_RING_FILL`
and `CONTEXT_COLOR` are the palette's `ring`, `ring_fill` and `context`.
A third bundled theme is one more table in `reporting/theme.py`, one more
palette in `figures/palette.py` and, if it is dark, its name in
`DARK_THEMES`; the switch goes around the names in order.

## D149 — On-plot editing: a click places, a drag previews, written values are snapped (2026-10-08)

**Context.** D147 left the second half of step E unbuilt: a handle
committed only on release, with the fields a screen below showing the old
value until then; a row could be added only by `Add row`, which puts it in
the largest gap and leaves the author to move it; a field could be set from
the plot only where it had a handle to drag; and a drag wrote where the
pointer happened to stop, to 12 significant digits (`0.373778382112`). The
owner ruled on the five behaviours below; the controller made the calls
numbered after them, said to the owner and reversible. A spike on NiceGUI
3.16 / plotly.js 3.1.1 with a real mouse came first, and two checks it had
not made were made before anything was built (see What was measured).

**Decision.**

1. *Ctrl+click adds a row (the owner's ruling).* Ctrl+click on a plot adds
   one row to the OPEN criterion drawn on it; ⌘+click is the same gesture
   where Ctrl+click is the context menu. It does nothing on a metric with
   no rows: `nrmse`, `range`, `scalar`, a tube of constant width. The
   2026-09-15 design brief (§3.5) and its artboards said Shift+click; this
   ruling replaces that, and those sentences are corrected. Shift is
   Plotly's own modifier on a drag, and a Shift+click on a text field of
   the same page extends a selection.
2. *The row settles on the data (the owner's ruling).* A click names a
   place by what is there, not by its pixel. A checkpoint takes the clicked
   TIME and no value of its own, so it follows the reference at that time;
   where the criterion has no reference to follow it takes the run's value
   there as its `value`. A declared event takes the time of the run's
   nearest event. A declared peak — a click on the spectrum panel — takes
   the frequency of the nearest peak of the run's spectrum, a local maximum
   over the noise floor the scorer matches peaks over. A tube control point
   takes the clicked time and the tube's CURRENT widths there, interpolated
   as the scorer interpolates them, so adding it changes nothing until it
   is dragged. Every other field of the row is what `Add row` gives. A
   tube of constant width has no control point, and a click adds none;
   its FIRST one, by `Add row`, takes the same rule: the width in effect —
   the constant its mode reads, `tube_abs` on `band` and `tube_rel` on
   `rel`, a number or a lower/upper pair — where it took 0 and 0, and a
   passing tube read "0.0% inside → FAIL" at one click (2026-10-11). On
   `abs` there is no constant to take (literal bounds, unscorable with no
   point): its first row is still 0 / 0. It is
   the metric's own function, `MetricDescriptor.place_row` over a
   `handles.RowPlace`, beside `edit_handle`; `row_noun` says what the row
   is called, or that a click adds none to that leaf. A row placed ON
   one that stands there is refused, naming it ("a checkpoint already
   stands at t=0.6 (row 2)"): within ONE snap step of a row's time —
   after any clamp — or, for a peak, inside a declared peak's own
   window. Two Ctrl+clicks on one spot left two rows.
3. *Arm a field, then click (the owner's ruling).* What a click can
   place has a ⌖. Pressing it arms it — one per tab, which one editor per
   page makes true — fills its ⌖, and says so twice: the editor's hint
   becomes "Click the plot to set <its label> — Esc cancels.", and the
   PLOT's own card says the same on a line under the plot, in the accent,
   with an accent outline about the plot (an attribute of the card the
   stylesheet reads) — the ⌖ can be a screen below the plot the click is
   made on. The next plain click on the panel it belongs to sets it and
   disarms. Disarmed besides by: Esc; the ⌖ again; closing the editor;
   opening another criterion; a row added to or removed from a table; a
   change of mode; and ANY edit of the test — a Discard, a Save, another
   tab's, a Ctrl+click, a typed value — since a ⌖ names a row by its index
   and a field by its keys, and an edit can put another under the name.
   The ⌖ is out of the Tab order (the controller's): it is the first half
   of a gesture whose second is a click on the plot.
4. *The fields follow a held handle (the owner's ruling).* While a ring, an
   arrow or a bound line is held and moved, the fields its release would
   change show what it would write. Nothing is staged before the release.
5. *Placed values are snapped (the owner's ruling).* A dragged or clicked
   value is rounded to a readable step: the power of ten nearest to a
   thousandth of the visible axis span. A typed value is never snapped.
6. *One click channel, in the page (the controller's).* `grips.js`, which
   already holds the press, the travel and the ranges, hears a capture-
   phase DOM `click` on the graph div and sends `dstf_plot_click`: the
   panel under the pointer, the position in its axes by their `p2d`, what
   both axes show, and the four modifier keys. Plotly's own click event
   reaches NiceGUI with neither a position nor a key. It sends nothing for
   a click whose `detail` is 2 or more (a double-click resets the axes), a
   click more than 4 px from its press (a Ctrl+drag ends in a real click
   where it is let go), a click on a shape an editor moves (a press there
   is a drag's), or outside every panel's
   plot area, where the toolbar and the legend are. A panel is a pair of axes that overlays no other: the time
   plot's, the spectrum's. What a click MEANS is the server's: the page
   never knows what is armed or which metric is open.
7. *A click that sets a field is a drag of its handle released at the
   click (the controller's; which fields carry a ⌖ is the review's).* ONE
   rule, `edit_on_plot._target`, says how a click sets a thing in the
   criterion's CURRENT state — the ⌖ is drawn by it (`placeable`) and the
   click acts by it (`placed`), so no ⌖ stands where a click would be
   refused as setting nothing.
   - A TABLE ROW has one ⌖, in its first cell ("row 2"): the click is its
     ring let go there, through the ring's own editor — its time, a peak's
     frequency, from the click's x; its value from the click's y only
     where the row HOLDS one, so a checkpoint that follows the baseline
     goes on following it. A declared event has no ring: its time is the
     click's x. A row's tolerance sides have their arrows, or are typed.
   - A criterion's own field has a ⌖ where a handle of the leaf writes it
     (`handles.handle_fields`): a bound, a side of a tolerance the plot
     draws. Of several handles that write one field (a tolerance its rows
     defer to) the one nearest the click is used, and the side's EDGE
     lands on the click. So there is none on the width of a tube's
     inactive mode, on a scalar's bounds while it is scored by its
     tolerance (or the reverse), on a time tolerance nothing defers to.
   - Two fields with no handle are set from the click itself: a window's
     start and end (its x), and a BLANK bound of a leaf that draws its
     other one (its y, then the bounds' own refusal).
   Every one of these belongs to ONE panel — the axes its handle or its
   meaning lives on — and a click on the other is refused in words that
   name where to click ("row 1 is set on the time plot"), stages nothing
   and leaves it armed: a click on the spectrum wrote its magnitude as a
   value. A refused click says why on the line under the plot and stays
   armed. A refusal is a person's own (`unscorable.Refused`): shown, never logged.
8. *A live drag is fields only (the controller's).* Plotly says nothing
   during a shape drag and its cover takes every move from the plot, so
   the page listens on the document in the capture phase and sends the
   held handle's would-be position (`dstf_grip_move`) — the figure's
   position at the press plus the pointer's travel, under 8 px on an axis
   not moved — once a frame at the most and 66 ms apart, and once more
   where it is let go, shaped as the relayout its release will be. The
   server reads it with the SAME pure
   function the release uses (`edit_on_plot.dragged`) and hands the leaf to
   every panel of the editor (`Panel.preview` / `on_preview`), which shows
   it without staging or rescoring. The box or band a handle belongs to
   still follows on release: a figure changed under a held shape loses the
   drag. A refused, unmoved or abandoned drag puts the fields back.
9. *A preview is not a value, and not a prop.* `TypedNumber.preview(
   value, revision)` calls a METHOD of the field's component in the page:
   what to show instead of what it holds, and the number of the drag it is
   of. The value is untouched, so nothing reaches `on_change` and nothing
   can be staged by showing it. It was a prop first: a prop write resends
   the element, and NiceGUI 3.16 then renders the page's whole tree again
   — a 50 to 77 ms task in the browser for every move of a held handle
   with thirty rows open. A method call redraws that field alone. The
   component shows a preview whatever its typing state, except in a field
   typed in since that drag began: what a person types is never painted
   over. (D147 expected a field to keep the focus through a drag; in
   Chromium the press on a shape takes it — see What was measured — and
   the rule holds either way.)
10. *Every move and release is numbered (the controller's).* The page
    numbers them, rising; the release carries a number past every move of
    its drag, and `FigureHook` drops a move numbered at or under the last
    release (or drag end) it heard. The page also stops sending at the
    release. A websocket delivers in order, so only a move sent late can
    arrive late — the guard is for that one, and is the server's.
11. *Snapping is one function (the controller's).*
    `comparison.snap.snapped(value, span, fraction=False)`: the step is
    `10 ** round(log10(span / 1000))` — nearest in the exponent; a
    coordinate, and a tolerance side in the axis' units, is rounded to a
    multiple of it as a decimal (so the result is the float a person would
    type; an exact half goes to the even multiple); a RELATIVE side, a
    fraction on no axis, to 3 significant digits; never `-0.0`; with no
    span, nothing. The span is the page's — the ranges held at the press,
    or at the click (`HandleEdit.visible`, new: `HandleEdit.span` is the
    run's time span, another thing). The editors snap FIRST and then clamp
    and refuse, so what is judged is what would be written. An axis the
    pointer went under 8 px along sends no position, so its value is not
    re-snapped: a hand-written `0.123456` stays until that axis is really
    dragged. What a row SETTLES on — an event's time, a peak's frequency, a
    tube's widths, the run's value — is read off the data and not snapped.
    `_written`'s 12 digits stay under everything.
12. *The hint.* The editor's hint line adds "Ctrl+click adds a point." (an
    event, a peak, a control point) for a criterion a click adds a row to.

**Rejected, and why.**

- *Plotly's `plotly_click`.* Through NiceGUI it carries no click position
  and no modifier key; under x-unified hover it lists every trace at that
  x. A DOM listener and the axis' `p2d` gives the position within a pixel,
  on the spectrum panel and after a zoom too.
- *Redrawing the plot's dependent shapes during a drag.* A figure changed
  mid-drag loses the held shape (it is named by index), and plotly.js
  rejects a call made while it is dragging.
- *Setting the field's value for a preview.* A value set from the server
  fires the field's change handler, which stages; guarded, the value would
  still be the field's when the drag was refused.
- *A mapping from field to handle written per metric.* `handle_fields` is
  the editors' own choice of holder said once, and a test moves every
  handle and checks that what changed is what it names.
- *Snapping the EDGE of a tolerance instead of the side.* The field shows
  the side; a round edge over a target of 0.3141 is a side of 0.0537.
- *Deleting a row from the plot, a brush for windows, touch gestures.* Not
  in this step: rows are removed in the table.

**What was measured.** Before the build, on the tool as it was: a DOM click
on the SPECTRUM panel read by `xaxis2` / `yaxis2` lands within 0.64 px of
an independent reading (the drag layer's box and the axis range), plain and
with Ctrl, before and after a zoom of that axis; five element updates sent
to a field while its arrow was held did not disturb the drag (the release
staged the travelled value to the pixel), an update addressed to a field
the release had already rebuilt away raised nothing, and one addressed to
the NEW field landed — which is why the late-move guard is the server's.
After: a row added by Ctrl+click stands 1.0 px from the click along x (a
mouse event lands on a whole pixel) and on the baseline to 1e-9; a field
shows a held arrow's value 57 to 109 ms after the pointer moved (two
runs); a held drag sent 30 moves in 2.05 s, 14.6 a second; the same 37 px drag wrote
`0.28` on an axis showing 6 (step 0.01) and `0.108` on one showing 0.6
(step 0.001); eight moves of a finished drag sent again under a server
slowed by 250 ms a stage changed neither the fields nor the leaf. A press
on a ring or an arrow takes the focus off a field in Chromium
(`document.activeElement` is the body). plotly.js rejects a promise where a
Ctrl+drag is let go — on the tool before this step too; nothing is shown
for it. The cost of an editor of thirty checkpoints, before this step /
with a ⌖ in every cell and the preview as a prop / as built (one machine,
medians of three): open 431–450 / 579 / 469 ms; the rebuild after a
release 531–608 / 696 / 557 ms; DOM nodes of the editor 1921 / 2841 /
2081; browser tasks over 50 ms during a two-second drag 0 / 21 (60 ms the
median) / 0; pointer moves the page took in those two seconds 56–60 / 21
/ 46. A points row is one line at 1280 px and two at 1024 px, as before.

**Known limits, left.**

- A plain double-click with a field ARMED sets the field with its first
  click before the second resets the axes: a click is acted on when it
  arrives, not after a double-click's wait.
- A Ctrl+double-click adds one row, by its first click.
- While a handle is held the box, band, window and arrows that depend on
  it stay where they were; they are redrawn on release.
- A redraw under a held handle — a value typed in another field with the
  keyboard while the mouse holds one — can make the preview differ from
  what the release stages (0.46 shown, 0.53 staged in the review's probe).
- A tolerance side dragged less than half a step past its target is
  written 0, not refused: it is snapped first.
- The window losing the focus mid-drag stops the previews for that drag.
- A key typed in a field during a drag that leaves a valid value stages it
  — a typed value always does — and the plot is redrawn under the held
  shape.
- A ⌖ costs a little: an editor of thirty checkpoints has 32 (one a row,
  two on its window) — see the cost measured below.
- Only Chromium was run; the Playwright browser tests are not run in CI.

**What it reverses.** D147's "a handle still commits on release, the fields
unchanged until then", and its expectation that `TypedNumber` take a server
value by a revision prop "whatever its typing state": the prop is there,
and a field typed in since the drag began is the exception. The 2026-09-15
brief's Shift+click.

**Consequences.** `test_spec.json` gains nothing: every gesture stages the
one `replace` of a leaf that a drag and a form edit stage. What a drag
WRITES changes: a value on a readable grid in place of twelve digits — a
spec edited by drags before this holds the long numbers until they are
dragged again. `MetricDescriptor` gains `place_row` and `row_noun`,
`HandleEdit` `visible`, the `Panel` contract `place_button`, `on_place`,
`preview` and `on_preview`, the figure hook two kinds (`dstf_click`,
`dstf_move`); the pointer map (`layout.meta.dstf`) and every figure are
unchanged.

## D150 — The simulation queue: one FIFO per tool process over a live worker pool (2026-10-08)

**Context.** `dstf author` ran one job at a time: a Rerun locked its tests
against every edit, refused Save, Discard and Reload until it ended, could
not be cancelled, started its backend's workers for that run and closed
them after it, and then re-read and rescored every test of the project. A
rerun-edit-rerun loop paid a library load each time, and a second test
could not be added while the first ran. The redesign brief (§3.6) asks for
a queue; this is it, in two tiers — the pool in `backends/`, the queue in
the tool.

**Decision.**

1. *One queue per tool process, in the Session.* `Session.enqueue` /
   `remove` / `cancel`, the items in run order (`Session.queue`), first in,
   first out, a test never in it twice (the pool's refusal, said as its
   sentence). The pool (`backends/pool.py`, `pipeline.open_pool`) is opened
   at the first enqueue and lives, its workers with it, until the tool
   stops, the project is reopened, or Stop workers in the panel's ⋮ — which
   frees the licence seats and is refused while a test runs. A stopped pool
   takes nothing more, so the next enqueue after one replaces it.
2. *Each result is read back and rescored as its test ends — that test
   only.* The pool hands the item its result; the Session stores it and
   scores that one model. No reload per item: it read every result of the
   project for each. The pool's callbacks arrive on its threads and do
   nothing there but post to the UI loop, where every Session mutation
   happens.
3. *A queued or running test is not locked.* A queued test takes every
   edit and runs with the spec as it stands when it STARTS
   (`SimulationPool.update` hands a waiting item its test again after each
   re-merge). A running test takes its criteria's edits, scored when its
   result arrives with the criteria as they are then; its simulation
   settings are refused with a sentence, in `Session.stage`. Save, Discard
   and Reload are not refused. What a test's run started with is gone from
   memory when the run starts — the run cleared it on disk — so a running
   test reads NOT RUN, and a per-leaf edit of a flat entry is refused there
   as on any test with nothing scored.
4. *Rerun is Queue.* The capability (`EnqueueTests`, name `enqueue` — the
   shell names no capability, and "queue" is its word for the queue) returns
   an `Enqueue` outcome; its caption counts what it adds and what the queue
   already holds ("Queue 3 (2 already queued)") and says the unsaved edits
   are taken when each test starts. The `Job` outcome, `JobContext`,
   `RunningJob`, `LockedError`, `JobRunningError`, `Session.running` /
   `last_job` and the `job` event are deleted. Accept refuses a queued or
   running test with a sentence; a reopen refuses while the queue holds one.
5. *The queue panel, beside the Suite table.* One row per item in run
   order: its number, status badge, test (a link to its page), its state in
   words — `waiting · 3 ahead`, `running · 42 s · worker 2`, `finished · 9 s`,
   `failed: <message, one line; the whole on hover>`, `cancelled` — and
   Remove (waiting), Cancel (running, where the backend can), Open test and
   the Suite's actions for that test (ended). Its header: counts, a Workers
   field (1 to the machine's cores, from `Config.parallel` — 1 in the tool:
   `testing.json` has no key for it and `dstf author` no flag; applies to
   workers started after the change), Clear finished, and ⋮ → Stop workers. Above
   the table under 1200 px, and one line while empty. The rows are data
   (`authoring/queue_rows.py`); the shell draws them.
6. *The header's indicator, on every page.* `3 running · 5 queued` or
   `idle`; red, with the first words of the pool's message, when no worker
   could start, until Clear finished or the next test that starts. A click
   opens the Suite page at the panel.
7. *The Test page of a test the queue holds.* The strip says "Queued —
   waiting · 3 ahead" with Remove, "Running on worker 2 — 42 s" with Cancel,
   "Cancelled — no result (its earlier result was replaced when the run
   started)." or the backend's message with Queue again.
8. *No worker can start.* The pool fails the waiting items with the
   backend's own message and stops; nothing stays queued. The bound is the
   existing `worker_startup_timeout`.
9. *`status.json` and the dashboard are written when the queue drains* —
   nothing waiting, nothing running — for the items that ran since the last
   write, off the UI loop, with the Session's verdicts for every test. Not
   per item: both are one run's snapshot. The runs card and its one-second
   `status.json` read are deleted; the shell's one-second `poll` event
   remains only as the clock a running row's seconds move by, and reads no
   file. The snapshot's `elapsed` and `start_wall` are its items' — first
   start to last end; made after the run, the reporter counted its own
   0.0 s. A row of the page links to a report page only where the page's
   writer read that page's sidecar, fresh (`TestRow.report_page`): a run
   wipes its test's report dir, so a test the queue ran has no page until
   a `--report` writes one, and its name linked to nothing — as every
   row's did after a plain `dstf run` or `compare`. The sidecar is looked
   for where the suite writes it (the baseline compared against, else
   the test key), not only under the name the run guessed. Such a row's
   Reference is its id as text: the link's target comes from the sidecar,
   and the page's writer is handed no reference directory. A sidecar
   names its test's baseline wherever the store holds one, and a null in
   a sidecar replaces nothing its row knows: a failed simulation's, whose
   comparison names no baseline, said null beside the baseline's file,
   and once found took the Reference id off its row.
10. *Stop server asks while the queue holds a test* ("3 tests are queued
    or running — stop anyway?"), then removes the waiting ones, stops the
    running ones and closes the pool; NiceGUI's shutdown hook closes it too
    (a real Ctrl-C was not run).
11. *Every backend with a single-test path can be queued.* One that runs
    whole batches only, or cannot be built on the machine, refuses the
    first Queue with its sentence, and the button carries it from then on.
12. *A cancelled run has a word.* It is recorded `cancelled` and read back
    as no result, so the classifier's verdict stays NOT RUN (D145: nothing
    failed, the exit code is unchanged). `CANCELLED` is what a live row
    says for why, on NOT RUN's class, so its pill, tint and filter count
    it; a cancelled row keeps the word when the compare phase scores it
    NOT RUN. One rule decides that, `reporting.status.scored_status`, and
    every surface that puts a verdict on a row asks it: the Suite's row,
    the sidecar's enrichment and the dashboard's comparison in hand — the
    last had none, so every dashboard a command wrote (the queue's drain,
    `dstf compare`, each of which hands the page its comparisons) said NOT
    RUN where the Suite said CANCELLED (found by an executing review,
    2026-10-08). A row keeps the word only while its `status.json` row
    says `cancelled` and its verdict is NOT RUN; any other verdict means
    the test ran since. The console and JUnit have no row to read why
    from: they print the verdict — `NOT RUN`, `<skipped message="not
    run"/>` — and count the test there, as the pill does.

**From the backend tier** (measured there, not again here): cancel is per
backend — Dymola's `stop` file ended a simulating test in 1.7 s with the
worker reused, and a test still translating is stopped by killing Dymola
and restarting the worker; OpenModelica kills omc's children, 0.02 s; the
JSON-driver backends kill the test's subprocess; FMPy simulates in-process
and cannot. A worker's start is bounded, and its failure is the backend's
own words. A tool that dies takes its children with it. One that died
while its worker sat idle fails no test: the dispatch step asks whether
the tool's process is still there before every test, and restarts the
worker first (found by an executing review, 2026-10-08, where it failed
the next test queued with "Worker process exited during the test … during
simulateModel(<the test before>)"; after the fix, with the idle tool
killed by PID, the next item FINISHED 12.1 s after it was queued on
Dymola 2026x and 2.6 s on OpenModelica, bit for bit a plain run's).

**What the gate measured** (2026-10-08, the example library copied to a
scratch directory with one stiff model added, driven through the Session —
not clicked in a browser): Dymola, three queued — the third removed while
waiting, the stiff one cancelled 8 s into its simulation and ended 0.46 s
later, the next ran on the same worker and was rescored PASS 3 s after;
`status.json` listed the cancelled and the passed test; closing the pool
took 1.2 s and left no process. OpenModelica — a run completed and was
rescored PASS; with omc killed during its start both waiting tests failed
1.3 s later with "No OpenModelica worker could start — worker 0: omc exited
(code -9) during setCommandLineOptions(…)", the indicator red, nothing left
running.

**Not built.** The growing trace of a running test (the next step, on this
queue — built since, as D151); a log viewer for a failed row; cross-backend chains through the
queue; an idle timeout for workers; a simulation-settings editor — `/simulation`
is not stageable for any test (`patch_apply`'s whitelist, the `spec-update`
seam), so item 3's lock has no field to grey yet, and a queued test's
settings cannot be edited in the tool either.

**Known limits.** The page of a running test draws no criterion (it has no
result), so criteria are edited mid-run only where the tool offers an edit
without one (reversed by D151). A cancel is a request, and Dymola's is a `stop` file dymosim
reads between steps: a model blocked in an external call
(`Modelica.Utilities.System.command`) never reads it, so the cancel takes
the worker's whole grace (`Worker.cancel_grace`, 10 s) and then the
worker itself, which is restarted — 11.6 to 11.7 s from the click, 4 of
4, where a CPU-bound model stopped in 0.16 to 0.38 s and kept its worker
(measured 2026-10-11 by the release gate, on a real Dymola). Meanwhile the item is still
running: its row reads `cancelling…` with no second Cancel and its strip
`Cancelling on worker 1…` (`QueueItem.stopping`; the Session says a
`queue` event, state `cancelling`) — they read `running · 42 s` beside a
Cancel button until the end. Stop workers and Stop server close the pool on the UI loop
(1.2 s with one Dymola worker). The pool starts one worker per item as items are submitted: with
several workers asked for, one whose start fails at once can stop the pool
before a second was started. Every load of a Test page logs NiceGUI's
"Event listeners changed after initial definition" in the browser console
— two listeners the page adds to the layout after the handshake — with or
without a queue. A tool process killed with SIGKILL leaves its simulators
running: the pool's close is what ends them (Stop server calls it, and
NiceGUI's shutdown hook), and after a SIGKILL nothing is left to. A row
in the panel follows its test's CURRENT state, not its own run's: an old
`cancelled` row offers Accept once a later run of that test has finished.

**QUEUED means the queue** (2026-10-11). A Suite row the Session had not
scored yet — every row while a project opens — read `…` and wore the
`queued` class, so the pills said `QUEUED 331`, counting down, beside a
header reading `idle` and a panel reading `queue empty`. Such a row reads
`SCORING` (`reporting.status.SCORING`, class `scoring`: the neutral grey
under a third name) and has a pill of that word, drawn only while a row
counts under it, as QUEUED's and RUNNING's are — which the table's
`PILLS` now says, not the script. The Tests drawer has no pill for it and
its badge still reads `…`. On the dashboard of a CLI run QUEUED stays: a
waiting test there IS queued for simulation.

**A Queue click runs the library as it is on disk** (2026-10-12). The
workers stay alive, and a live worker loaded the library once: queue a
test, edit its `.mo` — break it, even — queue it again, and the worker ran
the OLD model and the row said PASS (reproduced on Dymola and on
OpenModelica). And the Session's tests were the scan at open: an
`experiment(StopTime=10)` edited to 4 still ran to 10. Now each click
(`Session.enqueue`):

1. **Sweeps the source files once** (`discovery/library_state.py`): the
   modification time and size of every `*.mo` and `package.order` under
   `source_path` and under each dependency given as a PATH; a Julia
   project's `*.jl` and `*.toml`; nothing for an FMU or Python project,
   whose tests each start a process. A sub-directory that is a symbolic
   link is walked as the directory it points at (a sub-package kept
   elsewhere is part of what the simulator loads; until 2026-10-12 it was
   skipped, and an edit behind the link ran the old text on both
   simulators): each real directory is entered through a link once and
   the root never, so a link that leads back ends the walk; a dangling
   link is nothing. A sweep that finds nothing changed is
   the last click's own `LibraryState` object, else the next serial. Every
   item of the click carries it (`QueueItem.library`).
2. **Reads the queued tests' changed files again**: only the files of the
   tests it queues, and only those whose stamp is not the one the test was
   last read at — into the project's scan and the scan cache, by the
   re-merge the rescan uses, so the `spec` event redraws the test's row
   and page and every later re-merge (`_update_waiting`) hands a waiting
   item that test. A test that only the spec declares has its model looked
   for again (`mo_parser.find_class`). A test whose file is gone, or no
   longer holds it, is refused with one sentence — `<model>: its file
   <path> is gone` / `… no longer holds it` — and the click's other tests
   are queued. A test that is already waiting when a later click names it
   is handed what that click read, and that click's state.
3. **Is read off the UI loop**: with a loop, `Session.apply(Enqueue)`
   returns at once, the sweep and the file reads run on the Session's
   background thread (the one that writes `status.json` and the
   dashboard), and the tests are queued by a call posted to the loop; a
   refusal found by the read is a notice. `Session.enqueue` itself stays
   synchronous for a Session without a loop. **The tests a click names
   show as taken at once** (`Session.checking`, since 2026-10-12): from
   the click until the read is back — 0.1 to 3.6 s measured, during which
   nothing on the page moved — each reads QUEUED in the Suite table, has a
   row in the panel that says `waiting · checking the library`
   (`queue_rows.CHECKING`, the one phrase) with no button, and is counted
   by the header (`2 queued`); a test the read then refuses loses that row
   by a `queue` event of state `refused`, and its sentence is the notice
   it always was. The queue holds no item of such a test yet, so the Queue
   button still offers it, and a second click says `<model> is already
   being queued` in the dialog.

Before a worker runs an item whose state is newer than the one it read,
it reads the library again — once per state, not per item; a worker
started or brought back after the sweep read the files as they are
(`Worker.reload(changes)`, `backends/persistent.py`). The default restarts
the worker, on the dispatch thread and uncharged, as Julia does. A restart
that fails — for a reload or after a death — retires the worker with the
backend's own reason in the sentence: `Worker dead; restart exhausted — no
licence: FlexNet error -15` on the item, and `No <Backend> worker could
start — worker 0: Worker dead; restart exhausted — no licence: …` on every
item still waiting (until 2026-10-12 the reason reached stderr only, which
the tool does not have). A restart given up at its bound says the bound
(`… — did not start within 600 s (raise "worker_startup_timeout" …)`) and
is not tried a second time. **A Cancel while the worker is being brought
back ends the item at once**: the request, on record from the moment the
item is armed, gives up the start in flight (`Worker._start_bounded` looks
for it at each poll), the item ends CANCELLED — 0.26 s on real omc, where
it had waited out the start's bound, 600 s by default — and the worker is
not retired: a `Worker` whose start was given up is never started again
(its `start()` may still be parked on a thread), so the runner takes a new
one for its slot, and the old one closes whatever its start still brings
up. Dymola
and OpenModelica read in place, on the test's exec thread under its
watchdog — a reload that hangs is the test's timeout, a cancel during it
the test's: Dymola by `openModel(<package.mo>, changeDirectory=False)` for
the library and for each path dependency holding a changed file;
OpenModelica by `loadFile`. Which dependency a changed file belongs to is
asked in the sweep's own spelling, by one function for the sweep and both
backends (`library_state.dependency_root` and `under`): Dymola compared
the RESOLVED path until 2026-10-12, so a dependency reached through a
symbolic link, or given as an absolute path holding `..`, was never
reopened. omc KEEPS the classes it holds when a load
fails, so **a failed reload fails the item** with omc's message
(`MyLib does not load: Examples/Pipe.mo:12: [syntax:error:2] …`: the
file under the library and the line, whether one file was read or the
whole library — the whole load's message named no file until 2026-10-12,
because OMPython's own check of omc's messages keeps what each says and
drops where it points; the load and the read of the messages are sent on
the session's socket, `_raw_located`), and the
next item asks again. **After a failed reload what the worker holds is
unknown, and its next reload reads everything** — the whole library and
each path dependency, whatever has changed by then
(`WorkerRunner._reload` leaves the runner's state at None until a reload
comes back whole; said once, for every backend that reads in place). The
first build held the worker to the state it had read before the failure
and handed the next reload the files that differed from THAT: one click
that edited `Enc.mo` validly and broke `K.mo` loaded the new `Enc`,
failed on `K`, and after both files were put back exactly (bytes and
modification time) nothing differed — the worker ran the `Enc` of the
failed reload for good, where a fresh worker ran the file on disk
(executed on omc; the OpenModelica worker's own list of files a failed
load had left without a class never came to be consulted, and is gone).
The price: the click after a failure costs a whole load, which on a slow
mount is the library's first load over again. omc loads the whole
library again when a file was added or removed, a `package.mo` or
`package.order` changed, one file at a time would cost more, or a changed
file's path does not say its class; else each changed file by itself:
`parseFile` must name exactly the class the file's path implies, that
class is deleted, the file loaded, and the class must then exist.
(Executed on a model file, a component's and a base class's — the model
ran the new text each time — and on a syntax error, a renamed class, a
wrong `within`, a second class in the file and an empty file: refused
wherever the whole load refuses. That was first written "as strictly as
the whole load", and it was STRICTER in two places where a fresh worker
runs: a `.mo` file in a directory with no `package.mo` — no part of the
library omc loads; the 3,270-file library has twelve — failed its load
with `Failed to insert class easeIn within …`, and a file in a directory
two symbolic links reach, which the sweep spells through one of them, was
refused with `it holds X, not Y`. So a file is loaded by itself only when
every directory from the library down to it holds a `package.mo` and none
of them below the library's own is a link
(`OpenModelicaWorker._path_is_its_class`); any other changed file makes
the reload the whole load. A dependency given as a path is always loaded whole, and the
classes that must exist after it are the ones `parseFile` of its file
names: its directory need not carry its package's name — `DepLib 1.0.0`,
a symbolic link called anything. Until 2026-10-12 the directory's name
was looked for, and every item after an edit in such a dependency failed
with `DepLib 1.0 does not load: loadFile left no class DepLib 1.0`.) While a worker reads, its
item's row says `running · reading the library`, the strip `Running on
worker 1 — reading the library`, and the panel `worker 1 reading the
library — loadFile("…/package.mo") · 12 s` — on Dymola `openModel("…/
package.mo")`, quoted too, so the panel cuts both to the path's last part
(`queue_rows.READING`,
`StartingWorker.reload`).

*Costs, measured over a library of 3,270 `.mo` files (4,117 swept files,
331 tests).* The sweep: 0.06 s on a native file system on one thread; on
/mnt/d 6.9 s on one thread and 1.3 s on eight, so a root whose own listing
is slow is walked on threads. A click, native: the tests are queued 0.09
to 0.17 s after it, 0.71 s when all 331 are queued for the first time
(each file read once); on /mnt/d 1.2 s, and 3.6 s the first time. On the
loop: `apply` 0.2 to 2.4 ms, the posted call that queues 0.3 to 5 ms, the
longest stall of a 5 ms heartbeat during a click 18 ms. Reading again,
native: Dymola 0.01 to 0.02 s on the build's own runs and 0.07 s in the
review's, over the same library; OpenModelica 0.33 s for one file, 1.1 to
1.3 s for the whole library. On /mnt/d: Dymola 8 to 9.5 s (the spike's
figure, not re-measured), OpenModelica 0.39 s for one file and 107 to
110 s for the whole library — what its first load took (92 s with the
mount idle).

**Known limits of it.** A dependency given by NAME (`"Modelica"`) is found
by the simulator and not swept. `testing.json` itself — `backend_setup`,
`dependencies` — is read when the tool opens. An edit made after a click
is seen by the next click; the test queued before it runs what the worker
holds when its turn comes. A file rewritten with the same modification
time and size is not seen (a rewrite of the same length within one tick of
the file system's clock, or one whose time is put back). A REAL file an
editor leaves beside a source file under a name that ends in `.mo` is a
source file to the sweep: an added file, so a reload — the whole library on
OpenModelica — and no error. Emacs's lock `.#A.mo` is not one: it is a
dangling symbolic link, and a dangling link is nothing to the sweep
(written here, until 2026-10-12, as a file it saw). A rescan that lands after a click does not
take back what the click read of a test: a file edited again between the
two shows the click's values until that test's next click. A test that did
not exist when the tool opened
is listed by the next open. Dymola's message for a model that no longer
parses is its generic "Translation failed", the parser's words in the
translation log; an emptied or garbage BASE-CLASS file reads the same on
Dymola (the model misses what it declared), and on OpenModelica, with the
file named: `MyLib does not load: Parts/K.mo: [grammar:error:234] Modelica
library files should contain exactly one package, …` for an emptied one
(`Parts/K.mo: it holds no class, not MyLib.Parts.K` when that file is
loaded by itself) and `Parts/K.mo:1: [syntax:error:2] Parser error: …`
for garbage, either way. A test's OWN file emptied or
turned to garbage never reaches the simulator: the click refuses it — a
file that cannot be opened with `cannot be read: Permission denied`, the
system's words — and a refusal's note leaves the panel when that test is
next queued (`no
longer holds it`). A test only the spec declares whose model was replaced
by an EMPTY class of the same name still runs on both simulators, and its
result holds none of the variables it tracks. The comparator read that as
PASS — the AND of no criteria — until 2026-10-12; it is now FAIL with the
reason where a failing criterion would be named, `the result
holds none of the variables this test tracks (x) — nothing was scored`, on every surface
and with or without a baseline, and no accept stores a baseline for it
(`comparator.nothing_scored`, the one decision). A test with a `metrics`
tree is not asked — each leaf fails by itself for a variable the result
does not hold — and a simulate-only test passes by having run. A result
that holds SOME of a flat test's tracked variables was at first scored on
those alone, the backend's log line all that said one was missing: `x` of
{x, y} read PASS, Criteria 1/1, and a following `--accept` rewrote the
baseline with `x` only, in silence — where a `metrics` tree fails the same
loss. The flat path now does what the tree does
(`comparator._lost_variable_comparisons`): a variable the test tracks
(`result_variables.tracks`, the one rule `tracked_results` asks too) that
the BASELINE holds and the result does not is a criterion not scored, with
the tree's sentence — `Not scored: variable 'y' not found in the result`
— under its override's metric, and the test fails. What only the spec
names stays the log line: a pattern that matches nothing on either side
has no baseline to say it was ever there. An accept over such a run is not
refused, since accepting is how a variable is dropped on purpose; every
accept path says `ref_0042.json (MyLib.M) no longer holds y — the accepted
result does not hold it.` beside the large-file warning
(`storage.accept.lost_variables_warning`). Over a copy of the 331-test
library's stored results the change flipped no verdict and changed no
entry of `verdicts.json`; with a tracked variable added to three baselines
there, those three tests and no other went PASS → FAIL.

That decision first failed one test too many: an entry that tracks
nothing and has no criteria — what `dstf add <model>` writes, `"variables":
[]` — read FAIL `this test tracks no variable and has no criteria` on every
run. A test that DECLARES nothing to score is a simulate-only test
(`TestModel.is_simulate_only`, the one rule: a recognizer's mark, or no
UnitTests variable, no `variables` pattern and no `metrics` tree): it
passes iff its simulation completes, no accept stores or skips it, and the
sentence is gone. The FAIL is for the test that declares variables and
whose result holds none of them. `TestModel.simulate_only` stays the
recognizer's mark alone — the tool's add form is grey on a marked test,
where a staged criterion would never be scored, and open on one that only
declares nothing, where the criterion is what makes it a scored test.
Its Test page says so on both lines: the strip's `Simulate-only: it runs,
and nothing is compared.` and the tree's `nothing scored: this test is
simulate-only`, which read `this test has no baseline` under that strip
for a test that had one.

That rule in turn passed one test too many (2026-10-13): `"variables":
[]` with a `comparison.variable_overrides` entry — `{"x": {"mode":
"range", "min_value": 100, "max_value": 200}}`, `x` in [0, 10] — read
PASS, simulate-only, with no message and no warning, where it had read
FAIL before. An override is a declaration: whoever wrote it asked for `x`
to be checked. The one rule gains the clause — no UnitTests variable, no
`variables` pattern, no `metrics` tree **and no `variable_overrides`
entry** — so such a test takes the nothing-scored refusal, with a sentence
of its own, since no tracked variable is missing from its result:
`variable_overrides key(s) name no variable this test tracks (x) — an
override changes how a tracked variable is scored, and "variables" (or a
"metrics" leaf) is what tracks one; nothing was scored`. It is the scored
path's log line for an override key that names nothing (`… name no
variable of this result and are ignored`), said as the test's reason where
nothing else is scored beside it. An accept skips the test with it; a test
a recognizer marked stays simulate-only whatever it overrides.

**A worker that cannot start says why** (2026-10-12). On OpenModelica a
library that did not parse when a worker STARTED — the first click, or
the click after a kill — failed the waiting items with `No OpenModelica
worker could start — worker 0: failed to load library …/package.mo — `
and nothing after the dash: the start asked `loadFile` for a bool and
then `getErrorString()`, which OMPython had emptied to decide the call
failed. A refused load is read in ONE place, for the start and for the
reload (`_load_refused`: the error with the file and line it points at,
under the directory loaded): `… failed to load library
/work/MyLib/package.mo — Parts/K.mo:3: [syntax:error:2] No viable
alternative near token: ;`, and the same after `failed to load
dependency '…' — ` for a dependency.

**An earlier run's row is a run's record** (2026-10-12). A test queued
twice has two rows, and both read the test's verdict as it stands — the
later run's — beside the words of a run that had ended otherwise, and
both offered Accept, which stores the test's LATEST results: under the
earlier row's name that read as accepting that run's. Only the latest
item of a test carries the verdict and what acts on the test
(`QueueRow.latest`, `QueueRow.status` None): an earlier finished row says
`finished · 3 s`, wears no badge and no tint, and offers Open test alone.
An earlier CANCELLED or failed row keeps its word — it is how that run
ended, no verdict of the test.

**What it reverses.** D137 item 6: the `Job`, its `after_reload` and the
refusal of Save, Discard and Reload while one runs. D129's capability
contract loses the `Job` outcome and gains `Enqueue`.

**Consequences.** A plug-in capability that returned a `Job` has no
outcome to return: the tool pre-dates any outside plug-in. `status.json`
may now carry a row with `status: "cancelled"`. The `queue` event has a
state `cancelling`. The CLI is unchanged.

## D151 — Live results: the running test's page reads its growing result file (2026-10-08)

**Context.** A test the queue ran showed nothing until it ended: its run
cleared its last result (D150), so its page read "nothing scored" with no
plot, and its strip counted seconds. A real library's tests run for minutes;
whether one was converging, oscillating or stuck was visible only in the
simulator's own files. A run that was cancelled or hit its timeout left a
result file with everything it had computed, and the page said "no result".
The redesign brief (§3.7) asks for the growing trace on the test's own
page. It was built in two tiers: the reader in `backends/` (the `.mat`
reader's row count taken from the file's size, `LiveMatReader`,
`Capability.LIVE_RESULTS`), and — this entry — the tool.

**Decision.**

1. *The running test's page keeps its layout and draws the trace so far.*
   When a run starts, the Session keeps the comparison and the result it
   replaces for the LAYOUT alone (`authoring/live_runs.py`): the plots stay
   the ones the test had, in the order the tree names them, and a test that
   never had a result is laid out by the variables its model and spec name
   (its UnitTests variables, each `variables` entry that names one). No
   verdict and no sample of the replaced result is shown. On a backend
   whose result file grows, each plot's Actual trace is the rows read so
   far, the Reference the baseline's as always, and the time axis spans 0
   to the test's stop time from the first second, so the trace grows into
   the plot. One reader per running test, in the Session, polled once a
   second from the UI loop for the union of what the open pages asked for:
   two tabs on one test share it, and the queue panel's row needs the time
   with no page open. A page asks only for the variables of plots that are
   DRAWN (a card draws when it scrolls into view) and not hidden by Focus.
   A plot is re-sent only when the poll brought it a sample, as a
   `Plotly.react`; each run's figures carry that run's own `uirevision`
   (`live_rows.run_revision`, its partial result's too), so a zoom and the
   legend's choices survive every update, and the plot starts over whole
   at the run's first figure — whether the last result's or another run's
   partial one stood there — and at the result's (pinning the time axis
   under the figure's one revision reset x and kept a zoom's y; one
   revision for every run carried a zoom made on a partial result into
   the next run's plot).
   Samples are kept per name, dropped when the reader starts over on
   another file, held to 200,000 a name (thinned evenly past that) and
   drawn under the figure layer's cap with its own LTTB — once past the
   cap and then again each time a quarter of it is new, not on every poll:
   one decimation is 12 ms whatever it is given.
2. *The words while it runs.* The strip reads `Running on worker 2 — t =
   42 / 100 s`: the last whole row's time over the stop time (`t = 42 s`
   where the stop time is not known, `starting` until the file holds a
   row). The queue panel's row reads `running · t = 42 / 100 s`. Every
   criteria row, the tree card's line and a folded family's row read
   `waiting for result`, and no pill is drawn anywhere on the page: the
   page's tree view is the criteria as authored with that word in place of
   every verdict (`live_rows.unscored_view`), so the rows are the ones a
   scored test has, and an edit made while it runs shows in them.
3. *A backend without a growing file says so once.* Python, Julia and FMPy
   write their result at the end. Their running test keeps its plots, with
   the baseline and no Actual, the strip counts seconds, and one line under
   it reads `live view not available for this backend`. Nothing is polled.
4. *The end is the queue's word.* A finished file reads `running` to the
   reader too, so "in progress" is never asked of it: when the Session
   takes an item's end, the live entry goes, no sample of it is kept, and
   the page shows the result scored as D150 has it. A page opened on a
   test already running is served the file from its start, thinned.
5. *A run that did not finish shows what it wrote.* Cancelled, timed out
   or failed with rows in its file: the page draws the file to where it
   stops — read by the size-based reader's partial read, not the live
   reader — with a dashed marker `stopped at t = 5.85 s` in the theme's
   muted text; the strip reads `Cancelled at t = 19.2 of 200 s — not
   scored` (`Timed out at …`, `Failed at …`), every criterion `not scored`.
   It is shown and nothing else: nothing hands it to the comparator, the
   test's result stays none (or the failed one), its Suite row keeps
   CANCELLED or SIM_FAIL, and Accept refuses it with its own sentence ("a
   partial result is never a baseline: queue it again") before the shared
   function's refusal is reached. Its file's header is parsed once
   (`mat_reader.ResultMatFile`, kept with the run's entry) for every
   series and the pickers' listing, and its series share one time array:
   the page asks a name at a time, and each ask parsed the header again.
   It is kept until the test's next run or
   the project's reopen, and is the process's: a tool started later reads
   the test as it did before this entry.
6. *The pickers list the growing file.* Once the file's header is whole,
   the Variables drawer and the add form offer its variables (the live
   reader's own `names`, from the header it reads the rows through — the
   backend's `result_names` parsed that header a second time on the loop;
   the Session says a `queue` event, state `live`, so the open page lists
   them), and a watch plot added while the test runs draws live.
7. *The directory wipe.* A rerun's directory is cleared before its key is
   handed back, and the reader is built only once the item has its key —
   so there is no old file under it. Until the new file exists the page
   says `starting`; a file that goes away under a poller is another run
   (the reader's `run` counts up) and every sample is dropped. Until a
   file's first row is served its header is read again at each poll, and
   another STRUCTURE (names, columns, row shape) is another run: a file
   put in place of a header-only one, no smaller, was read through the
   first one's columns. Parameter values written again under the same
   structure count no run.
8. *Starting workers are listed.* The queue panel shows each worker being
   brought up — `starting worker 1 — loadModel(Modelica) · 85 s`, the phase
   in the backend's own words, a path cut to its last part — and the next
   waiting row reads `waiting · worker starting` instead of `waiting ·
   next`. A worker brought back after a death is restarted under the test
   it is to run: its line reads `restarting worker 1 — …` (the pool's
   `StartingWorker.restart`), and that test's row `running · worker
   restarting` — it read `running · N s` for the whole load. The shell's
   once-a-second `poll` now also runs while a worker starts. A real library
   took 143 s to load; "queued" for that long read as a hang.
9. *`compare` says a timed-out run timed out.* A run killed by its timeout
   leaves a partial file, whose reader fails the result with "Partial
   result: …"; the recorded verdict then found it already failed and added
   nothing. The stored message is now both: `Run recorded timed_out —
   Partial result: …`. Whether or not the file holds a row: a run killed
   before its first one reads back `Run recorded timed_out — Empty result:
   …`, one that left no file `… — Result file not found: …` or `… — No
   Python result at …` — the reader's sentence alone said nothing of the
   run. A failure in the run's own words is not led by the verdict
   (`TestResult.run_reason`: the run result's message, or a reason a
   driver wrote into its result file).
10. *A failed run's reason is on its page.* The strip of a failed or
    timed-out test said "The simulation failed. Fix the model or its
    settings, then queue it again."; the backend's reason was on the
    queue's row alone, which Clear finished removes. The strip says it —
    one line, the whole on hover — and then the next action: `Timed out
    after 12s (…) — fix the model or its settings, then queue it again.`
    After a partial result's `Timed out at t = … — not scored.` where there
    is one. It is the comparison's message, read from the run's stored
    result (what the Suite table's Message column shows), so it survives
    the cleared row. After a restart of the tool it is what is read back
    from disk: the same line where the run wrote its reason into its
    result file (a JSON driver's `simulate` that raised), and otherwise
    the verdict the run recorded with what its file is (item 9: `Run
    recorded timed_out — No Python result at …`) — the run's own sentence
    for a timeout or a driver that died is kept in no file. A test no
    worker could start says one line of the pool's message; a run that
    recorded no reason keeps the plain sentence.

OpenModelica writes its rows a 4 KB block at a time unless asked; the pool
the tool opens sets `Config.live_results`, which adds `-mat_sync=1` to its
simulate call. No CLI run sets it.

**From the reader tier** (measured there, not again here): the row count
taken from the file's size reads every stored result checked exactly as its
header describes it — 997 files of this repository and 330 TRANSFORM
result files, 660,154 variables. A poll opens, reads and closes the file:
a memory map over a file that is then truncated kills the process, and a
handle held open blocks the directory's removal on Windows. Polls took 2 to
14 ms on the Windows mount. `-mat_sync=1` cost +3 % run time on a local
disk and +59 % on one output-heavy run on the Windows mount.

**What the gate measured** (2026-10-08; the example library copied to a
scratch directory with a model that pauses 0.1 s at each of 300 samples,
30 s of simulated time; a real Chromium). *Dymola 2026x, work dir on the
Windows mount:* the worker's start listed (`starting worker 1 — its launch
· 0 s` to `2 s`, the row `waiting · worker starting`); the first run's
trace grew in 33 of 35 one-second reads, 32 distinct `t =` values, 0 to
901 points on an axis of 0 to 30. A rerun of the same test read `starting`
with no point for three seconds and then the new run from `t = 0`; no read
showed a sample of the run before, and the server logged no exception; the
finished trace equalled the result file's series, 901 rows, for both
variables. Against an accepted baseline, mid-run: two rows `waiting for
result`, no pill visible, 370 Actual points beside the baseline's 901; at
the end PASS and the pills back. Cancelled at `t = 8.05`: `Cancelled at t =
18 of 30 s — not scored` 12.1 s later (this model spends its time in a
system call; D150 measured 0.46 s on one that computes), the marker at 18,
the Suite row CANCELLED. A 25 s timeout: the partial trace to `t = 24.2`,
the badge and the Suite row SIM_FAIL; read again on 2026-10-11 with item
10 built, the strip said `Timed out at t = 25.1 of 30 s — not scored.
Timed out after 25s (raise it per test with simulation.timeout in
test_spec.json, or globally with --timeout) — fix the model or its
settings, then queue it again.` Nothing left running.
*OpenModelica:* the trace grew in 28 of 30 reads, 30 to 40 rows a second;
the tool's simulate call carried `simflags="-mat_sync=1"` and the same
library run by `dstf run` did not; a cancel took 0.17 s. *The example
Python library:* `Running on worker 1 — 2 s` over the line `live view not
available for this backend`, gone at the end.

*Cost* (six plots drawn, a file growing to 100,000 rows of 500 variables,
401 MB at 2,000 rows a second, on a local disk): the tick that reads the
file and re-sends six plots took 35 ms at the median, 46 ms at the 95th
percentile and 82 ms at most on the UI loop, the read itself 1.6 ms (4.9
at most); the browser's one long task per update was 72 ms at the median
and 125 ms at most, a `Plotly.react` 9.6 ms a plot. Under the 100 ms set
for the loop, so the read stays on it.

*A header's parse* (2026-10-11, a loaded machine; synthetic files of
2,000 / 20,000 / 60,000 variables with 60- to 100-character names): a
partial result's 20-member family, asked a name at a time, cost 508 ms /
3.75 s / 12.0 s on the loop — every ask parsed the header. One parse per
file: 16 / 184 / 651 ms. With the name matrix decoded a row at a time
(it was a character at a time; the same names over all 703 result files
in this checkout's fixtures and work dirs, 1,338,159 of them): 4 / 20 /
64 ms, and a run's first poll 2 / 18 / 64 ms where it was 27 / 371 /
609 ms. Listing the file for the pickers costs no parse of its own.

**Not built.** Verdicts before a run ends (a tube already left, a range
already broken); a progress bar; live reads for `dstf run`; a page watching
many runs at once; a partial result found on disk by a tool started later;
a live source that is not a DSresult file.

A worker's restart was not produced on a real simulator: its words are
tested against a pool that says it is restarting.

**Known limits.** A test with no result whose spec names its variables only
by pattern has no plot until its first result (a watch plot can be added
from the Variables drawer while it runs). The live trace is a preview: a
backlog longer than the cap is thinned by the reader and not read again.
A plot's zoom does not carry across a run's start or end — on a backend
with a live view; without one (Python, Julia, FMPy) a run's figures keep
the figure layer's own `uirevision` (`LiveRuns.dress` leaves them as they
are), so nothing there resets a zoom. Under a partial
result of a test with no baseline the tree card says "nothing scored: this
test has no result". A flat entry's criteria still cannot be edited one by
one while its test runs (D150): their rows say so. The file is read on the
UI loop; a mount slow enough to make that matter would move it off.

**What it reverses.** D150's known limit "the page of a running test draws
no criterion", and its item 9's "the shell's one-second `poll` … reads no
file": it now reads the running tests' result files.

**Consequences.** `SessionView` gains `partial(model_id)`. The `queue`
event has a state `live`. The pool's `StartingWorker` gains `restart`.
A failure a JSON-driver backend's driver WROTE into its result file reads
back in the words its run result said it in (`JsonDriverBackend._failure`:
"Python execution failed: …"; "… the driver gave no reason" on both sides
for a failure file that gives none), so the strip's line of that failure
is the same before and after a restart of the tool; it read back bare
when this entry was written. A run that left no such file — killed by its
timeout, a driver that died — reads back led by its recorded verdict
(item 9), which is not the sentence its run said. `status.json`'s `detail`
for a JSON driver's failure is the result's message, not the driver's bare
reason. The Session sets `live_results` on the open project's
`Config` when it opens its pool. A plug-in backend that declares
`LIVE_RESULTS` must write a DSresult file at its `live_result_path` for the
tool to show it live; its `result_names` is asked of a finished result
only. The CLI's runs are unchanged;
`compare`'s stored message for a failed or timed-out run whose file does
not say why is longer; nothing reads that text but a person
(classification, `--rerun sim-failed`, JUnit and the dashboard go by the
result's flags).

## D152 — A criterion over an array family: chosen by indices, shown folded, edited as one (2026-10-08)

**Context.** A criterion on twenty elements of one array was twenty
criteria: added one at a time in the add form, listed as twenty rows under
the family's plot, and loosened one editor at a time. The redesign's 3c
folded a family's plot and its Variables-drawer row and left "the slice
field, save-time expansion and the folded criteria row" to 3d (D134; the
brief's ruling §3.10: leaves stay literal, the tool shows them folded). The
owner ruled the two questions that were his on mock-ups on 2026-10-08 (R1,
R2 below); the controller made the calls numbered D1-D8, said to the owner
and reversible. Built on its own branch from those rulings, with no written
plan, and then corrected by the fix batch of an executing review, whose
decisions are folded in below; what follows is the contract as built.

**Decision.**

1. *The slice is chosen in the add form (R1).* When the variable picked in
   the add form is an element of an array the last result holds more of
   (`pipe.T[12]` of `pipe.T[1]` … `pipe.T[20]`), an **Indices** field shows
   under the picker, filled with every element it has (`1:20`). It takes an
   index (`3`), a range with its end INCLUDED (`1:5`), a stepped range
   written start:step:end as Modelica writes it (`1:5:20` is 1, 6, 11, 16),
   and a comma list of those in any order (`1:3,7,10:2:20`)
   (`figures.families.parse_slice`). The indices are the result file's:
   any whole number from 0, in ASCII digits, and what is checked is that
   the family HAS each one — a zero-based array (`y[0]` … `y[4]`) is
   filled `0:4` and takes it; a floor of 1 refused the field's own text.
   Add — or Enter in the field — writes ONE criterion per chosen element,
   literal leaves, siblings where a single criterion would have landed: at
   the end of a combinator, right after the criterion whose `+` was
   clicked, or beside a root leaf as an `and` of them all. This REPLACES
   the brief's and DESIGN.md's field on the Variables drawer's folded row:
   a drawer row adds series to a plot, and a field there chose elements
   for nothing that scores them. The leaves are seeded ONCE, from the
   first chosen element: a `range`'s bounds or a `tube`'s width seeded per
   element would be twenty different criteria, and nothing would fold.
2. *What the field starts at, and what it says.* Opened from the plot's
   `+` or a group's, the field holds every element — the picker lists
   elements, not families, and a family has no row to pick. Opened from
   the `+` on the row of ONE element (a criterion's, under a plot or in
   the tree), it holds that element's own index: that `+` is asked for a
   second criterion there, and "all twenty" added nineteen nobody meant.
   Beside the field a count of what it names, as it is typed (`20
   elements`), and Add reads the same (`Add 20 criteria`): `1:20:5` is
   start:step:end — ONE element — and reads as five. A text that names
   nothing yet says why in the count's place. The form row opened from a
   plot's `+` is titled by what is being added to, following the pick and
   the Indices (`Add a criterion for pipe.T[1:5:16]`, `Panel.retitle`),
   not by the plot it was opened from.
3. *One folded row that edits all (R2).* Under a family's plot, the
   elements whose criterion is IDENTICAL fold into one row: a chevron, the
   worst of their verdicts as its pill, `metric · <the family with the
   folded indices>` (`tube · pipe.T[1:20]`) and a count by verdict (`18
   pass · 2 fail`, with an advisory and an unscored count when either is
   non-zero). An element whose criterion differs from the rest is its own
   row, as before.
4. *"Identical" (D1).* Leaves fold when they are elements of ONE family by
   result-file name (`families.family_of`, D140's rule), children of ONE
   node, and ONE criterion: the same metric, parameters, window and
   baseline — everything but `variable` — compared as JSON
   (`spec_document.json_equal`: `1` is `1.0`, `true` is no number). Read
   off the tree view, so a leaf that names `"against": "primary"` and one
   that leaves it out are the same criterion. Two or more fold; a group of
   one is a row. Two groups of one family are two folds (`pipe.T[1:10]`,
   `pipe.T[11:20]`), each standing where its first member's row was. A
   second identical leaf on an element the fold already holds stays its
   own row: a fold names each element once. A `UnitTests` element never
   folds and gets no Indices field: each is a test quantity and a plot of
   its own (D140), so no plot's rows hold two of them — the add form asks
   the test, and the fold needs no rule of its own. Why the same parent:
   "edit all" under an `and` must not reach into an `or` beside it.
5. *The label.* The index text is `families.slice_text`'s, the one place
   that prints a slice: an arithmetic run as before (`1:20`, `1:5:16`),
   and — new — a list with an unbroken run of three or more prints that
   run as a range, so a family with one element taken out reads
   `pipe.T[1,2,4:20]` and one with a gap `1:6,8:20`.
6. *The fold is derived every time (D2).* Nothing remembers it: not the
   spec, not the Session, not the page. A member edited on its own is its
   own row at the next paint and, edited back, folds again. The chevron
   unfolds a fold to its members' rows — each exactly the row it was, with
   its own swatch, editor, handles and node controls, indented under the
   fold. Which folds are open is the browser tab's
   (`TabState.family_unfolded`), kept by MEMBER path so a fold whose first
   member left it is still the one that was opened; an edit that is not
   the tab's folds them again, as it unfolds the tree card's groups — each
   back to its default. A criterion asked for by name — the tree line's
   jump, an add's reveal, the tree's ⤓, "Edit on its plot" — unfolds its
   fold and shows its own row; so does a criterion typed to a SHUT fold's
   value, which joins it with its editor open.
7. *Rows are repainted, never rebuilt or moved, when the fold moves.* The
   criteria's rows stand flat, in tree order, where the card drew them; a
   fold's row is put in before its first member's row, and a member is its
   own row with two attributes — `data-member` (the indent) and `hidden`
   (under a closed fold). Attributes, not classes: a server repaint of a
   row's classes writes its whole class attribute. Where a row is SHOWN is
   its CSS `order`: a fold's row, its members as ONE block under it, and
   then — a sibling of the fold, flush with it, never between two of its
   members — the row of an element with its own criterion. So a field
   being typed in is the same field, with the focus, after the keystroke
   that takes its row out of a fold; rebuilt or moved in the document, it
   lost the focus on the first key. The cost: Tab goes through the rows in
   tree order, not in the order shown.
8. *The folded row's editor is fields only (D3).* The same form as one
   member's criterion (Edit criteria at a new placement, FAMILY_CRITERION,
   with the members in `Selection.leaf_paths`), showing the value they
   share; each change replaces EVERY member's leaf, its own variable kept.
   No handle is drawn for it — its hint says "These fields change all 20
   criteria. Unfold to drag one on the plot." — and Edit on plot is not
   placed there: a handle belongs to one criterion's drawing, and twenty
   at one place cannot be told apart or dragged as one. A Ctrl+click (or
   Cmd+click) on the plot while it is open adds nothing and says so on
   the line under the plot ("Ctrl+click adds a control point to one
   criterion: unfold this row and open the one to add to."); a plain
   click says nothing, and the line goes when that row unfolds. A refusal — a
   value that can never score, a window that ends before it starts — is
   one refusal, and stages nothing for any member. A mode switched there
   is seeded from the first member, as an add is. The act is handed each
   member by path AND by name, and refuses the whole edit when a path no
   longer holds that member: an edit staged elsewhere moves the tree under
   an open row's paths, and "every member" then overwrote whatever
   criterion stood there.
9. *One gesture, one op (D4).* An add over several elements, a folded
   field's change and an Indices edit each stage ONE op: the whole tree
   their per-leaf ops give (`family_rows.as_one` — `add /metrics`, as a
   move stages, D144), applied by the spec's own patch applier, every
   node the gesture does not name as it was. A second such op in a row
   replaces the first, so a value typed into a folded field is one edit
   and not a keystroke log; and a tree put back as the saved entry scores
   is no edit and leaves nothing pending. On a test with a saved tree the
   header counts 1. On a FLAT test it counted 2 when this was written, as
   it did for one criterion's edit there, and counts 1 since D155 (one act
   is one pending edit, however many ops); the two ops are still staged:
   the op is marked as an edit of the tree
   (`PatchOp.edits_tree`), so `Session.stage` writes the implicit tree out
   first, marked, and the preview diffs the gesture against that tree and
   says "Save also writes this test's criteria out as a tree". Unmarked —
   as first built — the preview showed every line of the tree as added,
   with no note, and an Indices removal showed nothing deleted. N per-leaf
   ops were the other honest shape, and a batch is never collapsed
   (`Session.stage`): twenty ops per keystroke.
10. *What a gesture did, in words.* Its op carries one sentence per thing
    it did (`PatchOp.notes`), which the preview lists above the entry's
    diff: `tolerance → 0.05 on 20 criteria of pipe.T[1:20]`, `Indices:
    removed pipe.T[20]`, `added 4 criteria: pipe.T[1:5:16]`. Where an op
    replaces the one before, a sentence about the same field replaces its
    sentence and any other is kept: a value typed over is one line, two
    gestures are two.
11. *Indices in the folded editor (D5).* The field holds the elements the
    criterion is on, with a count of what Enter would do beside it as it
    is typed (`→ 5 elements (removes 15)`, `(adds 2)`, `(no change)`).
    Enter or Apply removes the leaf of each element left out and adds one,
    with the fold's parameters, for each new element; Esc puts back the
    text applied last. The group's leaves stay in INDEX order — a new
    element goes right after the member with the next smaller index,
    before the first when it is smaller than all — so `1:20`, `1:5:20`,
    `1:20` leaves the tree as it was, and nothing pending; added after the
    last member, the leaves came back 1, 6, 11, 16, 2, 3, … and Save
    rewrote a tree nobody had changed. (This replaces the controller's
    "right after the group's last member".) An index the family does not
    have — the family being every element the last result's file holds
    and every name the test scores — is refused in a sentence naming its
    size ("pipe.T[1:20] has 20 elements; 21 is not one of them."). An
    element that already has a criterion of the fold's metric under that
    node — its own tolerance, or a `warn` of its own — is not given a
    second one beside it: "pipe.T[3] already has its own nrmse criterion
    under this group — edit or remove that one, or leave 3 out". An
    element that already has the fold's very criterion there is a member,
    and never gets a second leaf. A change that would remove more than
    half of the fold's criteria asks once, inline ("Removes 19 of 20
    criteria — Apply again to confirm"). An empty field is refused:
    "Indices is empty. To remove the criterion, unfold this row and remove
    its criteria instead." The elements it already holds stage nothing.
    Afterwards the rows are redrawn (leaves came or went) and the fold's
    editor is open again on what it holds; down to one element, that
    criterion's own editor is. An element the last run did not record is a
    criterion all the same (D142): the strip asks for a rerun.
12. *An open fold's members are held together — until its editor
    closes.* While a fold's editor is open, its members stay one fold and
    apart from every other leaf, whatever they equal. A value typed there
    passes through others on its way (`0.05` is `0.0` first); one equal to
    another group's would merge the two, and the next key would carry
    that group along. Closing the editor — Done, another criterion or
    form opened, the card rebuilt — releases the hold and repaints the
    rows from the tree AT ONCE: no event follows a close, and left as
    painted a fold typed to its neighbour's value read as two rows of one
    criterion, whose Indices then offered the neighbour as a gap. If an
    edit from elsewhere takes the members apart, the editor closes. This
    is the one thing the derivation is told beside the tree.

**What the spec holds.** Nothing new. `test_spec.json` holds one literal
leaf per element, in the tree, exactly as twenty criteria added by hand
would be; no slice, no glob and no family key is written, and a spec
written before this folds the same way when its leaves are identical. The
leaves are literal from the moment they are staged — the preview shows
them — so nothing is expanded at Save.

**What is unchanged (D6, D7).** Scoring, the spec format and the patch
envelope. The report's criteria: `reporting.tree_rows.criteria_rows` is
one row per leaf, the report's page lists every leaf of a family's plot,
and its page for a fixture with a six-element family is byte-identical
before and after this change. The tree card keeps one line per leaf. The
Variables drawer's folded row is as D134 left it, with no field.

**One report string changes.** A family whose elements are neither all of
a run nor evenly stepped — `pipe.T[1]`, `[2]`, `[4]`, `[5]`, `[6]` on one
plot — printed every index in its plot title and legend
(`pipe.T[1,2,4,5,6]`) and now prints its runs (`pipe.T[1,2,4:6]`), on the
report as in the tool: item 5's `slice_text` is the one place a slice is
printed. A whole or evenly stepped family prints as it did.

**A multi-dimensional family (D8).** The fold rule is `families.py`'s,
not extended: a name folds on its LAST single-integer index. So Indices
means that last index — for `port[2, 1].C[3]` the 3, within the family
`port[2, 1].C[]` — and a two-dimensional position (`x[2,3]`) is no family,
folds with nothing and gets no field.

**Known limits.** Under a `weighted` node an Indices change or an add over
a slice is refused: the node carries one weight per child, and neither
writes weights (a single add there is refused the same way). With about
200 elements in one fold, a key in a folded field holds the page for
about half a second and an Indices change for about 1.3 s (measured by
the executing review): each rescores the test once and repaints two
hundred rows. Tab follows the tree's order of rows, not the order shown
(item 7).

**What is not built.** A remove-all on the folded row: an empty Indices
says to unfold and remove the rows. Handles for a fold on the plot. A glob
leaf (`pipe.T[*]`) scoring every element under one leaf, deferred by the
brief (§5): an array resized by a later run is not picked up by itself —
its fold's Indices adds the new elements. Indices over more than one
dimension. The profile plot kind (`docs/ideas.md`). A folded row in the
report or in the tree card. The page's own keyboard access to a folded
row's editor: its chevron is a button, its row — like a criterion's — is
opened by a click. Esc closes no editor.

**Consequences.** Only the tool changes, and the one printed text above.
A capability placed at CRITERION is not offered for a fold unless it also
names FAMILY_CRITERION and handles `leaf_paths`. `Panel` gains `retitle`;
`PatchOp` gains two fields that are no part of the op (`edits_tree`,
`notes`). The TRANSFORM gate is the controller's; nothing of it is
recorded here.

## D153 — A baseline keeps every point its run wrote, unless a number is given (2026-10-11)

**Context.** `ReferenceStore.store_reference` thinned every baseline it
wrote to at most 2000 points: `_downsample(time, values, max_points=2000)`,
a default argument that no decision set and no key could change. `nrmse`
reads the run AT the baseline's time points and averages the squared error
over them, so the cap was also the resolution at which a regression could
be seen — and nobody had chosen it. The owner ruled on 2026-10-11: a stored
baseline keeps every point the simulator wrote, unless a number is given.

**Decision.**

1. *`baseline_points` in `testing.json`* is the project's value: the string
   `"all"` — the default, and what an absent key means — or a whole number
   of 2 or more (a thinned baseline keeps both ends of its run). Anything
   else — `true`, `2000.0`, `0`, `1`, a negative, another string, `null` —
   is a config error in one sentence that names the key, the value given
   and the two forms: `testing.json baseline_points is 1: the points a
   baseline keeps are "all" or a whole number of 2 or more`
   (`config.baseline_points_refused`). There is no CLI flag.
2. *`baseline_points` on a `test_spec.json` entry* — at the entry's top
   level, beside `simulation` and `comparison` — is that test's own value,
   in the same two forms. The order is the test's, then `testing.json`'s,
   then `"all"`: one long run can be thinned in a project that keeps
   everything, and one test can say `"all"` in a project that gives a
   number. A value that is neither form is refused as `plots` is — a
   config error at discovery that names the file and the entry
   (`<test_spec.json>: tests[<model>].baseline_points is 1: …`), `null`
   included: dropped with a warning, the test would be stored at the
   project's setting and nobody told. `TestModel.baseline_points` carries
   it (`None` for an entry without the key); `export-schema` lists it. It
   is written by hand: it is outside the subtrees a patch may touch, so
   `spec-update` and the tool do not edit it, and the tool's Save keeps it
   on an entry it rewrites.
3. *Where a baseline is written.* `store_reference` is the one place a
   primary baseline is written, and the only caller of `_downsample`.
   `"all"` stores every sample the run has, in order, the two samples of
   each event included. A number N calls the thinning that was already
   there with `max_points=N`: the first and the last row, and both rows of
   every event, are kept (events are thinned evenly only when they alone
   are more than N), and the rest are spread evenly by row number. N at or
   over the run's rows stores them all — a point is never invented.
   `_downsample` has no default argument: every call says its number. With
   `baseline_points: 2000` the stored arrays are exactly what the code
   before this stored — a test pins the kept rows against that function's
   output, over runs with and without events, with more events than
   points, and shorter and longer than the number.
4. *The reference JSON records what was done*, under a top-level key that
   is always written: `"sampling": {"setting": "all" | N, "source_points":
   <rows the run had>, "stored_points": <points stored>}`. A reference
   without it — every one stored before this — loads and scores exactly as
   before: old data is read as it is, and nothing is migrated.
5. *A large baseline is said at accept.* When a baseline file just
   written is larger than 5 MB (`storage.accept.LARGE_BASELINE_BYTES`, a
   constant and not a key), the accept says so once per file:
   `ref_0123.json (<model>) is 5.3 MB (74,167 points × 3 variables) — set
   "baseline_points" for this test to store fewer.` It is a warning: the
   baseline IS stored and the exit code is unchanged. The sentence is made
   in the one accept path (`accept_run_results`, which now returns
   `(stored, skipped, warnings)`), so `run --accept`, `compare --accept`
   and the interactive review print it and the tool's Accept carries it in
   its notice. It names the model beside the file: the remedy is that
   test's key, and a file name alone does not say whose. The size is the
   file's on disk and the counts are read back from it, only for a file
   that is over. To tell the accept path which files it wrote,
   `ReferenceStore.accept_results` returns the stored model ids, where it
   returned their count.

**What was measured.** On a real library of 330 tests: 11 of the 330
baselines had been thinned by the cap; the median run has 502 rows; keeping
every point takes the baselines from 10.7 MB to about 24.6 MB, two runs of
about 74,000 rows being about 5 MB each.

**Known limits.** A number thins evenly by row number — not by time, and
not by what the signal does — so it can drop a spike that lies between two
kept points. A baseline has ONE time vector for all its variables: the
points are chosen per test, not per variable. An existing baseline keeps
the points it has until its test is accepted again, whatever the key says
now; its `sampling` record, or the lack of one, says how it was stored.
"Every point" is every row. The values of a double-precision run were
written at 15 significant digits, as they always were, and are at 17
since the second 2026-10-12 addendum below; a single-precision
run's (every Dymola result file measured here) are written at 9 and read
back through single precision — see the first 2026-10-12 addendum below. This
entry first said 7, "as they always were": at 7 digits a stored value is a
different float32 than the run's more often than not (56–67 % of random
values, measured; 0.7–1.2 % at 8; none at 9), and a baseline accepted from
a run did not score 0 against it.

**What is not built.** Sampling by region — segments, chosen times, a
different density in different parts of a run — is deferred
(`docs/ideas.md`). Uniform resampling, or any interpolation: a stored point
is always a row the simulator wrote. A CLI flag. A notice at compare for a
baseline stored under another setting than the one now in effect. A check
for a baseline that ends before its stop time. Any change to a scoring
function.

**Every path that stores a primary baseline** goes through
`accept_run_results` and from there `ReferenceStore.accept_results` and
`store_reference`: `run --accept`, `compare --accept`, the interactive
review's `[a]`, and the tool's Accept (the Suite page's, the Test page's
and the queue panel's are one capability). Soft-checks and companions are
written by their own commands and are not thinned, as before.

**Consequences.** `Config` and `TestModel` gain `baseline_points`; a
primary reference JSON gains `sampling`. A long run's baseline is larger
the next time it is accepted, and its `nrmse` is then an average over more
points — the score of such a test can differ from the one its thinned
baseline gave. A project that wants its baselines stored as before sets
`"baseline_points": 2000`.

**Addendum, 2026-10-12 — a baseline holds its run's own numbers.** A
baseline accepted from a run must score exactly 0 against that run, and
did not: `_to_json_list` wrote a float32 array at 7 significant digits,
and a JSON reader makes a double of what it reads. On the 330 real runs —
all single precision — 665 of the 873 scored criteria were non-zero
against their own run: median 4.8e-8, 74 above 1e-6, the largest 6.28e-2
(a signal of range 0.4 at a level of 130450, where the seventh digit is
the signal). Three changes, in one place each:

- a float32 array is written at **9** significant digits
  (`reference_store.SINGLE_DIGITS`), the fewest that identify a float32;
  float64 stays at 15;
- `sampling` gains **`precision`**, always written: `"single"` when the
  time vector and EVERY stored value array were float32, else `"double"`;
- `reference_store.read_reference_file`, the one reader of a
  reference-shape file (the store, the sibling-partition overlays,
  `import-baseline`), takes each stored number of a `"single"` file back
  through float32. The arrays stay lists of Python floats: each is the
  run's own single-precision number, widened.

A file without the field reads exactly as before — the same 873 scores,
bit for bit, against the baselines stored before this. Measured after an
accept of the 330: every one of the 873 criteria is exactly 0.0; verdicts
unchanged (330 PASS, 1 SIM_FAIL); the reference directory goes from 24.7
MB (every point, 7 digits) to 27.2 MB, its two largest files from 5.4 and
5.2 MB to 5.8 and 5.5 MB — the same two files the 5 MB warning named; and
`"baseline_points": 2000` keeps the same rows as before for all 330 (11
of them thinned). Known limit: a run of mixed precision is `"double"`,
and its single-precision arrays are then the doubles their 9 digits
spell — within 1e-9 of the run, not equal to it. And a result with NO
rows is refused at accept (`no rows to store`): it was stored as a
baseline of no points.

**Addendum, 2026-10-12 — a double-precision run's numbers too.** The
addendum above left float64 "at 15", under a heading that said a
baseline holds its run's own numbers: 15 digits do not identify a
double, and a baseline of a float64 run (OpenModelica, FMPy, Julia,
Python) scored 6.7e-10 against that run on the same kind of signal
(range 0.4 at 130450). `DOUBLE_DIGITS` is 17, the fewest that
round-trip every binary64; JSON writes each value at its shortest
spelling, so a round number costs nothing. Measured on a scratch copy
of each example, one simulation and two accepts: the OpenModelica
partition of the Modelica example goes from 271,202 to 284,877 bytes
(+5.0 %; eleven of its twelve stored tests by 0–4.6 %, `NoUnitTest`
+47 %), the Python example from 24,698 to 27,504 (+11.4 %; `SimpleRamp`
+43 %, its time grid being sums like `0.30000000000000004`). A
`"double"` file stored before this holds 15 digits and reads as it did.
The shipped example baselines were not accepted again for this (the
Modelica example's `OpenModelica/linux` ones were, later that day, for
their state count: D138's addendum).

**Addendum, 2026-10-12 — the tool's Accept refuses over a spec changed on
disk.** `baseline_points` is the entry's, and the tool's Accept wrote the
baseline with the entry as the Session had LOADED it: an entry edited by
hand from 50 to 77 while the tool was open was accepted at 50, with no
word, where Save in the same state refused (`StaleSpecError`). Every
store write a capability returns is now refused by the Session when the
spec's hash is not the loaded one (`Session.apply`, the check Save makes):
`<path> changed on disk since it was loaded; reload before accepting`,
said in the dialog Save's refusal uses, with its Reload — from the
Suite's bar, the Test page's strip and a queue row alike
(`Header.stale`). The CLI's accepts read the file as they start and are
not concerned.

That dialog said the file had changed and offered `Reload` / `Keep my
edits`: not that the Accept had NOT been done (its own dialog closes for
this one), `Keep my edits` with no edit to keep, and nothing of the edits
a Reload drops. It now reads the path's line, then `Nothing was
accepted.` (`Nothing was saved.` for a Save); its Reload button is
`Reload and discard 2 pending edits` while edits are pending — the
header's count — and `Reload` beside `Close` when none are. And a Reload
that cannot be done leaves the Session as it was (`Session.reload`): over
a spec file DELETED under the tool it read the empty document and the
Suite went empty; it now says `<path> is gone: nothing was reloaded — put
the file back, then Reload`, the rows and the pending edits stay, and the
same Reload goes through once the file is back. A file that is read and
is no spec is refused with its own sentence, the same way.

## D154 — What nothing reads is an error; an unscorable criterion is refused by the write that makes it (2026-10-12)

**Context.** `test_spec.json` is written by hand as well as by the tool,
and a key the scorer does not read was kept and ignored. A `range` leaf
written `"max_val": 0.5` was accepted by `spec-update`, written to the
file and scored as a range with no upper bound: PASS. The same hole took
a point row's `tolerence`, a window's `strt`, a leaf's `agianst` (scored
against the primary baseline), an entry's `simulaton` (a run on
defaults), and — one level out — `{"Tests": [...]}` (a spec of no tests,
exit 0), `"simulation": "stop_time=5"`, a `quantity` of `{"reduce":
"max", "over": "x"}`, and a stored baseline's own override. The posture
until then was the opposite one, on purpose: the exported schema left
its objects open (the 2026-07-06 review's finding 72 wired the mode
`$defs` in "with a lenient posture"), `docs/vision.md` said `spec-update`
preserved unknown keys, and this log's Phase 6 notes count tests of
"unknown-key preservation at every nesting level". This entry reverses
it.

**Decision.**

1. *A key nothing reads is an error that names it* — `unknown field
   "max_val" — did you mean "max_value"?`. ONE check
   (`comparison/strict_keys.py`), and what is known is never listed in
   it: it is read from what already says it — a metric's config dataclass
   (its fields, and the `columns` of its row tables), the tree parser's
   key tables, and `discovery/spec_shape.py`, the table `export-schema`
   publishes. Checked: the file's root (`tests`, and `$schema` for an
   editor); an entry's keys and its `simulation` / `comparison` blocks';
   a flat override's and a leaf's; a table row's; a window's and a
   combinator's; the keys inside a tolerance pair (`lower`, `upper`) and
   inside the `quantity` object of a metric that declares one
   (`reduce`); a `plots` entry's. A block that is no object where one is
   read (`"simulation": "stop_time=5"`, `null`, `"variable_overrides":
   [1]`, an override written `"x": "tube"`) is the same error — `metrics`
   and a leaf's `window` among them: `"metrics": null` was a test scored
   by the flat path and `"window": null` a leaf with no window, both
   refused by the exported schema, and `spec-update` wrote the first. A
   window's `start` / `end` is a number or is left out (`null` there was
   a `TypeError` out of the parser). `tolerance`
   is a key of every leaf and override; `quantity` on a metric that does
   not declare it stays the one documented inert param.
   `description` / `info` / `metadata` on an entry and on its
   `comparison` block are the place for notes, so no other unread key has
   to be tolerated to leave room for one.
2. *A hint is offered only for a near miss of one field*
   (`strict_keys.near_miss`): the same name in another case; else at most
   two edits and at most one per three letters typed, with no other name
   as near; else the start, three letters or more, of exactly one name
   that is ONE word (`tol` → `tolerance`, `childs` → `children`).
   Anything else lists the fields. A wrong hint sends the reader to the
   wrong field: `start_time` was offered `stop_time`, and the first build
   of the last rule — the start of any one name — offered `time_tolerance`
   for `time`, `tube_width_mode` for `tube_width` and `baseline_points`
   for `baseline`, three in about a hundred typos a review tried. A whole
   word that begins a longer name is another thing's name, not a slip of
   the longer one; `min` is no longer offered `min_value` either, since
   nothing tells it from `time`.
3. *Where it is refused.*
   - **At parse** — `run`, `compare`, `discover` and `dstf author` (before
     it serves): one `Config error: <file>: <place>: …` line, exit 2, the
     path a malformed `metrics` tree already took. The same line for what
     cannot be a test or a value: an entry of `tests` that is no object or
     has no `model`, `variables` that is no list of strings, a `source`
     that is no string, a `simulation` value of another type than
     `spec_shape.SIMULATION` gives its key (`null` stays "not set") — each
     was a warning and a test skipped or run on defaults, and
     `"variables": [1]` a traceback at the compare.
   - **At the file** — a `test_spec.json` that is no UTF-8 text, no JSON,
     empty, or whose root cannot hold a `tests` list has ONE decoder
     (`spec_parser.spec_document_from_bytes`) that every reader comes
     through: discovery, `spec-update`, the `add` helpers,
     `migrate-variable-names` and the tool's Session. `Config error:
     <file>: not JSON (…)`, exit 2, from each of them; `discover` / `run`
     / `compare` used to log `Failed to read test spec` and go on with a
     spec of no tests, and a UTF-16 or binary file was a traceback out of
     every command but `dstf author`. A missing file, `{}` and `{"tests":
     []}` are a spec of no tests.
   - **For a stored baseline** — the `variable_overrides` a reference
     JSON keeps are merged under the spec's, so each goes through the
     override check: one decision (`comparator.stored_override_problem`),
     a sentence naming the reference file, with two readers. A command
     that scores stops on it before it does anything else — the same
     `Config error`, exit 2, from `run` BEFORE it simulates (the
     reference files are there at discovery; the first build found it at
     the compare, after every simulation, and left no verdict) and from
     `compare` before it wipes or stamps; each for the baselines it will
     compare against (a `--filter`'s tests, every discovered test under
     `--merge` / `--rerun`), and an `--accept`, which compares nothing,
     is not stopped. A comparison of ONE test (`compare_one`) refuses
     that test with the sentence: `dstf author` opens, shows that test
     FAIL with `Not scored: <reference file>: comparison.
     variable_overrides[x]: range · x: unknown field "max_val" — did you
     mean "max_value"?` where it would name a failing criterion, and
     scores every other test.
   - **At a file write** — `spec-update` (`Patch failed: …`, exit 1, the
     file untouched) and the tool's Save, both through
     `spec_update.apply_validated_patches`.
   - **At a stage** — the tool's in-memory `Session.stage` refuses an
     unknown key at once, with the sentence alone.
4. *A criterion that can never be scored* — a `range` with neither bound,
   a `scalar` with no `quantity`, a tolerance that is no number or pair —
   has every key right and fails at every compare. The rule for it is the
   DELTA rule, in one place (`spec_update.refuse_newly_unscorable`),
   applied where a file is written: **a write is refused only for a
   criterion that is unscorable after the patch and was not already
   there, with the same content, before it.** "Already there" is said of
   the criterion, followed through the patch (the ops are replayed over a
   marked copy of the entry): one that is still the object it was, with
   the content it had — the metric, variable, baseline, window and params
   — is the one that was there, wherever a sibling's removal has shifted
   it. Any other unscorable criterion — one the patch added or wrote over
   — is the one that was there only if an equal one of the entry is GONE
   from the criteria that score: a move (a remove and an add), a leaf
   wrapped in a group, a flat entry's implicit tree written out over its
   override. So an entry that already holds one takes every
   other edit, the edit that fills it and the edit that removes it; two of
   them do not block each other; a patch that adds one, or edits one and
   leaves it unscorable, is refused. The second build COUNTED by content
   and nothing else: a patch that filled the bare `range` an entry held
   and added an equal bare one beside it was written — one before, one
   after. The first build refused EVERY
   unscorable criterion of the patched entry, at every patch and every
   stage: an entry holding one took no edit at all — not the fix — and the
   tool could no longer add a bare `range` for its editor to fill.
5. *The tool stages an unscorable criterion and Save refuses it.* A stage
   is not a write: the leaf is staged, its row says why it is not scored
   (`metric 'range' misconfigured: needs at least one of min_value /
   max_value`), and Save refuses in the dialog its stale-file refusal
   uses, with one sentence — `range · pipe.T[1]: needs at least one of
   min_value / max_value — fill it or remove it before saving`
   (`Session.save` → `UnscorableSaveError`); nothing is written and every
   edit stays pending.

**What is deliberately NOT checked.**

- A flat `comparison.variable_overrides` entry under a `metrics` tree is
  not checked for scorability: the tree replaces the overrides there, they
  score nothing, and the tool itself leaves them beside the tree it
  writes out. Its keys are still checked. Remove the tree and such an
  override scores again — that patch is then the write that makes it.
- A metric whose `config_cls` is not a dataclass, and a `metric` / `mode`
  name nothing is registered under: nothing says what such a metric
  reads, so nothing is refused for it here (the parser and the flat path
  each say an unknown name themselves).
- `quantity` on a metric that does not declare it: of any shape, read by
  nothing, by the documented contract (`docs/extensibility.md`).
- An unscorable criterion already in a file is not refused at parse: the
  test is compared, and that criterion fails with its reason, as before.
- A value an editor of the tool refuses to stage (a negative tolerance,
  bounds out of order: `authoring/unscorable.py`) is the editor's rule and
  not the parser's: a spec written by hand that holds one is read and
  scored as it always was.
- `testing.json`'s own keys are not part of this entry.

**Known limits.** The exported schema closes the root, the entry, its
blocks, every mode and override def, a table's rows, a pair and a
scalar's `quantity`; a pair written inside a table ROW's cell is refused
by the parser and not by the schema (a row's cells are untyped there).
The delta rule compares content: a patch that rewrites an unscorable
criterion into a different unscorable one is refused, and one that
reorders the keys of an untouched one is not. A criterion taken out
stands for an equal one put in, anywhere in the entry: a patch that
removes one bare `range` and adds an equal one on the same variable
elsewhere is a move, whatever its author meant. A reference JSON's stored
override with an unknown key stops a whole `run` or `compare`, not that
test alone — it is a file to repair, and no accept repairs it (an accept
keeps the stored overrides it does not replace); the tool, which has no
exit code to give, refuses that test alone. `spec-update` checks
the file and the entry it patches, not the other entries: a patch of one
test is written into a file whose other entry is no object.

**Consequences.** A `test_spec.json` or a reference JSON holding a key
nothing reads no longer loads: it is named, and the command exits 2.
The low-level patch applier (`discovery/patch_apply.py`) still carries
whatever an entry holds through a patch — it is `apply_validated_patches`
above it that refuses the result. `export-schema` is as strict as the
parser wherever it types a value, so a file that validates there is not
stopped for a key.

## D155 — One act is one pending edit (2026-10-12)

**Context.** The tool counted pending OPS: the header's "N pending edits",
Save's caption, Discard's, the Suite's Edits cell, the Tests drawer's mark,
the strip's "N unsaved edits", the preview's "N edits", the Queue caption's
"with N unsaved edits" and the refusal to close a project. A test with no
`metrics` tree has that tree written out under its first per-criterion
edit, so ONE Ctrl+click on a `points` criterion's plot of the shipped
example read `2 pending edits` and `2 unsaved edits · verdict stays PASS`
(a first-time user's finding, reproduced in a browser). D139's controller
call had said so on purpose ("N unsaved edits" counts ops, so the first
criterion edit on a flat test reads 2), and D152 §9 kept it for a fold's
gesture ("On a FLAT test it counts 2").

**Decision.** What is counted is what the user did. `Session.stage`
numbers its calls, and every op of one call carries the number
(`PatchOp.gesture`: no part of the op — not in `raw()`, not in its
equality — like `materializes` and `notes`). `spec_document.edit_counts`
is the ONE count, by entry: the distinct numbers among its pending ops;
every surface named above reads it. An op that supersedes the last one
(the collapse rules of D134's `Session.stage`) keeps the number of the op
it replaces: a value typed over is the edit it was. An op nothing staged
counts as itself.

What the header reads, each on a test with no `metrics` in the file, where
the act takes two ops or more (executed: `test_each_act_on_a_flat_tests_
tree_is_one_pending_edit`, `TestOneStageIsOneEdit`, the family pages, and
a real Ctrl+click in Chromium):

| act | ops | header |
|---|---|---|
| a tolerance typed; a handle dragged; a Ctrl+click that adds a row | 2 | 1 pending edit |
| the same field typed over, a second row clicked into the same table | 2 | 1 (it replaces the last op) |
| a criterion added | 2 | 1 |
| Add over twenty Indices | 2 (the tree, given whole) | 1 |
| a folded row's field changed; its Indices edited | 2 | 1 |
| a criterion wrapped, then unwrapped in place | 3 | 1 for the wrap; the unwrap rewrites the same node and stays that edit |
| a move | +1 (the tree, given whole) | +1; a second move right after replaces it |
| the edit put back | 0 | 0 pending edits |
| one act over two tests | n | 1 of each test |

**What it reverses.** D139's "counts ops" and D152 §9's "On a FLAT test
it counts 2": both read 1 now. The preview's note "Save also writes this
test's N criteria out as a tree" is unchanged and is what says the second
op is there.

**Consequences.** `Export N edits to spec_patch.json` counts edits; the
envelope it writes holds the ops, which can be more. `PendingEditsError.
count` is edits. A plug-in capability's `Patch(ops=[...])` is one edit,
however many ops it returns. Two Ctrl+clicks that add two rows to one
table are ONE pending edit — the table's — because the second lands on
the path of the first; that is the collapse rule's reading of "the same
field", not this entry's.

**Addendum, 2026-10-12 — what the collapse rule makes of two acts, and a
rescore that is none.** The count is of acts only up to the collapse
rule, which folds an op into the LAST pending one when it lands on its
path — and an editor writes its criterion whole, at the criterion's path.
Two readings follow, each executed
(`TestOneStageIsOneEdit`):

- *Two acts on one criterion in a row read 1.* A tolerance typed, then a
  window set on the same criterion: the second op replaces the first, and
  the header reads `1 pending edit` — the criterion's.
- *A criterion revisited after another is its own edit.* Criterion 0,
  criterion 1, criterion 0 again read 3, since only the last op is
  superseded; criterion 0 put back as saved lands on the third and the
  count stays 3 while only criterion 1 differs from the file. Put that
  back too and the entry is as saved: 0.

Both are the rule's and stay: the preview's diff is what says what Save
writes, whatever the number. One count was wrong: `Session.rescore` —
`SessionView.rescore`, a capability's call — stages a flat test's tree,
and that op STAYS where a pending op edited the overrides the tree is
built from. It was staged as a call of its own, so one override staged
and a rescore read `2 pending edits`. The tree is part of the edit that
made it necessary (`Session.stage(part_of=…)`: the number of the test's
last pending op): 1.

