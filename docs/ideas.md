# Future Ideas

## Priority Matrix

Ideas ranked by implementation ease and user impact. Ease: L (days), M (week), H (weeks+). Impact: how much it improves the daily workflow.

| # | Idea | Ease | Impact | Notes |
|---|------|------|--------|-------|
| 1 | ~~Filtered interactive review~~ | L | High | **DONE** — `-i [FILTER]` with categories: `failed`, `no-baseline`, `warnings`, `sim-failed`, `passed`, `all` |
| 2 | ~~Link to simulation artifacts from HTML~~ | L | High | **DONE** — `file://` links to dslog, translation_log, dsin, dsfinal, simulate.mos, dsres.mat; prominent for failed tests, collapsible for passing |
| 3 | ~~Full reference data in HTML reports~~ | L | Medium | **DONE** — added `status`, `date_added` to metadata table; diagnostic finals (CPUtime, EventCounter) were already in simulation stats table; fixed column header |
| 4 | Manifest compaction / ID reset | L | Low | Niche — only needed after major restructuring |
| 5 | ~~Condensed HTML with progressive disclosure~~ | M | High | **DONE** — key stats cards at top, condensed variable table, full details/stats/params/diagnostics in collapsible sections. Jinja2 templates + `comparison_data.json` sidecar |
| 6 | ~~Auto-generate HTML report suite~~ | M | High | **DONE** — `--report` generates index page + per-test reports with plots; JS filter buttons; opens in browser |
| 7 | Configurable variable ordering | M | Medium | Config plumbing + sort logic in reports and references |
| 8 | Interactive tolerance editing | M | Medium | Phase 1-4 DONE (NRMSE tolerance, tube with 3 width modes, interactive Plotly reports, Shift+click/drag tube editing on plot); remaining: cubic interpolation, independent upper/lower point arrays |
| 9 | One-click "open in Dymola" | M | Medium | .mos generation straightforward; protocol handler is platform-specific |
| 10 | Interactive setup wizard | M | Low | Nice onboarding but power users skip it quickly |
| 11 | Test discovery + potential test helper | H | High | Extends/folder scanning, user-configurable criteria, suggest tests needing spec/UnitTests |
| 12 | Model health analysis from reference data | H | High | Mining + ranking logic across all refs; powerful but complex |
| 13 | Dependency-aware test ordering | H | Medium | Requires dependency graph extraction from Modelica sources |
| 14 | Link to reference JSON file from HTML | L | Medium | Artifacts link to sim files but not the ref JSON; filename shown as text in ref_info but not clickable |
| 15 | WebGL for large traces (scattergl) | L | High | Switch to `scattergl` when trace >5k points; SVG unusable beyond ~10k |
| 16 | LTTB data decimation | M | High | Browser-side downsampling for 10k+ point traces; ~50 lines JS |
| 17 | Linked panel zoom | L | Medium | Sync x-axis across trajectory/abs-error/NRMSE panels via `plotly_relayout` |
| 18 | Worst-violation annotation | L | Medium | Arrow + callout at worst error point on trajectory plot; data already in context |
| 19 | Zoom-dependent statistics | L | Medium | Recompute NRMSE/max-error for visible window on zoom; ~40 lines JS |
| 20 | Sparkline error profiles | L | Low | Inline SVG polylines in variable sidebar; ~20 lines JS |
| 21 | Summary heatmap on index page | M | High | Variables × tests grid colored by NRMSE; click-to-navigate |
| 22 | Color-coded trajectories | M | Low | Line colored by local error magnitude via 5-8 band traces |
| 23 | Frechet distance & area between curves | L | Medium | `similaritymeasures` package; supplementary diagnostic metrics |
| 24 | Spectral coherence comparison | M | Medium | `scipy.signal.coherence`; frequency-domain agreement per variable |
| 25 | X-direction time tolerance | M | High | Handle solver-dependent event timing shifts; concept from pyfunnel |
| 26 | ISO 18571 failure diagnostics | M | Medium | Phase/magnitude/slope decomposition via `objective-rating-metrics` |
| 27 | Phase-space plots | M | Low | Variable vs variable plots; data already embedded |
| 28 | JSON size management for large suites | M | Medium | Sidecar files + lazy fetch when embedded JSON exceeds ~20 MB |
| 29 | Progressive enhancement: optional server | H | Medium | Thin FastAPI layer for accept-from-browser, re-compare, lazy loading |
| 30 | Notebook integration helper | L | Low | Data-loading utility for `comparison_data.json` in Jupyter |
| 31 | ~~Parallel process progress reporting~~ | M | High | **DONE** — `ProgressReporter` writes `status.json` + auto-refreshing `dashboard.html` to work_dir; per-test status, worker attribution, ETA, links to per-test work dir + reports |
| 32 | Parallelize report generation | M | Medium | `--report` is sequential matplotlib/Jinja2 per test; embarrassingly parallel |
| 33 | ~~Batch actions from HTML report~~ | M | High | **DONE** — checkbox column + action panel on index page. Bulk selectors (+ Failed/Sim Failed/No Baseline/Warnings/Stale), copy filter list, download selected.txt, copy run command. Live command preview |
| 34 | ~~NRMSE panel annotations + metric clarity~~ | L | Medium | **DONE** — avg-NRMSE line + shaded fail zone (above tolerance) on both matplotlib and Plotly NRMSE panels; "Pass/Fail Criterion" column added to variable table (mode-aware: NRMSE-vs-tolerance, %-inside-tube, or final-error-vs-tolerance) |
| 35 | ~~Filter by test list file~~ | L | Medium | **DONE** — `--filter` accepts glob, comma-separated list, or `@file` (one pattern per line, `#` comments) |
| 36 | Cross-platform reference comparison | H | High | Compare results across OS/simulator versions; use alternate ref as baseline |
| 37 | ~~Per-test report section reordering~~ | L | Low | **DONE** — Sim Params → Statistics → Diagnostics now sit above Variables in both `comparison.html` and `interactive.html`; structural warnings rendered as prominent yellow box near top |
| 38 | ~~Incremental run + report workflow~~ | M | High | **DONE** — persistent test_keys (`assign_test_keys` in `simulators/base.py`); `--merge` expands report scope to all known tests; `--rerun [CATEGORIES]` selects tests by prior status (default: failed; implies --merge); `last_run_at` shown in index column (relative time + tooltip) and per-test report header; stale rows greyed out |
| 39 | Live log tailing for per-test progress | M | Medium | Inside a Dymola batch, individual test transitions are invisible. Tail stdout/translation_log.txt and parse "Translating ModelX" / "Integration terminated" markers to flip dashboard status mid-batch. Per-backend log-marker hook |
| 40 | ~~Persistent Dymola workers with dynamic dispatch~~ | H | High | **DONE** — `--persistent` uses `DymolaInterface` (auto-discovered `.whl`/`.egg`). Per-test timeout watchdog, worker restart (cap 3), PID-tracked hard kill via psutil, per-worker startup progress, noise suppression during kills. `check-dymola` diagnoses loader |
| 41 | Dashboard refinements | L | Low | Per-test report link uses `ref_NNNN`/`test_NNNN` correctly; could add: filter buttons on dashboard (failed only, running only), keyboard-jump to next failure, dark mode |

**Recommended order**: 1-3, 5-6, 8 are done. Next priorities: 14-16 (performance + ref link), 17-19 (quick HTML improvements), 11-12 (high-effort, high-value), or 7, 9 (medium effort).

---

## Interactive setup wizard

- Guided terminal flow for creating or editing `testing.json`
- Prompts for: library path, simulator selection (scan for installed Dymola versions), dependencies, reference root location
- If `testing.json` already exists, offer to edit fields interactively
- Reduces onboarding friction for new libraries — no need to hand-write JSON

## Test discovery + potential test helper

- Find candidate test models by scanning for classes that extend a specific base (e.g., `extends Modelica.Icons.Example` or a custom test icon)
- Alternatively, discover all models within a specific package/folder (e.g., everything under `MyLib.Examples.*`)
- Complements UnitTests and test_spec: UnitTests requires in-model instrumentation, test_spec requires manual enumeration, extends-based discovery is automatic
- Could be a CLI command like `modelica-testing find-tests --extends Modelica.Icons.Example --package MyLib.Fluid`
- **Interactive selection mode**: present discovered candidates in a checklist, user selects which to add to `test_spec.json` — avoids manually writing JSON for dozens of models
- Could show which candidates already have UnitTests or test_spec entries (skip those)
- Variable selection: offer `["*"]` (track all), `[]` (simulate only), or prompt for specific patterns per model
- **Potential test helper**: identify models that *could* be tested but aren't — no UnitTests component and no test_spec entry. User-configurable criteria (extends a certain class, lives in a certain package, etc.) defined in `testing.json`. Output: list of candidates with recommendation to add spec or UnitTests. Useful for tracking coverage gaps as a library grows.

## Dependency-aware test ordering

- Parse model dependencies (extends, component instantiation) to build a dependency graph
- Order tests bottom-up: base models first, composed models later
- Benefits:
  - Early failure of a base model explains why complex models also fail — avoids chasing symptoms in the wrong model
  - Could skip dependent tests when a base model fails (fail-fast with clear root cause)
  - Provides natural priority when time-constrained (test fundamentals first)
- Challenges:
  - Dependency parsing requires reading Modelica model structure (extends chains, component types)
  - Cross-package dependencies add complexity
  - Some test models may not have clean dependency relationships

## Manifest compaction / ID reset

- Over time, obsolete test IDs accumulate in the manifest (marked obsolete but never reused)
- A "compact" operation would: remove all obsolete entries, renumber active tests starting from 0001, rename reference files to match, and rewrite the manifest
- Useful when a library has undergone major restructuring and the manifest has significant gaps
- Should be a deliberate, manual operation (not automatic) since it rewrites all reference files

## Configurable variable ordering in references and reports

- Allow users to define preferred variable display order via config (e.g., in `testing.json` or a separate preference)
- Pinned variables appear first in reference JSON, HTML reports, and plots; remaining variables follow in their natural order
- Default pins could include diagnostic variables (`CPUtime`, `EventCounter`) at the top
- Could be simulator-specific (Dymola diagnostics differ from OpenModelica) or user-specific
- Keeps the most important signals visible without scrolling through dozens of variables

## ~~Filtered interactive review~~ (DONE)

- **Implemented**: `-i [FILTER]` accepts an optional filter value
- Categories: `failed`, `no-baseline`, `warnings`, `sim-failed`, `passed`, `all`
- Non-matching tests are silently skipped without user interaction
- Default `-i` (no filter) prompts for everything, same as before

## ~~Auto-generate HTML report suite with navigation~~ (DONE)

- **Implemented**: `--report` flag on `run` and `compare`
- Generates per-test comparison HTML with plots in `reports/<model_name>/`
- Index page (`reports/index.html`) with pass/fail summary, worst NRMSE, variable counts, warning counts
- JS filter buttons (All, Failed, Sim Failed, No Baseline, Warnings, Passed)
- Auto-opens index in browser
- Future: swap matplotlib PNGs for Plotly inline charts for interactive plots

## ~~Condensed HTML report with progressive disclosure~~ (DONE)

- **Implemented**: Jinja2 template with progressive disclosure layout
- Key stats cards at top: worst NRMSE, continuous states, nonlinear count/max, CPUtime, events (with change highlighting)
- Condensed variable table: status + name + NRMSE only
- Full variable details (RMSE, range, max error, finals) in collapsible section
- Statistics, simulation parameters, and diagnostics all in collapsible `<details>` sections
- Trajectory plots open by default; everything else collapsed
- Also outputs `comparison_data.json` sidecar for downstream tooling

## ~~Link to simulation artifacts from HTML reports~~ (DONE)

- **Implemented**: `file://` links to `dslog.txt`, `translation_log.txt`, `dsin.txt`, `dsfinal.txt`, `simulate.mos`, `dsres.mat`
- Failed tests: prominent yellow box with artifact links
- Passing tests: collapsible `<details>` section
- Only shows links for files that exist in the test directory

## One-click "open in Dymola" from interactive mode

- In interactive mode (or HTML reports), provide a clickable link or command that opens Dymola, loads all dependencies + the library, and navigates to the failed model
- Could generate a temporary `.mos` script that does the loading and opens the model, then launch Dymola with it
- Terminal hyperlinks (OSC 8 escape sequences) are supported by modern terminals (Windows Terminal, iTerm2, etc.) — could link to the `.mos` script or a `file://` URL
- In HTML reports, this is straightforward — a link that triggers a `.mos` download or `dymola://` protocol handler
- Useful for debugging failures: see the model, inspect equations, re-simulate with different settings

## Interactive tolerance editing and tolerance tubes (partially done)

**Phase 1 — DONE**: Per-test and per-variable NRMSE tolerance overrides via `comparison.variable_overrides` in test_spec.json and reference JSON. Multi-level tolerance resolution. `tolerance_used` recorded per variable.

**Phase 2 — DONE**: Tube-based comparison mode. Configured per-variable with `"mode": "tube"`. Three width modes via `tube_width_mode`: `"rel"` (fraction of |reference|, default in UI), `"band"` (offset in signal units), `"absolute"` (literal y-axis bounds). Legacy format: `max(tube_abs, tube_rel * |ref|)`. Supports constant and time-varying tubes via `tube_points`. Strict pass/fail. Metrics: `tube_points_inside`, `tube_worst_violation`, `tube_worst_violation_time`. NRMSE still computed alongside.

**Phase 3 — DONE**: Interactive Plotly reports (`interactive.html`). All traces on shared x-grid for unified hover. Live tolerance editing with per-variable overrides. Error overlay dropdown (signed, abs, NRMSE on right y-axis). Tube mode: inline editor with point table, synced/unsynced upper-lower, three width modes, time-varying interpolation. Tube visualization uses `fill: 'toself'` polygon for reliable rendering. Export panel with live JSON, copy/download. CLI `spec-update` applies tolerance JSON to `test_spec.json`.

**Phase 4 — DONE**: Interactive tube editing on plot. Shift+click to add control points, Shift+drag to move, Shift+right-click to delete. Control points decoupled from reference grid (placed at arbitrary times). Rendering grid merges ref grid + control point times so tube lines pass through markers. Scroll-wheel zoom works alongside editing. CP markers shown as triangle-up/down on upper/lower bounds.

**Remaining**:
- Independent upper/lower point arrays — allow different numbers of control points per side (e.g., 3 upper, 5 lower). Requires splitting the `{time, upper, lower}` tuple model into two separate spline definitions, with corresponding changes to table UI, export format, and backend comparator. Per-point sync/unsync (some points symmetric, others not) is a lighter variant of the same idea.
- Cubic interpolation mode for time-varying tubes

## Model health analysis from reference data

- Mine stored reference data (statistics + variable trajectories) to surface potential model quality issues across the library
- **Structural complexity flags**: rank models by number of nonlinear systems (before/after manipulation), numerical Jacobians, mixed systems — highlights models that may benefit from simplification or reformulation
- **Event-heavy models**: flag models with high EventCounter or high state_events — candidates for smoother formulations or noEvent() wrapping
- **Simulation cost outliers**: rank by CPUtime, identify models where cost is disproportionate to complexity (e.g., simple model but high CPU)
- **Trajectory anomalies**: detect variables with extreme dynamic range (values spanning many orders of magnitude), sudden jumps to +/- infinity and back, or values that collapse to zero mid-simulation — often indicates numerical issues or missing limiters
- **Trend analysis**: compare statistics across reference updates (date_added vs last_updated) to detect regressions — did a model get slower, gain events, or grow more nonlinear systems over time?
- Could be a CLI command like `modelica-testing analyze` or `modelica-testing health` that produces a summary table sorted by severity
- Output as console table, CSV for spreadsheet analysis, or HTML dashboard
- Useful for library maintainers to prioritize optimization work across hundreds of models

## Link to reference JSON file from HTML report

- Artifacts section links to simulation files (dslog.txt, dsin.txt, dsres.mat, etc.) but not to the reference JSON file (e.g., `ref_0042.json`)
- The reference filename is already shown as text in the "Reference Information" table via `ref_info` (see `plot_comparison.py:98-104`) but it's not a clickable link
- Need to resolve the full path to the reference file and add a `file://` URI link, either as an artifact entry or as a clickable link in the ref_info table
- Requires passing the resolved reference file path through to `_build_template_context()`

## Performance: WebGL rendering for large traces (scattergl)

- Switch Plotly traces from `scatter` (SVG) to `scattergl` (WebGL) when point count exceeds ~5k per trace
- SVG creates one DOM element per point — sluggish on zoom/pan beyond 5k, unusable beyond ~10k
- `scattergl` renders all points in a single WebGL draw call
- Decision can be made at report generation time: set `type: 'scattergl'` in the JSON context per-trace based on array length
- Caveats: `scattergl` supports `fill:'toself'` for tube envelopes but `hoveron:'fills'` doesn't work (hover only on data points); line `dash` styles are limited; `shape: 'spline'` not supported
- Threshold guidelines: <5k SVG fine, 5k-100k use scattergl, >100k also needs LTTB

## Performance: LTTB data decimation for large traces

- Implement browser-side Largest-Triangle-Three-Buckets (LTTB) downsampling (~50 lines of JS)
- LTTB preserves visual peaks and valleys far better than uniform sampling or min/max binning
- Downsample to ~2k display points on initial load; re-run on zoom via `plotly_relayout` with visible window data
- LTTB on 100k→2k runs in ~5-15ms in modern browsers — fast enough for interactive zoom
- Only activate above a threshold (e.g., 10k points)
- Alternative: min-max binning (keeps min and max per bucket, faster, preserves extremes perfectly but creates "fuzzy band" look)

## Performance: JSON embedding size management

- Currently all trajectory data is embedded as JSON in the HTML
- Parse time benchmarks: <10 MB imperceptible, 10-50 MB noticeable (50-800ms), >50 MB problematic (1-10s)
- Memory: a 50 MB JSON string becomes ~100-200 MB of live JS objects
- Solution: `--report-mode standalone|directory` CLI flag
  - Under 20 MB: embed everything (current behavior)
  - Over 20 MB: HTML index + per-test sidecar JSON files loaded via `fetch()`
  - HTML detects `file://` vs `http:` protocol and adapts
- Alternative: base64-encoded Float64Array is 10-50x faster to parse than JSON number arrays (8 bytes per number vs ~15-20 bytes in JSON text)

## Linked panel zoom synchronization

- Three panels per variable (trajectory, abs error, NRMSE) are currently independent Plotly charts — separate divs, not subplots, no shared axes
- Zooming on the trajectory panel should sync x-axis range to the error panels
- Implementation: hook `plotly_relayout` on `plot-{idx}`, propagate `xaxis.range` to `plot-abserr-{idx}` and `plot-nrmse-{idx}` via `Plotly.relayout()`
- Guard against infinite event loops (zoom on panel A triggers relayout on B which triggers relayout on A)

## Worst-violation annotation overlay

- Mark the worst error point on the trajectory plot with an arrow annotation and callout
- Data already in template context: `max_abs_error`, `max_abs_error_time` (line 175-176 of `plot_comparison.py`); for tube mode: `tube_worst_violation`, `tube_worst_violation_time`
- The abs-error panel already draws a vertical dotted line at worst error time, but the main trajectory plot has no marker
- Show only for failed variables to avoid clutter; could add a "Show annotations" toggle
- ~15 lines of JS: add `Plotly.relayout(el, {annotations: [...]})` after `renderPlots()`

## Zoom-dependent statistics

- Small stats bar between trajectory and error panels showing NRMSE, max error, and point count for the visible x-range
- Hook `plotly_relayout` → get `xaxis.range[0/1]` → slice cached arrays (`el._commonTime`, `el._actOnCommon`, `el._refOnCommon`) → recompute
- Performance: <1ms for typical sizes, ~5ms for 100k points; `plotly_relayout` fires once per zoom (not continuously during drag)
- Display in a `<div id="zoom-stats-{idx}">` between panels; empty when full range is shown

## Sparkline error profiles in variable sidebar

- Tiny inline SVG polylines (~200 bytes each) next to each variable name showing error distribution at a glance
- Downsample abs-error to ~50 points via uniform stepping
- Much lighter than Plotly micro-charts (which add ~50ms init overhead each)
- Add an "Error Profile" column to the existing variable table
- ~20 lines of JS + one `sparklineSVG()` helper function

## Summary heatmap on index page

- Variables × tests grid colored by NRMSE on the index page for spotting patterns across a test suite
- Plotly heatmap handles 500 tests × 20 variables (10k cells) fine (uses WebGL automatically)
- Normalize values as `nrmse / tolerance` so 1.0 is the pass/fail boundary; diverging colorscale (green below, red above, gray for no-baseline, black for sim-failed)
- Click-to-navigate via `plotly_click` event
- Requires extending `generate_report_suite()` to pass per-variable NRMSE arrays (currently only `worst_nrmse`)
- Ragged matrix (tests have different variable counts): use union of variable names with null for missing entries
- Add as a toggle "Table / Heatmap" view on the index page; add Plotly CDN link to `index.html`

## Color-coded trajectories by local error

- Color the actual simulation line by local error magnitude (green→red gradient)
- Plotly can't do per-point line color on `type: 'scatter'` with `mode: 'lines'`
- Approach: quantize errors into 5-8 color bands, render as separate traces with null-gap segments between each point pair
- Wire as an option in the existing overlay dropdown ("Color by Error")
- Performance: 5 traces with ~5k points each (including nulls) is fine for Plotly

## New comparison metrics: Frechet distance & area between curves

- `pip install similaritymeasures` (v1.4.0, actively maintained, numpy/scipy only)
- **Frechet distance**: worst-case shape deviation when traversing both curves simultaneously — captures shape similarity better than NRMSE for signals with slight time shifts; similar to `max_abs_error` but geometrically aware
- **Area Between Curves**: intuitive "total deviation" metric complementing NRMSE (average) and max_abs_error (single point)
- Functions take `(n, 2)` arrays (time-value pairs) — straightforward to integrate
- Add as optional supplementary diagnostic metrics alongside existing NRMSE/tube

## Spectral coherence comparison

- `scipy.signal.coherence` — already a transitive dependency (no new install)
- Per-frequency agreement score (0-1): 1.0 = perfect match at that frequency, 0.0 = unrelated
- Flags cases where low-frequency behavior matches but high-frequency dynamics diverge (which NRMSE averages away)
- Particularly useful for oscillatory models (HVAC, power systems, mechanical vibration)
- Precompute in Python via `scipy.signal.welch` (error PSD) and `coherence`, embed as additional fields in template context
- Render as collapsible "Frequency Analysis" panel per variable with two subplots: error PSD (log scale) and coherence (0-1)
- Make scipy an optional dependency for this feature

## X-direction (time) tolerance

- Current comparator assumes exact time alignment by interpolating actual onto reference time grid
- Small time shifts from solver differences (event timing, step size selection) cause false failures
- Concept from **pyfunnel** (LBNL, `pip install pyfunnel`, v2.0.1): builds L1-norm tolerance rectangles around each reference point with both x and y tolerance, constructs upper/lower envelope polygons from rectangle corners
- Could either use pyfunnel directly as an alternative comparison backend, or adapt its x-tolerance concept into the existing comparator
- Even a small x-tolerance (1-2 solver steps) would significantly reduce false failures from solver-dependent event timing

## ISO 18571 failure diagnostics

- `pip install objective-rating-metrics` (v1.3, actively maintained, from VSI TU Graz / OpenVT)
- Implements ISO/TS 18571:2024 — decomposes comparison into four independent sub-metrics: **corridor**, **phase**, **magnitude**, **slope** (each scored 0-1)
- When a test fails, sub-ratings tell you *why*: magnitude offset? phase shift? wrong slope? Much richer than a single NRMSE
- Originally from automotive crash simulation validation (CORA method)
- Add as optional diagnostic mode alongside primary pass/fail; integrate ratings into `VariableComparison` and reporting

## Phase-space plots

- Plot variable A vs variable B (not vs time) — useful for thermodynamic cycles, control loops, mechanical oscillations
- All trajectory data already embedded as `TRAJECTORIES` — purely a JS addition with two dropdown selectors
- Compare reference vs actual paths in phase space
- Caveat: time is invisible in phase plots — a point at the correct (x,y) but wrong time looks fine; add time-colored segments or interval markers to show direction/timing
- Quantitative phase-space comparison (Frechet distance on the path) is a natural extension

## Progressive enhancement: optional server mode

- Keep self-contained HTML as the base; if served through a thin FastAPI server, enable additional features
- "Accept as baseline" button → server writes to reference store
- "Re-compare with different tolerance" → server triggers comparison code
- Lazy data loading for large suites → `fetch('/api/tests/{id}/data')` instead of embedded JSON
- HTML detects `window.location.protocol === 'http:'` and enables/disables server features
- The server would be minimal (~100-200 lines of FastAPI)
- Defer until standalone workflow limitations are felt

## Notebook integration helper

- Provide a data-loading utility that reads `comparison_data.json` (already written as sidecar) into a convenient format
- For ad-hoc Jupyter deep dives into specific test failures: custom analysis, multi-baseline overlays, frequency-domain inspection
- Not a parallel reporting pipeline — just a clean API for engineers who want to do custom analysis
- Use static Plotly plots in notebooks (survive `nbconvert` to HTML); ipywidgets for live sessions but accept they won't export

## Parallel process progress reporting

- Currently, parallel simulation (`--parallel N`) shows no output until all workers finish — looks frozen on large suites
- Standard approaches:
  - **tqdm**: lightweight progress bar with ETA; supports parallel via `tqdm.contrib.concurrent` or manual `update()` calls from worker callbacks
  - **rich**: fancier — live table with per-worker status, spinners, ETA. Heavier dependency but excellent terminal UX
  - **Custom callback**: workers post status updates (model name, pass/fail, timing) to a queue; main thread renders a rolling summary. No external dependency
- Key constraint: Dymola batch execution groups multiple tests per worker, so progress granularity is per-batch, not per-test. Could report "Batch 3/10: translating ModelName..." by parsing partial dslog output
- Consider also a `--quiet` / `--verbose` flag to control output level

## Parallelize report generation

- `--report` generates matplotlib PNGs + Jinja2 HTML sequentially per test — dominates wall time on large suites (matplotlib is slow for many plots)
- matplotlib releases the GIL during rendering, so `ThreadPoolExecutor` or `ProcessPoolExecutor` should work
- Each test's report generation is independent (separate output directory, no shared state)
- Could also defer PNG generation entirely: the interactive report uses Plotly (no PNGs needed), so PNGs could be optional (`--report --no-png` or only generate on demand)
- Alternative: switch to Plotly-only static export via `kaleido` — faster than matplotlib for bulk generation

## Batch actions from HTML report

- Add checkboxes next to each test in the index page for multi-select
- Action buttons: "Rerun selected", "Accept selected", "Mark obsolete", "Disable"
- Output: generate a ready-to-paste CLI command (e.g., `modelica-testing run --filter Model1,Model2,Model3`) or a JSON payload
- Lighter than the full server approach (#29) — purely client-side JS that builds command strings
- Could also generate a filter file compatible with `--filter @tests.txt` (#35)
- "Mark obsolete/disable" would need a status field in test_spec or reference JSON — design this carefully to avoid scope creep

## NRMSE panel annotations + metric clarity

- On the per-variable NRMSE error plot:
  - Vertical dashed line at the time of max absolute error (data already in `max_abs_error_time`)
  - Horizontal line for average NRMSE across the trajectory
  - Horizontal line at the tolerance threshold (data already in `tolerance_used`)
  - Shaded region above tolerance to visually show "fail zone"
- **Metric clarity**: clearly label which metric drives pass/fail — currently NRMSE for trajectory mode, fraction-inside for tube mode, relative error for final-only
  - Add a "Pass/Fail Criterion" label in the variable detail: e.g., "NRMSE 2.3e-4 < tolerance 1e-3 → PASS"
  - For tube mode: "98/100 points inside tube → FAIL (requires 100%)"
  - Consider whether users should be able to select alternative pass/fail metrics (e.g., max error instead of NRMSE) — this would tie into the strategy pattern refactoring
- Related: #18 (worst-violation annotation on trajectory plot), #19 (zoom-dependent statistics)

## Filter by test list file

- Allow `--filter` to accept a file path: `--filter @tests.txt` where each line is a model ID or glob pattern
- Also support comma-separated inline lists: `--filter "Model1,Model2,Model3"`
- Complements the existing `--filter` pattern matching with explicit lists
- Natural pairing with #33 (batch actions from HTML report) — report exports a filter file, CLI consumes it
- Implementation: detect `@` prefix → read file, split lines, filter whitespace/comments

## Cross-platform reference comparison

- Reference results are partitioned by `<simulator>/<os>/` — each platform has its own baselines
- Use case 1: **Cross-platform summary** — after running on Windows and Linux, show a table of which tests differ across platforms (identifies platform-sensitive models)
- Use case 2: **Use alternate platform as baseline** — run on Linux but compare against Windows references (useful when Linux references don't exist yet, or to verify platform equivalence)
  - CLI: `--reference-os windows` to override the auto-detected OS when looking up references
  - Or: `--cross-compare windows` to compare against both native and alternate references
- Use case 3: **Simulator version comparison** — same OS but different Dymola versions (e.g., "Dymola 2024" vs "Dymola 2025" in the simulator name). Currently these map to the same backend ("Dymola") and same reference partition. Would need version-aware partitioning or a comparison mode
- Output: summary table or heatmap of per-test differences across platforms/versions

## Per-test report section reordering

- Currently the per-test comparison report (comparison.html / interactive.html) shows variables first, with simulator info, statistics, and diagnostics in collapsible sections below
- Preferred order: **Simulator → Statistics → Diagnostics → Variables** (all still collapsible)
- Rationale: when investigating a failure, you want structural context (did nonlinear system count change? did events increase?) before diving into per-variable error plots
- Implementation: reorder sections in the Jinja2 templates (`comparison.html`, `interactive.html`)

## Incremental run + report workflow

- **Problem**: running 200 tests takes 30 minutes. If 3 fail and you fix the models, you want to rerun just those 3 and see an updated full report — not wait another 30 minutes
- **Current state**: `run --filter Model1,Model2` reruns those tests, but creates a new batch manifest that only covers 2 tests. `compare --report` reads the latest manifest and only sees those 2. The other 198 tests' results are still on disk in their `test_NNNN` directories but aren't picked up
- **Proposed workflows**:
  - **`run --rerun-failed --report`**: automatically filter to previously-failed tests, rerun them, merge results with previous passing tests, generate combined report
  - **`run --filter X --merge --report`**: rerun filtered subset, merge fresh results into the existing work directory (update manifest entries for rerun tests, keep the rest), then generate a full report
  - **`compare --report`** (already exists): regenerate report from whatever results are on disk — no simulation. Currently works but only reads the latest batch manifest. Could be enhanced to read *all* manifests or scan test directories directly
  - **`report` command** (new): purely regenerate reports from existing work directory + references, no simulation or comparison logic. Useful after accepting new baselines to get an updated report
- **Key design question**: should `run --filter` overwrite test directories in-place (allowing natural merging) or always create fresh directories? Currently it creates `test_0001` etc. per batch — a selective rerun of 3 tests creates `test_0001`, `test_0002`, `test_0003` which collides with the full run's directories
  - Option A: namespace by batch (e.g., `batch_001/test_0001`) — no collisions but complicates merging
  - Option B: use stable test keys derived from model ID (e.g., `test_<hash>`) — reruns naturally overwrite the same directory
  - Option C: keep current scheme, add a merge step that copies fresh results into the canonical work directory
- Related: #33 (batch actions from report), #35 (filter by test list)

## ~~Full reference data representation in HTML reports~~ (DONE)

- **Implemented**: All reference JSON fields now represented in HTML
- Added `status` and `date_added` to metadata table
- Diagnostic finals (CPUtime, EventCounter) were already merged into simulation stats table with change highlighting
- Fixed metadata table column header from "Simulation" to "Current"
