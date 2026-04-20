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
| 40 | ~~Persistent Dymola workers with dynamic dispatch~~ | H | High | **DONE** — now the default (`--batch` inverse escape hatch). `DymolaInterface` auto-discovered (`.whl`/`.egg`), launches parallelized via `dymola_lock` patch, PID-tracked via `._dymola_process.pid`. Per-test timeout watchdog with disk-check rescue (trusts dsfinal.txt + reached stop_time). Worker restart (cap 3). Per-phase timing breakdown (translate/sim/other/total, rounded to 2 decimals), phase visible live on dashboard. Noise suppression during kills via `DymolaLogger` monkey-patch. `check-dymola` diagnoses loader |
| 41 | Dashboard refinements | L | Low | Filter buttons on dashboard (failed only, running only), keyboard-jump to next failure, dark mode. Index page got sortable columns + timing columns — dashboard could match |
| 42 | Quick-save selected results to reference | M | High | CLI: `accept --filter X` reads existing work_dir results and writes to reference, no resim. HTML report: "Copy accept command" button next to "Copy rerun command". Pairs with Phase 1.7 multi-baseline (accept writes to `primary` baseline by default; `--baseline <name>` overrides) |
| 43 | Per-test timeout + other per-test knobs in test_spec | L | Medium | Extend `test_spec.json` to allow per-test `timeout`, `batch_size_hint`, etc. Currently only simulation/comparison settings are per-test. Show in per-test report (Sim Params section). Belongs in same schema expansion as MetricTree |
| 44 | Dymola-interface resilience tracking | M | Medium | Port-bind race on worker startup (~1/20 fails), "Mismatch request/response ID in JSON-RPC call", "Remote end closed connection without response". Existing worker-restart rescues most. Could add: post-port-find `SO_REUSEADDR`+bind-probe before returning, broader noise-pattern filter, exponential-backoff retry on RPC mismatch |
| 45 | Python-driven tests (user-code backend) | H | High | New sibling backend `CustomPythonRunner` + `PythonTestRecognizer` for `.py` test files. Contract = `run(context) -> SimulationResult` dataclass (time, variables, diagnostics, metadata); framework owns persistence. User implementation is free — fmpy-with-custom-inputs, pyomo, scipy.integrate, custom solvers, CSV loaders — framework only enforces the return shape. Prerequisite: refactor `FmpyRunner` to produce `SimulationResult` first, proving the contract on the existing case. Single backend per testing.json stays the pattern (no per-test dispatch). Post Phase-6-MVP; pairs with the deferred D65 FMU-path semantic-gap closure. |
| 46 | Time-windowed leaf metrics | M | High | Scope any leaf metric to a `[t_start, t_end]` window — NRMSE on steady-state segment only, tube during transient only, final-only unchanged. Uniform field on all leaves (`"window": {"start": 10, "end": 50}`) rather than a new mode. Natural fit with MetricTree: compose window-scoped leaves via AND/OR for piecewise criteria. Ripples into 6.1 per-leaf UI (two inputs + range-brush on trajectory plot). |
| 47 | Time-array dedup in interactive.html (6.0.1) | M | High | Every variable currently embeds `act_time` + `ref_time` per trajectory — within a single test these are shared across all variables, so a 50-var test embeds 100 redundant time arrays. Dedup = emit one shared pair per test + reference-by-index per variable; compounds ~50% payload reduction on top of LTTB decimation. Would raise the 6.0 cap from 1000 back to 2000 under the same 5 MB budget. Touches template JS at 6+ call sites (`TRAJECTORIES[idx].act_time`) — coordinated Python + Jinja + JS change. |
| 48 | Lazy-fetch full-res on zoom (6.0.2) | M | High | Tier-2 of the payload strategy: JS detects zoom events via `plotly_relayout`, fetches `comparison_data.json` (full-resolution, already on disk next to the HTML), slices to the visible x-window, rerenders the window at native fidelity. Works from `file://` URLs — no server needed. Restores full visual fidelity for users who actually need it without inflating the standalone-HTML payload. |
| 49 | Per-test max_embedded_samples override (6.0.3) | L | Medium | Extend `test_spec.json` `comparison` block with an optional `max_embedded_samples` field — escape hatch for tests with pathological signals (stiff ringing, sharp events) that legitimately need higher embedded fidelity than the global cap. Per-test resolution order same as tolerance (variable_override → test → config → default). Small, additive; can land anytime. |

**Recommended order**: 1-3, 5-6, 8 are done. Next priorities: 14-16 (performance + ref link), 17-19 (quick HTML improvements), 11-12 (high-effort, high-value), or 7, 9 (medium effort). **#46 (time-windowed leaves)** is a Phase-6.1.1 inclusion candidate; **#45 (python-driven tests)** sits after the Phase 6 MVP alongside the deferred FMU-path semantic gap closure. **#47 / #48 / #49 are 6.0 follow-ups** (payload tier-2/tier-3); land them in that order for the interactive HTML to scale cleanly past 50 variables.

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

## Quick-save selected results to reference (#42)

- **Motivation**: Current accept workflow requires either `-i` (one-at-a-time review) or `--accept` (all at once). Neither is ideal when the user has already reviewed the HTML report and knows which subset of results to accept. Forcing a resim via `run --filter X --accept` is wasteful when the results are already on disk from the previous run.
- **Proposed CLI**: `modelica-testing accept --filter X` — reads `work_dir` artefacts and writes to reference, no simulation. Mirrors the `run --filter` UX exactly. Accepts `@file` and glob syntax.
- **HTML report**: add a "Copy accept command" button next to "Copy rerun command" on the index page. Uses the same row-selection UI. Generated command shape: `modelica-testing [--config ...] accept --filter "Model1,Model2"` (or `@selected.txt` for >3).
- **Phase 1.7 interaction**: once named baselines land, `accept` needs a `--baseline <name>` flag (defaults to `primary`). Accepting against `experiment` would require the user to have supplied experiment data separately — probably out of scope for the HTML-button flow; CLI-only.
- **Edge cases**: what if the work_dir is from a different filter-run than the one the user selected? Need to verify each selected test has actual results before writing. Silent skip vs. error is a UX call.

## Per-test timeout + other per-test knobs in test_spec (#43)

- **Motivation**: Today `timeout` is global (`config.timeout`, default 60s). Complex tests legitimately need longer; fast tests could benefit from shorter bounds to fail-fast. Users already think per-test ("this CIET nureth model takes 10+ minutes") — the config doesn't reflect that.
- **Additions to `test_spec.json`**:
  - `simulation.timeout` (seconds; overrides config default)
  - `simulation.batch_size_hint` (run alone / with others) — useful for resource-intensive tests
  - `simulation.memory_limit_mb` (future) — when the backend supports it
- **Report surface**: show the effective per-test timeout in the per-test report's "Simulation Parameters" section (like we already show stop_time, tolerance, method).
- **Schema compatibility**: additive — existing specs continue to use the global default.
- **Scope alignment**: belongs in the same schema pass that introduces MetricTree (Phase 3). Both are `test_spec` extensions; handle them together to avoid two schema bumps.

## Dymola-interface resilience tracking (#44)

- **Observed symptoms** (pre-existing, not regression):
  - *Port race*: `Worker N: start FAILED — The port XXXXX is already in use. Please use another port.` Occurs ~1/20 worker starts under `--parallel 20`. Framework auto-recovers via worker-restart logic (`persistent_runner.py:723`).
  - *RPC ID mismatch*: `DymolaInterface error: Mismatch request/response ID in JSON-RPC call.` Dymola's internal RPC got confused — usually a timed-out request whose late response collided with the next request's ID.
  - *Remote end closed*: `Remote end closed connection without response.` Dymola subprocess died or the HTTP channel dropped. Not currently in `_NOISE_PATTERNS`.
- **Possible mitigations**:
  - *Port race*: after `_find_available_port`, try a brief `socket.bind(port)` probe inside the lock; if it fails, loop to next candidate. Closes the find-to-bind window.
  - *RPC mismatch*: on this exception, force a worker restart rather than propagating (one-shot retry at the test level). Existing watchdog already does this via timeout — make the exception path symmetric.
  - *Remote end closed*: add to `_NOISE_PATTERNS` to suppress noise; treat as worker death and restart.
- **Diagnostic telemetry**: emit a per-worker `resilience_events.json` sidecar in `work_dir` counting start-failures, RPC-mismatches, and worker-restarts so users can see whether parallelism is healthy.
- **When to work on**: watch for frequency in real-world use. If it's 1-in-20 starts and the rescue always works, leave it. If it grows or masks real failures, promote to Phase 2 work.

## Python-driven tests (user-code backend) (#45)

- **Motivation**: The current `FmpyRunner` calls `fmpy.simulate_fmu(...)` with defaults — no external inputs, no start-value overrides, no CS-vs-ME choice, no step-level callbacks. D65 flagged this as the "FMU-path semantic gap" and scope-labeled the cross-backend chain **EXPERIMENTAL**. Beyond FMU-driven workflows, users also want to plug in custom solvers, pyomo post-processing, scipy.integrate models, analytical comparisons, or even CSV loaders into the same regression framework. The framework should *not* grow handlers for each — it should define a return contract and let user python produce trajectories however it wants.
- **Proposal — result contract**: Introduce a `SimulationResult` dataclass as the sole python-level contract:
  ```python
  @dataclass
  class SimulationResult:
      time: np.ndarray                       # 1D, monotonic
      variables: dict[str, np.ndarray]       # each aligned with time
      diagnostics: dict[str, float] = {}     # scalars (CPUtime, EventCounter-shape)
      metadata: dict[str, Any] = {}          # solver info, status, wall-time notes
  ```
  Framework owns persistence (cache format — npz, parquet, whatever — stays internal). Users never touch the cache layer. This kills the "is fmpy's npz format the standard?" question — the dataclass is the standard; fmpy is one producer of it.
- **Prerequisite refactor**: port `FmpyRunner` to produce `SimulationResult` internally *before* #45 lands. Proves the contract holds the existing case without regression and de-couples the cache format from fmpy's structured ndarray. Worth doing on its own merits regardless of #45.
- **Discovery + execution split (layers intact)**:
  - **Recognizer** (Discovery layer): new `PythonTestRecognizer` walks declared folders (`paths_include`), imports each `.py`, reads a module-level `TEST_SPEC = {...}` dict for tracked variables, stop_time, tolerances, metric tree, `against` targets. Produces `TestModel(source_file=py_path, ...)`. Slots into the PTA (D59) recognizer registry like any other.
  - **Backend** (Backend layer): new sibling `CustomPythonRunner` (alongside `DymolaRunner`, `FmpyRunner`). Takes any `TestModel` whose source is a `.py`, imports the module, calls `run(context) -> SimulationResult`, wraps the output through the standard cache + comparator path. User writes **one** file; framework supplies both recognizer and executor — zero plugin friction on the user side.
- **FmpyRunner naming**: stays `FmpyRunner`. `CustomPythonRunner` is a sibling, not a subclass. No visible "python-hosted" umbrella — users pick a named simulator in `testing.json`, same as today. Shared helpers (timeout, SimulationResult packaging) live in plain modules if they emerge, not a class hierarchy.
- **No per-test backend dispatch (YAGNI)**: single backend per testing.json stays the pattern. Real-world evidence: `examples/modelica/ModelicaTestingLib/` and `examples/fmu/` are already separate testing.json files today, each single-backend, and that works. Mixing scenarios are rare — Modelica-plus-python in the same project is usually "python tooling around Modelica models," not independent python tests; cross-implementation comparisons (Modelica-vs-Julia) are niche; the D63 cross-backend chain is one test verified two ways and is already handled without per-test dispatch. **Trigger for revisiting**: a real user arriving with a single-project mixing need that isn't already the cross-backend chain shape.
- **Security / trust boundary**: running user python is a trust shift. No sandboxing, no subprocess isolation — the user's code runs in the framework venv. Document explicitly: pointing `testing.json` at a python-test folder means trusting that code to run arbitrarily on your machine. Same trust model as pytest conftest.
- **Scope discipline — NOT a pytest replacement**: `run(context) -> SimulationResult` only. User python produces a trajectory; the framework's MetricTree scores it. User code does NOT run its own assertions or pass/fail logic. If someone wants property/fuzz testing, parametric sweeps that dispatch on internal state, or assertion-style unit tests, that's pytest's job (D66 scope identity).
- **Integration with Phase 6 MVP**: **out of scope**. Post-MVP; pairs with the deferred D65 FMU-path semantic-gap closure phase. The `SimulationResult` refactor is smaller and could land earlier as a standalone cleanup if a natural opportunity arises.

## Time-windowed leaf metrics (#46)

- **Motivation**: Many regression criteria are naturally piecewise — NRMSE matters during the steady-state tail, tube matters during the transient, final-only is window-irrelevant. Today users either accept a single global score or hand-author overlapping leaves that each recompute on the full trajectory. A time window is the missing axis.
- **Proposal**: A uniform `"window": {"start": <t>, "end": <t>}` field on every leaf (optional; defaults to full trajectory). Evaluated by slicing the actual + reference trajectories to the window before handing off to the existing `ComparisonMode`. Both endpoints optional — open-ended on either side supported.
- **Architecture fit**: One change in `comparison/tree_eval.py` — slice inputs before the `ComparisonMode.compare(...)` call. No change to the six compute modes. Leaves that already don't use time (range) treat the field as a no-op.
- **Not a combinator**: deliberately *not* a new AND-with-time-bounds combinator — that would explode the grammar. The window is a leaf property, scoped narrowly; AND/OR above still compose as today.
- **Composition**: NRMSE-on-[0,10] + NRMSE-on-[10,100] + tube-on-[10,50] under a root AND gives a piecewise regression contract. Same grammar, richer expressiveness.
- **Integration with Phase 6**:
  - **6.1 ripple**: every auto-derived UI panel gains two number inputs (`start`, `end`). Cheap — two fields, one new validator (`end > start`, both in trajectory range). Adds maybe ½ day to 6.1.1/6.1.2.
  - **Live preview**: the JS port slices the already-cached arrays; trivial for nrmse/tube/range/final-only. No new recompute logic.
  - **Custom override candidate**: a range-brush on the trajectory plot to visually pick window bounds (shaded region) — defer to post-MVP or batch with 6.1.4's range-plot-handles work (same UI primitive).
  - **6.4 patch shape**: patch paths like `/tests/<id>/metrics/<ptr>/window/start` fit the whitelist trivially. Round-trip fidelity applies as-is.
- **Recommendation for Phase 6**: the scalar-field version (two inputs, no brush) fits inside the MVP budget if bundled into 6.1.1. Flag as a candidate for inclusion at the **6.1.1 auto-derive machinery** checkpoint — if the config-to-UI generator can absorb window as a shared cross-mode subschema without leaking into each mode's Config, include it; if it forces mode-specific coupling, defer to post-MVP.
- **Exit criteria if included**: every mode's config gains an optional `window`; comparator slices before leaf evaluation; UI auto-derives; one new fixture test exercising a piecewise-AND tree.

## Time-array dedup in interactive.html — 6.0.1 follow-up (#47)

- **Motivation**: Phase 6.0 introduced an LTTB decimation cap at 1000 samples/var because the same trajectory currently embeds four arrays per variable (`act_time`, `act_values`, `ref_time`, `ref_values`). A 50-var test at the 5 MB budget only fits cap=1000, not the PHASE_6_PLAN-intended cap=2000. The binding constraint is structural redundancy — within any single test, every variable shares the same `act_time` (from one simulation) and `ref_time` (from one baseline). Embedding them 50 times per test is pure waste.
- **Proposal**: hoist the shared time arrays once to a per-test container:
  ```js
  const SHARED = { act_time: [...], ref_time: [...] };
  const TRAJECTORIES = [
    { index: 0, name: "h", act_values: [...], ref_values: [...] },
    ...
  ];
  ```
  JS accesses become `SHARED.act_time` instead of `TRAJECTORIES[idx].act_time`.
- **Compounding payload win**: halves the per-test trajectory payload. Lets us raise the default cap back to 2000 at the same 5 MB budget — same visual fidelity plan as PHASE_6_PLAN originally specified.
- **Scope & risk**: touches `reporting/plot_comparison.py` (trajectory-building) + `reporting/templates/interactive.html` (template structure + JS references at ~6 call sites: lines ~461, 713, 757, 904, 1082, 1307 of interactive.html). Coordinated Python + Jinja + JS change — non-trivial but contained.
- **Edge case**: some backends may return different `act_time` grids per variable (irregular output). Current code already allows this; dedup assumes a shared grid. Guard: detect mismatched grids, fall back to per-variable time embedding (current shape). Rare enough that the guard path rarely triggers.
- **Testing**: extend `test_report_size_budget.py` to assert that after 6.0.1 the cap=2000 still fits in 5 MB. Add JS smoke check via golden-file HTML snapshot (part of 6.1 testing infrastructure).
- **When**: after 6.0 MVP ships; bundled with or just before 6.1.1 work starts so the reporter is structurally clean for the per-leaf panel additions.

## Lazy-fetch full-res on zoom — 6.0.2 follow-up (#48)

- **Motivation**: The decimation cap in 6.0 gives a fast, standalone "at-a-glance" visual. Users zooming in for detail see the same down-sampled curve, which is wrong — the full-resolution data is already on disk next to the HTML in `comparison_data.json`. Tier-2 of the payload strategy: fetch it on demand.
- **Proposal**: hook Plotly's `plotly_relayout` event → read the new x-axis range → if the window contains significantly more decimated points than originally kept there (say > 2× compression loss), `fetch('./comparison_data.json')`, slice to the visible x-window, rerender that trace at full fidelity. Subsequent zooms reuse the already-fetched array.
- **File-URL compatibility**: `fetch('./comparison_data.json')` works from `file://` URLs in modern browsers without a server. No backend needed.
- **UX**: a subtle loading indicator during the first zoom fetch; silent on subsequent zooms within the cached window. "Full fidelity" badge when the visible window is un-decimated.
- **Fallback**: if the fetch fails (file missing, CORS edge case, protocol restriction), degrade silently to the decimated trace — no UX break.
- **Scope**: pure JS addition to `interactive.html` (~50 lines); no Python changes. Bundling pairs well with idea #17 (linked panel zoom) since both hook the same event.
- **When**: after 6.0 / 6.0.1 ship; likely a small independent slice before or alongside 6.1.

## Per-test max_embedded_samples override — 6.0.3 follow-up (#49)

- **Motivation**: Most tests fit comfortably under the global 1000-sample cap (or 2000 once dedup lands). A few pathological cases won't: stiff solvers producing dense ringing, sharp-event signals where LTTB still loses shape at the global cap, or long-horizon tests where the user genuinely wants more samples. Escape hatch avoids lowering the global default for everyone.
- **Proposal**: extend `test_spec.json` `comparison` block:
  ```json
  "comparison": {
    "tolerance": 1e-4,
    "max_embedded_samples": 10000,
    "variable_overrides": {...}
  }
  ```
  Resolution order: per-variable override (future, if needed) → per-test `comparison.max_embedded_samples` → `config.max_embedded_samples` → default.
- **Implementation**: mirror the existing `comparison_tolerance` plumbing (test_registry field, spec_parser read, reporter threading). Small change — ~30 lines across 3 files.
- **Size-regression safety**: the budget test should still pass; this just gives individual tests an opt-out, it doesn't raise the default.
- **When**: after 6.0 ships. Low urgency — ship only when a real test surfaces the need.
