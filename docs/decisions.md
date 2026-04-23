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

## D27: Variable naming fallback for complex expressions

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
- **Defer-window rationale**: Horizon to feature-complete is ~2-4 months with a conference paper landing this week (idea-level, no live demo) and a public demo later. No planned work hooks `export_fmu`. Blast radius is contained: `export_fmu` has one caller (`produce_dymola_via_fmpy_baseline`); the chain has one caller (`cli._run_cross_backend_chains`); both gated on opt-in `requested_baselines`; zero tests currently set it. `translateModelFMU` API has been stable in Dymola for ~10 years. Code is well-defended (empty-return path, glob fallback, exception wrapping). Rollback cost if validation surfaces a bug at month 3: signature-level issue = tens of minutes; cwd/path quirk = half a day.
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


