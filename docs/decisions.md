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